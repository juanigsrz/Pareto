#!/usr/bin/env python3
"""Check the generated Pareto instance against the FTM source it came from.

Two things have to hold for the replay to mean anything:
  1. every offered item may receive exactly the set of copies it could in FTM
     (dummies expanded), minus the copies that can provably never move; and
  2. every FTM want group survives as one dupcap row, so "at most one copy of
     this game" still binds.
"""
import collections
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ftm_to_pareto import WISH_RE, read_wants, read_cities


def main(wants_path, users_path, instance_path):
    cities = read_cities(users_path)
    offers, owner_of, _ = read_wants(wants_path)
    groups = {i: w for _, i, w in offers if i.startswith("%")}
    pu_of = {u: (cities[u][0] if u in cities else u.split("---")[0])
             for u, _, _ in offers}

    # --- what the generated instance says -----------------------------------
    take_union = collections.defaultdict(set)
    dupcap_sets = collections.defaultdict(set)
    give_user = {}
    for line in open(instance_path, encoding="utf-8"):
        line = line.partition("#")[0].strip()
        if not line:
            continue
        if line.startswith("dupcap "):
            _, u, rest = line.split(" ", 2)
            dupcap_sets[u].add(frozenset(rest.split()))
        elif " : (1for1) " in line:
            u, _, body = line.partition(" : ")
            give, _, take = body[len("(1for1) "):].partition(" -> ")
            give_user[give] = u
            take_union[give] |= set(take.split())

    live = set(take_union)                      # items the instance can still move

    # --- what FTM meant ------------------------------------------------------
    bad_edges = bad_caps = 0
    for user, item, wants in offers:
        if item.startswith("%"):
            continue
        expected = set()
        for w in wants:
            for t in (groups[w] if w.startswith("%") else [w]):
                if t in live and owner_of[t] != user:
                    expected.add(t)
        got = take_union.get(item, set())
        if expected != got:
            bad_edges += 1
            if bad_edges <= 3:
                print(f"  MISMATCH {item}: +{sorted(got - expected)[:4]} "
                      f"-{sorted(expected - got)[:4]}")
        if item in live and give_user[item] != pu_of[user]:
            print(f"  OWNER MISMATCH {item}")
        # every want group this item used must still be a dupcap row
        for w in wants:
            if not w.startswith("%"):
                continue
            members = frozenset(t for t in groups[w]
                                if t in live and owner_of[t] != user)
            if members and members not in dupcap_sets[pu_of[user]]:
                bad_caps += 1

    dropped = {i for _, i, _ in offers if not i.startswith("%")} - live
    print(f"items compared        : {len({i for _, i, _ in offers if not i.startswith('%')})}")
    print(f"  still live          : {len(live)}")
    print(f"  pruned as unmovable : {len(dropped)}")
    print(f"want-set mismatches   : {bad_edges}")
    print(f"lost want groups      : {bad_caps}")
    print(f"dupcap rows           : {sum(len(v) for v in dupcap_sets.values())}")

    # every pruned item really is unmovable: nothing it asked for is live
    leak = 0
    for user, item, wants in offers:
        if item.startswith("%") or item in live:
            continue
        if any(t in live and owner_of[t] != user
               for w in wants for t in (groups[w] if w.startswith("%") else [w])):
            leak += 1
    print(f"wrongly pruned items  : {leak}")
    print("RESULT:", "equivalent" if not (bad_edges or bad_caps or leak) else "DIVERGES")


if __name__ == "__main__":
    main(*sys.argv[1:])
