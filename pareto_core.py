"""Importable Pareto solver core: parse -> build MIP -> optimize -> collect.

All per-solve state lives on an ``Instance`` (parse output) and a ``Build``
(model + variable handles), so the same process can solve many instances
without leakage -- the Modal worker relies on this. Rendering (text / JSON)
lives in ``serialize``; the CLI lives in ``main``.
"""
import sys
import os
import re
import math
import time
import json
from collections import defaultdict
from dataclasses import dataclass, field

import gurobipy as gp
from gurobipy import GRB

import pareto_io

__version__ = "1.0.0"

ALLOWED_KPIS = ("trades", "users", "distance")


def warn(msg):
    print(f"WARNING: {msg}", file=sys.stderr)


# --- Parsing -----------------------------------------------------------------

@dataclass
class Instance:
    item_to_id: dict = field(default_factory=dict)
    id_to_item: dict = field(default_factory=dict)
    wishes: list = field(default_factory=list)   # (user, give, take, N, M)
    users: set = field(default_factory=set)
    budget: dict = field(default_factory=dict)   # user -> X_u (absent => +inf)
    owner: dict = field(default_factory=dict)     # item_id -> user
    ask: dict = field(default_factory=dict)       # item_id -> Z_i (absent => 0)
    bids: dict = field(default_factory=dict)      # (user, item_id) -> Y_ui
    take_groups: list = field(default_factory=list)  # (user, N, [item_id, ...])
    give_groups: list = field(default_factory=list)  # (user, N, [item_id, ...])
    location: dict = field(default_factory=dict)  # user -> (lat, lng)

    def intern(self, token):
        if token not in self.item_to_id:
            self.item_to_id[token] = len(self.item_to_id)
            self.id_to_item[self.item_to_id[token]] = token
        return self.item_to_id[token]

    def set_owner(self, iid, u, line):
        if iid in self.owner and self.owner[iid] != u:
            raise ValueError(
                f"Item '{self.id_to_item[iid]}' has conflicting owners "
                f"'{self.owner[iid]}' and '{u}': {line}")
        self.owner[iid] = u


def intern_lookup(inst, token):
    """Test helper: id of an already-interned token."""
    return inst.item_to_id[token]


def parse_wish_body(inst, body, line):
    if not body.startswith('('):
        raise ValueError(f"Missing options: {line}")
    r = body.find(')')
    if r == -1:
        raise ValueError(f"Missing closing ')': {line}")
    if '->' not in body:
        raise ValueError(f"Missing '->': {line}")

    options = body[1:r].strip().split()
    rest = body[r + 1:].strip()

    groups = [part.strip().split() for part in rest.split("->")]
    if len(groups) > 2:
        raise ValueError(f"Non supported amount of groups (max 2): {line}")

    N, M = len(groups[0]), len(groups[1])
    if len(options) > 1:
        warn(f"multiple options in parens; only the last ('{options[-1]}') is used: {line.strip()}")
    for opt in options:
        match = re.fullmatch(r'(\d+)for(\d+)', opt)
        if not match:
            raise ValueError(f"Option must be in 'NforM' format, e.g., '2for1': {line}")
        N = int(match.group(1))
        M = int(match.group(2))

    give = [inst.intern(t) for t in groups[0]]
    take = [inst.intern(t) for t in groups[1]]
    if N > len(give) or M > len(take):
        warn(f"combo can never activate: asks to give {N} of {len(give)} listed and "
             f"take {M} of {len(take)} listed: {line.strip()}")
    return give, take, N, M


def parse_text(inst, text):
    for raw in text.splitlines(keepends=True):
        line = raw.partition('#')[0].strip()
        if not line:
            continue

        m_user = re.fullmatch(r'user\s+(\S+)\s+budget\s+(\d+)', line)
        m_item = re.fullmatch(r'item\s+(\S+)\s+owner\s+(\S+)(?:\s+ask\s+(\d+))?', line)
        m_bid = re.fullmatch(r'bid\s+(\S+)\s+(\S+)\s+(\d+)', line)
        m_take = re.fullmatch(r'takecap\s+(\S+)\s+(\d+)\s+(.+)', line)
        m_give = re.fullmatch(r'givecap\s+(\S+)\s+(\d+)\s+(.+)', line)
        m_dup = re.fullmatch(r'dupcap\s+(\S+)\s+(.+)', line)
        m_loc = re.fullmatch(
            r'location\s+(\S+)\s+(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)', line)

        if m_user:
            inst.users.add(m_user.group(1))
            inst.budget[m_user.group(1)] = int(m_user.group(2))
        elif m_item:
            iid = inst.intern(m_item.group(1))
            u = m_item.group(2)
            inst.users.add(u)
            inst.set_owner(iid, u, raw)
            if m_item.group(3) is not None:
                inst.ask[iid] = int(m_item.group(3))
        elif m_bid:
            u = m_bid.group(1)
            inst.users.add(u)
            iid = inst.intern(m_bid.group(2))
            inst.bids[(u, iid)] = int(m_bid.group(3))
        elif m_take:
            u = m_take.group(1)
            inst.users.add(u)
            inst.take_groups.append((u, int(m_take.group(2)),
                                     [inst.intern(t) for t in m_take.group(3).split()]))
        elif m_give:
            u = m_give.group(1)
            inst.users.add(u)
            inst.give_groups.append((u, int(m_give.group(2)),
                                     [inst.intern(t) for t in m_give.group(3).split()]))
        elif m_dup:
            u = m_dup.group(1)
            inst.users.add(u)
            inst.take_groups.append((u, 1, [inst.intern(t) for t in m_dup.group(2).split()]))
        elif m_loc:
            u = m_loc.group(1)
            lat = float(m_loc.group(2))
            lng = float(m_loc.group(3))
            if not (-90 <= lat <= 90):
                raise ValueError(f"latitude out of range [-90, 90]: {raw}")
            if not (-180 <= lng <= 180):
                raise ValueError(f"longitude out of range [-180, 180]: {raw}")
            inst.users.add(u)
            inst.location[u] = (lat, lng)
        elif ':' in line:
            u, _, body = line.partition(':')
            u = u.strip()
            inst.users.add(u)
            give, take, N, M = parse_wish_body(inst, body.strip(), raw)
            for g in give:
                inst.set_owner(g, u, raw)  # giving an item implies owning it
            inst.wishes.append((u, give, take, N, M))
        else:
            raise ValueError(f"Unrecognized line: {raw}")
    _post_parse_checks(inst)


def parse_json_input(inst, obj):
    for u in obj.get("budgets", []):
        inst.users.add(u["user"]); inst.budget[u["user"]] = int(u["budget"])
    for it in obj.get("items", []):
        iid = inst.intern(it["name"]); inst.users.add(it["owner"])
        inst.set_owner(iid, it["owner"], f"item {it['name']}")
        if "ask" in it:
            inst.ask[iid] = int(it["ask"])
    for b in obj.get("bids", []):
        inst.users.add(b["user"])
        inst.bids[(b["user"], inst.intern(b["item"]))] = int(b["max_price"])
    for lo in obj.get("locations", []):
        lat, lng = float(lo["lat"]), float(lo["lng"])
        if not (-90 <= lat <= 90):
            raise ValueError(f"latitude out of range [-90, 90]: {lo}")
        if not (-180 <= lng <= 180):
            raise ValueError(f"longitude out of range [-180, 180]: {lo}")
        inst.users.add(lo["user"]); inst.location[lo["user"]] = (lat, lng)
    for c in obj.get("takecaps", []):
        inst.users.add(c["user"])
        inst.take_groups.append((c["user"], int(c["n"]),
                                 [inst.intern(t) for t in c["items"]]))
    for c in obj.get("givecaps", []):
        inst.users.add(c["user"])
        inst.give_groups.append((c["user"], int(c["n"]),
                                 [inst.intern(t) for t in c["items"]]))
    for w in obj.get("wishes", []):
        u = w["user"]; inst.users.add(u)
        give = [inst.intern(t) for t in w["give"]]
        take = [inst.intern(t) for t in w["take"]]
        N = int(w.get("n", len(give))); M = int(w.get("m", len(take)))
        if N > len(give) or M > len(take):
            warn(f"combo can never activate: asks to give {N} of {len(give)} listed "
                 f"and take {M} of {len(take)} listed: {w}")
        for g in give:
            inst.set_owner(g, u, f"wish {u}")  # giving an item implies owning it
        inst.wishes.append((u, give, take, N, M))
    _post_parse_checks(inst)


def _post_parse_checks(inst):
    # A cap that names an item nobody owns is almost always a typo: the phantom item
    # matches no real copy, so it silently protects nothing (weakening dup/give limits).
    cap_iids = set()
    for _u, _n, _iids in inst.take_groups:
        cap_iids.update(_iids)
    for _u, _n, _iids in inst.give_groups:
        cap_iids.update(_iids)
    for _iid in sorted(cap_iids, key=lambda i: inst.id_to_item[i]):
        if _iid not in inst.owner:
            warn(f"cap references item '{inst.id_to_item[_iid]}' with no declared owner "
                 f"(typo? it protects nothing)")


def parse(raw, in_format="auto"):
    """Parse instance text or JSON into an Instance. 'auto' peeks the first char."""
    inst = Instance()
    if in_format == "auto":
        in_format = pareto_io.detect_input_format(raw)
    if in_format == "json":
        parse_json_input(inst, json.loads(raw))
    else:
        parse_text(inst, raw)
    return inst


def parse_kpi_list(s):
    """Comma-separated KPIs in priority order, e.g. 'trades,users'."""
    import argparse
    kpis = []
    for tok in s.split(","):
        tok = tok.strip()
        if not tok:
            raise argparse.ArgumentTypeError("empty KPI in --kpi list")
        if tok not in ALLOWED_KPIS:
            raise argparse.ArgumentTypeError(
                f"invalid KPI '{tok}' (choose from {', '.join(ALLOWED_KPIS)})")
        if tok in kpis:
            raise argparse.ArgumentTypeError(f"duplicate KPI '{tok}'")
        kpis.append(tok)
    # 'distance' as the first (or only) objective is degenerate: zero trades ships zero
    # km, so the unconstrained optimum is to trade nothing. It only makes sense as a
    # tie-breaker after a volume objective, so require it to follow 'trades' or 'users'.
    if kpis and kpis[0] == "distance":
        raise argparse.ArgumentTypeError(
            "'distance' cannot be the first/only objective (degenerate: zero trades "
            "gives zero distance); put it after 'trades' or 'users'")
    return kpis


def normalized_input(inst):
    """Canonical, format-independent view of the parsed instance for hashing.
    Item ids are resolved to names; lists are sorted where order is semantically
    irrelevant so equal instances hash equally regardless of source ordering."""
    def name(iid):
        return inst.id_to_item[iid]
    wishes_out = sorted(
        ({"user": u, "give": sorted(name(g) for g in give),
          "take": sorted(name(t) for t in take), "n": N, "m": M}
         for (u, give, take, N, M) in inst.wishes),
        key=lambda w: (w["user"], w["give"], w["take"], w["n"], w["m"]))
    items_out = sorted(
        ({"name": name(iid), "owner": o, **({"ask": inst.ask[iid]} if iid in inst.ask else {})}
         for iid, o in inst.owner.items()),
        key=lambda it: it["name"])
    bids_out = sorted(
        ({"user": u, "item": name(iid), "max_price": y}
         for (u, iid), y in inst.bids.items()),
        key=lambda b: (b["user"], b["item"]))
    budgets_out = sorted(
        ({"user": u, "budget": b} for u, b in inst.budget.items()),
        key=lambda x: x["user"])
    locations_out = sorted(
        ({"user": u, "lat": lat, "lng": lng} for u, (lat, lng) in inst.location.items()),
        key=lambda x: x["user"])
    takecaps_out = sorted(
        ({"user": u, "n": n, "items": sorted(name(i) for i in iids)}
         for (u, n, iids) in inst.take_groups),
        key=lambda x: (x["user"], x["n"], x["items"]))
    givecaps_out = sorted(
        ({"user": u, "n": n, "items": sorted(name(i) for i in iids)}
         for (u, n, iids) in inst.give_groups),
        key=lambda x: (x["user"], x["n"], x["items"]))
    return {"wishes": wishes_out, "items": items_out, "bids": bids_out,
            "budgets": budgets_out, "locations": locations_out,
            "takecaps": takecaps_out, "givecaps": givecaps_out}


# --- Model build -------------------------------------------------------------

@dataclass
class Build:
    model: object
    env: object                  # owned Env to free, or None
    kpi: list
    edge_vars: dict
    combo_records: list
    buy: dict
    spend_data: dict
    earn_data: dict
    participation: dict
    swaps: list
    buys: list
    real_item_ids: set
    fast_bound: object = None


def build(inst, kpi, *, env=None, quiet=True, threads=None,
          time_limit=None, mipgap=None, want_stats=False):
    """Construct the MIP for `inst` under objective list `kpi`.

    env/quiet/threads control the Gurobi environment (a WLS env for the service,
    the size-limited default for the CLI). time_limit/mipgap fall back to the
    PARETO_TIME_LIMIT / PARETO_MIPGAP env vars when None, preserving CLI behavior.
    Returns a Build carrying the model plus the handles collect() needs.
    """
    owned_env = None
    if env is not None:
        model = gp.Model(env=env)
        model.Params.OutputFlag = 0
    elif quiet:
        owned_env = gp.Env(empty=True)
        owned_env.setParam("OutputFlag", 0)
        owned_env.start()
        model = gp.Model(env=owned_env)
        model.Params.OutputFlag = 0
    else:
        model = gp.Model()
        model.Params.OutputFlag = 1
    model.Params.Symmetry = 2
    if threads:
        model.Params.Threads = threads

    edge_vars = {}        # (i, j) -> binary var
    combo_records = []    # list of (in_pairs, out_pairs); pair = (item_id, var)
    spend_swap = {}       # user -> list of (take_iid, take_var): cash legs of swap receipts
    in_terms = {}         # item_id -> list of vars where the item is given away in a swap
    out_terms = {}        # item_id -> list of vars where the item is received in a swap

    combo_node_id = len(inst.item_to_id)

    def add_edge(i, j, var):
        edge_vars[(i, j)] = var
        out_terms.setdefault(i, []).append(var)
        in_terms.setdefault(j, []).append(var)

    # Hub-and-spoke compaction (disable with PARETO_NOHUB). A common wish pattern is a user
    # offering several of their items, each as a separate 1for1 wish, for the SAME set of
    # copies of a wanted game, then dup-protecting that set to receive a single copy:
    #     u: (1for1) A -> X1 X2 ...
    #     u: (1for1) B -> X1 X2 ...
    #     dupcap u X1 X2 ...
    # Built naively this is J gives x K copies of highly symmetric barter edges. Instead route
    # them through one virtual hub node: K in-spokes ("u receives a copy") and J out-spokes
    # ("u gives an item"), with sum(in) == sum(out) and the dupcap row capping receipts at 1.
    # Exact same optimum, J*K -> J+K variables, and far less degeneracy. In-spokes carry the
    # receipt (budget/distance via spend_swap) but are NOT counted as trades -- the copy's move
    # is already counted at its owner's give edge; only the J out-spokes (items given) count.
    # (In-spokes land on the virtual hub node, so the "in-side is a real item" trades filter
    # below excludes them automatically -- no explicit key set needed.)
    hub_keys = set()       # (user, frozenset(take)) consumed by a hub -> skipped below
    if not os.environ.get("PARETO_NOHUB"):
        _dup_sets = defaultdict(set)
        for _u, _n, _iids in inst.take_groups:
            _dup_sets[_u].add(frozenset(_iids))
        _gives = defaultdict(list)   # (user, frozenset(take)) -> [(give_item, take_ids), ...]
        for _user, _send, _take, _N, _M in inst.wishes:
            if len(_send) == 1 and _N == 1 and _M == 1 and _take:  # true 1-for-1 only (N==1)
                _gives[(_user, frozenset(_take))].append((_send[0], _take))
        for (_user, _tset), _glist in _gives.items():
            _give_items = list(dict.fromkeys(g for g, _ in _glist))
            if len(_give_items) < 2 or _tset not in _dup_sets.get(_user, ()):
                continue  # only merge the dup-capped, multi-give pattern (the win case)
            hub_keys.add((_user, _tset))
            _hub = combo_node_id
            combo_node_id += 1
            _in_pairs, _out_pairs = [], []
            for _t in _glist[0][1]:                     # any wish's take list; order is cosmetic
                _v = model.addVar(vtype=GRB.BINARY)
                add_edge(_t, _hub, _v)                  # copy _t flows into the hub (u receives it)
                _in_pairs.append((_t, _v))
                spend_swap.setdefault(_user, []).append((_t, _v))   # receipt -> budget/distance
            for _g in _give_items:
                _v = model.addVar(vtype=GRB.BINARY)
                add_edge(_hub, _g, _v)                  # u gives item _g out of the hub
                _out_pairs.append((_g, _v))
            model.addConstr(gp.quicksum(v for _, v in _in_pairs)
                            == gp.quicksum(v for _, v in _out_pairs))   # receive iff give
            # cap (<= 1) is supplied by the existing dupcap row over _tset (also counts buys)
            combo_records.append((_in_pairs, _out_pairs))

    # Build swap / combo variables (unchanged barter structure), recording per-user swap cash legs
    for user, send_ids, take_ids, N, M in inst.wishes:
        if len(send_ids) == 1 and N == 1 and M == 1:  # true 1-for-1 (N==1 too, else dead combo)
            if (user, frozenset(take_ids)) in hub_keys:
                continue  # merged into a hub above
            s = send_ids[0]
            for t in take_ids:
                if (t, s) in edge_vars:
                    continue  # duplicate wish: reuse the existing edge var. Both would share the
                              # same single <=1 slot; a shadow var only risks an unreported trade
                              # (add_edge overwrites edge_vars but leaves both in in/out_terms).
                e = model.addVar(vtype=GRB.BINARY)
                add_edge(t, s, e)
                spend_swap.setdefault(user, []).append((t, e))
            continue

        combo_id = combo_node_id
        combo_node_id += 1

        in_pairs, out_pairs = [], []
        for s in send_ids:
            v = model.addVar(vtype=GRB.BINARY)
            add_edge(combo_id, s, v)
            out_pairs.append((s, v))
        for t in take_ids:
            v = model.addVar(vtype=GRB.BINARY)
            add_edge(t, combo_id, v)
            in_pairs.append((t, v))
            spend_swap.setdefault(user, []).append((t, v))

        out_vars = [v for _, v in out_pairs]
        in_vars = [v for _, v in in_pairs]

        # These ensure that no individual edge is active unless the whole combo is active
        active = model.addVar(vtype=GRB.BINARY)
        model.addConstr(gp.quicksum(out_vars) == N * active)
        model.addConstr(gp.quicksum(in_vars) == M * active)

        combo_records.append((in_pairs, out_pairs))

    # Cash purchase variables: created only for bids that clear the ask and aren't self-buys
    buy = {}
    explicit_bid_pairs = set(inst.bids)   # every (user, item) that had an explicit bid, cleared or not
    for (u, iid), y in inst.bids.items():
        o = inst.owner.get(iid)
        if o is None:
            raise ValueError(f"Cannot bid on item '{inst.id_to_item[iid]}' with no declared owner")
        if u == o:
            continue  # don't buy your own item
        if iid not in inst.ask:
            continue          # no ask -> not for sale
        if y < inst.ask[iid]:
            continue  # bid doesn't clear the ask -> edge filtered out
        buy[(u, iid)] = model.addVar(vtype=GRB.BINARY)

    # A wish's take-item may also be acquired with cash (implicit bid, willing to pay the ask),
    # funded by the net budget. Lets a swap intent complete via a cash chain when no barter swap
    # closes -- e.g. B sells B_GAME for cash and uses the proceeds to buy its wished C_GAME.
    # Two guards keep the implicit bid a conservative default rather than an imposition:
    #   * only for users who opted into cash (declared a budget or placed any explicit bid) --
    #     a pure-barter participant is never made to owe money they never signed up for;
    #   * never for a (user, item) that had an explicit bid -- the explicit bid governs, even a
    #     low one that was filtered out (a 5-bid on a 10-ask is a refusal to pay 10, not licence
    #     to charge the full ask implicitly).
    money_present = bool(inst.ask) or bool(inst.budget) or bool(inst.bids)
    cash_users = set(inst.budget) | {u for (u, _iid) in inst.bids}   # opted into spending cash
    if money_present:
        for user, send_ids, take_ids, N, M in inst.wishes:
            if user not in cash_users:
                continue          # never opted into cash -> no unbidden implicit purchase
            for t in take_ids:
                if t not in inst.ask:
                    continue          # can't implicitly buy an unlisted item
                if user == inst.owner.get(t):
                    continue
                if (user, t) in buy or (user, t) in explicit_bid_pairs:
                    continue          # already have a buy var, or an explicit bid governs
                buy[(user, t)] = model.addVar(vtype=GRB.BINARY)

    buy_terms = {}  # item_id -> list of buy vars
    for (u, iid), v in buy.items():
        buy_terms.setdefault(iid, []).append(v)

    real_item_ids = set(inst.item_to_id.values())

    # Duplicate protection: a user receives at most one copy of a protected game,
    # counting swap receipts and cash buys together. Demand-side mirror of the
    # per-item seller slot (out_sum + buys <= 1) built in the loop below.
    # Note: this excludes any combo whose take-set needs M>=2 of these protected
    # copies (it can never activate) -- the correct resolution of contradictory input.
    for u, n, iids in inst.take_groups:
        grp = set(iids)
        terms = [v for (it, v) in spend_swap.get(u, []) if it in grp]
        terms += [buy[(u, it)] for it in grp if (u, it) in buy]
        if len(terms) > n:
            model.addConstr(gp.quicksum(terms) <= n)

    # Give cap: a user gives at most N of the listed copies, counting swap supply
    # (in_terms, incl. combo/hub out-spokes) and cash sale (buy_terms). Mirror of
    # takecap. Items must be owned by the user.
    for u, n, iids in inst.give_groups:
        grp = set(iids)
        for it in grp:
            if inst.owner.get(it) != u:
                raise ValueError(
                    f"givecap user '{u}' lists item '{inst.id_to_item[it]}' "
                    f"owned by '{inst.owner.get(it)}'")
        terms = []
        for it in grp:
            terms += in_terms.get(it, [])
            terms += buy_terms.get(it, [])
        if len(terms) > n:
            model.addConstr(gp.quicksum(terms) <= n)

    # Build model constraints (swap balance kept; cash competes for the same single slot)
    for node in real_item_ids:
        ins = in_terms.get(node, [])
        outs = out_terms.get(node, [])
        if ins and outs:
            model.addConstr(gp.quicksum(ins) == gp.quicksum(outs))
        elif ins:
            model.addConstr(gp.quicksum(ins) == 0)   # given but no swap wants it -> can only leave via cash
        elif outs:
            model.addConstr(gp.quicksum(outs) == 0)  # wanted but never offered for swap
        if ins:
            model.addConstr(gp.quicksum(ins) <= 1)
        if node in buy_terms:
            model.addConstr(gp.quicksum(outs) + gp.quicksum(buy_terms[node]) <= 1)

    # Bucket buys and items by user once, so the budget build is linear instead of O(users^2).
    buys_by_user = {}    # user -> list of (item_id, var)
    for (u, iid), v in buy.items():
        buys_by_user.setdefault(u, []).append((iid, v))
    items_by_owner = {}  # user -> list of item_id
    for iid, o in inst.owner.items():
        items_by_owner.setdefault(o, []).append(iid)

    # Per-user net CASH budget: cash spend (buys) minus cash earnings (own items sold for
    # cash) <= X_u. Barter swaps move no money -- an item leaving via a swap edge is a trade,
    # not a sale -- so swap legs never touch the budget even when the item carries an ask (the
    # ask is only the *cash* sale price). A user can fund a buy by SELLING a game for cash, but
    # not by bartering one away: counting barter-at-ask as earnings would credit phantom cash
    # and let a user blow past their real cash cap.
    spend_data = {}  # user -> list of (coeff, var) for reporting
    earn_data = {}   # user -> list of (coeff, var) for reporting
    for u in inst.users:
        spend = [(inst.ask.get(iid, 0), v) for (iid, v) in buys_by_user.get(u, []) if inst.ask.get(iid, 0)]
        earn = []
        for iid in items_by_owner.get(u, []):
            z = inst.ask.get(iid, 0)
            if z:
                earn += [(z, v) for v in buy_terms.get(iid, [])]
        spend_data[u] = spend
        earn_data[u] = earn
        if u in inst.budget and (spend or earn):
            lhs = gp.quicksum(c * v for c, v in spend) - gp.quicksum(c * v for c, v in earn)
            model.addConstr(lhs <= inst.budget[u])

    # Objective. Integer coefficients are essential: a fractional tie-break (e.g. weighting swaps by
    # 1+eps) blocks Gurobi's integer-bound rounding and makes proving optimality 10-60x slower.
    # Count only edges whose in-side (j) is a real item: those are gives -- items that physically
    # move. Combo/hub receive-legs land on a virtual node and are excluded, so each moved item is
    # counted exactly once (at the giver's edge), matching the cash buys' 1-per-move.
    swaps = [v for (_i, j), v in edge_vars.items() if j in real_item_ids]
    buys = list(buy.values())

    # MIPGapAbs = ~1 lets Gurobi stop 1 trade short of the optimum. On a huge degenerate
    # instance that is a big speedup and 1 trade is negligible against thousands; on a small
    # instance it returns a visibly-suboptimal answer, and in a lexicographic --kpi solve it
    # can zero out the primary objective (dropping trades to 0 lets 'distance' pick 0 km / no
    # trades). So gate it: single-objective only, and only past a var-count threshold.
    # PARETO_GAPABS_MINVARS overrides the threshold; 0 disables the gap entirely.
    _gapabs_min = int(os.environ.get("PARETO_GAPABS_MINVARS", 20000))
    if _gapabs_min and len(kpi) == 1 and len(swaps) + len(buys) >= _gapabs_min:
        model.Params.MIPGapAbs = 1 - 1e-6

    _time_limit = time_limit if time_limit is not None else os.environ.get("PARETO_TIME_LIMIT")
    if _time_limit:
        model.Params.TimeLimit = float(_time_limit)
    _mipgap = mipgap if mipgap is not None else os.environ.get("PARETO_MIPGAP")
    if _mipgap is not None and _mipgap != "":
        model.Params.MIPGap = float(_mipgap)
    # Fast-solution knobs for instances too large to solve the root LP (huge events):
    # emphasize feasibility, run the NoRel heuristic before the root relaxation, and/or
    # pick the root LP method (1=dual simplex avoids the costly barrier ordering).
    if os.environ.get("PARETO_MIPFOCUS"):
        model.Params.MIPFocus = int(os.environ["PARETO_MIPFOCUS"])
    if os.environ.get("PARETO_NORELHEUR"):
        model.Params.NoRelHeurTime = float(os.environ["PARETO_NORELHEUR"])
    if os.environ.get("PARETO_METHOD"):
        model.Params.Method = int(os.environ["PARETO_METHOD"])

    # Per-user participation vars: a user participates if they receive any item (swap take or cash
    # buy) or give an owned item away (it leaves via swap or cash sale). Used for the 'users' KPI
    # and the users_traded report; skip the work when neither is requested.
    need_participation = ("users" in kpi) or want_stats
    participation = {}
    if need_participation:
        for u in inst.users:
            part = [v for _, v in spend_swap.get(u, [])]      # receive via swap
            part += [v for _, v in buys_by_user.get(u, [])]   # receive via cash
            for j in items_by_owner.get(u, []):               # give: an owned item leaves
                part += in_terms.get(j, [])
                part += buy_terms.get(j, [])
            if part:
                participation[u] = part

    # 'users' KPI: one binary per user that can be 1 only if the user has >= 1 trade.
    traded = []
    if "users" in kpi:
        for u, part in participation.items():
            t = model.addVar(vtype=GRB.BINARY)
            model.addConstr(t <= gp.quicksum(part))
            traded.append(t)

    dist_cache = {}

    def haversine_km(a, b):
        """Great-circle distance in integer km between (lat, lng) points a and b."""
        key = (a, b) if a <= b else (b, a)
        if key in dist_cache:
            return dist_cache[key]
        (lat1, lon1), (lat2, lon2) = a, b
        r1, r2 = math.radians(lat1), math.radians(lat2)
        dlat = math.radians(lat2 - lat1)
        dlon = math.radians(lon2 - lon1)
        h = math.sin(dlat / 2) ** 2 + math.cos(r1) * math.cos(r2) * math.sin(dlon / 2) ** 2
        km = round(2 * 6371 * math.asin(math.sqrt(h)))
        dist_cache[key] = km
        return km

    def distance_terms():
        """(coeff, var) for every item move: ship take-item from owner to receiver.
        Skips moves with an unknown owner or a missing location on either end."""
        terms = []

        def add(receiver, take_iid, var):
            o = inst.owner.get(take_iid)
            if o is None or receiver not in inst.location or o not in inst.location:
                return
            d = haversine_km(inst.location[o], inst.location[receiver])
            if d:
                terms.append((d, var))

        for u, legs in spend_swap.items():          # swap receive-legs (simple + combo)
            for iid, v in legs:
                add(u, iid, v)
        for (u, iid), v in buy.items():             # cash buys
            add(u, iid, v)
        return terms

    def kpi_expr(name):
        """Objective expression in MAXIMIZE form for one KPI."""
        if name == "trades":
            return gp.quicksum(swaps) + gp.quicksum(buys)
        if name == "users":
            return gp.quicksum(traded)
        if name == "distance":
            return -gp.quicksum(c * v for c, v in distance_terms())
        raise ValueError(f"unknown KPI: {name}")

    # Lexicographic multi-objective: leftmost KPI = highest priority. All objectives
    # share ModelSense; min-objectives (distance) are negated into maximize form.
    model.ModelSense = GRB.MAXIMIZE
    if len(kpi) == 1:
        model.setObjective(kpi_expr(kpi[0]), GRB.MAXIMIZE)
    else:
        n = len(kpi)
        for k, name in enumerate(kpi):
            model.setObjectiveN(kpi_expr(name), index=k, priority=n - k)

    # Optional heuristic fast mode (PARETO_FAST): trade a proven optimum for a large
    # speedup on big, degenerate instances. The barter/budget LP relaxation is highly
    # degenerate -- its objective bound is reached almost instantly, but Gurobi then burns
    # most of the runtime in heuristics digging out an integer point among thousands of
    # tied fractional variables. PARETO_FAST short-circuits that: solve the continuous
    # relaxation once (dual simplex gives a vertex with reduced costs and no costly
    # barrier crossover), fix every variable the relaxation leaves at 0, and solve the
    # much smaller residual MIP. Fixing only zeros can never make the residual infeasible
    # (the relaxation's own point stays feasible) and at worst drops a few tied trades.
    # The relaxation objective is a valid bound, so the achieved gap is reported.
    fast_bound = None
    if os.environ.get("PARETO_FAST"):
        if len(kpi) > 1:
            sys.exit("PARETO_FAST does not support multi-objective --kpi lists")

        print(f"\n--- PARETO_FAST: Aggressive LP Pruning ---", file=sys.stderr)
        _t0 = time.perf_counter()
        model.update()
        relaxed = model.relax()
        relaxed.Params.OutputFlag = 0
        relaxed.Params.Method = int(os.environ.get("PARETO_METHOD", 1))  # dual simplex
        relaxed.optimize()
        fast_bound = relaxed.ObjVal
        # Prune aggressively: Delete any variable (swaps AND buys) with low fractional probability
        # Increase this PARETO_FAST threshold (e.g., 0.05 or 0.1) for a smaller, faster model
        fixed_swaps = 0
        fixed_buys = 0

        # FIX: Store variable IDs in a set to avoid Gurobi's overloaded '==' operator
        buy_var_ids = {id(var) for var in buy.values()}

        # Zip original variables with relaxed variables to map the X values back
        for v, rv in zip(model.getVars(), relaxed.getVars()):
            if rv.X < float(os.environ.get("PARETO_FAST")):
                v.UB = 0.0
                # Just for reporting: distinguish buys from swaps by checking their names or sets
                if id(v) in buy_var_ids:
                    fixed_buys += 1
                else:
                    fixed_swaps += 1
        print(f"  LP bound = {fast_bound:.0f} (Solved in {time.perf_counter() - _t0:.2f}s)", file=sys.stderr)
        print(f"  Sheared matrix: locked {fixed_buys} cash buys and {fixed_swaps} swap edges.", file=sys.stderr)
        print(f"--- Solving Residual MIP ---\n", file=sys.stderr)

    return Build(
        model=model, env=owned_env, kpi=kpi, edge_vars=edge_vars,
        combo_records=combo_records, buy=buy, spend_data=spend_data,
        earn_data=earn_data, participation=participation, swaps=swaps,
        buys=buys, real_item_ids=real_item_ids, fast_bound=fast_bound,
    )


# --- Solve + collect ---------------------------------------------------------

_STATUS = {GRB.OPTIMAL: "Optimal", GRB.TIME_LIMIT: "TimeLimit", GRB.INFEASIBLE: "Infeasible"}


def _status_str(model):
    return _STATUS.get(model.Status, f"Status{model.Status}")


def _active(var):
    return var.X > 0.5


@dataclass
class Solution:
    result: dict             # status, kpi, trades, combos, cash_*, payments, settlement, [stats]
    input_checksum: str
    status: str
    has_solution: bool


def kpi_values(model, kpi):
    if model.SolCount == 0:
        return {k: None for k in kpi}
    vals = {}
    if len(kpi) == 1:
        raw = model.ObjVal
        vals[kpi[0]] = int(round(-raw if kpi[0] == "distance" else raw))
    else:
        for k, name in enumerate(kpi):
            model.params.ObjNumber = k
            raw = model.ObjNVal
            vals[name] = int(round(-raw if name == "distance" else raw))
    return vals


def _stats_dict(inst, b, status):
    model = b.model
    have = model.SolCount > 0
    users_traded = (sum(1 for part in b.participation.values() if any(v.X > 0.5 for v in part))
                    if have else 0)
    return {
        "swap_vars": len(b.swaps), "buy_vars": len(b.buys),
        "combos": len(b.combo_records), "items": len(b.real_item_ids),
        "users_traded": users_traded, "total_users": len(inst.users),
        "status": status, "kpi": kpi_values(model, b.kpi),
        "runtime": round(model.Runtime, 3),
    }


def print_stats(inst, b, status):
    """Reproduce the CLI's `STATS ...` stderr line (gated on PARETO_STATS)."""
    model = b.model
    have = model.SolCount > 0
    users_traded = (sum(1 for part in b.participation.values() if any(v.X > 0.5 for v in part))
                    if have else 0)
    # ObjVal / MIPGap are unavailable under multi-objective; report per-objective
    # values instead (distance is reported negated, i.e. in maximize form).
    if len(b.kpi) == 1:
        obj_str = f"obj={model.ObjVal:.0f}" if have else "obj=nan"
        gap = f"{model.MIPGap:.4f}" if have else "nan"
    else:
        parts = []
        for k, name in enumerate(b.kpi):
            model.params.ObjNumber = k
            val = f"{model.ObjNVal:.0f}" if have else "nan"
            parts.append(f"{name}={val}")
        obj_str = "obj[" + ",".join(parts) + "]"
        gap = "nan"
    print(
        f"STATS swap_vars={len(b.swaps)} buy_vars={len(b.buys)} combos={len(b.combo_records)} "
        f"items={len(b.real_item_ids)} users_traded={users_traded}/{len(inst.users)} "
        f"status={status} {obj_str} gap={gap} runtime={model.Runtime:.3f}",
        file=sys.stderr,
    )


def collect(inst, b, status, *, want_stats=False):
    """Read the optimized model into a canonical, JSON-ready result dict."""
    model = b.model
    if model.SolCount == 0:
        result = {
            "status": status,
            "kpi": kpi_values(model, b.kpi),
            "trades": [], "combos": [],
            "cash_purchases": [], "cash_summary": [],
            "payments": [], "settlement": [],
        }
        if want_stats:
            result["stats"] = _stats_dict(inst, b, status)
        return result

    trades = []
    for (i, j), var in b.edge_vars.items():
        if _active(var) and i in inst.id_to_item and j in inst.id_to_item:
            trades.append({"give": inst.id_to_item[j], "take": inst.id_to_item[i]})

    combos = []
    for in_pairs, out_pairs in b.combo_records:
        if any(_active(v) for _, v in in_pairs + out_pairs):
            combos.append({
                "sent": [inst.id_to_item[s] for s, v in out_pairs if _active(v)],
                "taken": [inst.id_to_item[t] for t, v in in_pairs if _active(v)],
            })

    has_money = bool(b.buy) or bool(inst.ask) or bool(inst.budget)
    cash_purchases, cash_summary, payments, settlement = [], [], [], []
    if has_money:
        for (u, iid), v in b.buy.items():
            if _active(v):
                cash_purchases.append({
                    "item": inst.id_to_item[iid], "from": inst.owner[iid],
                    "to": u, "price": inst.ask.get(iid, 0)})
        net = {}
        for u in sorted(inst.users):
            spent = sum(c for c, v in b.spend_data[u] if _active(v))
            earned = sum(c for c, v in b.earn_data[u] if _active(v))
            net[u] = spent - earned
            cash_summary.append({
                "user": u, "spent": spent, "earned": earned, "net": net[u],
                "cap": inst.budget[u] if u in inst.budget else None})
        assert sum(net.values()) == 0, "cash nets must balance to zero"

        flows = {}
        def add_flow(payer, payee, amt):
            if amt and payer != payee:
                flows[(payer, payee)] = flows.get((payer, payee), 0) + amt
        for (u, iid), v in b.buy.items():
            if _active(v):
                add_flow(u, inst.owner[iid], inst.ask.get(iid, 0))
        printed = set()
        for (a, bb) in list(flows):
            if (a, bb) in printed or (bb, a) in printed:
                continue
            pair_net = flows.get((a, bb), 0) - flows.get((bb, a), 0)
            if pair_net > 0:
                payments.append({"from": a, "to": bb, "amount": pair_net})
            elif pair_net < 0:
                payments.append({"from": bb, "to": a, "amount": -pair_net})
            printed.add((a, bb)); printed.add((bb, a))

        debtors = sorted(((u, n) for u, n in net.items() if n > 0), key=lambda x: -x[1])
        creditors = sorted(((u, -n) for u, n in net.items() if n < 0), key=lambda x: -x[1])
        i = j = 0
        while i < len(debtors) and j < len(creditors):
            du, dn = debtors[i]; cu, cn = creditors[j]
            pay = min(dn, cn)
            settlement.append({"from": du, "to": cu, "amount": pay})
            debtors[i] = (du, dn - pay); creditors[j] = (cu, cn - pay)
            if debtors[i][1] == 0: i += 1
            if creditors[j][1] == 0: j += 1

    # Canonical sort: a given solution must serialize identically regardless of
    # Gurobi variable-creation order / Python hash seed.
    trades.sort(key=lambda t: (t["give"], t["take"]))
    for c in combos:
        c["sent"].sort(); c["taken"].sort()
    combos.sort(key=lambda c: (c["sent"], c["taken"]))
    cash_purchases.sort(key=lambda p: (p["item"], p["from"], p["to"]))
    payments.sort(key=lambda p: (p["from"], p["to"]))
    settlement.sort(key=lambda p: (p["from"], p["to"]))
    # cash_summary is already built in sorted(users) order.

    result = {
        "status": status,
        "kpi": kpi_values(model, b.kpi),
        "trades": trades, "combos": combos,
        "cash_purchases": cash_purchases, "cash_summary": cash_summary,
        "payments": payments, "settlement": settlement,
    }
    if want_stats:
        result["stats"] = _stats_dict(inst, b, status)
    return result


def solve(raw, kpi=("trades",), *, in_format="auto", env=None, quiet=True,
          threads=None, time_limit=None, mipgap=None, want_stats=False):
    """Parse, build, optimize, collect. Returns a Solution.

    `raw` is instance text (or JSON when in_format is 'json'/'auto'-detected).
    Diagnostic stderr output (PARETO_FAST / PARETO_STATS / status warnings) is
    emitted exactly as the CLI does, so `main` is a thin wrapper over this.
    """
    kpi = list(kpi)
    inst = parse(raw, in_format)
    input_checksum = pareto_io.checksum(normalized_input(inst))
    b = build(inst, kpi, env=env, quiet=quiet, threads=threads,
              time_limit=time_limit, mipgap=mipgap, want_stats=want_stats)
    b.model.optimize()
    if b.fast_bound is not None and b.model.SolCount > 0:
        _gap = abs(b.fast_bound - b.model.ObjVal) / max(abs(b.fast_bound), 1.0)
        print(f"PARETO_FAST: obj={b.model.ObjVal:.0f} bound={b.fast_bound:.0f} gap={_gap:.4%}",
              file=sys.stderr)
    status = _status_str(b.model)
    if status != "Optimal":
        print(f"WARNING: solver status is {status}", file=sys.stderr)
    if os.environ.get("PARETO_STATS"):
        print_stats(inst, b, status)
    result = collect(inst, b, status, want_stats=want_stats)
    return Solution(result=result, input_checksum=input_checksum,
                    status=status, has_solution=b.model.SolCount > 0)
