from dataclasses import dataclass

import requests
from django.conf import settings

METERS_PER_MILE = 1609.344
ORS_URL = "https://api.openrouteservice.org/v2/directions/driving-car/geojson"


class RoutingError(Exception):
    status_code = 502

    def __init__(self, message, external_calls=0):
        super().__init__(message)
        self.external_calls = external_calls


class NoRouteError(RoutingError):
    status_code = 422


class ProviderError(RoutingError):
    status_code = 502


class ProviderTimeout(RoutingError):
    status_code = 504


@dataclass
class RouteResult:
    coordinates: list          # [[lng, lat], ...] GeoJSON order
    distance_miles: float
    duration_minutes: float
    provider: str
    external_calls: int


def _ors(start, finish):
    """start/finish are (lat, lng). Returns (coords, meters, seconds)."""
    body = {
        "coordinates": [[start[1], start[0]], [finish[1], finish[0]]],
        "radiuses": [-1, -1],       # city centroids may be far from a road, so don't limit snapping
        "instructions": False,      # smaller payload
    }
    try:
        r = requests.post(ORS_URL, json=body, timeout=settings.ROUTING_TIMEOUT,
                        headers={"Authorization": settings.ORS_API_KEY})
    except requests.Timeout:
        raise ProviderTimeout("OpenRouteService timed out")
    except requests.RequestException as e:
        raise ProviderError(f"OpenRouteService unreachable: {e}")
    if r.status_code == 404:
        raise NoRouteError("No drivable route between these locations")
    if r.status_code != 200:
        raise ProviderError(f"OpenRouteService returned HTTP {r.status_code}")
    try:
        feat = r.json()["features"][0]
        s = feat["properties"]["summary"]
        return feat["geometry"]["coordinates"], s["distance"], s["duration"]
    except (KeyError, IndexError, ValueError):
        raise ProviderError("Unexpected OpenRouteService response")


def _osrm(start, finish):
    url = (f"{settings.OSRM_BASE_URL}/route/v1/driving/"
        f"{start[1]},{start[0]};{finish[1]},{finish[0]}")
    try:
        r = requests.get(url, params={"overview": "full", "geometries": "geojson"},
                        timeout=settings.ROUTING_TIMEOUT)
    except requests.Timeout:
        raise ProviderTimeout("OSRM timed out")
    except requests.RequestException as e:
        raise ProviderError(f"OSRM unreachable: {e}")
    try:
        data = r.json()
    except ValueError:
        raise ProviderError(f"OSRM returned HTTP {r.status_code} (not JSON)")
    if data.get("code") in ("NoRoute", "NoSegment"):
        raise NoRouteError("No drivable route between these locations")
    if r.status_code != 200 or data.get("code") != "Ok":
        raise ProviderError(f"OSRM error: {data.get('code', r.status_code)}")
    try:
        route = data["routes"][0]
        return route["geometry"]["coordinates"], route["distance"], route["duration"]
    except (KeyError, IndexError):
        raise ProviderError("Unexpected OSRM response")


def get_route(start, finish):
    """One ORS call; OSRM only if ORS fails (never more than 2 calls). start/finish = (lat, lng)."""
    calls = 0
    providers = []
    if settings.ORS_API_KEY:
        providers.append(("ors", _ors))
    providers.append(("osrm", _osrm))

    for name, fn in providers:
        calls += 1
        try:
            coords, meters, seconds = fn(start, finish)
        except NoRouteError as e:       # a real "no route" answer, so don't retry elsewhere
            e.external_calls = calls
            raise
        except RoutingError as e:
            e.external_calls = calls
            last = e
            continue
        return RouteResult(coords, meters / METERS_PER_MILE, seconds / 60, name, calls)
    raise last