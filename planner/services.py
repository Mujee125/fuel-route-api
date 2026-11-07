"""One route-plan request: resolve -> route -> corridor -> optimise -> cost -> payload."""
from types import SimpleNamespace
from urllib.parse import urlencode

from django.conf import settings

from .corridor import find_corridor_stations
from .costing import build_costs
from .geometry import RouteGeometry
from .locations import resolve_pair
from .mapdata import build_geojson, thin_coordinates
from .optimizer import plan_with_widening
from .routing import get_route

MAP_PATH = "/api/route-plan/map/"  # keep in sync with planner/urls.py


def _route_args(loc):
    """Adjust here if get_route expects Location objects or (lng, lat)."""
    return (loc.lat, loc.lng)


def _same_point(a, b):
    return round(a.lat, 3) == round(b.lat, 3) and round(a.lng, 3) == round(b.lng, 3)


def _tag(exc, calls):
    exc.total_external_calls = calls  # read by api_errors.describe_error
    return exc


def _initial_price(plan, geo):
    """Price used when include_initial_tank_cost=true (README D1)."""
    if plan.stops:
        return plan.stops[0].price
    reach = min(settings.VEHICLE_RANGE_MILES, geo.total_miles)
    prices = [s.price for s in find_corridor_stations(geo, plan.corridor_miles)
            if s.mile_marker <= reach]
    return min(prices) if prices else None


def _loc(query, loc):
    return {"query": query, "label": loc.label, "lat": round(float(loc.lat), 5),
            "lng": round(float(loc.lng), 5)}


def _assemble(start, finish, loc_a, loc_b, coords, distance, duration, provider,
            plan, costs, calls, flag):
    fuel_stops = []
    for c in costs.stops:
        st = c.stop.station
        fuel_stops.append({
            "order": c.order, "opis_id": st.opis_id, "name": st.name, "address": st.address,
            "city": st.city, "state": st.state,
            "lat": round(float(st.lat), 5), "lng": round(float(st.lng), 5),
            "mile_marker": round(float(c.stop.mile_marker), 1),
            "distance_from_route_miles": round(float(st.distance_from_route_miles), 2),
            "price_per_gallon": float(c.price),
            "gallons_purchased": float(c.gallons),
            "cost": float(c.cost),
        })

    start_d, finish_d = _loc(start, loc_a), _loc(finish, loc_b)
    geometry = thin_coordinates(coords)
    distance = round(float(distance), 2)

    map_query = {"start": start, "finish": finish}
    if flag:
        map_query["include_initial_tank_cost"] = "true"

    return {
        "start": start_d,
        "finish": finish_d,
        "route": {"distance_miles": distance, "duration_minutes": int(round(duration)),
                "geometry": {"type": "LineString", "coordinates": geometry}},
        "fuel_stops": fuel_stops,
        "summary": {
            "vehicle_range_miles": settings.VEHICLE_RANGE_MILES,
            "mpg": settings.VEHICLE_MPG,
            "stops_count": len(fuel_stops),
            "total_gallons_needed": float(costs.total_gallons_needed),
            "total_fuel_cost": float(costs.total_cost),
            "currency": "USD",
            "initial_tank_assumed_full": True,
            "initial_tank_gallons": float(costs.initial_gallons),
            "initial_tank_charged": costs.initial_charged,
            "initial_tank_price_per_gallon":
                float(costs.initial_price) if costs.initial_price is not None else None,
            "initial_tank_cost": float(costs.initial_cost),
            "vehicle_type": settings.VEHICLE_TYPE,
        },
        "map": {
            "geojson": build_geojson(start_d, finish_d, geometry, fuel_stops, distance),
            "html_url": f"{MAP_PATH}?{urlencode(map_query)}",
        },
        "meta": {
            "external_api_calls": calls,
            "cache_hit": False,
            "routing_provider": provider,
            "corridor_miles": plan.corridor_miles,
            "routing_profile": _routing_profile(provider),
        },
    }


def plan_route(start: str, finish: str, include_initial_tank_cost: bool = False) -> dict:
    try:
        loc_a, loc_b, geocode_calls = resolve_pair(start, finish)
    except Exception as exc:
        raise _tag(exc, getattr(exc, "external_calls", 0))

    if _same_point(loc_a, loc_b):  # zero-distance trip: no routing call wasted
        empty = SimpleNamespace(stops=[], corridor_miles=None)
        costs = build_costs(empty, 0.0, include_initial_tank_cost, None)
        coords = [[loc_a.lng, loc_a.lat], [loc_a.lng, loc_a.lat]]
        return _assemble(start, finish, loc_a, loc_b, coords, 0.0, 0, None,
                    empty, costs, geocode_calls, include_initial_tank_cost)

    max_calls = min(2, settings.TOTAL_EXTERNAL_CALL_BUDGET - geocode_calls)
    try:
        route = get_route(_route_args(loc_a), _route_args(loc_b), max_calls=max_calls)
    except Exception as exc:
        raise _tag(exc, geocode_calls + getattr(exc, "external_calls", 0))

    calls = geocode_calls + route.external_calls
    try:
        geo = RouteGeometry(route.coordinates, route.distance_miles)
        plan = plan_with_widening(geo)
        initial_price = _initial_price(plan, geo) if include_initial_tank_cost else None
    except Exception as exc:
        raise _tag(exc, calls)

    costs = build_costs(plan, route.distance_miles, include_initial_tank_cost, initial_price)
    return _assemble(start, finish, loc_a, loc_b, route.coordinates, route.distance_miles,
                    route.duration_minutes, route.provider, plan, costs, calls,
                    include_initial_tank_cost)

def _routing_profile(provider):
    if provider is None:
        return None
    if provider == "ors":
        return settings.ORS_PROFILE
    return "car (fallback: OSRM public demo has no truck profile)"