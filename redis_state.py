import json
import logging
import os
import time
from contextlib import contextmanager
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class RedisStateCoordinator:
    """Optional Redis-backed locks and short-lived run status.

    The app must keep working without Redis, so every public method degrades
    to a no-op when the redis package or server is unavailable.
    """

    def __init__(self):
        self.enabled = False
        self.client = None
        self.url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
        self.prefix = os.getenv("LCA_REDIS_PREFIX", "lca")

        try:
            import redis  # type: ignore

            client = redis.Redis.from_url(self.url, decode_responses=True)
            client.ping()
            self.client = client
            self.enabled = True
            logger.info("Redis 状态协调层已启用: %s", self.url)
        except Exception as exc:  # noqa: BLE001
            logger.info("Redis 状态协调层未启用，使用本地降级模式: %s", exc)

    def _key(self, name: str) -> str:
        return f"{self.prefix}:{name}"

    @contextmanager
    def lock(self, name: str, ttl: int = 30, wait_timeout: float = 5.0):
        if not self.enabled or not self.client:
            yield False
            return

        key = self._key(f"lock:{name}")
        token = f"{os.getpid()}:{time.time_ns()}"
        deadline = time.time() + wait_timeout
        acquired = False

        while time.time() <= deadline:
            acquired = bool(self.client.set(key, token, nx=True, ex=ttl))
            if acquired:
                break
            time.sleep(0.05)

        if not acquired:
            raise TimeoutError(f"Redis lock timeout: {name}")

        try:
            yield True
        finally:
            try:
                if self.client.get(key) == token:
                    self.client.delete(key)
            except Exception as exc:  # noqa: BLE001
                logger.warning("释放 Redis 锁失败: %s err=%s", name, exc)

    def set_run_status(self, run_id: str, payload: Dict[str, Any], ttl: int = 3600):
        if not self.enabled or not self.client:
            return
        data = dict(payload)
        data["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        self.client.setex(self._key(f"run:{run_id}"), ttl, json.dumps(data, ensure_ascii=False))

    def get_run_status(self, run_id: str) -> Optional[Dict[str, Any]]:
        if not self.enabled or not self.client:
            return None
        raw = self.client.get(self._key(f"run:{run_id}"))
        if not raw:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None


redis_state = RedisStateCoordinator()
