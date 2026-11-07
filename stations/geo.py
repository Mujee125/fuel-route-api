import csv
import difflib
import re
import unicodedata
from collections import defaultdict

TOKEN_MAP = {"saint": "st", "sainte": "ste", "mount": "mt", "fort": "ft"}

SUFFIX_RE = re.compile(
    r"\s+(city and borough|consolidated government|metropolitan government|"
    r"unified government|urban county|charter township|city|town|village|borough|"
    r"municipality|cdp|ccd|township|plantation|comunidad|zona urbana)(\s*\(balance\))?$",
    re.I,
)


def strip_suffix(name: str) -> str:
    """'Abbeville city' -> 'Abbeville' (Census adds a type suffix)."""
    return SUFFIX_RE.sub("", name).strip()


def norm_key(name: str) -> str:
    """Compact key: 'Mc Calla' == 'McCalla', 'Saint Johns' == 'St. Johns'."""
    s = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[.'`]", "", s)
    s = re.sub(r"[^a-z0-9]+", " ", s).strip()
    return "".join(TOKEN_MAP.get(t, t) for t in s.split())


def load_gazetteer(path):
    """{state: {norm_key: (lat, lng, name)}}. First row wins, so 'place' rows beat 'cousub'."""
    idx = defaultdict(dict)
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            idx[r["state"]].setdefault(
                norm_key(r["name"]), (float(r["lat"]), float(r["lng"]), r["name"]))
    return idx


def match_city(places: dict, city: str):
    """Return (lat, lng, method) or None. Methods: exact, normalized, prefix, fuzzy."""
    key = norm_key(city)
    if not key:
        return None
    hit = places.get(key)
    if hit:
        method = "exact" if city.strip().lower() == hit[2].lower() else "normalized"
        return hit[0], hit[1], method
    if len(key) >= 5:   # 'Hot Springs National Park' -> 'Hot Springs'
        cands = [k for k in places
                if len(k) >= 5 and (key.startswith(k) or k.startswith(key))]
        if cands:
            k = min(cands, key=lambda c: abs(len(c) - len(key)))
            return places[k][0], places[k][1], "prefix"
    close = difflib.get_close_matches(key, places.keys(), n=1, cutoff=0.88)
    if close:
        return places[close[0]][0], places[close[0]][1], "fuzzy"
    return None