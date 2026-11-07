import csv
import io
import zipfile
from pathlib import Path

import requests
from django.core.management.base import BaseCommand, CommandError

from stations.geo import strip_suffix

BASE = "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2023_Gazetteer/"
FILES = [("place", "2023_Gaz_place_national.zip"), ("cousub", "2023_Gaz_cousubs_national.zip")]


class Command(BaseCommand):
    help = "Build data/us_places.csv (state,name,source,lat,lng) from Census gazetteer files."

    def handle(self, *args, **opts):
        raw = Path("data/raw")
        raw.mkdir(parents=True, exist_ok=True)
        out_rows = []
        for source, fname in FILES:
            zpath = raw / fname
            if not zpath.exists():
                self.stdout.write(f"downloading {fname} ...")
                resp = requests.get(BASE + fname, timeout=60)
                if resp.status_code != 200:
                    raise CommandError(
                        f"Download failed ({resp.status_code}). Download {BASE + fname} "
                        f"manually into {raw}/ and re-run.")
                zpath.write_bytes(resp.content)
            with zipfile.ZipFile(zpath) as z:
                txt = next(n for n in z.namelist() if n.endswith(".txt"))
                text = io.TextIOWrapper(z.open(txt), encoding="utf-8", errors="replace")
                reader = csv.DictReader(text, delimiter="\t")
                reader.fieldnames = [h.strip() for h in reader.fieldnames]
                n = 0
                for r in reader:
                    out_rows.append((r["USPS"].strip(), strip_suffix(r["NAME"].strip()), source, r["INTPTLAT"].strip(), r["INTPTLONG"].strip()))
                    n += 1
            self.stdout.write(f"{source}: {n} rows")

        with open("data/us_places.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["state", "name", "source", "lat", "lng"])
            w.writerows(out_rows)
        self.stdout.write(self.style.SUCCESS(f"wrote data/us_places.csv ({len(out_rows)} rows)"))