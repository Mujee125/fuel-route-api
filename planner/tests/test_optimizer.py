from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from planner.optimizer import InfeasiblePlan, plan_fuel_stops, plan_with_widening


class S:
    def __init__(self, mile, price):
        self.mile_marker, self.price, self.name = mile, Decimal(str(price)), f"S{mile}"


class OptimizerTests(SimpleTestCase):
    def test_short_trip_no_stops(self):                     # Dallas -> Houston (~240 mi)
        p = plan_fuel_stops([S(100, 3.0)], 240)
        self.assertEqual((len(p.stops), p.total_cost), (0, Decimal("0.00")))
        self.assertEqual(p.total_gallons_needed, 24.0)

    def test_700_miles_one_stop(self):
        p = plan_fuel_stops([S(300, 3.0)], 700)
        self.assertEqual(len(p.stops), 1)
        self.assertEqual((p.stops[0].gallons, p.total_cost), (20.0, Decimal("60.00")))

    def test_prefers_cheaper_station_not_nearest(self):
        p = plan_fuel_stops([S(100, 4.0), S(400, 3.0)], 900)
        self.assertEqual([s.station.mile_marker for s in p.stops], [400])
        self.assertEqual(p.total_cost, Decimal("120.00"))   # 40 gal x 3.00

    def test_buys_only_what_is_needed_to_reach_cheaper(self):
        p = plan_fuel_stops([S(200, 3.0), S(600, 2.5)], 1000)
        self.assertEqual([(s.mile_marker, s.gallons) for s in p.stops], [(200, 10.0), (600, 40.0)])
        self.assertEqual(p.total_cost, Decimal("130.00"))   # 30.00 + 100.00

    def test_long_trip_many_stops_and_range_cap(self):      # LA -> NY style, 2800 mi
        stations = [S(m, 3.0) for m in range(200, 2800, 200)]
        p = plan_fuel_stops(stations, 2800)
        self.assertGreaterEqual(len(p.stops), 5)
        self.assertAlmostEqual(p.gallons_purchased, 230.0, places=2)   # 280 needed - 50 initial
        self.assertEqual(p.total_cost, Decimal("690.00"))
        marks = [0] + [s.mile_marker for s in p.stops] + [2800]
        self.assertLessEqual(max(b - a for a, b in zip(marks, marks[1:])), 500)

    def test_gap_over_range_is_infeasible(self):
        with self.assertRaises(InfeasiblePlan) as cm:
            plan_fuel_stops([S(100, 3.0), S(700, 3.0)], 1000)
        self.assertEqual((cm.exception.status_code, cm.exception.at_mile), (422, 100))

    def test_first_station_out_of_reach(self):
        with self.assertRaises(InfeasiblePlan) as cm:
            plan_fuel_stops([S(600, 3.0)], 900)
        self.assertEqual(cm.exception.at_mile, 0.0)

    @patch("planner.optimizer.find_corridor_stations")
    def test_widening_retries_wider_corridor(self, find):
        find.side_effect = [[], [S(300, 3.0)]]
        plan = plan_with_widening(SimpleNamespace(total_miles=700))
        self.assertEqual((plan.corridor_miles, len(plan.stops)), (10, 1))

    @patch("planner.optimizer.find_corridor_stations", return_value=[])
    def test_widening_gives_up_with_422(self, find):
        with self.assertRaises(InfeasiblePlan):
            plan_with_widening(SimpleNamespace(total_miles=700))
        self.assertEqual(find.call_count, 3)

class OptimizerBoundaryTests(SimpleTestCase):
    # --- exact 500 -----------------------------------------------------------
    def test_exactly_500_miles_needs_no_stop(self):
        p = plan_fuel_stops([], 500)
        self.assertEqual((len(p.stops), p.total_cost), (0, Decimal("0.00")))
        self.assertEqual(p.total_gallons_needed, 50.0)

    def test_station_exactly_at_range_limit_is_reachable(self):
        p = plan_fuel_stops([S(500, 3.0)], 1000)          # arrive with an empty tank, then 500 more
        self.assertEqual([s.station.mile_marker for s in p.stops], [500])
        self.assertEqual((p.stops[0].gallons, p.total_cost), (50.0, Decimal("150.00")))

    def test_just_over_500_without_a_station_is_infeasible(self):
        with self.assertRaises(InfeasiblePlan) as cm:
            plan_fuel_stops([], 500.5)
        self.assertEqual(cm.exception.status_code, 422)

    # --- empty corridor --------------------------------------------------------
    def test_empty_corridor_is_fine_for_a_short_trip(self):
        self.assertEqual(len(plan_fuel_stops([], 300).stops), 0)

    def test_empty_corridor_is_422_for_a_long_trip(self):
        with self.assertRaises(InfeasiblePlan) as cm:
            plan_fuel_stops([], 700)
        self.assertEqual(cm.exception.status_code, 422)

    # --- ties ------------------------------------------------------------------
    def test_equal_prices_are_deterministic_and_cost_is_hand_computed(self):
        stations = [S(400, 3.0), S(500, 3.0)]
        a = plan_fuel_stops(stations, 900)
        b = plan_fuel_stops(list(stations), 900)
        self.assertEqual([s.station.mile_marker for s in a.stops],
                         [s.station.mile_marker for s in b.stops])
        self.assertEqual(a.total_cost, b.total_cost)
        self.assertEqual(a.total_cost, Decimal("120.00"))      # 90 gal needed - 50 initial = 40 x 3.00
        marks = [0] + [s.station.mile_marker for s in a.stops] + [900]
        self.assertLessEqual(max(y - x for x, y in zip(marks, marks[1:])), 500)