import math

EARTH_RADIUS_MILES = 3958.8
MILES_PER_DEG_LAT = EARTH_RADIUS_MILES * math.pi / 180      # ~69.09
MAX_STEP_MILES = 0.25


def haversine_miles(lat1, lng1, lat2, lng2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_MILES * math.asin(math.sqrt(h))


class RouteGeometry:
    """Route polyline, densified, with cumulative miles from the start.

    coordinates: [[lng, lat], ...] (GeoJSON order).
    distance_miles: provider's total; if given, markers are scaled to match it.
    """

    def __init__(self, coordinates, distance_miles=None, max_step=MAX_STEP_MILES):
        if len(coordinates) < 2:
            raise ValueError("Route needs at least 2 points")
        lats, lngs, cum = [coordinates[0][1]], [coordinates[0][0]], [0.0]
        for a, b in zip(coordinates, coordinates[1:]):
            lng1, lat1, lng2, lat2 = a[0], a[1], b[0], b[1]
            seg = haversine_miles(lat1, lng1, lat2, lng2)
            n = max(1, math.ceil(seg / max_step))
            for k in range(1, n + 1):
                f = k / n
                lats.append(lat1 + (lat2 - lat1) * f)
                lngs.append(lng1 + (lng2 - lng1) * f)
                cum.append(cum[-1] + seg / n)
        raw = cum[-1]
        scale = distance_miles / raw if distance_miles and raw > 0 else 1.0
        self.lats, self.lngs = lats, lngs
        self.cum = [c * scale for c in cum]
        self.total_miles = self.cum[-1]