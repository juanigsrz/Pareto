#!/usr/bin/env python3
"""Convert a FastTradeMaximizer wants file + a user/location CSV into a Pareto
instance with the city/hub directives the 'hubload' KPI needs.

The FTM dummy items (%NNNN) are not copied over: Pareto has no dummy concept and
drops take-legs on items the taker owns, which would silently delete every want
group. They are translated instead into the pattern Pareto models natively --
one 1for1 wish per (offered item, want group) plus a `dupcap` over the group:

    (U) A : %G ...        ->   U : (1for1) A -> X1 X2 ...
    (U) %G : X1 X2 ...         U : (1for1) B -> X1 X2 ...
    (U) B : %G ...             dupcap U X1 X2 ...

which is exactly the shape main.py's hub-and-spoke compaction collapses, and
carries the same meaning: at most one copy out of the group reaches U, and it
costs U exactly one of the items that asked for it.

Usage: ftm_to_pareto.py WANTS.txt USERS.csv OUT.txt [OUT_ITEMS.csv] [--cities A,B]

--cities keeps only participants from those (canonical) cities, for a cut-down
instance that fits a size-limited Gurobi licence.
"""
import collections
import csv
import re
import sys
import unicodedata

HUB = "AMBA"
BOX_MIN = 5

# Places that shipped as one destination in the 2025 event. Key -> canonical city.
CITY_GROUPS = {
    "La Plata":     HUB,                                  # travelled with the capital
    "Rosario":      "Rosario/Parana/Esperanza/Rafaela",
    "Parana":       "Rosario/Parana/Esperanza/Rafaela",
    "Esperanza":    "Rosario/Parana/Esperanza/Rafaela",
    "Rafaela":      "Rosario/Parana/Esperanza/Rafaela",
    "Resistencia":  "Resistencia/Corrientes",
    "Corrientes":   "Resistencia/Corrientes",
}

WISH_RE = re.compile(r'^\(([^)]+)\)\s+(\S+)\s*:\s*(.*)$')
NAME_RE = re.compile(r'^(\S+)\s+==>\s+(.*?)\s*\(from\s+([^)]+)\)\s*$')


def fold(s):
    """Strip accents and upper-case -- the shape usernames take inside the wants file."""
    s = unicodedata.normalize("NFKD", s)
    return "".join(c for c in s if not unicodedata.combining(c)).upper()


def ftm_token(csv_user):
    """usuarios.csv 'user' column -> the token FTM writes in the wants file."""
    left, sep, right = fold(csv_user).partition("---")
    keep = lambda s: re.sub(r"[^A-Z0-9_ -]", "", s)
    return keep(left) + sep + keep(right)


def read_cities(path):
    """-> {ftm token: (pareto user id, raw city, canonical city)}"""
    out = {}
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(f, delimiter=";"):
            tok = ftm_token(row["user"])
            raw = row["location__name"].strip()
            out[tok] = (tok.split("---")[0], raw, CITY_GROUPS.get(fold_city(raw), raw))
    return out


def fold_city(name):
    """Accent-insensitive key for CITY_GROUPS ('Paraná' -> 'Parana')."""
    n = unicodedata.normalize("NFKD", name)
    return "".join(c for c in n if not unicodedata.combining(c))


def read_wants(path):
    """-> (offers, owner_of, titles). offers is [(ftm user, item, [want tokens])]."""
    offers, owner_of, titles = [], {}, {}
    official = False
    for raw in open(path, encoding="utf-8", errors="replace"):
        line = raw.rstrip("\n")
        if line.startswith("!BEGIN-OFFICIAL-NAMES"):
            official = True
            continue
        if line.startswith("!END-OFFICIAL-NAMES"):
            official = False
            continue
        if official:
            m = NAME_RE.match(line)
            if m:
                titles[m.group(1)] = (m.group(2), m.group(3))
            continue
        m = WISH_RE.match(line)
        if m:
            user, item, wants = m.group(1), m.group(2), m.group(3).split()
            owner_of[item] = user
            offers.append((user, item, wants))
    return offers, owner_of, titles


def main(wants_path, users_path, out_path, items_path=None, only=None):
    cities = read_cities(users_path)
    offers, owner_of, titles = read_wants(wants_path)

    if only:
        keep = set(only.split("=", 1)[1].split(","))
        users = {u for u in {o[0] for o in offers}
                 if u in cities and cities[u][2] in keep}
        offers = [o for o in offers if o[0] in users]
        owner_of = {i: u for u, i, _ in offers}

    groups = {i: w for _, i, w in offers if i.startswith("%")}   # dummy -> members
    tradeable = {i for i in owner_of if not i.startswith("%")}   # items with a want line

    missing_city = sorted({u for u, _, _ in offers if u not in cities})
    stats = collections.Counter()

    def clean(items, owner, live, count=False):
        """Keep live items only, drop the owner's own copies, preserve order."""
        seen, out = set(), []
        for it in items:
            if it in seen:
                if count:
                    stats["dup_want_entry"] += 1
                continue
            seen.add(it)
            if it not in live:
                if count:
                    stats["dropped_not_offered" if it not in tradeable
                          else "dropped_unreachable"] += 1
            elif owner_of[it] == owner:
                if count:
                    stats["dropped_own_copy"] += 1
            else:
                out.append(it)
        return out

    # An item nobody can give is an item nobody can receive: Pareto declares an
    # owner only for items that appear on a give side, so leaving a dead copy in
    # someone's want list buys a "cap references item with no declared owner"
    # warning and a variable that is pinned to 0 anyway. An item goes dead when
    # every game it asked for is itself unavailable, which can cascade -- so shrink
    # the live set until it stops moving.
    live = set(tradeable)
    while True:
        dead = set()
        for user, item, wants in offers:
            if item.startswith("%") or item not in live:
                continue
            if not any(clean(groups[w] if w.startswith("%") else [w], user, live)
                       for w in wants):
                dead.add(item)
        if not dead:
            break
        stats["dropped_unreachable_items"] += len(dead)
        live -= dead

    wishes = []                       # (pareto user, item, [take])
    dupcaps = {}                      # (pareto user, tuple(take)) -> None, ordered
    for user, item, wants in offers:
        if item.startswith("%") or item not in live:
            continue
        pu = cities[user][0] if user in cities else user.split("---")[0]
        loose = []
        for w in wants:
            if not w.startswith("%"):
                loose.append(w)
                continue
            members = clean(groups[w], user, live, count=True)
            if not members:
                stats["empty_group"] += 1
                continue
            wishes.append((pu, item, members))
            dupcaps.setdefault((pu, tuple(members)), None)
            stats["group_wishes"] += 1
        loose = clean(loose, user, live, count=True)
        if loose:
            wishes.append((pu, item, loose))
            stats["loose_wishes"] += 1

    user_of_item = {i: (cities[u][0] if u in cities else u.split("---")[0])
                    for u, i, _ in offers if not i.startswith("%")}
    listed = {i for _, i, _ in wishes}

    with open(out_path, "w", encoding="utf-8") as f:
        w = f.write
        w("# Argentina math trade 2025 -- replayed as a Pareto instance.\n"
          "#\n"
          f"# Source : {wants_path.split('/')[-1]} (FastTradeMaximizer) + "
          f"{users_path.split('/')[-1]}\n"
          "# Built  : ftm_to_pareto.py -- see that file for the dummy -> dupcap mapping.\n"
          "#\n"
          "# Cities follow how the 2025 event actually shipped:\n"
          f"#   * La Plata travelled with the capital, so it is folded into the hub ({HUB}).\n"
          "#   * Rosario, Parana, Esperanza and Rafaela shipped together -> one city.\n"
          "#   * Resistencia and Corrientes shipped together -> one city.\n"
          f"#\n# {len(listed)} tradeable items, {len(set(user_of_item.values()))} users, "
          f"{len(dupcaps)} want groups.\n\n")
        w(f"hub {HUB}\n")
        w(f"boxmin {BOX_MIN}\n\n")

        w("# --- cities ---------------------------------------------------------------\n")
        for tok in sorted({u for u, _, _ in offers}):
            if tok in cities:
                pu, raw, canon = cities[tok]
                note = "" if raw == canon else f"   # {raw}"
                w(f"city {pu} {canon}{note}\n")
            else:
                w(f"# NO CITY ON FILE: {tok}\n")

        w("\n# --- want groups: at most one copy of each wanted game ---------------------\n")
        for (pu, members) in dupcaps:
            w(f"dupcap {pu} {' '.join(members)}\n")

        w("\n# --- wishes ---------------------------------------------------------------\n")
        for pu, item, take in wishes:
            w(f"{pu} : (1for1) {item} -> {' '.join(take)}\n")

    if items_path:
        with open(items_path, "w", encoding="utf-8", newline="") as f:
            out = csv.writer(f)
            out.writerow(["item", "title", "owner_ftm", "owner", "city", "tradeable"])
            for iid, (title, owner) in sorted(titles.items()):
                pu = cities[owner][0] if owner in cities else owner.split("---")[0]
                out.writerow([iid, title, owner, pu,
                              cities[owner][2] if owner in cities else "",
                              int(iid in listed)])

    edges = sum(len(t) for _, _, t in wishes)
    print(f"users            : {len(set(user_of_item.values()))}"
          f"  (no city on file: {len(missing_city)})")
    print(f"items offered    : {len(tradeable)}  (reachable in a wish: {len(listed)})")
    print(f"want groups      : {len(dupcaps)}  (from {len(groups)} FTM dummies)")
    print(f"wishes           : {len(wishes)}  ({stats['group_wishes']} group, "
          f"{stats['loose_wishes']} loose)")
    print(f"take-legs (edges): {edges}")
    for k in sorted(stats):
        if k not in ("group_wishes", "loose_wishes"):
            print(f"  {k:24s}: {stats[k]}")
    if missing_city:
        print("no city:", missing_city)


if __name__ == "__main__":
    main(*sys.argv[1:])
