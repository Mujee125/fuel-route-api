from types import SimpleNamespace as NS
from unittest.mock import patch

from django.test import TestCase
from rest_framework.test import APIClient

from django.core.cache import cache
from django.test import override_settings
from planner.tests.helpers import LOCMEM_CACHES

from planner.routing import ProviderError

URL = "/api/route-plan/"
DALLAS = NS(label="Dallas, TX", lat=32.7767, lng=-96.7970, source="gazetteer", external_calls=0)
HOUSTON = NS(label="Houston, TX", lat=29.7604, lng=-95.3698, source="gazetteer", external_calls=0)
ROUTE = NS(coordinates=[[-96.797, 32.7767], [-95.3698, 29.7604]], distance_miles=239.0,
        duration_minutes=225.0, provider="ors", external_calls=1)


@override_settings(CACHES=LOCMEM_CACHES)
class RoutePlanApiTests(TestCase):
    def setUp(self):
        cache.clear()
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
        self.assertEqual(body["summary"]["vehicle_type"], "truck")
        self.assertEqual(body["meta"]["external_api_calls"], 1)
        self.assertEqual(body["meta"]["routing_profile"], "driving-hgv")
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

    @patch("planner.services.resolve_pair", return_value=(DALLAS, HOUSTON, 0))
    def test_osrm_fallback_is_labelled_as_car_profile(self, _resolve):
        osrm = NS(coordinates=ROUTE.coordinates, distance_miles=239.0,
                  duration_minutes=225.0, provider="osrm", external_calls=2)
        with patch("planner.services.get_route", return_value=osrm):
            r = self.client.post(URL, {"start": "a", "finish": "b"}, format="json")
        self.assertEqual(r.status_code, 200)
        self.assertIn("car", r.json()["meta"]["routing_profile"])
        self.assertEqual(r.json()["meta"]["external_api_calls"], 2)

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

def _find(obj, key):
    """Find `key` anywhere in a nested JSON body (error bodies may nest meta)."""
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for v in obj.values():
            hit = _find(v, key)
            if hit is not None:
                return hit
    elif isinstance(obj, list):
        for v in obj:
            hit = _find(v, key)
            if hit is not None:
                return hit
    return None


SEATTLE = NS(label="Seattle, WA", lat=47.6062, lng=-122.3321, source="gazetteer", external_calls=0)
LOS_ANGELES = NS(label="Los Angeles, CA", lat=34.0522, lng=-118.2437, source="gazetteer", external_calls=0)
ROUTE_LONG = NS(coordinates=[[-122.3321, 47.6062], [-118.2437, 34.0522]], distance_miles=1135.0,
                duration_minutes=1000.0, provider="ors", external_calls=1)


@override_settings(CACHES=LOCMEM_CACHES)
class RoutePlanErrorTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()

    @patch("planner.services.get_route")
    def test_non_usa_city_is_400_and_never_routes(self, route_mock):
        r = self.client.post(URL, {"start": "Toronto, ON", "finish": "Dallas, TX"}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("invalid_location", r.content.decode())
        self.assertEqual(_find(r.json(), "external_api_calls"), 0)
        route_mock.assert_not_called()

    @patch("planner.services.get_route")
    def test_non_usa_coordinates_are_400_and_never_route(self, route_mock):
        r = self.client.post(URL, {"start": "51.5074,-0.1278", "finish": "Dallas, TX"}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(_find(r.json(), "external_api_calls"), 0)
        route_mock.assert_not_called()

    @patch("planner.services.get_route")
    @patch("planner.services.resolve_pair")
    def test_unresolvable_location_is_404_and_never_routes(self, resolve_mock, route_mock):
        from planner.locations import LocationNotFound
        resolve_mock.side_effect = LocationNotFound("Could not resolve 'zzzz qqqq'")
        r = self.client.post(URL, {"start": "zzzz qqqq", "finish": "Dallas, TX"}, format="json")
        self.assertEqual(r.status_code, 404)
        self.assertIn("location_not_found", r.content.decode())
        self.assertIsNotNone(_find(r.json(), "external_api_calls"))
        route_mock.assert_not_called()

    @patch("planner.optimizer.find_corridor_stations", return_value=[])
    @patch("planner.services.get_route", return_value=ROUTE_LONG)
    @patch("planner.services.resolve_pair", return_value=(SEATTLE, LOS_ANGELES, 0))
    def test_no_feasible_fuel_plan_is_422_with_call_count(self, _resolve, _route, _find_stations):
        r = self.client.post(URL, {"start": "Seattle, WA", "finish": "Los Angeles, CA"}, format="json")
        self.assertEqual(r.status_code, 422)
        self.assertIn("no_feasible_fuel_plan", r.content.decode())
        self.assertEqual(_find(r.json(), "external_api_calls"), 1)