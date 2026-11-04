import os
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

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    use_native_hstore=False,
    # Pool limitado com pre_ping substitui o NullPool: NullPool abria conexão
    # nova (TCP+SSL) por request — na rede degradada cada handshake era uma
    # chance a mais de "SSL connection has been closed unexpectedly", além do
    # custo de ~600ms por request. O pool reutiliza conexões saudáveis e o
    # pre_ping descarta as mortas antes de usar.
    poolclass=QueuePool,
    pool_size=5,
    max_overflow=10,
    pool_timeout=30,
    pool_recycle=300,
    pool_pre_ping=True,
    connect_args={
        "prepare_threshold": None,
        "keepalives": 1,
        "keepalives_idle": 30,
        "keepalives_interval": 10,
        "keepalives_count": 5,
        # Lock-wait no índice único de base_guias derrubava threads presas por
        # tempo indeterminado e esgotava o threadpool (503 de 2026-09-02): com
        # lock_timeout o runner falha rápido e o job vai para quarentena em
        # segundos em vez de congelar o backend.
        "options": "-c lock_timeout=5000",
    }
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
