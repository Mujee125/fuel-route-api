from unittest.mock import patch

import requests
from django.test import SimpleTestCase, override_settings

from planner import routing

from .helpers import FakeStreamResponse

LA, NY = (34.05, -118.24), (40.71, -74.0)
LINE = [[-118.2, 34.0], [-74.0, 40.7]]
# Google's reference polyline (3 points) stands in for the ORS encoded geometry.
ENCODED = "_p~iF~ps|U_ulLnnqC_mqNvxq`@"
ORS_OK = {"routes": [{"summary": {"distance": 4506000, "duration": 144000},
                      "geometry": ENCODED}]}
OSRM_OK = {"code": "Ok", "routes": [{"geometry": {"coordinates": LINE},
                                    "distance": 4506000, "duration": 144000}]}


@override_settings(ORS_API_KEY="k")
class RoutingTests(SimpleTestCase):
    @patch("planner.routing.requests.post")
    def test_ors_success_is_one_call(self, post):
        post.return_value = FakeStreamResponse(200, ORS_OK)
        r = routing.get_route(LA, NY)
        self.assertEqual((r.provider, r.external_calls), ("ors", 1))
        self.assertAlmostEqual(r.distance_miles, 2800, delta=10)
        self.assertEqual(len(r.coordinates), 3)          # decoded polyline
        self.assertAlmostEqual(r.coordinates[0][0], -120.2, places=5)   # [lng, lat]

    @patch("planner.routing.requests.get")
    @patch("planner.routing.requests.post")
    def test_ors_failure_falls_back_to_osrm(self, post, get):
        post.return_value = FakeStreamResponse(500, {})
        get.return_value = FakeStreamResponse(200, OSRM_OK)
        r = routing.get_route(LA, NY)
        self.assertEqual((r.provider, r.external_calls), ("osrm", 2))

    @patch("planner.routing.requests.get")
    @patch("planner.routing.requests.post")
    def test_malformed_ors_body_falls_back_to_osrm(self, post, get):
        post.return_value = FakeStreamResponse(200, {"routes": []})
        get.return_value = FakeStreamResponse(200, OSRM_OK)
        r = routing.get_route(LA, NY)
        self.assertEqual((r.provider, r.external_calls), ("osrm", 2))

    @patch("planner.routing.requests.get", side_effect=requests.Timeout)
    @patch("planner.routing.requests.post", side_effect=requests.Timeout)
    def test_both_timeout_gives_504_with_call_count(self, post, get):
        with self.assertRaises(routing.ProviderTimeout) as cm:
            routing.get_route(LA, NY)
        self.assertEqual((cm.exception.status_code, cm.exception.external_calls), (504, 2))

    @override_settings(ROUTING_DEADLINE=-1)
    @patch("planner.routing.requests.get")
    @patch("planner.routing.requests.post")
    def test_total_deadline_stops_a_slow_trickle(self, post, get):
        # Each read succeeds, but the overall deadline has already passed.
        post.return_value = FakeStreamResponse(200, ORS_OK)
        get.return_value = FakeStreamResponse(200, OSRM_OK)
        with self.assertRaises(routing.ProviderTimeout) as cm:
            routing.get_route(LA, NY)
        self.assertEqual((cm.exception.status_code, cm.exception.external_calls), (504, 2))

    @patch("planner.routing.requests.get")
    @patch("planner.routing.requests.post")
    def test_no_route_is_422_and_no_fallback(self, post, get):
        post.return_value = FakeStreamResponse(404, {})
        with self.assertRaises(routing.NoRouteError) as cm:
            routing.get_route(LA, NY)
        self.assertEqual((cm.exception.status_code, cm.exception.external_calls), (422, 1))
        get.assert_not_called()

    @override_settings(ORS_API_KEY="secret-key-123")
    @patch("planner.routing.requests.get", side_effect=requests.ConnectionError("boom"))
    @patch("planner.routing.requests.post", side_effect=requests.ConnectionError("boom"))
    def test_error_messages_never_contain_the_key(self, post, get):
        with self.assertRaises(routing.ProviderError) as cm:
            routing.get_route(LA, NY)
        self.assertNotIn("secret-key-123", str(cm.exception))
        self.assertEqual(cm.exception.status_code, 502)