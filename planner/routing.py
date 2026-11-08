import json
import time
from dataclasses import dataclass

import requests
from django.conf import settings

METERS_PER_MILE = 1609.344
# Encoded-polyline endpoint: ~6x smaller than /geojson (73 KB vs 446 KB for LA->NY).
ORS_URL = f"https://api.openrouteservice.org/v2/directions/{settings.ORS_PROFILE}"


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


def _decode_polyline(encoded, precision=5):
    """Decode a Google-style encoded polyline into [[lng, lat], ...]."""
    factor = 10 ** precision
    coords, index, lat, lng = [], 0, 0, 0
    while index < len(encoded):
        for axis in (0, 1):
            shift = result = 0
            while True:
                b = ord(encoded[index]) - 63
                index += 1
                result |= (b & 0x1F) << shift
                shift += 5
                if b < 0x20:
                    break
            delta = ~(result >> 1) if result & 1 else result >> 1
            if axis == 0:
                lat += delta
            else:
                lng += delta
        coords.append([lng / factor, lat / factor])
    return coords


def _fetch(send, url, name, **kwargs):
    """Streamed request with a TOTAL deadline (requests' timeout is per connect/read only).

    Returns (status_code, body_bytes). Never includes request headers in errors.
    """
    deadline = time.monotonic() + getattr(settings, "ROUTING_DEADLINE", 45)
    try:
        with send(url, stream=True, timeout=settings.ROUTING_TIMEOUT, **kwargs) as r:
            parts = []
            for chunk in r.iter_content(65536):
                if time.monotonic() > deadline:
                    raise ProviderTimeout(f"{name} timed out")
                parts.append(chunk)
            return r.status_code, b"".join(parts)
    except requests.Timeout:
        raise ProviderTimeout(f"{name} timed out")
    except requests.RequestException as e:
        raise ProviderError(f"{name} unreachable: {e}")


def _ors(start, finish):
    """start/finish are (lat, lng). Returns (coords, meters, seconds)."""
    body = {
        "coordinates": [[start[1], start[0]], [finish[1], finish[0]]],
        "radiuses": [-1, -1],       # city centroids may be far from a road, so don't limit snapping
        "instructions": False,      # smaller payload
    }
    status, raw = _fetch(requests.post, ORS_URL, "OpenRouteService", json=body,
                        headers={"Authorization": settings.ORS_API_KEY})
    if status == 404:
        raise NoRouteError("No drivable route between these locations")
    if status != 200:
        raise ProviderError(f"OpenRouteService returned HTTP {status}")
    try:
        route = json.loads(raw)["routes"][0]
        summary = route["summary"]
        coords = _decode_polyline(route["geometry"])
        if len(coords) < 2:
            raise ValueError("empty geometry")
        return coords, summary["distance"], summary["duration"]
    except (KeyError, IndexError, ValueError, TypeError):
        raise ProviderError("Unexpected OpenRouteService response")


def _osrm(start, finish):
    url = (f"{settings.OSRM_BASE_URL}/route/v1/driving/"
        f"{start[1]},{start[0]};{finish[1]},{finish[0]}")
    status, raw = _fetch(requests.get, url, "OSRM",
                        params={"overview": "full", "geometries": "geojson"})
    try:
        data = json.loads(raw)
    except ValueError:
        raise ProviderError(f"OSRM returned HTTP {status} (not JSON)")
    if data.get("code") in ("NoRoute", "NoSegment"):
        raise NoRouteError("No drivable route between these locations")
    if status != 200 or data.get("code") != "Ok":
        raise ProviderError(f"OSRM error: {data.get('code', status)}")
    try:
        route = data["routes"][0]
        return route["geometry"]["coordinates"], route["distance"], route["duration"]
    except (KeyError, IndexError):
        raise ProviderError("Unexpected OSRM response")


def get_route(start, finish, max_calls=2):
    """One ORS call; OSRM only if ORS fails (never more than 2 calls). start/finish = (lat, lng)."""
    calls = 0
    providers = []
    if settings.ORS_API_KEY:
        providers.append(("ors", _ors))
    providers.append(("osrm", _osrm))
    providers = providers[:max_calls]

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