from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

from django.conf import settings

from .corridor import find_corridor_stations

EPS = 1e-9
CENT = Decimal("0.01")


class InfeasiblePlan(Exception):
    status_code = 422

    def __init__(self, message, at_mile):
        super().__init__(message)
        self.at_mile = at_mile


@dataclass
class PlannedStop:
    station: object
    mile_marker: float
    price: Decimal
    gallons: float
    cost: Decimal


@dataclass
class FuelPlan:
    stops: list
    total_cost: Decimal
    gallons_purchased: float
    total_gallons_needed: float
    corridor_miles: float = None


def _cheapest_in_range(sts, marks, i, reach):
    best = None
    for k in range(i + 1, len(sts)):
        if marks[k] > reach + EPS:
            break
        if best is None or sts[k].price < sts[best].price:
            best = k
    return best


def plan_fuel_stops(stations, total_miles, vehicle_range=None, mpg=None):
    """stations need .mile_marker and .price. Returns FuelPlan or raises InfeasiblePlan."""
    vehicle_range = float(vehicle_range or settings.VEHICLE_RANGE_MILES)
    mpg = float(mpg or settings.VEHICLE_MPG)

    nodes = sorted(((min(s.mile_marker, total_miles), s) for s in stations),
                key=lambda n: n[0])
    marks = [m for m, _ in nodes]
    sts = [s for _, s in nodes]
    for s in sts:
        s.price = Decimal(str(s.price))
    n = len(sts)

    stops = []
    pos, fuel, i = 0.0, vehicle_range, -1          # D1: full tank, free

    if total_miles > fuel + EPS:
        j = _cheapest_in_range(sts, marks, i, pos + fuel)
        if j is None:
            raise InfeasiblePlan("No fuel station within range of the start", 0.0)
        fuel -= marks[j] - pos
        pos, i = marks[j], j

        while True:
            price = sts[i].price
            full_reach = pos + vehicle_range

            target = None
            for k in range(i + 1, n):                      # nearest cheaper station
                if marks[k] > full_reach + EPS:
                    break
                if sts[k].price < price:
                    target = k
                    break
            if target is None and total_miles <= full_reach + EPS:
                target = n                                  # destination in range

            if target is None:                              # fill up, go to cheapest in range
                j = _cheapest_in_range(sts, marks, i, full_reach)
                if j is None:
                    raise InfeasiblePlan(
                        f"No fuel station within {vehicle_range:.0f} miles after mile {pos:.0f}", pos)
                buy, target = vehicle_range - fuel, j
            else:
                leg_end = total_miles if target == n else marks[target]
                buy = max(0.0, (leg_end - pos) - fuel)      # only what is needed

            if buy > EPS:
                gallons = round(buy / mpg, 3)
                cost = (Decimal(str(gallons)) * price).quantize(CENT, ROUND_HALF_UP)
                stops.append(PlannedStop(sts[i], pos, price, gallons, cost))
            fuel += buy

            leg_end = total_miles if target == n else marks[target]
            fuel -= leg_end - pos
            pos = leg_end
            if target == n:
                break
            i = target

    return FuelPlan(
        stops=stops,
        total_cost=sum((s.cost for s in stops), Decimal("0.00")),
        gallons_purchased=round(sum(s.gallons for s in stops), 3),
        total_gallons_needed=round(total_miles / mpg, 3),
    )


def plan_with_widening(geo, levels=None):
    """D4: try corridor 5 -> 10 -> 25 miles; raise InfeasiblePlan (422) if all fail."""
    last = None
    for miles in (levels or settings.CORRIDOR_LEVELS_MILES):
        stations = find_corridor_stations(geo, miles)
        try:
            plan = plan_fuel_stops(stations, geo.total_miles)
        except InfeasiblePlan as e:
            last = e
            continue
        plan.corridor_miles = miles
        return plan
    raise last