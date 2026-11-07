from unittest.mock import Mock, patch

import requests
from django.test import SimpleTestCase, override_settings

from planner import locations as L
from planner.routing import ProviderTimeout


def http(status, payload):
    m = Mock()
    m.status_code = status
    m.json.return_value = payload
    return m


@override_settings(GEOCODER_FALLBACK=False)
class LocalResolutionTests(SimpleTestCase):
    def test_city_state_dallas(self):
        loc = L.resolve("Dallas, TX")
        self.assertEqual((loc.source, loc.external_calls), ("gazetteer", 0))
        self.assertAlmostEqual(loc.lat, 32.8, delta=0.5)
        self.assertAlmostEqual(loc.lng, -96.8, delta=0.5)

    def test_new_york_and_los_angeles(self):
        ny, la = L.resolve("New York, NY"), L.resolve("Los Angeles, CA")
        self.assertAlmostEqual(ny.lat, 40.7, delta=0.5)
        self.assertAlmostEqual(la.lng, -118.3, delta=0.5)

    def test_case_and_spacing_insensitive(self):
        loc = L.resolve("  dallas ,  tx ")
        self.assertEqual(loc.label, "Dallas, TX")

    def test_coordinates_need_no_lookup(self):
        loc = L.resolve("34.05,-118.24")
        self.assertEqual((loc.source, loc.external_calls), ("coordinates", 0))

    def test_conus_inside_points(self):
        for name, lat, lng in [("Dallas", 32.78, -96.8), ("Chicago", 41.88, -87.63),
                            ("Boston", 42.36, -71.06), ("Seattle", 47.6, -122.3),
                            ("Miami", 25.76, -80.19), ("Minneapolis", 44.98, -93.27),
                            ("El Paso", 31.76, -106.49), ("San Diego", 32.7, -117.16)]:
            with self.subTest(name):
                self.assertTrue(L.in_conus(lat, lng))

    def test_conus_outside_points(self):
        for name, lat, lng in [("Toronto", 43.65, -79.38), ("Vancouver", 49.28, -123.12),
                            ("Montreal", 45.5, -73.57), ("Mexico City", 19.4, -99.1)]:
            with self.subTest(name):
                self.assertFalse(L.in_conus(lat, lng))
                with self.assertRaises(L.InvalidLocation):
                    L.resolve(f"{lat},{lng}")

    def test_canada_state_code_is_400(self):
        with self.assertRaises(L.InvalidLocation) as cm:
            L.resolve("Toronto, ON")
        self.assertEqual(cm.exception.status_code, 400)

    def test_alaska_is_400(self):
        with self.assertRaises(L.InvalidLocation):
            L.resolve("Anchorage, AK")

    def test_unknown_city_is_404(self):
        with self.assertRaises(L.LocationNotFound) as cm:
            L.resolve("Zzzzzz, TX")
        self.assertEqual(cm.exception.status_code, 404)

    def test_empty_is_400(self):
        with self.assertRaises(L.InvalidLocation):
            L.resolve("  ")


@override_settings(GEOCODER_FALLBACK=True)
class FallbackTests(SimpleTestCase):
    @patch("planner.locations.requests.get")
    def test_free_text_uses_one_call(self, get):
        get.return_value = http(200, [{"lat": "38.8977", "lon": "-77.0365"}])
        loc = L.resolve("1600 Pennsylvania Ave NW, Washington")
        self.assertEqual((loc.source, loc.external_calls), ("nominatim", 1))

    @patch("planner.locations.requests.get", side_effect=requests.Timeout)
    def test_geocoder_timeout_is_504(self, get):
        with self.assertRaises(ProviderTimeout) as cm:
            L.resolve("some street address")
        self.assertEqual((cm.exception.status_code, cm.exception.external_calls), (504, 1))

    @patch("planner.locations.requests.get")
    def test_no_results_is_404(self, get):
        get.return_value = http(200, [])
        with self.assertRaises(L.LocationNotFound) as cm:
            L.resolve("qwertyuiop nowhere")
        self.assertEqual(cm.exception.external_calls, 1)

    @patch("planner.locations.requests.get")
    def test_pair_counts_remote_calls(self, get):
        get.return_value = http(200, [{"lat": "38.9", "lon": "-77.0"}])
        _, _, calls = L.resolve_pair("some address one", "some address two")
        self.assertEqual(calls, 2)

    def test_pair_local_is_zero_calls(self):
        _, _, calls = L.resolve_pair("Dallas, TX", "Houston, TX")
        self.assertEqual(calls, 0)