from unittest import mock

from redis.exceptions import ConnectionError as RedisConnectionError

from django.test import SimpleTestCase
from django_redis.cache import RedisCache
from django_redis.exceptions import ConnectionInterrupted

from planner import cache_backend
from planner.cache_backend import BreakerRedisCache


class BreakerTests(SimpleTestCase):
    def setUp(self):
        cache_backend.reset_breaker()
        self.addCleanup(cache_backend.reset_breaker)
        self.cache = BreakerRedisCache("redis://127.0.0.1:1/0", {"OPTIONS": {}})

    def test_failure_opens_breaker_and_skips_redis(self):
        err = ConnectionInterrupted(connection=None)
        with mock.patch.object(RedisCache, "get", side_effect=err) as m, \
            self.assertLogs("planner.cache", level="WARNING"):
            self.assertIsNone(self.cache.get("k"))
            self.assertEqual(self.cache.get("k", "dflt"), "dflt")
            self.assertEqual(m.call_count, 1)  # second call never reached Redis

    def test_breaker_closes_after_cooldown(self):
        err = ConnectionInterrupted(connection=None)
        with mock.patch.object(RedisCache, "get", side_effect=err), \
            self.assertLogs("planner.cache", level="WARNING"):
            self.cache.get("k")
        with mock.patch.object(RedisCache, "get", return_value="v") as m, \
            mock.patch("planner.cache_backend.time.monotonic",
                        return_value=10**9):
            self.assertEqual(self.cache.get("k"), "v")
            self.assertEqual(m.call_count, 1)

    def test_raw_redis_connection_error_opens_breaker(self):
        # django-redis re-raises the original redis error, not ConnectionInterrupted
        err = RedisConnectionError("Name or service not known")
        with mock.patch.object(RedisCache, "get", side_effect=err) as m, \
            self.assertLogs("planner.cache", level="WARNING"):
            self.assertIsNone(self.cache.get("k"))
            self.assertIsNone(self.cache.get("k"))
            self.assertEqual(m.call_count, 1)        