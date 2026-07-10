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
        
    def _init_connection(self):
        self.lock = threading.Lock()
        self.in_memory_db = {}  # key -> (expiry_timestamp, value_json_str)
        
        REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
        REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
        REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", None)
        REDIS_DB = int(os.getenv("REDIS_DB", 0))
        
        try:
            self.redis_client = redis.Redis(
                host=REDIS_HOST,
                port=REDIS_PORT,
                password=REDIS_PASSWORD,
                db=REDIS_DB,
                socket_timeout=2.0,
                socket_connect_timeout=2.0,
                retry_on_timeout=True
            )
            # Test connection
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
        
        if self.redis_enabled and self.redis_client:
            try:
                val = self.redis_client.get(key)
                if val:
                    return json.loads(val)
            except Exception as e:
                logger.error(f"Erro ao ler do Redis para chave {key}: {e}")
            return None
            
        # In-Memory fallback
        with self.lock:
            cached = self.in_memory_db.get(key)
            if cached:
                expiry, val_str = cached
                if time.time() < expiry:
                    return json.loads(val_str)
                else:
                    self.in_memory_db.pop(key, None)
        return None

    def set(self, tenant_id: int, resource: str, query_params: dict, value: Any, ttl: int = 60) -> bool:
        if not self.enabled:
            return False
        key = self._make_key(tenant_id, resource, query_params)
        
        if self.redis_enabled and self.redis_client:
            try:
                self.redis_client.setex(
                    key,
                    ttl,
                    json.dumps(value, default=str)
                )
                return True
            except Exception as e:
                logger.error(f"Erro ao salvar no Redis para chave {key}: {e}")
                return False
                
        # In-Memory fallback
        with self.lock:
            try:
                expiry = time.time() + ttl
                val_str = json.dumps(value, default=str)
                self.in_memory_db[key] = (expiry, val_str)
                return True
            except Exception as e:
                logger.error(f"Erro ao salvar no cache local para chave {key}: {e}")
                return False

    def invalidate_tenant(self, tenant_id: int) -> bool:
        if not self.enabled:
            return False
            
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
                    logger.info(f"Invalidados {len(keys_to_delete)} itens de cache do tenant {tenant_id}.")
                return True
            except Exception as e:
                logger.error(f"Erro ao invalidar cache para o tenant {tenant_id}: {e}")
                return False
                
        # In-Memory fallback
        prefix = f"tenant:{tenant_id}:"
        with self.lock:
            try:
                keys_to_del = [k for k in self.in_memory_db.keys() if k.startswith(prefix)]
                for k in keys_to_del:
                    self.in_memory_db.pop(k, None)
                logger.info(f"Invalidados {len(keys_to_del)} itens de cache local do tenant {tenant_id}.")
                return True
            except Exception as e:
                logger.error(f"Erro ao invalidar cache local para o tenant {tenant_id}: {e}")
                return False

# Export singleton instance
cache = TenantCache()
