import os
import logging
import json
import hashlib
import redis
from typing import Optional, Any

logger = logging.getLogger(__name__)

REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", None)
REDIS_DB = int(os.getenv("REDIS_DB", 0))

class TenantCache:
    _instance = None
    
    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = super(TenantCache, cls).__new__(cls, *args, **kwargs)
            cls._instance._init_connection()
        return cls._instance
        
    def _init_connection(self):
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
            self.enabled = True
            logger.info("Conexão com Redis estabelecida com sucesso.")
        except Exception as e:
            self.redis_client = None
            self.enabled = False
            logger.warning(f"Não foi possível conectar ao Redis: {e}. O cache está desativado (fail-open).")

    def _make_key(self, tenant_id: int, resource: str, query_params: dict) -> str:
        # Sort keys to guarantee exact same hash for identical dicts
        serialized = json.dumps(query_params, sort_keys=True, default=str)
        query_hash = hashlib.md5(serialized.encode('utf-8')).hexdigest()
        return f"tenant:{tenant_id}:{resource}:{query_hash}"

    def get(self, tenant_id: int, resource: str, query_params: dict) -> Optional[Any]:
        if not self.enabled or not self.redis_client:
            return None
        key = self._make_key(tenant_id, resource, query_params)
        try:
            val = self.redis_client.get(key)
            if val:
                return json.loads(val)
        except Exception as e:
            logger.error(f"Erro ao ler do Redis para chave {key}: {e}")
        return None

    def set(self, tenant_id: int, resource: str, query_params: dict, value: Any, ttl: int = 60) -> bool:
        if not self.enabled or not self.redis_client:
            return False
        key = self._make_key(tenant_id, resource, query_params)
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

    def invalidate_tenant(self, tenant_id: int) -> bool:
        """
        Invalida todo o cache associado a um tenant (user_id).
        Utiliza SCAN para buscar todas as chaves iniciadas em tenant:{tenant_id}:* e deleta em batch.
        """
        if not self.enabled or not self.redis_client:
            return False
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

# Export singleton instance
cache = TenantCache()
