import os
import re
import socket
import time
from urllib.parse import urlsplit
from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

# Handle Supabase Pooler usually requiring transaction mode or specific port
# The .env has keys like SUPABASE_DB_HOST, etc.
# Or we can construct from standard postgres connection string.
# Using the .env values if available, or falling back to a constructed string.

DB_USER = os.getenv("SUPABASE_DB_USER", "postgres")
DB_PASSWORD = os.getenv("SUPABASE_PASSWORD", "")
DB_HOST = os.getenv("SUPABASE_DB_HOST", "db.ourddwzetcbdjnbvamgj.supabase.co")
DB_PORT = os.getenv("SUPABASE_DB_PORT", "5432")
DB_NAME = os.getenv("SUPABASE_DB_NAME", "postgres")

from sqlalchemy.pool import QueuePool

# Prefer DATABASE_URL when explicitly set. A reescrita de porta 5432→6543 só
# se aplica ao POOLER (modo transação): URLs diretos (db.<ref>.supabase.co:5432)
# devem ser honrados como estão — reescrever a porta de um host direto apontava
# para um endpoint inexistente.
_DATABASE_URL_ENV = os.getenv("DATABASE_URL", "").strip()
if _DATABASE_URL_ENV:
    if "pooler.supabase.com" in _DATABASE_URL_ENV:
        SQLALCHEMY_DATABASE_URL = _DATABASE_URL_ENV.replace(":5432", ":6543")
    else:
        SQLALCHEMY_DATABASE_URL = _DATABASE_URL_ENV
elif DB_HOST and "supabase.co" in DB_HOST and DB_PASSWORD:
    SQLALCHEMY_DATABASE_URL = f"postgresql://postgres:{DB_PASSWORD}@{DB_HOST}:5432/{DB_NAME}"
else:
    SQLALCHEMY_DATABASE_URL = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

# ── Resolver do host do banco em camadas ─────────────────────────────────────
# O host direto do Supabase é IPv6-only (não existe registro A) e o resolver
# do Windows desta máquina falha intermitentemente (getaddrinfo 11001/11004)
# mesmo com o DNS respondendo — cada falha devolvia 500 em TODOS os endpoints
# (get_current_user → db.query) e derrubava jobs do worker com erro vazio.
# Camadas: 1) getaddrinfo (resolver SO, quando saudável); 2) dnspython
# consultando 8.8.8.8/1.1.1.1 direto (bypassa o resolver do SO); 3) último IP
# válido em cache ("last known good", mesmo expirado).
_IPS_CACHE = {"ips": None, "expires": 0.0}
_IP_TTL = 300.0
_DNS_PUBLICOS = ("8.8.8.8", "1.1.1.1", "8.8.4.4")
_IP_TURNO = {"i": 0}


def _resolver_todos(hostname):
    """Todos os IPs do host (A records; AAAA só se não houver A). O DNS do
    pooler do Supabase retorna múltiplos IPs e nem todos respondem — fixar
    um único IP fazia threads caírem no IP morto e queimarem connect_timeout
    x3 (~30s) por tentativa, esgotando o pool (estouro de 2026-11-06)."""
    try:
        infos = socket.getaddrinfo(hostname, None)
    except Exception:
        return []
    v4 = sorted(set(i[4][0] for i in infos if i[0] == socket.AF_INET))
    if v4:
        return v4
    return sorted(set(i[4][0] for i in infos if i[0] == socket.AF_INET6))


def _dns_direto(hostname):
    """Consulta DNS pública direto por UDP (dnspython), bypassando o resolver
    do sistema operacional. Retorna todos os registros encontrados."""
    try:
        import dns.resolver
        r = dns.resolver.Resolver(configure=False)
        r.nameservers = list(_DNS_PUBLICOS)
        r.lifetime = 4
        for tipo in ("A", "AAAA"):
            try:
                resp = r.resolve(hostname, tipo)
                if resp:
                    return sorted(set(str(x) for x in resp))
            except Exception:
                continue
    except Exception:
        pass
    return []


def _hosts_do_banco() -> list:
    """IPs candidatos do host do banco, com cache, rotação e fallback em camadas.
    A rotação distribui o ponto de partida: threads distintas começam por IPs
    distintos, e um IP morto custa no máximo um connect_timeout por thread."""
    now = time.time()
    if _IPS_CACHE["ips"] and now < _IPS_CACHE["expires"]:
        ips = _IPS_CACHE["ips"]
    else:
        ips = _resolver_todos(_HOST_DB) or _dns_direto(_HOST_DB)
        if ips:
            _IPS_CACHE["ips"] = ips
            _IPS_CACHE["expires"] = now + _IP_TTL
        elif _IPS_CACHE["ips"]:
            # Nada resolveu agora: usa os últimos IPs conhecidos mesmo expirados —
            # conectar num IP velho tem chance de funcionar; falhar tem certeza.
            ips = _IPS_CACHE["ips"]
        else:
            raise OSError(f"DNS indisponível: impossível resolver {_HOST_DB}")
    i = _IP_TURNO["i"] = (_IP_TURNO["i"] + 1) % len(ips)
    return ips[i:] + ips[:i]


_parsed_url = urlsplit(SQLALCHEMY_DATABASE_URL)
_HOST_DB = _parsed_url.hostname or DB_HOST
_DB_PORT_NUM = _parsed_url.port or 5432
_DB_USER_URL = _parsed_url.username or DB_USER
_DB_PASS_URL = _parsed_url.password or DB_PASSWORD
_DB_NAME_URL = (_parsed_url.path or "/postgres").lstrip("/") or "postgres"

import psycopg2

_PS2_KW = {
    "sslmode": "require",
    "prepare_threshold": None,
    "keepalives": 1,
    "keepalives_idle": 30,
    "keepalives_interval": 10,
    "keepalives_count": 5,
    "connect_timeout": 5,
    # Lock-wait no índice único de base_guias derrubava threads presas por
    # tempo indeterminado e esgotava o threadpool (503 de 2026-09-02): com
    # lock_timeout o runner falha rápido e o job vai para quarentena em
    # segundos em vez de congelar o backend.
    "options": "-c lock_timeout=5000 -c idle_in_transaction_session_timeout=15000",
}


def _criar_conexao():
    """Creator do pool: conecta por IP tentando TODOS os candidatos em cada
    tentativa (resolver em camadas + rotação). sslmode=require mantém TLS
    obrigatório; conectar por IP é seguro porque 'require' não faz verificação
    de hostname (comportamento igual ao DSN antigo)."""
    ultimo = None
    for tentativa in range(3):
        try:
            ips = _hosts_do_banco()
        except OSError:
            if ultimo:
                raise
            continue
        for host_ip in ips:
            try:
                return psycopg2.connect(
                    host=host_ip,
                    port=_DB_PORT_NUM,
                    dbname=_DB_NAME_URL,
                    user=_DB_USER_URL,
                    password=_DB_PASS_URL,
                    **_PS2_KW,
                )
            except psycopg2.OperationalError as exc:
                ultimo = exc
                # IP pode ter ficado obsoleto: invalida cache para re-resolver
                _IPS_CACHE["ips"] = None
                _IPS_CACHE["expires"] = 0.0
        time.sleep(0.3 * (tentativa + 1))
    raise ultimo


engine = create_engine(
    "postgresql://",  # apenas seleção de dialect; conexão real via creator
    creator=_criar_conexao,
    use_native_hstore=False,
    # Pool limitado com pre_ping substitui o NullPool: NullPool abria conexão
    # nova (TCP+SSL) por request — na rede degradada cada handshake era uma
    # chance a mais de "SSL connection has been closed unexpectedly", além do
    # custo de ~600ms por request. O pool reutiliza conexões saudáveis e o
    # pre_ping descarta as mortas antes de usar.
    poolclass=QueuePool,
    pool_size=15,
    max_overflow=25,
    pool_timeout=30,
    pool_recycle=300,
    pool_pre_ping=True,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
