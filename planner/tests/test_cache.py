from types import SimpleNamespace as NS
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from planner.routing import ProviderError
from planner.tests.helpers import LOCMEM_CACHES

URL = "/api/route-plan/"
DALLAS = NS(label="Dallas, TX", lat=32.7767, lng=-96.7970, source="gazetteer", external_calls=0)
HOUSTON = NS(label="Houston, TX", lat=29.7604, lng=-95.3698, source="gazetteer", external_calls=0)
ROUTE = NS(coordinates=[[-96.797, 32.7767], [-95.3698, 29.7604]], distance_miles=239.0,
        duration_minutes=225.0, provider="ors", external_calls=1)


@override_settings(CACHES=LOCMEM_CACHES)
class RoutePlanCacheTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()
        p_resolve = patch("planner.services.resolve_pair", return_value=(DALLAS, HOUSTON, 0))
        p_route = patch("planner.services.get_route", return_value=ROUTE)
        self.resolve = p_resolve.start()
        self.route = p_route.start()
        self.addCleanup(p_resolve.stop)
        self.addCleanup(p_route.stop)

    def post(self, start="Dallas, TX", finish="Houston, TX", **extra):
        return self.client.post(URL, {"start": start, "finish": finish, **extra}, format="json")

    def test_repeat_request_is_a_cache_hit_with_zero_calls(self):
        b1 = self.post().json()
        b2 = self.post().json()
        self.assertFalse(b1["meta"]["cache_hit"])
        self.assertEqual(b1["meta"]["external_api_calls"], 1)
        self.assertTrue(b2["meta"]["cache_hit"])
        self.assertEqual(b2["meta"]["external_api_calls"], 0)
        self.assertEqual(self.route.call_count, 1)
        self.assertEqual(b1["summary"], b2["summary"])
        self.assertEqual(b1["route"], b2["route"])
        self.assertIn("processing_ms", b2["meta"])

    def test_text_variants_hit_without_resolving(self):
        self.post()
        r = self.post(start="  DALLAS,   tx ", finish="houston, TX")
        self.assertTrue(r.json()["meta"]["cache_hit"])
        self.assertEqual(self.resolve.call_count, 1)
        self.assertEqual(self.route.call_count, 1)

    def test_free_text_repeat_costs_zero_calls(self):
        self.resolve.return_value = (DALLAS, HOUSTON, 1)  # pretend one Nominatim call
        b1 = self.post("some address").json()
        b2 = self.post("some address").json()
        self.assertEqual(b1["meta"]["external_api_calls"], 2)  # 1 geocode + 1 route
        self.assertEqual(b2["meta"]["external_api_calls"], 0)
        self.assertEqual(self.resolve.call_count, 1)

    def test_same_coordinates_share_one_plan(self):
        self.resolve.return_value = (DALLAS, HOUSTON, 1)
        self.post("first spelling")
        body = self.post("second spelling").json()
        self.assertTrue(body["meta"]["cache_hit"])
        self.assertEqual(body["meta"]["external_api_calls"], 1)  # geocode only, no routing
        self.assertEqual(self.route.call_count, 1)
        self.assertEqual(body["start"]["query"], "second spelling")
        self.assertIn("second+spelling", body["map"]["html_url"])

    def test_initial_tank_flag_gets_its_own_entry(self):
        self.post()
        r = self.post(include_initial_tank_cost=True)
        self.assertFalse(r.json()["meta"]["cache_hit"])
        self.assertEqual(self.route.call_count, 2)

    def test_provider_errors_are_not_cached(self):
        exc = ProviderError("down")
        exc.external_calls = 1
        self.route.side_effect = [exc, ROUTE]
        self.assertEqual(self.post().status_code, 502)
        r = self.post()
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()["meta"]["cache_hit"])

    def test_cache_backend_failure_does_not_break_requests(self):
        with patch("planner.services.cache") as broken:
            broken.get.side_effect = ConnectionError("redis down")
            broken.set.side_effect = ConnectionError("redis down")
            r = self.post()
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()["meta"]["cache_hit"])

    def test_map_page_uses_the_same_cache(self):
        self.post()
        r = self.client.get("/api/route-plan/map/", {"start": "Dallas, TX", "finish": "Houston, TX"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.route.call_count, 1)