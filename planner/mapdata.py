"""GeoJSON + geometry thinning for the response/map (D10)."""


def thin_coordinates(coords, min_step_deg=0.01, ndigits=5):
    """Drop points closer than ~0.3 mi to the last kept point (display only)."""
    pts = [(c[0], c[1]) for c in coords]
    if len(pts) <= 2:
        return [[round(x, ndigits), round(y, ndigits)] for x, y in pts]
    kept = [pts[0]]
    lx, ly = pts[0]
    for x, y in pts[1:-1]:
        if abs(x - lx) + abs(y - ly) >= min_step_deg:
            kept.append((x, y))
            lx, ly = x, y
    kept.append(pts[-1])
    return [[round(x, ndigits), round(y, ndigits)] for x, y in kept]


def _point(lng, lat, props):
    return {"type": "Feature", "properties": props,
            "geometry": {"type": "Point", "coordinates": [lng, lat]}}


def build_geojson(start, finish, route_coords, fuel_stops, distance_miles):
    """start/finish: dicts with label/lat/lng. fuel_stops: response-shaped dicts."""
    features = [{
        "type": "Feature",
        "properties": {"kind": "route", "distance_miles": distance_miles},
        "geometry": {"type": "LineString", "coordinates": route_coords},
    }]
    features.append(_point(start["lng"], start["lat"], {"kind": "start", "label": start["label"]}))
    features.append(_point(finish["lng"], finish["lat"], {"kind": "finish", "label": finish["label"]}))
    for s in fuel_stops:
        features.append(_point(s["lng"], s["lat"], {
            "kind": "stop", "order": s["order"], "name": s["name"], "city": s["city"],
            "state": s["state"], "mile_marker": s["mile_marker"],
            "price_per_gallon": s["price_per_gallon"],
            "gallons_purchased": s["gallons_purchased"], "cost": s["cost"],
        }))
    return {"type": "FeatureCollection", "features": features}