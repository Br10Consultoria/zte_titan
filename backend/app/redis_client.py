import redis
import json
from datetime import datetime
from typing import Optional, Any
from .config import settings


class RedisCache:
    def __init__(self):
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                kwargs = {
                    "host": settings.REDIS_HOST,
                    "port": settings.REDIS_PORT,
                    "db": settings.REDIS_DB,
                    "decode_responses": True,
                    "socket_connect_timeout": 3,
                    "socket_timeout": 3,
                }
                if settings.REDIS_PASSWORD:
                    kwargs["password"] = settings.REDIS_PASSWORD
                self._client = redis.Redis(**kwargs)
                self._client.ping()
            except Exception as e:
                print(f"⚠️  Redis não disponível: {e}. Cache desabilitado.")
                self._client = None
        return self._client

    def is_available(self) -> bool:
        try:
            client = self._get_client()
            if client:
                client.ping()
                return True
        except Exception:
            self._client = None
        return False

    def get(self, key: str) -> Optional[Any]:
        try:
            client = self._get_client()
            if not client:
                return None
            value = client.get(key)
            if value:
                return json.loads(value)
        except Exception as e:
            print(f"Redis GET error: {e}")
        return None

    def set(self, key: str, value: Any, ttl: int = None) -> bool:
        try:
            client = self._get_client()
            if not client:
                return False
            ttl = ttl or settings.CACHE_TTL
            serialized = json.dumps(value, default=str)
            client.setex(key, ttl, serialized)
            return True
        except Exception as e:
            print(f"Redis SET error: {e}")
        return False

    def delete(self, key: str) -> bool:
        try:
            client = self._get_client()
            if not client:
                return False
            client.delete(key)
            return True
        except Exception as e:
            print(f"Redis DELETE error: {e}")
        return False

    def delete_pattern(self, pattern: str) -> int:
        """Deleta todas as chaves que correspondem ao padrão."""
        try:
            client = self._get_client()
            if not client:
                return 0
            keys = client.keys(pattern)
            if keys:
                return client.delete(*keys)
        except Exception as e:
            print(f"Redis DELETE PATTERN error: {e}")
        return 0

    def get_ttl(self, key: str) -> int:
        """Retorna o TTL restante em segundos."""
        try:
            client = self._get_client()
            if not client:
                return -1
            return client.ttl(key)
        except Exception:
            return -1

    def get_cache_info(self, key: str) -> dict:
        """Retorna informações sobre o cache de uma chave."""
        ttl = self.get_ttl(key)
        if ttl > 0:
            hours = ttl // 3600
            minutes = (ttl % 3600) // 60
            return {
                "cached": True,
                "ttl_seconds": ttl,
                "expires_in": f"{hours}h {minutes}m"
            }
        return {"cached": False, "ttl_seconds": 0, "expires_in": None}

    # ============================================================
    # CHAVES PADRONIZADAS
    # ============================================================

    @staticmethod
    def key_pon_status(olt_id: int, slot: int, port: int) -> str:
        return f"olt:{olt_id}:pon:{slot}:{port}:status"

    @staticmethod
    def key_onu_detail(olt_id: int, slot: int, port: int, onu_id: int) -> str:
        return f"olt:{olt_id}:onu:{slot}:{port}:{onu_id}:detail"

    @staticmethod
    def key_onu_power(olt_id: int, slot: int, port: int, onu_id: int) -> str:
        return f"olt:{olt_id}:onu:{slot}:{port}:{onu_id}:power"

    @staticmethod
    def key_onu_full(olt_id: int, slot: int, port: int, onu_id: int) -> str:
        return f"olt:{olt_id}:onu:{slot}:{port}:{onu_id}:full"

    @staticmethod
    def key_olt_ports(olt_id: int) -> str:
        return f"olt:{olt_id}:ports"

    @staticmethod
    def key_olt_status(olt_id: int) -> str:
        return f"olt:{olt_id}:status"

    @staticmethod
    def key_uncfg_onus(olt_id: int) -> str:
        return f"olt:{olt_id}:uncfg_onus"


cache = RedisCache()
