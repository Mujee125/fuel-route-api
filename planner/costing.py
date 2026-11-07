"""US-09: turn a FuelPlan into API-ready gallons and money (D1, D9). Pure; no DB."""
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings

CENT = Decimal("0.01")
MILLI = Decimal("0.001")
ZERO = Decimal("0")


def _d(value) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _q(value, step: Decimal) -> Decimal:
    return _d(value).quantize(step, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class StopCost:
    order: int
    stop: object          # PlannedStop
    price: Decimal        # $/gal, 3 dp
    gallons: Decimal      # 3 dp
    cost: Decimal         # gallons * price, cents


@dataclass(frozen=True)
class CostBreakdown:
    stops: list
    total_gallons_needed: Decimal
    en_route_cost: Decimal
    initial_gallons: Decimal
    initial_price: Decimal | None
    initial_cost: Decimal
    initial_charged: bool
    total_cost: Decimal


def build_costs(plan, total_miles, include_initial_tank_cost=False, initial_price=None):
    needed = _q(_d(total_miles) / _d(settings.VEHICLE_MPG), MILLI)

    stops = []
    for order, stop in enumerate(plan.stops, start=1):
        price = _q(stop.price, MILLI)
        gallons = _q(stop.gallons, MILLI)
        stops.append(StopCost(order, stop, price, gallons, _q(price * gallons, CENT)))

    en_route = sum((s.cost for s in stops), ZERO)
    bought = sum((s.gallons for s in stops), ZERO)
    initial_gallons = max(needed - bought, ZERO)  # fuel burned from the free starting tank

    charged = bool(include_initial_tank_cost) and initial_price is not None
    price = _q(initial_price, MILLI) if charged else None
    initial_cost = _q(initial_gallons * price, CENT) if charged else ZERO

    return CostBreakdown(
        stops=stops,
        total_gallons_needed=needed,
        en_route_cost=en_route,
        initial_gallons=initial_gallons,
        initial_price=price,
        initial_cost=initial_cost,
        initial_charged=charged,
        total_cost=en_route + initial_cost,
    )