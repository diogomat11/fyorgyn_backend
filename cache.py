import os
import logging
import json
import hashlib
import redis
import time
import threading
from typing import Optional, Any

logger = logging.getLogger(__name__)

class TenantCache:
    _instance = None
    
    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = super(TenantCache, cls).__new__(cls, *args, **kwargs)
            cls._instance._init_connection()
        return cls._instance
        
    def _build_client(self, redis_url, redis_host, redis_port, redis_password, redis_db, ssl_cert_reqs=None):
        """Constroi o cliente redis. Para rediss:// (ex.: Upstash) aplica TLS."""
        common = dict(socket_timeout=2.0, socket_connect_timeout=2.0, retry_on_timeout=True)
        if redis_url:
            kwargs = dict(common)
            # So repassa ssl_cert_reqs para URLs TLS (rediss://); evita afetar redis:// comum.
            if redis_url.lower().startswith("rediss://") and ssl_cert_reqs is not None:
                kwargs["ssl_cert_reqs"] = ssl_cert_reqs
            return redis.Redis.from_url(redis_url, **kwargs)
        return redis.Redis(
            host=redis_host,
            port=redis_port,
            password=redis_password,
            db=redis_db,
            **common,
        )

    def _init_connection(self):
        self.lock = threading.Lock()
        self.in_memory_db = {}  # key -> (expiry_timestamp, value_json_str)

        REDIS_URL = os.getenv("REDIS_URL")
        REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
        REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
        REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", None)
        REDIS_DB = int(os.getenv("REDIS_DB", 0))

        is_tls = bool(REDIS_URL) and REDIS_URL.lower().startswith("rediss://")

        try:
            if is_tls:
                # Upstash/Redis sobre TLS: tentar com validacao de certificado (CA publica valida).
                self.redis_client = self._build_client(
                    REDIS_URL, REDIS_HOST, REDIS_PORT, REDIS_PASSWORD, REDIS_DB, ssl_cert_reqs="required"
                )
                try:
                    self.redis_client.ping()
                except Exception:
                    # CA nao disponivel no ambiente -> tentar sem verificacao de certificado.
                    self.redis_client = self._build_client(
                        REDIS_URL, REDIS_HOST, REDIS_PORT, REDIS_PASSWORD, REDIS_DB, ssl_cert_reqs=None
                    )
                    self.redis_client.ping()
                    logger.warning(
                        "Redis TLS conectado SEM verificacao de certificado "
                        "(CA nao disponivel no ambiente)."
                    )
            else:
                self.redis_client = self._build_client(
                    REDIS_URL, REDIS_HOST, REDIS_PORT, REDIS_PASSWORD, REDIS_DB
                )
                self.redis_client.ping()
            self.redis_enabled = True
            self.enabled = True
            logger.info("Conexão com Redis estabelecida com sucesso.")
        except Exception as e:
            self.redis_client = None
            self.redis_enabled = False
            self.enabled = True  # Mantém enabled True para habilitar Fallback local em memória
            logger.warning(f"Não foi possível conectar ao Redis: {e}. Usando Cache Local em Memória (In-Memory Fallback).")

    def _make_key(self, tenant_id: int, resource: str, query_params: dict) -> str:
        # Sort keys to guarantee exact same hash for identical dicts
        serialized = json.dumps(query_params, sort_keys=True, default=str)
        query_hash = hashlib.md5(serialized.encode('utf-8')).hexdigest()
        return f"tenant:{tenant_id}:{resource}:{query_hash}"

    def get(self, tenant_id: int, resource: str, query_params: dict) -> Optional[Any]:
        if not self.enabled:
            return None
        key = self._make_key(tenant_id, resource, query_params)
        
        # 1. Tier 1: Instant In-Memory RAM lookup (sub-millisecond <0.05ms)
        with self.lock:
            cached = self.in_memory_db.get(key)
            if cached:
                expiry, val_str = cached
                if time.time() < expiry:
                    return json.loads(val_str)
                else:
                    self.in_memory_db.pop(key, None)
                    
        # 2. Tier 2: Remote Redis lookup (L2)
        if self.redis_enabled and self.redis_client:
            try:
                val = self.redis_client.get(key)
                if val:
                    # Promote back to Tier 1 RAM cache
                    with self.lock:
                        self.in_memory_db[key] = (time.time() + 60, val)
                    return json.loads(val)
            except Exception as e:
                logger.error(f"Erro ao ler do Redis para chave {key}: {e}")
                
        return None

    def set(self, tenant_id: int, resource: str, query_params: dict, value: Any, ttl: int = 60) -> bool:
        if not self.enabled:
            return False
        key = self._make_key(tenant_id, resource, query_params)
        val_str = json.dumps(value, default=str)
        
        # 1. Save to Tier 1 (In-Memory RAM)
        with self.lock:
            try:
                expiry = time.time() + ttl
                self.in_memory_db[key] = (expiry, val_str)
            except Exception as e:
                logger.error(f"Erro ao salvar no cache local RAM para chave {key}: {e}")

        # 2. Save to Tier 2 (Redis)
        if self.redis_enabled and self.redis_client:
            try:
                self.redis_client.setex(key, ttl, val_str)
            except Exception as e:
                logger.error(f"Erro ao salvar no Redis para chave {key}: {e}")

        return True

    def invalidate_tenant(self, tenant_id: int) -> bool:
        if not self.enabled:
            return False
            
        # 1. Wipe from Tier 1 (In-Memory RAM)
        prefix = f"tenant:{tenant_id}:"
        with self.lock:
            try:
                keys_to_del = [k for k in self.in_memory_db.keys() if k.startswith(prefix)]
                for k in keys_to_del:
                    self.in_memory_db.pop(k, None)
            except Exception as e:
                logger.error(f"Erro ao invalidar cache RAM local para tenant {tenant_id}: {e}")

        # 2. Wipe from Tier 2 (Redis)
        if self.redis_enabled and self.redis_client:
            pattern = f"tenant:{tenant_id}:*"
            try:
                cursor = 0
                keys_to_delete = []
                while True:
                    cursor, keys = self.redis_client.scan(cursor=cursor, match=pattern, count=100)
                    keys_to_delete.extend(keys)
                    if cursor == 0:
                        break
                
                if keys_to_delete:
                    self.redis_client.delete(*keys_to_delete)
            except Exception as e:
                logger.error(f"Erro ao invalidar cache Redis para tenant {tenant_id}: {e}")

        return True

# Export singleton instance
cache = TenantCache()
