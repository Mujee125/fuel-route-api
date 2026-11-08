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
## Tests (TODO: update after US-14)
```bash
docker compose exec web python manage.py test
```

## Postman (TODO: US-16)
Collection in `postman/`.