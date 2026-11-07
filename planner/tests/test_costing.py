from decimal import Decimal
from types import SimpleNamespace as NS

from django.test import SimpleTestCase

from planner.costing import build_costs


def stop(price, gallons):
    return NS(price=price, gallons=gallons)


def plan(*stops):
    return NS(stops=list(stops))


class CostingTests(SimpleTestCase):
    def test_no_stops_short_trip(self):
        c = build_costs(plan(), 250.0)
        self.assertEqual(c.total_gallons_needed, Decimal("25.000"))
        self.assertEqual(c.total_cost, Decimal("0.00"))
        self.assertEqual(c.initial_gallons, Decimal("25.000"))
        self.assertFalse(c.initial_charged)

    def test_two_stops_hand_computed(self):
        c = build_costs(plan(stop(3.499, 10.5), stop(3.25, 20)), 700.0)
        self.assertEqual([s.cost for s in c.stops], [Decimal("36.74"), Decimal("65.00")])
        self.assertEqual(c.total_cost, Decimal("101.74"))
        self.assertEqual(c.total_gallons_needed, Decimal("70.000"))

    def test_total_is_sum_of_rounded_stop_costs(self):
        c = build_costs(plan(stop(3.333, 1.001), stop(3.333, 1.001)), 100.0)
        self.assertEqual(c.total_cost, sum(s.cost for s in c.stops))

    def test_initial_tank_flag_charges_burned_gallons(self):
        c = build_costs(plan(stop(3.499, 10.5), stop(3.25, 20)), 700.0, True, Decimal("3.499"))
        self.assertEqual(c.initial_gallons, Decimal("39.500"))
        self.assertEqual(c.initial_cost, Decimal("138.21"))
        self.assertEqual(c.total_cost, Decimal("239.95"))
        self.assertTrue(c.initial_charged)

    def test_flag_without_price_is_not_charged(self):
        c = build_costs(plan(), 250.0, True, None)
        self.assertFalse(c.initial_charged)
        self.assertEqual(c.total_cost, Decimal("0.00"))

    def test_zero_distance(self):
        c = build_costs(plan(), 0.0)
        self.assertEqual(c.total_gallons_needed, Decimal("0.000"))
        self.assertEqual(c.initial_gallons, Decimal("0.000"))