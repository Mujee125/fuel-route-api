import math
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal

from stations.models import FuelStation

from .geometry import MILES_PER_DEG_LAT


@dataclass
class CorridorStation:
    opis_id: int
    name: str
    address: str
    city: str
    state: str
    lat: float
    lng: float
    price: Decimal
    mile_marker: float
    distance_from_route_miles: float


def find_corridor_stations(geo, corridor_miles):
    """Stations within corridor_miles of the route, sorted by mile marker."""
    lats, lngs = geo.lats, geo.lngs

    # Grid cells sized to the corridor, so a 3x3 neighbourhood covers every point in range.
    max_abs_lat = min(max(abs(min(lats)), abs(max(lats))), 70)
    cell_lat = corridor_miles / MILES_PER_DEG_LAT
    cell_lng = corridor_miles / (MILES_PER_DEG_LAT * math.cos(math.radians(max_abs_lat)))

    grid = defaultdict(list)
    for i in range(len(lats)):
        grid[(int(lats[i] // cell_lat), int(lngs[i] // cell_lng))].append(i)

    rows = (FuelStation.objects
            .filter(lat__range=(min(lats) - cell_lat, max(lats) + cell_lat),
                    lng__range=(min(lngs) - cell_lng, max(lngs) + cell_lng))
            .values_list("opis_id", "name", "address", "city", "state", "lat", "lng", "price"))

    found = []
    for opis_id, name, address, city, state, lat, lng, price in rows.iterator():
        cy, cx = int(lat // cell_lat), int(lng // cell_lng)
        kx = MILES_PER_DEG_LAT * math.cos(math.radians(lat))
        best, best_i = None, -1
        for gy in (cy - 1, cy, cy + 1):
            for gx in (cx - 1, cx, cx + 1):
                for i in grid.get((gy, gx), ()):
                    dy = (lats[i] - lat) * MILES_PER_DEG_LAT
                    dx = (lngs[i] - lng) * kx
                    d = dx * dx + dy * dy
                    if best is None or d < best:
                        best, best_i = d, i
        if best is None:
            continue
        dist = math.sqrt(best)
        if dist <= corridor_miles:
            found.append(CorridorStation(
                opis_id, name, address, city, state, lat, lng, price,
                round(geo.cum[best_i], 2), round(dist, 2)))

    found.sort(key=lambda s: s.mile_marker)
    return found