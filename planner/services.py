"""One route-plan request: cache -> resolve -> route -> corridor -> optimise -> cost -> payload."""
import hashlib
import logging
import re
from types import SimpleNamespace
from urllib.parse import urlencode

from django.conf import settings
from django.core.cache import cache

from .corridor import find_corridor_stations
from .costing import build_costs
from .geometry import RouteGeometry
from .locations import resolve_pair
from .mapdata import build_geojson, thin_coordinates
from .optimizer import plan_with_widening
from .routing import get_route

logger = logging.getLogger(__name__)

MAP_PATH = "/api/route-plan/map/"  # keep in sync with planner/urls.py
CACHE_PREFIX = "routeplan:v1"      # bump when the response shape or algorithm changes


# ---------------------------------------------------------------- cache helpers (D8)
def _cache_get(key):
    try:
        return cache.get(key)
    except Exception:  # cache must never break a request
        logger.warning("cache get failed", exc_info=True)
        return None


FALLBACK_CACHE_TTL = 300  # seconds; degraded (non-ORS) results should not live for 24 h


def _cache_set(key, value, ttl=None):
    try:
        cache.set(key, value, settings.ROUTE_CACHE_TTL if ttl is None else ttl)
    except Exception:
        logger.warning("cache set failed", exc_info=True)


def _norm(text):
    return re.sub(r"\s+", " ", text.strip().lower())


def _alias_key(start, finish, flag):
    """Normalised request text -> plan key. Lets repeats skip geocoding entirely."""
    raw = f"{_norm(start)}|{_norm(finish)}|{int(flag)}"
    return f"{CACHE_PREFIX}:q:{hashlib.sha1(raw.encode()).hexdigest()}"


def _plan_key(loc_a, loc_b, flag):
    """D8: rounded (3 dp) lat/lng pair of the resolved locations."""
    return (f"{CACHE_PREFIX}:p:{loc_a.lat:.3f},{loc_a.lng:.3f}"
            f"|{loc_b.lat:.3f},{loc_b.lng:.3f}|{int(flag)}")


def _map_url(start, finish, flag):
    query = {"start": start, "finish": finish}
    if flag:
        query["include_initial_tank_cost"] = "true"
    return f"{MAP_PATH}?{urlencode(query)}"


def _from_cache(payload, start, finish, flag, calls=0):
    """Copy a cached payload, restoring this request's own text and call count."""
    out = dict(payload)
    out["start"] = {**payload["start"], "query": start}
    out["finish"] = {**payload["finish"], "query": finish}
    out["map"] = {**payload["map"], "html_url": _map_url(start, finish, flag)}
    out["meta"] = {**payload["meta"], "external_api_calls": calls, "cache_hit": True}
    return out


# ---------------------------------------------------------------- helpers
def _route_args(loc):
    """Adjust here if get_route expects Location objects or (lng, lat)."""
    return (loc.lat, loc.lng)


def _same_point(a, b):
    return round(a.lat, 3) == round(b.lat, 3) and round(a.lng, 3) == round(b.lng, 3)


def _tag(exc, calls):
    exc.total_external_calls = calls  # read by api_errors.describe_error
    return exc


def _routing_profile(provider):
    if provider is None:
        return None
    if provider == "ors":
        return settings.ORS_PROFILE
    return "car (fallback: OSRM public demo has no truck profile)"


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

    return {
        "start": start_d,
        "finish": finish_d,
        "route": {"distance_miles": distance, "duration_minutes": int(round(duration)),
                "geometry": {"type": "LineString", "coordinates": geometry}},
        "fuel_stops": fuel_stops,
        "summary": {
            "vehicle_type": settings.VEHICLE_TYPE,
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
        },
        "map": {
            "geojson": build_geojson(start_d, finish_d, geometry, fuel_stops, distance),
            "html_url": _map_url(start, finish, flag),
        },
        "meta": {
            "external_api_calls": calls,
            "cache_hit": False,
            "routing_provider": provider,
            "routing_profile": _routing_profile(provider),
            "corridor_miles": plan.corridor_miles,
        },
    }


def _compute(start, finish, loc_a, loc_b, geocode_calls, flag):
    """Cache miss path: one routing call, corridor, optimiser, costs."""
    max_calls = min(2, settings.TOTAL_EXTERNAL_CALL_BUDGET - geocode_calls)
    try:
        route = get_route(_route_args(loc_a), _route_args(loc_b), max_calls=max_calls)
    except Exception as exc:
        raise _tag(exc, geocode_calls + getattr(exc, "external_calls", 0))

    calls = geocode_calls + route.external_calls
    try:
        geo = RouteGeometry(route.coordinates, route.distance_miles)
        plan = plan_with_widening(geo)
        initial_price = _initial_price(plan, geo) if flag else None
    except Exception as exc:
        raise _tag(exc, calls)

    costs = build_costs(plan, route.distance_miles, flag, initial_price)
    return _assemble(start, finish, loc_a, loc_b, route.coordinates, route.distance_miles,
                    route.duration_minutes, route.provider, plan, costs, calls, flag)


# ---------------------------------------------------------------- entry point
def plan_route(start: str, finish: str, include_initial_tank_cost: bool = False) -> dict:
    flag = bool(include_initial_tank_cost)

    # 1) Alias hit: same request text seen before -> no resolving, no external calls.
    alias = _alias_key(start, finish, flag)
    known_key = _cache_get(alias)
    if known_key:
        cached = _cache_get(known_key)
        if cached is not None:
            return _from_cache(cached, start, finish, flag)

    # 2) Resolve locations (local gazetteer first; free text may cost 1 geocode call).
    try:
        loc_a, loc_b, geocode_calls = resolve_pair(start, finish)
    except Exception as exc:
        raise _tag(exc, getattr(exc, "external_calls", 0))

    if _same_point(loc_a, loc_b):  # zero-distance trip: no routing call, nothing to cache
        empty = SimpleNamespace(stops=[], corridor_miles=None)
        costs = build_costs(empty, 0.0, flag, None)
        coords = [[loc_a.lng, loc_a.lat], [loc_a.lng, loc_a.lat]]
        return _assemble(start, finish, loc_a, loc_b, coords, 0.0, 0, None,
                        empty, costs, geocode_calls, flag)

    # 3) Plan hit by rounded coordinates (D8): different spelling, same places.
    plan_key = _plan_key(loc_a, loc_b, flag)
    cached = _cache_get(plan_key)
    if cached is not None:
        _cache_set(alias, plan_key)
        return _from_cache(cached, start, finish, flag, calls=geocode_calls)

    # 4) Miss: compute, then store (errors raise above and are never cached).

    payload = _compute(start, finish, loc_a, loc_b, geocode_calls, flag)
    # Only ORS (truck profile) results get the full TTL; a fallback result is cached briefly.
    ttl = None if payload["meta"].get("routing_provider") == "ors" else FALLBACK_CACHE_TTL
    _cache_set(plan_key, payload, ttl)
    _cache_set(alias, plan_key, ttl)
    return payload