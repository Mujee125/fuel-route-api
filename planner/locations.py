import re
from dataclasses import dataclass
from functools import lru_cache

import requests
from django.conf import settings

from stations.geo import load_gazetteer, match_city
from stations.management.commands.load_fuel_stations import CANADA, US_STATES

from .routing import ProviderError, ProviderTimeout

COORD_RE = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$")
CITY_STATE_RE = re.compile(r"^\s*(.+?)\s*,\s*([A-Za-z]{2})\s*$")
NON_CONTIGUOUS = {"AK", "HI"}

# Coarse outline of the contiguous US as (lng, lat). Rejects Canada and Mexico
# without any external call. Approximate near borders (a few miles).
CONUS = [
    (-125.0, 48.4), (-123.2, 49.0), (-95.1, 49.0), (-89.5, 48.0), (-84.9, 46.5),
    (-83.5, 46.0), (-82.5, 45.3), (-82.4, 43.0), (-83.1, 42.1), (-82.5, 41.7),
    (-79.0, 42.8), (-79.0, 43.3), (-76.5, 43.6), (-75.0, 45.0), (-71.5, 45.0),
    (-70.8, 45.4), (-70.0, 46.7), (-69.2, 47.45), (-67.8, 47.07), (-67.8, 45.7),
    (-67.0, 44.8), (-66.9, 44.5), (-69.0, 40.0), (-74.0, 35.0), (-78.0, 31.0),
    (-79.0, 25.0), (-82.0, 24.3), (-83.5, 29.5), (-86.0, 29.5), (-90.0, 28.5),
    (-94.5, 28.0), (-96.8, 26.0), (-97.15, 25.95), (-99.1, 26.4), (-100.6, 28.7),
    (-101.4, 29.8), (-103.1, 28.98), (-104.5, 29.6), (-106.5, 31.75), (-108.2, 31.78),
    (-108.2, 31.33), (-111.1, 31.33), (-114.8, 32.5), (-117.1, 32.5), (-117.5, 32.5),
    (-121.0, 34.0), (-123.0, 37.0), (-125.0, 40.4), (-125.0, 42.0), (-124.9, 46.0),
]


class LocationError(Exception):
    status_code = 400

    def __init__(self, message, external_calls=0):
        super().__init__(message)
        self.external_calls = external_calls


class InvalidLocation(LocationError):
    status_code = 400


class LocationNotFound(LocationError):
    status_code = 404


@dataclass
class Location:
    label: str
    lat: float
    lng: float
    source: str            # coordinates | gazetteer | nominatim
    external_calls: int


def in_conus(lat, lng):
    inside = False
    j = len(CONUS) - 1
    for i, (xi, yi) in enumerate(CONUS):
        xj, yj = CONUS[j]
        if (yi > lat) != (yj > lat) and lng < (xj - xi) * (lat - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def _require_conus(lat, lng, label, calls=0):
    if not in_conus(lat, lng):
        raise InvalidLocation(
            f"'{label}' is outside the contiguous USA (only the lower 48 states are supported)",
            external_calls=calls)


@lru_cache(maxsize=1)
def _gazetteer():
    return load_gazetteer(settings.BASE_DIR / "data" / "us_places.csv")


def resolve(text, allow_remote=True) -> Location:
    text = (text or "").strip()
    if not text:
        raise InvalidLocation("Location is empty")
    if len(text) > 200:
        raise InvalidLocation("Location text is too long")

    m = COORD_RE.match(text)
    if m:
        lat, lng = float(m[1]), float(m[2])
        _require_conus(lat, lng, text)
        return Location(f"{lat:.4f},{lng:.4f}", lat, lng, "coordinates", 0)

    m = CITY_STATE_RE.match(text)
    if m:
        city, st = m[1], m[2].upper()
        if st in CANADA:
            raise InvalidLocation(f"'{text}' is not in the USA")
        if st in NON_CONTIGUOUS:
            raise InvalidLocation("Only the contiguous USA (lower 48) is supported")
        if st in US_STATES:
            hit = match_city(_gazetteer().get(st, {}), city)
            if hit:
                return Location(f"{city.title()}, {st}", hit[0], hit[1], "gazetteer", 0)

    if not (settings.GEOCODER_FALLBACK and allow_remote):
        raise LocationNotFound(f"Could not resolve '{text}'. Use 'City, ST' or 'lat,lng'.")
    return _nominatim(text)


def _nominatim(text) -> Location:
    try:
        r = requests.get(
            settings.NOMINATIM_URL,
            params={"q": text, "format": "jsonv2", "limit": 1, "countrycodes": "us"},
            headers={"User-Agent": settings.GEOCODER_USER_AGENT},
            timeout=settings.ROUTING_TIMEOUT)
    except requests.Timeout:
        raise ProviderTimeout("Geocoder timed out", external_calls=1)
    except requests.RequestException as e:
        raise ProviderError(f"Geocoder unreachable: {e}", external_calls=1)
    if r.status_code != 200:
        raise ProviderError(f"Geocoder returned HTTP {r.status_code}", external_calls=1)
    try:
        data = r.json()
    except ValueError:
        raise ProviderError("Geocoder returned invalid JSON", external_calls=1)
    if not data:
        raise LocationNotFound(f"Could not resolve '{text}'", external_calls=1)
    lat, lng = float(data[0]["lat"]), float(data[0]["lon"])
    _require_conus(lat, lng, text, calls=1)
    return Location(text, lat, lng, "nominatim", 1)


def resolve_pair(start, finish):
    """Returns (start_loc, finish_loc, geocode_calls). Errors carry the calls made so far."""
    a = resolve(start)
    try:
        b = resolve(finish)
    except (LocationError, ProviderError) as e:
        e.external_calls += a.external_calls
        raise
    return a, b, a.external_calls + b.external_calls