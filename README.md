# Fuel Route API

Django REST API that takes a start and finish location in the USA and returns the driving route,
the cheapest fuel stops along it, and the total fuel cost.

- **Vehicle:** 500-mile maximum range, 10 miles per gallon
- **Prices:** only the supplied CSV (`data/fuel-prices.csv`)
- **External calls:** at most 3 per request (geocoding included), reported in `meta.external_api_calls`

## Stack
Django X.Y.Z (pinned in `requirements.txt`), Django REST Framework, PostgreSQL 16, Redis 7,
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

## API

`POST /api/route-plan/` with a JSON body (`GET /api/route-plan/?start=...&finish=...` also works).
Both locations must be in the lower 48 states. A location can be `"City, ST"`, `"lat,lng"` or free text
(free text costs one geocoder call).

```json
{ "start": "Los Angeles, CA", "finish": "New York, NY" }
```

Optional boolean `include_initial_tank_cost` (default `false`): also charge for the fuel in the starting tank (D1).

Trimmed 200 response (route geometry and most of the stops omitted):

```json
{
  "start":  { "query": "Los Angeles, CA", "label": "Los Angeles, CA", "lat": 34.0522, "lng": -118.2437 },
  "finish": { "query": "New York, NY", "label": "New York, NY", "lat": 40.7128, "lng": -74.006 },
  "route":  { "distance_miles": 2794.0, "duration_minutes": "...", "geometry": { "type": "LineString", "coordinates": ["..."] } },
  "fuel_stops": [
    { "order": 1, "name": "...", "city": "Williams", "state": "AZ", "mile_marker": 444.0,
      "price_per_gallon": 3.219, "gallons_purchased": "...", "cost": "..." }
  ],
  "summary": { "vehicle_type": "truck", "stops_count": 13, "total_gallons_needed": 279.4,
               "total_fuel_cost": 676.5, "currency": "USD", "initial_tank_assumed_full": true },
  "map":  { "geojson": { "type": "FeatureCollection", "features": ["..."] },
            "html_url": "/api/route-plan/map/?start=...&finish=..." },
  "meta": { "external_api_calls": 1, "cache_hit": false, "routing_provider": "ors",
            "routing_profile": "driving-hgv", "corridor_miles": 5, "processing_ms": 5840 }
}
```

Open `html_url` in a browser for the Leaflet map (route, start, finish and numbered stops with price, gallons and cost popups).

### Errors

Every error uses the same shape, and `meta.external_api_calls` is reported even on errors:

```json
{ "error": { "code": "invalid_location", "message": "'Toronto, ON' is not in the USA" },
  "meta": { "external_api_calls": 0, "cache_hit": false, "processing_ms": 343 } }
```

| Status | When | Example `code` |
|---|---|---|
| 400 | Missing or malformed input, or a location outside the USA (no routing call is made) | `invalid_location` |
| 404 | Location cannot be resolved | `location_not_found` |
| 422 | No drivable route, or no feasible fuel plan (a station gap over 500 miles even after widening to 25 miles) | `no_feasible_fuel_plan` |
| 502 / 504 | Routing provider error or timeout. The message never contains the API key | |

Start equal to finish returns 200 with distance 0, no stops, cost 0 and no external calls.

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
6. **Cache:** two-level Redis cache with a 24 h TTL and a locmem fallback; see [Cache](#cache).

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

**Example: Los Angeles, CA -> New York, NY (truck / `driving-hgv`)**: 2,794 mi, 279.4 gal needed, 13 fuel stops, **$676.50**, 1 external call. No California stops (sparse CA data plus higher CA prices make the optimiser buy in AZ/NM/TX/OK/MO/IL/OH). Longest leg between fuel points: 472 mi.

| # | Stop | Mile | $/gal |
|---|---|---|---|
| 1 | Williams, AZ | 444.0 | 3.219 |
| 2 | Holbrook, AZ | 566.5 | 3.139 |
| 3 | Gallup, NM | 661.2 | 3.119 |
| 4 | Moriarty, NM | 835.9 | 3.116 |
| 5 | Santa Rosa, NM | 914.4 | 2.879 |
| 6 | Amarillo, TX | 1082.9 | 2.846 |
| 7 | Oklahoma City, OK | 1335.8 | 2.879 |
| 8 | Joplin, MO | 1553.4 | 2.899 |
| 9 | Springfield, MO | 1624.1 | 2.899 |
| 10 | Saint Louis, MO | 1833.0 | 2.899 |
| 11 | Effingham, IL | 1935.3 | 2.999 |
| 12 | London, OH | 2227.9 | 2.999 |
| 13 | New Concord, OH | 2321.8 | 3.059 |

## Performance

Measured inside the container (Python 3.12, 3 gunicorn sync workers), cache bypassed, three runs per route:

| Route | Miles | Stops | Local work (everything except the ORS call) |
|---|---|---|---|
| Dallas, TX → Houston, TX | 239 | 0 | 31-46 ms |
| Atlanta, GA → Chicago, IL | 721 | 2 | 108-118 ms |
| Los Angeles, CA → New York, NY | 2794 | 13 | 181-223 ms |

Local work stays under the 300 ms target. For the coast-to-coast route the phases are: corridor query 62-131 ms,
geometry 101-135 ms, optimiser ~1 ms, cost ~0 ms, response and GeoJSON assembly 12-75 ms. The first request on a
fresh worker used to spend ~1.9 s lazily loading the gazetteer; it is now loaded at worker boot (`config/wsgi.py`).

**Routing provider time dominates a cache miss.** It is network-bound, not compute-bound: ORS compute (time to first
byte) was 0.7-3 s, while on my connection (the same connection pulled PyPI at ~15 kB/s) TLS setup took 2-9 s and the
download of a 446 KB response took 10-60 s. HGV and car profiles perform the same. Mitigations in the code:

- ORS is called on the encoded-polyline endpoint instead of `/geojson`: 73 KB instead of 446 KB for LA→NY
  (decoding costs about 38 ms). The route result is unchanged (2794 mi, 13 stops, $676.50).
- A total deadline per provider (`ROUTING_DEADLINE`, default 45 s) is enforced while streaming, because the
  `requests` timeout is per read and cannot stop a slow trickle. A miss then falls back to OSRM or returns 504.
- Gunicorn `timeout = 180` (`gunicorn.conf.py`) so a slow provider cannot get a worker killed.
- Results are cached for 24 h (see Cache), so only the first request for a route pays the provider cost.

A cache miss therefore takes roughly 0.2-0.4 s of local work plus whatever the provider needs (about 6 s for LA→NY on
the last measurement; much slower on a poor connection). A cache hit is served in a few ms server-side.

## Cache

Plans are cached in Redis (db 1) for 24 hours (`ROUTE_CACHE_TTL`, default 86400).

| Key | Purpose |
|---|---|
| `routeplan:v1:q:<sha1>` | Alias: normalised start/finish text + flag -> resolved coordinates. A repeated free-text request skips geocoding. |
| `routeplan:v1:p:<lat,lng\|lat,lng\|flag>` | Full plan, keyed by coordinates rounded to 3 decimals. Different spellings of the same places share one entry. |

- A cache hit makes **0 external calls** (`meta.cache_hit: true`). `include_initial_tank_cost` has its own entry.
- Errors (no route, provider failures) are never cached.
- An entry is about 90 KB (geometry is thinned).
- Measured locally: a miss costs the local work (0.2-0.4 s) plus one provider call, which is network-bound (see Performance) for LA->NY (one ORS truck-routing call, which dominates), and a hit takes about 3 ms server-side. The first request on a freshly booted worker can take 100-300 ms.
- **Redis failure:** requests still succeed. After the first failed Redis call, a worker skips the cache for 30 s (`CACHE_BREAKER_SECONDS`). When Redis is stopped, the first call per worker costs about 4 s because the `redis` hostname no longer resolves.
- Without `REDIS_URL` the app falls back to an in-process locmem cache. That cache is per process, so each gunicorn worker has its own.
- Clear the cache: `docker compose exec redis redis-cli -n 1 flushdb`.
- Redis has no `maxmemory` limit by default. In production set `maxmemory` with `maxmemory-policy allkeys-lru`.
## Tests

76 tests (Django test runner). They run against PostgreSQL and use local-memory caches, so no Redis or
network is needed. All routing and geocoding HTTP is mocked.

```
docker compose exec web python manage.py test
```

GitHub Actions (`.github/workflows/ci.yml`) runs the same suite against a Postgres 16 service, plus
`manage.py check` and `makemigrations --check`.

| Scenario (backlog Sec. 9) | Test |
|---|---|
| Short trip, 0 stops, gallons = miles / 10 | `test_short_trip_no_stops`, `test_short_trip_post_matches_contract` |
| ~700 miles, 1 stop | `test_700_miles_one_stop` |
| Cheaper station beats nearer one | `test_prefers_cheaper_station_not_nearest`, `test_buys_only_what_is_needed_to_reach_cheaper` |
| Coast to coast, many stops, no leg over 500 miles | `test_long_trip_many_stops_and_range_cap` |
| Exactly 500 miles / station at exactly 500 | `test_exactly_500_miles_needs_no_stop`, `test_station_exactly_at_range_limit_is_reachable` |
| Empty corridor | `test_empty_corridor_is_fine_for_a_short_trip`, `test_empty_corridor_is_422_for_a_long_trip` |
| Price ties | `test_equal_prices_are_deterministic_and_cost_is_hand_computed` |
| Gap over range, corridor widening, 422 | `test_gap_over_range_is_infeasible`, `test_widening_*`, `test_no_feasible_fuel_plan_is_422_with_call_count` |
| Cost at 10 mpg, initial-tank flag | `test_costing.py` (6 tests) |
| Location parser | `test_locations.py` (15 tests) |
| Start equals finish | `test_start_equals_finish_makes_no_routing_call` |
| Non-USA 400 / unknown 404, no routing call wasted | `test_non_usa_*`, `test_unresolvable_location_is_404_and_never_routes` |
| Repeat request is a cache hit, Redis down | `test_cache.py`, `test_cache_breaker.py` |
| Provider down 502/504, no key leak | `test_provider_error_is_502_with_call_count`, `test_both_timeout_gives_504_with_call_count`, `test_error_messages_never_contain_the_key` |
| Polyline decoder, total routing deadline | `test_polyline.py`, `test_total_deadline_stops_a_slow_trickle` |


## Postman (TODO: US-16)
Collection in `postman/`.