from django.test import SimpleTestCase

from planner.routing import _decode_polyline


class DecodePolylineTests(SimpleTestCase):
    def test_google_reference_example(self):
        # Reference example from Google's polyline algorithm docs.
        coords = _decode_polyline("_p~iF~ps|U_ulLnnqC_mqNvxq`@")
        expected = [[-120.2, 38.5], [-120.95, 40.7], [-126.453, 43.252]]  # [lng, lat]
        self.assertEqual(len(coords), 3)
        for got, want in zip(coords, expected):
            self.assertAlmostEqual(got[0], want[0], places=5)
            self.assertAlmostEqual(got[1], want[1], places=5)

    def test_empty_string(self):
        self.assertEqual(_decode_polyline(""), [])