"""Pure, dependency-free I/O helpers for Pareto: canonical serialization,
checksums, input-format detection, and instance normalization. Deliberately
imports nothing from gurobipy and has no import-time side effects, so it is
fast to unit-test and safe to vendor into a consumer (e.g. a web platform
that wants to recompute `input_checksum` without a solver)."""
import hashlib
import json


def canonical_json(obj):
    """Deterministic bytes for `obj`: sorted keys, no whitespace, UTF-8.
    The single canonicalization rule used for every checksum."""
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def checksum(obj):
    """`'sha256:<hex>'` over `canonical_json(obj)`."""
    return "sha256:" + hashlib.sha256(canonical_json(obj)).hexdigest()


def detect_input_format(raw):
    """Peek the first meaningful character of `raw` (blank lines and `#`
    comments skipped). Leading `{` ⇒ 'json', otherwise 'text'."""
    for line in raw.splitlines():
        line = line.partition("#")[0].strip()
        if not line:
            continue
        return "json" if line[0] == "{" else "text"
    return "text"


def normalize_instance(doc):
    """Canonical, format-independent view of a JSON instance (the dict
    `main.py`'s `parse_json_input` reads) for hashing. Equal instances hash
    equally regardless of key order, list order (where order is semantically
    irrelevant), or whether they came from text or JSON.

    Mirrors the solver's parse rules exactly: owners come from `items` and
    from the give side of every wish; for repeated keys the later entry wins
    (`items` ask, `bids`, `budgets`, `locations`, `cities`); `n`/`m` default
    to the give/take list lengths; caps sort their item lists; optional
    sections (`locations`, `cities`, `hub`, `box_min`) appear in the output
    only when present, so instances that never use them keep their checksum.
    Runs before the solver's own-item sanitization, so the checksum reflects
    the instance as declared."""
    owner, ask, budget, bids, location, city = {}, {}, {}, {}, {}, {}
    for u in doc.get("budgets", []):
        budget[str(u["user"])] = int(u["budget"])
    for it in doc.get("items", []):
        name = str(it["name"])
        owner[name] = str(it["owner"])
        if "ask" in it:
            ask[name] = int(it["ask"])
    for b in doc.get("bids", []):
        bids[(str(b["user"]), str(b["item"]))] = int(b["max_price"])
    for lo in doc.get("locations", []):
        location[str(lo["user"])] = (float(lo["lat"]), float(lo["lng"]))
    for c in doc.get("cities", []):
        city[str(c["user"])] = str(c["city"])
    takecaps = [(str(c["user"]), int(c["n"]), [str(t) for t in c["items"]])
                for c in doc.get("takecaps", [])]
    givecaps = [(str(c["user"]), int(c["n"]), [str(t) for t in c["items"]])
                for c in doc.get("givecaps", [])]
    wishes = []
    for w in doc.get("wishes", []):
        u = str(w["user"])
        give = [str(t) for t in w["give"]]
        take = [str(t) for t in w["take"]]
        n = int(w.get("n", len(give)))
        m = int(w.get("m", len(take)))
        for g in give:
            owner[g] = u  # giving an item implies owning it
        wishes.append((u, give, take, n, m))

    out = {
        "wishes": sorted(
            ({"user": u, "give": sorted(give), "take": sorted(take), "n": n, "m": m}
             for (u, give, take, n, m) in wishes),
            key=lambda w: (w["user"], w["give"], w["take"], w["n"], w["m"])),
        "items": sorted(
            ({"name": name, "owner": o, **({"ask": ask[name]} if name in ask else {})}
             for name, o in owner.items()),
            key=lambda it: it["name"]),
        "bids": sorted(
            ({"user": u, "item": item, "max_price": y} for (u, item), y in bids.items()),
            key=lambda b: (b["user"], b["item"])),
        "budgets": sorted(
            ({"user": u, "budget": b} for u, b in budget.items()),
            key=lambda x: x["user"]),
        "locations": sorted(
            ({"user": u, "lat": lat, "lng": lng} for u, (lat, lng) in location.items()),
            key=lambda x: x["user"]),
        "takecaps": sorted(
            ({"user": u, "n": n, "items": sorted(items)} for (u, n, items) in takecaps),
            key=lambda x: (x["user"], x["n"], x["items"])),
        "givecaps": sorted(
            ({"user": u, "n": n, "items": sorted(items)} for (u, n, items) in givecaps),
            key=lambda x: (x["user"], x["n"], x["items"])),
    }
    if city:
        out["cities"] = sorted(
            ({"user": u, "city": c} for u, c in city.items()), key=lambda x: x["user"])
    if doc.get("hub") is not None:
        out["hub"] = str(doc["hub"])
    if doc.get("box_min") is not None:
        out["box_min"] = int(doc["box_min"])
    return out
