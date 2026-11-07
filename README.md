# Fuel Route API

Django REST API that takes a start and finish location in the USA and returns the driving route,
the cheapest fuel stops along it, and the total fuel cost.

- **Vehicle:** 500-mile maximum range, 10 miles per gallon
- **Prices:** only the supplied CSV (`data/fuel-prices.csv`)
- **External calls:** at most 3 per request (geocoding included), reported in `meta.external_api_calls`

## Stack
Django <VERSION> (pinned in `requirements.txt`), Django REST Framework, PostgreSQL 16, Redis 7,
gunicorn, Docker Compose. Routing: OpenRouteService (primary), public OSRM (fallback).

## Quick start
```bash
cp .env.example .env              # add your free ORS_API_KEY (openrouteservice.org)
docker compose up --build
docker compose exec web python manage.py load_fuel_stations
docker compose exec web python manage.py geocode_stations
curl localhost:8000/api/health/
```
`data/us_places.csv` (offline gazetteer) is committed, so geocoding needs no network.
To rebuild it: `python manage.py build_gazetteer`.

## API (TODO: fill in after US-10)
`POST /api/route-plan/` with `{"start": "Los Angeles, CA", "finish": "New York, NY"}`
Also `GET /api/route-plan/?start=...&finish=...` and `GET /api/route-plan/map/?start=...&finish=...`.
Accepted location formats: `"City, ST"` and `"lat,lng"` (resolved locally, 0 external calls).
Free-text addresses use one Nominatim call.

Status codes: 400 bad input or outside the lower 48, 404 unresolvable location,
422 no route or no feasible fuel plan, 502/504 routing provider error or timeout.

## How it works
1. **Data load:** drop Canadian rows, dedupe by OPIS ID (keep the lowest price), flag prices above $5.
2. **Offline geocoding:** the CSV has no coordinates, so each station is placed at its city centroid
   using the US Census gazetteer (exact, normalised, prefix and fuzzy matching within the state).
3. **Request:** resolve both locations locally, make one routing call, densify the route and compute
   mile markers, find stations in a corridor around the route, run the optimiser.
4. **Corridor:** 5 miles around the route, widening to 10 then 25 miles if no feasible plan exists,
   otherwise 422.
5. **Optimiser (greedy lookahead):**
   - Start with a full tank (the initial fuel is free).
   - At each station, if a cheaper station (or the destination) is within a full tank, buy only enough
     to reach it.
   - Otherwise fill up and drive to the cheapest station in range.
   - The 500-mile range is a hard cap. Stations where nothing is bought are not listed as stops.
6. **Cache (TODO):** Redis, key = rounded (3 decimal) start/finish coordinates, TTL 24h.

## Assumptions
- The tank starts full and the initial 50 gallons are not charged (`total_gallons_needed` = miles / 10).
- Partial fills: only the fuel needed is bought, never more than the tank allows.
- Money is in USD rounded to cents, price per gallon to 3 decimals, distances in miles.

## Known limitations
- **Sparse California data:** the CSV has only about 8 California stations, all in the southeast
  desert. Routes along I-5 or US-101 (for example Seattle to Los Angeles) can have gaps over 500 miles
  and return 422. LA to NY works because the optimiser routes fuel purchases through Arizona.
- **Duplicate prices:** many OPIS IDs appear with several prices. The loader keeps the lowest, which
  can pick a low outlier.
- **Price outliers:** 7 stations priced above $5 are flagged (`price_flagged`) but kept. The optimiser
  avoids them naturally.
- **Geocoding is approximate:** stations sit at the city centroid, which can be a few miles from the
  real truck stop. Ambiguous names follow Census order unless listed in `data/city_overrides.csv`.
- **Unmatched stations:** about 2.2% of stations (147 of 6,626) are in unincorporated places missing
  from the Census file. They have no coordinates and are excluded from planning.
- **Lower 48 only:** Alaska, Hawaii and Canada return 400. The border check uses a coarse polygon,
  accurate to a few miles near borders.
- **Corridor distance is straight-line:** `distance_from_route_miles` is measured to the nearest route
  point, and the detour is not added to the range budget (at most about 10 extra miles round trip
  at the 5-mile corridor).
- **Mile markers** are scaled so the final marker equals the routing provider's total distance.
- **Many small stops:** because the optimiser buys only what it needs and waits for cheaper stations,
  long trips have many small purchases (LA to NY: 13 stops, $680.42). This is cost-optimal, not a bug.
- **Free-text addresses** cost one Nominatim call, which is fine within the 3-call budget.

## Example result: Los Angeles to New York
Route about 2,790 miles, 1 external call. 13 fuel stops, 230.685 gallons purchased of 280.685 needed
(the first 50 gallons are the free initial tank), total $680.42, with no California stops.

## Tests (TODO: update after US-14)
```bash
docker compose exec web python manage.py test
```

## Postman (TODO: US-16)
Collection in `postman/`.