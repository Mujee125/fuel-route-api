from decimal import Decimal

from django.test import SimpleTestCase, TestCase

from planner.corridor import find_corridor_stations
from planner.geometry import MILES_PER_DEG_LAT, RouteGeometry, haversine_miles
from stations.models import FuelStation

ROUTE = [[-100.0, 35.0], [-90.0, 35.0]]        # ~566 miles due east at lat 35


def north(miles):
    return 35.0 + miles / MILES_PER_DEG_LAT


class GeometryTests(SimpleTestCase):
    def test_haversine_la_to_ny(self):
        self.assertAlmostEqual(haversine_miles(34.05, -118.24, 40.71, -74.0), 2445, delta=15)

    def test_densify_limits_segment_length(self):
        g = RouteGeometry(ROUTE)
        steps = [b - a for a, b in zip(g.cum, g.cum[1:])]
        self.assertLessEqual(max(steps), 0.26)
        self.assertAlmostEqual(g.total_miles, 566, delta=3)

    def test_markers_scale_to_provider_distance(self):
        g = RouteGeometry(ROUTE, distance_miles=1000)
        self.assertAlmostEqual(g.total_miles, 1000, places=6)


class CorridorTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        def make(opis_id, lat, lng):
            FuelStation.objects.create(opis_id=opis_id, name=f"S{opis_id}", city="X",
                                    state="TX", price=Decimal("3.500"), lat=lat, lng=lng)
        make(1, 35.0, -95.0)            # on the route, middle
        make(2, north(3), -92.0)        # 3 mi off
        make(3, north(8), -97.0)        # 8 mi off
        make(4, north(30), -94.0)       # 30 mi off
        make(5, None, None)             # not geocoded
        make(6, 45.0, -80.0)            # nowhere near
        cls.geo = RouteGeometry(ROUTE)

    def ids(self, miles):
        return [s.opis_id for s in find_corridor_stations(self.geo, miles)]

    def test_5_mile_corridor(self):
        self.assertEqual(self.ids(5), [1, 2])

    def test_widening_to_10_adds_8_mile_station(self):
        self.assertEqual(self.ids(10), [3, 1, 2])      # sorted by mile marker

    def test_40_mile_corridor_adds_30_mile_station(self):
        self.assertEqual(self.ids(40), [3, 1, 4, 2])

    def test_marker_and_distance_values(self):
        by_id = {s.opis_id: s for s in find_corridor_stations(self.geo, 5)}
        self.assertAlmostEqual(by_id[1].mile_marker, self.geo.total_miles / 2, delta=0.5)
        self.assertAlmostEqual(by_id[2].distance_from_route_miles, 3.0, delta=0.3)