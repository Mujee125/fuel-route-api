from types import SimpleNamespace as NS
from unittest.mock import patch

from django.test import TestCase
from rest_framework.test import APIClient

from planner.routing import ProviderError

URL = "/api/route-plan/"
DALLAS = NS(label="Dallas, TX", lat=32.7767, lng=-96.7970, source="gazetteer", external_calls=0)
HOUSTON = NS(label="Houston, TX", lat=29.7604, lng=-95.3698, source="gazetteer", external_calls=0)
ROUTE = NS(coordinates=[[-96.797, 32.7767], [-95.3698, 29.7604]], distance_miles=239.0,
        duration_minutes=225.0, provider="ors", external_calls=1)


class RoutePlanApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    @patch("planner.services.get_route", return_value=ROUTE)
    @patch("planner.services.resolve_pair", return_value=(DALLAS, HOUSTON, 0))
    def test_short_trip_post_matches_contract(self, _resolve, _route):
        r = self.client.post(URL, {"start": "Dallas, TX", "finish": "Houston, TX"}, format="json")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(set(body), {"start", "finish", "route", "fuel_stops", "summary", "map", "meta"})
        self.assertEqual(body["fuel_stops"], [])
        self.assertEqual(body["summary"]["total_gallons_needed"], 23.9)
        self.assertEqual(body["summary"]["total_fuel_cost"], 0.0)
        self.assertEqual(body["meta"]["external_api_calls"], 1)
        self.assertIn("processing_ms", body["meta"])
        kinds = [f["properties"]["kind"] for f in body["map"]["geojson"]["features"]]
        self.assertEqual(kinds, ["route", "start", "finish"])
        self.assertTrue(body["map"]["html_url"].startswith("/api/route-plan/map/?"))

    @patch("planner.services.get_route", return_value=ROUTE)
    @patch("planner.services.resolve_pair", return_value=(DALLAS, HOUSTON, 0))
    def test_get_works(self, _resolve, _route):
        r = self.client.get(URL, {"start": "Dallas, TX", "finish": "Houston, TX"})
        self.assertEqual(r.status_code, 200)

    @patch("planner.services.get_route")
    @patch("planner.services.resolve_pair", return_value=(DALLAS, DALLAS, 0))
    def test_start_equals_finish_makes_no_routing_call(self, _resolve, route_mock):
        r = self.client.post(URL, {"start": "Dallas, TX", "finish": "Dallas, TX"}, format="json")
        self.assertEqual(r.status_code, 200)
        route_mock.assert_not_called()
        self.assertEqual(r.json()["route"]["distance_miles"], 0.0)
        self.assertEqual(r.json()["meta"]["external_api_calls"], 0)

    @patch("planner.services.resolve_pair")
    def test_missing_finish_is_400_with_zero_calls(self, resolve_mock):
        r = self.client.post(URL, {"start": "Dallas, TX"}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["error"]["code"], "invalid_input")
        self.assertEqual(r.json()["meta"]["external_api_calls"], 0)
        resolve_mock.assert_not_called()

    def test_malformed_json_is_400(self):
        r = self.client.post(URL, data="{bad", content_type="application/json")
        self.assertEqual(r.status_code, 400)

    @patch("planner.services.resolve_pair", return_value=(DALLAS, HOUSTON, 0))
    def test_provider_error_is_502_with_call_count(self, _resolve):
        exc = ProviderError("upstream down")
        exc.external_calls = 1
        with patch("planner.services.get_route", side_effect=exc):
            r = self.client.post(URL, {"start": "a", "finish": "b"}, format="json")
        self.assertEqual(r.status_code, 502)
        self.assertEqual(r.json()["error"]["code"], "routing_provider_error")
        self.assertEqual(r.json()["meta"]["external_api_calls"], 1)

    @patch("planner.services.get_route", return_value=ROUTE)
    @patch("planner.services.resolve_pair", return_value=(DALLAS, HOUSTON, 0))
    def test_map_page_renders(self, _resolve, _route):
        r = self.client.get("/api/route-plan/map/", {"start": "Dallas, TX", "finish": "Houston, TX"})
        self.assertEqual(r.status_code, 200)
        html = r.content.decode()
        self.assertIn("leaflet", html)
        self.assertIn('id="route-geojson"', html)

    def test_map_page_requires_params(self):
        self.assertEqual(self.client.get("/api/route-plan/map/").status_code, 400)