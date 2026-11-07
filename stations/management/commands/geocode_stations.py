import csv
from collections import Counter
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from stations.geo import load_gazetteer, match_city, norm_key
from stations.models import FuelStation


def load_overrides(path="data/city_overrides.csv"):
    """{(state, norm_key(city)): (lat, lng, 'override')} from the hand-made CSV."""
    if not Path(path).exists():
        return {}
    # utf-8-sig tolerates the BOM that Windows PowerShell may add
    with open(path, newline="", encoding="utf-8-sig") as f:
        return {
            (r["state"].strip().upper(), norm_key(r["city"])):
                (float(r["lat"]), float(r["lng"]), "override")
            for r in csv.DictReader(f)
        }


class Command(BaseCommand):
    help = "Fill lat/lng from the committed city gazetteer plus overrides (offline, D3)."

    def add_arguments(self, parser):
        parser.add_argument("--path", default="data/us_places.csv")
        parser.add_argument("--force", action="store_true", help="re-geocode all stations")

    def handle(self, *args, **opts):
        if not Path(opts["path"]).exists():
            raise CommandError("Run `build_gazetteer` first.")
        idx = load_gazetteer(opts["path"])
        overrides = load_overrides()

        qs = (FuelStation.objects.all() if opts["force"]
            else FuelStation.objects.filter(lat__isnull=True))
        stations = list(qs)
        cache, methods, unmatched = {}, Counter(), Counter()

        for s in stations:
            ck = (s.state, s.city)
            if ck not in cache:
                cache[ck] = (overrides.get((s.state, norm_key(s.city)))
                            or match_city(idx.get(s.state, {}), s.city))
            hit = cache[ck]
            if hit:
                s.lat, s.lng, s.geocode_method = hit
                methods[hit[2]] += 1
            else:
                unmatched[ck] += 1

        FuelStation.objects.bulk_update(stations, ["lat", "lng", "geocode_method"], batch_size=1000)

        total = FuelStation.objects.count()
        ok = FuelStation.objects.filter(lat__isnull=False).count()
        pct = 100 * ok / total
        out = self.stdout.write
        out("=== geocode report ===")
        out(f"processed this run: {len(stations)}")
        for m, c in methods.most_common():
            out(f"  {m:<11}{c}")
        out(f"matched overall:    {ok}/{total} = {pct:.2f}%  (target >= 95%)")
        out(f"unmatched stations: {sum(unmatched.values())}")
        for (st, city), c in unmatched.most_common(30):
            out(f"   {st} | {city} ({c})")
        if pct < 95:
            self.stdout.write(self.style.WARNING("Below 95%: paste the unmatched list to me."))