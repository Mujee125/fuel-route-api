from unittest.mock import Mock, patch

import requests
from django.test import SimpleTestCase, override_settings

from planner import routing

LA, NY = (34.05, -118.24), (40.71, -74.0)
LINE = [[-118.2, 34.0], [-74.0, 40.7]]
ORS_OK = {"features": [{"geometry": {"coordinates": LINE},
                        "properties": {"summary": {"distance": 4506000, "duration": 144000}}}]}
OSRM_OK = {"code": "Ok", "routes": [{"geometry": {"coordinates": LINE},
                                    "distance": 4506000, "duration": 144000}]}


def resp(status, payload):
    m = Mock()
    m.status_code = status
    m.json.return_value = payload
    return m


@override_settings(ORS_API_KEY="k")
class RoutingTests(SimpleTestCase):
    @patch("planner.routing.requests.post")
    def test_ors_success_is_one_call(self, post):
        post.return_value = resp(200, ORS_OK)
        r = routing.get_route(LA, NY)
        self.assertEqual((r.provider, r.external_calls), ("ors", 1))
        self.assertAlmostEqual(r.distance_miles, 2800, delta=10)

    @patch("planner.routing.requests.get")
    @patch("planner.routing.requests.post")
    def test_ors_failure_falls_back_to_osrm(self, post, get):
        post.return_value = resp(500, {})
        get.return_value = resp(200, OSRM_OK)
        r = routing.get_route(LA, NY)
        self.assertEqual((r.provider, r.external_calls), ("osrm", 2))

    @patch("planner.routing.requests.get", side_effect=requests.Timeout)
    @patch("planner.routing.requests.post", side_effect=requests.Timeout)
    def test_both_timeout_gives_504_with_call_count(self, post, get):
        with self.assertRaises(routing.ProviderTimeout) as cm:
            routing.get_route(LA, NY)
        self.assertEqual((cm.exception.status_code, cm.exception.external_calls), (504, 2))

    @patch("planner.routing.requests.get")
    @patch("planner.routing.requests.post")
    def test_no_route_is_422_and_no_fallback(self, post, get):
        post.return_value = resp(404, {})
        with self.assertRaises(routing.NoRouteError) as cm:
            routing.get_route(LA, NY)
        self.assertEqual((cm.exception.status_code, cm.exception.external_calls), (422, 1))
        get.assert_not_called()