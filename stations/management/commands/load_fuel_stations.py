import csv
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from statistics import mean

from django.core.management.base import BaseCommand, CommandError

from stations.models import FuelStation

CANADA = {"AB", "BC", "MB", "NB", "NL", "NS", "NT", "NU", "ON", "PE", "QC", "SK", "YT"}
US_STATES = set(
    "AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO "
    "MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY".split()
)
OUTLIER_PRICE = Decimal("5.000")


def fix_mojibake(text: str) -> str:
    """'StuckeyÃ¢â‚¬â„¢s' -> "Stuckey's" (handles double-encoding)."""
    for _ in range(2):
        try:
            text = text.encode("cp1252").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            break
    return text


def clean(value) -> str:
    return " ".join(fix_mojibake(str(value or "")).split())


class Command(BaseCommand):
    help = "Load fuel stations from the CSV: US only, one row per OPIS ID (lowest price)."

    def add_arguments(self, parser):
        parser.add_argument("--path", default="data/fuel-prices.csv")
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **opts):
        path = Path(opts["path"])
        if not path.exists():
            raise CommandError(f"CSV not found: {path}")

        stats = dict(read=0, canada=0, invalid=0, exact_dups=0, merged=0)
        seen_rows = set()
        best = {}          # opis_id -> dict (lowest price wins, D5)
        price_spread = {}  # opis_id -> [min, max]

        with path.open(newline="", encoding="utf-8-sig", errors="replace") as f:
            reader = csv.DictReader(f)
            for raw in reader:
                row = {(k or "").strip(): v for k, v in raw.items()}
                stats["read"] += 1

                state = clean(row.get("State")).upper()
                if state in CANADA:
                    stats["canada"] += 1
                    continue
                if state not in US_STATES:
                    stats["invalid"] += 1
                    continue

                try:
                    opis_id = int(clean(row.get("OPIS Truckstop ID")))
                    price = Decimal(clean(row.get("Retail Price"))).quantize(
                        Decimal("0.001"), rounding=ROUND_HALF_UP)
                    if price <= 0:
                        raise InvalidOperation
                except (ValueError, InvalidOperation):
                    stats["invalid"] += 1
                    continue

                rack = clean(row.get("Rack ID"))
                address = clean(row.get("Address"))
                rec = dict(
                    opis_id=opis_id,
                    name=clean(row.get("Truckstop Name"))[:200],
                    address="" if address in {",", ""} else address[:300],
                    city=clean(row.get("City"))[:100],
                    state=state,
                    rack_id=int(rack) if rack.isdigit() else None,
                    price=price,
                    price_flagged=price > OUTLIER_PRICE,
                )

                key = (opis_id, rec["name"], rec["address"], rec["city"], state, rack, price)
                if key in seen_rows:
                    stats["exact_dups"] += 1
                    continue
                seen_rows.add(key)

                if opis_id in best:
                    lo, hi = price_spread[opis_id]
                    price_spread[opis_id] = [min(lo, price), max(hi, price)]
                    stats["merged"] += 1
                    if price < best[opis_id]["price"]:
                        best[opis_id] = rec
                else:
                    best[opis_id] = rec
                    price_spread[opis_id] = [price, price]

        records = list(best.values())

        if not opts["dry_run"]:
            FuelStation.objects.bulk_create(
                [FuelStation(**r) for r in records],
                batch_size=1000,
                update_conflicts=True,
                unique_fields=["opis_id"],
                # lat/lng/geocode_method are NOT overwritten, so reloads are safe
                update_fields=["name", "address", "city", "state",
                               "rack_id", "price", "price_flagged"],
            )

        self.report(stats, records, price_spread, opts["dry_run"])

    def report(self, s, records, spread, dry):
        prices = [r["price"] for r in records]
        wide = sorted(((hi - lo, i) for i, (lo, hi) in spread.items() if hi != lo), reverse=True)
        out = self.stdout.write
        out("=== load_fuel_stations report" + (" (DRY RUN)" if dry else "") + " ===")
        out(f"rows read:             {s['read']}")
        out(f"dropped (Canada):      {s['canada']}")
        out(f"dropped (invalid):     {s['invalid']}")
        out(f"exact duplicates:      {s['exact_dups']}")
        out(f"merged (diff price):   {s['merged']}  -> kept lowest price (D5)")
        out(f"stations loaded:       {len(records)}")
        out(f"price min/mean/max:    {min(prices)} / {mean(prices):.3f} / {max(prices)}")
        flagged = [r for r in records if r["price_flagged"]]
        out(f"flagged > $5:          {len(flagged)} (kept in table)")
        for r in sorted(flagged, key=lambda r: -r["price"])[:10]:
            out(f"   {r['opis_id']} {r['name']} {r['city']}, {r['state']} ${r['price']}")
        if wide:
            out("largest price spreads merged (id: spread):")
            for diff, i in wide[:5]:
                out(f"   {i}: ${diff}")