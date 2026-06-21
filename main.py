import sys
import os
import re
import math
import time
import argparse
from collections import defaultdict
import gurobipy as gp
from gurobipy import GRB

item_to_id = {}
id_to_item = {}
wishes = []   # (user, give, take, N, M): 'give' games given, 'take' games taken, give at most 'N', take at least 'M'
users = set()
budget = {}   # user -> X_u  (absent => +inf, unconstrained)
owner = {}    # item_id -> user (the original owner)
ask = {}      # item_id -> Z_i (absent => 0)
bids = {}     # (user, item_id) -> Y_ui (max cash the user will pay)
take_groups = []  # list of (user, N, [item_id, ...]); user receives <= N of these copies
give_groups = []  # list of (user, N, [item_id, ...]); user gives <= N of these copies
location = {}  # user -> (lat, lng) in degrees


def intern(token):
    if token not in item_to_id:
        item_to_id[token] = len(item_to_id)
        id_to_item[item_to_id[token]] = token
    return item_to_id[token]


def set_owner(iid, u, line):
    if iid in owner and owner[iid] != u:
        raise ValueError(f"Item '{id_to_item[iid]}' has conflicting owners '{owner[iid]}' and '{u}': {line}")
    owner[iid] = u


def parse_wish_body(body, line):
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
    for opt in options:
        match = re.fullmatch(r'(\d+)for(\d+)', opt)
        if not match:
            raise ValueError(f"Option must be in 'NforM' format, e.g., '2for1': {line}")
        N = int(match.group(1))
        M = int(match.group(2))

    give = [intern(t) for t in groups[0]]
    take = [intern(t) for t in groups[1]]
    return give, take, N, M


# Handle input
def parse_file(_file):
    with open(_file, 'r') as f:
        for raw in f:
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
                users.add(m_user.group(1))
                budget[m_user.group(1)] = int(m_user.group(2))
            elif m_item:
                iid = intern(m_item.group(1))
                u = m_item.group(2)
                users.add(u)
                set_owner(iid, u, raw)
                if m_item.group(3) is not None:
                    ask[iid] = int(m_item.group(3))
            elif m_bid:
                u = m_bid.group(1)
                users.add(u)
                iid = intern(m_bid.group(2))
                bids[(u, iid)] = int(m_bid.group(3))
            elif m_take:
                u = m_take.group(1)
                users.add(u)
                take_groups.append((u, int(m_take.group(2)),
                                    [intern(t) for t in m_take.group(3).split()]))
            elif m_give:
                u = m_give.group(1)
                users.add(u)
                give_groups.append((u, int(m_give.group(2)),
                                    [intern(t) for t in m_give.group(3).split()]))
            elif m_dup:
                u = m_dup.group(1)
                users.add(u)
                take_groups.append((u, 1, [intern(t) for t in m_dup.group(2).split()]))
            elif m_loc:
                u = m_loc.group(1)
                lat = float(m_loc.group(2))
                lng = float(m_loc.group(3))
                if not (-90 <= lat <= 90):
                    raise ValueError(f"latitude out of range [-90, 90]: {raw}")
                if not (-180 <= lng <= 180):
                    raise ValueError(f"longitude out of range [-180, 180]: {raw}")
                users.add(u)
                location[u] = (lat, lng)
            elif ':' in line:
                u, _, body = line.partition(':')
                u = u.strip()
                users.add(u)
                give, take, N, M = parse_wish_body(body.strip(), raw)
                for g in give:
                    set_owner(g, u, raw)  # giving an item implies owning it
                wishes.append((u, give, take, N, M))
            else:
                raise ValueError(f"Unrecognized line: {raw}")


_argp = argparse.ArgumentParser()
_argp.add_argument("file")
ALLOWED_KPIS = ("trades", "users", "distance")


def parse_kpi_list(s):
    """Comma-separated KPIs in priority order, e.g. 'trades,users'."""
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
    return kpis


_argp.add_argument("--kpi", type=parse_kpi_list, default=["trades"],
                   help="comma-separated objectives in priority order "
                        "(leftmost optimized first), e.g. 'trades,users'. "
                        "Choices: 'trades' = max total trades (default); "
                        "'users' = max users with >= 1 trade; "
                        "'distance' = min total shipping distance (km).")
_args = _argp.parse_args()
parse_file(_args.file)

model = gp.Model()
model.Params.OutputFlag = 1

edge_vars = {}        # (i, j) -> binary var
combo_records = []    # list of (in_pairs, out_pairs); pair = (item_id, var)
spend_swap = {}       # user -> list of (take_iid, take_var): cash legs of swap receipts
in_terms = {}         # item_id -> list of vars where the item is given away in a swap
out_terms = {}        # item_id -> list of vars where the item is received in a swap

combo_node_id = len(item_to_id)


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
hub_in_keys = set()    # (item, hub) edge keys of in-spokes: excluded from the trades objective
hub_keys = set()       # (user, frozenset(take)) consumed by a hub -> skipped below
if not os.environ.get("PARETO_NOHUB"):
    _dup_sets = defaultdict(set)
    for _u, _n, _iids in take_groups:
        _dup_sets[_u].add(frozenset(_iids))
    _gives = defaultdict(list)   # (user, frozenset(take)) -> [(give_item, take_ids), ...]
    for _user, _send, _take, _N, _M in wishes:
        if len(_send) == 1 and _M == 1 and _take:
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
            hub_in_keys.add((_t, _hub))
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
for user, send_ids, take_ids, N, M in wishes:
    if len(send_ids) == 1 and M == 1:
        if (user, frozenset(take_ids)) in hub_keys:
            continue  # merged into a hub above
        s = send_ids[0]
        for t in take_ids:
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
    for v in out_vars:
        model.addConstr(v <= active)
    for v in in_vars:
        model.addConstr(v <= active)

    # Total outgoing (sent) <= N if combo is active, make sure at least 1 item is sent
    model.addConstr(gp.quicksum(out_vars) <= N * active)
    model.addConstr(active <= gp.quicksum(out_vars))

    # Total incoming (received) >= M if combo is active
    model.addConstr(M * active <= gp.quicksum(in_vars))

    combo_records.append((in_pairs, out_pairs))

# Cash purchase variables: created only for bids that clear the ask and aren't self-buys
buy = {}
for (u, iid), y in bids.items():
    o = owner.get(iid)
    if o is None:
        raise ValueError(f"Cannot bid on item '{id_to_item[iid]}' with no declared owner")
    if u == o:
        continue  # don't buy your own item
    if iid not in ask:
        continue          # no ask -> not for sale
    if y < ask[iid]:
        continue  # bid doesn't clear the ask -> edge filtered out
    buy[(u, iid)] = model.addVar(vtype=GRB.BINARY)

# A wish's take-item may also be acquired with cash (implicit bid, willing to pay the ask),
# funded by the net budget. Lets a swap intent complete via a cash chain when no barter swap
# closes -- e.g. B sells B_GAME for cash and uses the proceeds to buy its wished C_GAME.
# Gated on money being present so pure-barter instances keep strict swap reciprocity.
money_present = bool(ask) or bool(budget) or bool(bids)
if money_present:
    for user, send_ids, take_ids, N, M in wishes:
        for t in take_ids:
            if t not in ask:
                continue          # can't implicitly buy an unlisted item
            if user == owner.get(t) or (user, t) in buy:
                continue
            buy[(user, t)] = model.addVar(vtype=GRB.BINARY)

buy_terms = {}  # item_id -> list of buy vars
for (u, iid), v in buy.items():
    buy_terms.setdefault(iid, []).append(v)

real_item_ids = set(item_to_id.values())

# Duplicate protection: a user receives at most one copy of a protected game,
# counting swap receipts and cash buys together. Demand-side mirror of the
# per-item seller slot (out_sum + buys <= 1) built in the loop below.
# Note: this excludes any combo whose take-set needs M>=2 of these protected
# copies (it can never activate) -- the correct resolution of contradictory input.
for u, n, iids in take_groups:
    grp = set(iids)
    terms = [v for (it, v) in spend_swap.get(u, []) if it in grp]
    terms += [buy[(u, it)] for it in grp if (u, it) in buy]
    if len(terms) > n:
        model.addConstr(gp.quicksum(terms) <= n)

# Give cap: a user gives at most N of the listed copies, counting swap supply
# (in_terms, incl. combo/hub out-spokes) and cash sale (buy_terms). Mirror of
# takecap. Items must be owned by the user.
for u, n, iids in give_groups:
    grp = set(iids)
    for it in grp:
        if owner.get(it) != u:
            raise ValueError(
                f"givecap user '{u}' lists item '{id_to_item[it]}' "
                f"owned by '{owner.get(it)}'")
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
for iid, o in owner.items():
    items_by_owner.setdefault(o, []).append(iid)

# Per-user net budget: spend (swap receipts + cash buys) minus earnings (own items leaving) <= X_u
spend_data = {}  # user -> list of (coeff, var) for reporting
earn_data = {}   # user -> list of (coeff, var) for reporting
for u in users:
    spend = [(ask.get(iid, 0), v) for (iid, v) in spend_swap.get(u, []) if ask.get(iid, 0)]
    spend += [(ask.get(iid, 0), v) for (iid, v) in buys_by_user.get(u, []) if ask.get(iid, 0)]
    earn = []
    for iid in items_by_owner.get(u, []):
        z = ask.get(iid, 0)
        if z:
            earn += [(z, v) for v in in_terms.get(iid, [])]
            earn += [(z, v) for v in buy_terms.get(iid, [])]
    spend_data[u] = spend
    earn_data[u] = earn
    if u in budget and (spend or earn):
        lhs = gp.quicksum(c * v for c, v in spend) - gp.quicksum(c * v for c, v in earn)
        model.addConstr(lhs <= budget[u])

# Objective. Integer coefficients are essential: a fractional tie-break (e.g. weighting swaps by
# 1+eps) blocks Gurobi's integer-bound rounding and makes proving optimality 10-60x slower.
swaps = [v for k, v in edge_vars.items() if k not in hub_in_keys]  # hub in-spokes aren't trades
buys = list(buy.values())

_time_limit = os.environ.get("PARETO_TIME_LIMIT")
if _time_limit:
    model.Params.TimeLimit = float(_time_limit)
if os.environ.get("PARETO_MIPGAP"):
    model.Params.MIPGap = float(os.environ["PARETO_MIPGAP"])
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
need_participation = ("users" in _args.kpi) or os.environ.get("PARETO_STATS")
participation = {}
if need_participation:
    for u in users:
        part = [v for _, v in spend_swap.get(u, [])]      # receive via swap
        part += [v for _, v in buys_by_user.get(u, [])]   # receive via cash
        for j in items_by_owner.get(u, []):               # give: an owned item leaves
            part += in_terms.get(j, [])
            part += buy_terms.get(j, [])
        if part:
            participation[u] = part

# 'users' KPI: one binary per user that can be 1 only if the user has >= 1 trade.
traded = []
if "users" in _args.kpi:
    for u, part in participation.items():
        t = model.addVar(vtype=GRB.BINARY)
        model.addConstr(t <= gp.quicksum(part))
        traded.append(t)


_dist_cache = {}


def haversine_km(a, b):
    """Great-circle distance in integer km between (lat, lng) points a and b."""
    key = (a, b) if a <= b else (b, a)
    if key in _dist_cache:
        return _dist_cache[key]
    (lat1, lon1), (lat2, lon2) = a, b
    r1, r2 = math.radians(lat1), math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    h = math.sin(dlat / 2) ** 2 + math.cos(r1) * math.cos(r2) * math.sin(dlon / 2) ** 2
    km = round(2 * 6371 * math.asin(math.sqrt(h)))
    _dist_cache[key] = km
    return km


def distance_terms():
    """(coeff, var) for every item move: ship take-item from owner to receiver.
    Skips moves with an unknown owner or a missing location on either end."""
    terms = []

    def add(receiver, take_iid, var):
        o = owner.get(take_iid)
        if o is None or receiver not in location or o not in location:
            return
        d = haversine_km(location[o], location[receiver])
        if d:
            terms.append((d, var))

    for u, legs in spend_swap.items():          # swap receive-legs (simple + combo)
        for iid, v in legs:
            add(u, iid, v)
    for (u, iid), v in buy.items():             # cash buys
        add(u, iid, v)
    return terms


def kpi_expr(kpi):
    """Objective expression in MAXIMIZE form for one KPI."""
    if kpi == "trades":
        return gp.quicksum(swaps) + gp.quicksum(buys)
    if kpi == "users":
        return gp.quicksum(traded)
    if kpi == "distance":
        return -gp.quicksum(c * v for c, v in distance_terms())
    raise ValueError(f"unknown KPI: {kpi}")


# Lexicographic multi-objective: leftmost KPI = highest priority. All objectives
# share ModelSense; min-objectives (distance) are negated into maximize form.
model.ModelSense = GRB.MAXIMIZE
if len(_args.kpi) == 1:
    model.setObjective(kpi_expr(_args.kpi[0]), GRB.MAXIMIZE)
else:
    n = len(_args.kpi)
    for k, kpi in enumerate(_args.kpi):
        model.setObjectiveN(kpi_expr(kpi), index=k, priority=n - k)
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
    if len(_args.kpi) > 1:
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

model.optimize()
if fast_bound is not None and model.SolCount > 0:
    _gap = abs(fast_bound - model.ObjVal) / max(abs(fast_bound), 1.0)
    print(f"PARETO_FAST: obj={model.ObjVal:.0f} bound={fast_bound:.0f} gap={_gap:.4%}",
          file=sys.stderr)

_STATUS = {GRB.OPTIMAL: "Optimal", GRB.TIME_LIMIT: "TimeLimit", GRB.INFEASIBLE: "Infeasible"}
status = _STATUS.get(model.Status, f"Status{model.Status}")
if status != "Optimal":
    print(f"WARNING: solver status is {status}", file=sys.stderr)

if os.environ.get("PARETO_STATS"):
    have = model.SolCount > 0
    users_traded = (sum(1 for part in participation.values() if any(v.X > 0.5 for v in part))
                    if have else 0)
    # ObjVal / MIPGap are unavailable under multi-objective; report per-objective
    # values instead (distance is reported negated, i.e. in maximize form).
    if len(_args.kpi) == 1:
        obj_str = f"obj={model.ObjVal:.0f}" if have else "obj=nan"
        gap = f"{model.MIPGap:.4f}" if have else "nan"
    else:
        parts = []
        for k, kpi in enumerate(_args.kpi):
            model.params.ObjNumber = k
            val = f"{model.ObjNVal:.0f}" if have else "nan"
            parts.append(f"{kpi}={val}")
        obj_str = "obj[" + ",".join(parts) + "]"
        gap = "nan"
    print(
        f"STATS swap_vars={len(swaps)} buy_vars={len(buys)} combos={len(combo_records)} "
        f"items={len(real_item_ids)} users_traded={users_traded}/{len(users)} "
        f"status={status} {obj_str} gap={gap} runtime={model.Runtime:.3f}",
        file=sys.stderr,
    )

if model.SolCount == 0:
    print("No solution found.", file=sys.stderr)
    sys.exit(0)


def active(var):
    return var.X > 0.5


print("\nTrade Results:")
for (i, j), var in edge_vars.items():
    if active(var) and i in id_to_item and j in id_to_item:
        print(f"{id_to_item[j]} -> {id_to_item[i]}")

for in_pairs, out_pairs in combo_records:
    if any(active(v) for _, v in in_pairs + out_pairs):
        sent = [id_to_item[s] for s, v in out_pairs if active(v)]
        taken = [id_to_item[t] for t, v in in_pairs if active(v)]
        print(*sent, sep=' ', end='')
        print(" -> ", end='')
        print(*taken, sep=' ')

show_money = bool(buy) or bool(ask) or bool(budget)
if show_money:
    cash_moves = [(u, iid) for (u, iid), v in buy.items() if active(v)]
    if cash_moves:
        print("\nCash Purchases:")
        for (u, iid) in cash_moves:
            o = owner[iid]
            print(f"{id_to_item[iid]}: {o} -> {u}  ({u} pays {o} ${ask.get(iid, 0)})")

    # Per-user net: every active cash leg (swap take or buy) means the receiver owes
    # ask[item] to the item's owner. spent - earned > 0 => owes, < 0 => receives.
    net = {}
    print("\nCash Summary:")
    for u in sorted(users):
        spent = sum(c for c, v in spend_data[u] if active(v))
        earned = sum(c for c, v in earn_data[u] if active(v))
        net[u] = spent - earned
        cap = budget[u] if u in budget else "inf"
        direction = "owes" if net[u] > 0 else "receives" if net[u] < 0 else "even"
        print(f"  {u}: spent ${spent:g}, earned ${earned:g}, "
              f"net ${net[u]:g} ({direction}) (cap ${cap})")
    assert sum(net.values()) == 0, "cash nets must balance to zero"

    # Itemized payments: reconstruct who owes whom from the active cash legs, then net
    # pairwise so A<->B collapses to a single directed line. Traceable to the items.
    flows = {}  # (payer, payee) -> amount

    def add_flow(payer, payee, amt):
        if amt and payer != payee:
            flows[(payer, payee)] = flows.get((payer, payee), 0) + amt

    for u, legs in spend_swap.items():
        for iid, v in legs:
            if active(v):
                add_flow(u, owner[iid], ask.get(iid, 0))
    for (u, iid), v in buy.items():
        if active(v):
            add_flow(u, owner[iid], ask.get(iid, 0))

    printed = set()
    payment_lines = []
    for (a, b) in list(flows):
        if (a, b) in printed or (b, a) in printed:
            continue
        pair_net = flows.get((a, b), 0) - flows.get((b, a), 0)
        if pair_net > 0:
            payment_lines.append(f"  {a} pays {b} ${pair_net:g}")
        elif pair_net < 0:
            payment_lines.append(f"  {b} pays {a} ${-pair_net:g}")
        printed.add((a, b))
        printed.add((b, a))
    if payment_lines:
        print("\nPayments:")
        print(*payment_lines, sep="\n")

    # Settlement plan: money is fungible through the clearinghouse, so settle each
    # user's net with the fewest transfers (greedy largest-debtor vs largest-creditor).
    # NOTE: this pays different counterparties than Payments above; both are valid
    # executions of the same outcome (budgets constrain nets, not pairwise flows).
    debtors = sorted(((u, n) for u, n in net.items() if n > 0), key=lambda x: -x[1])
    creditors = sorted(((u, -n) for u, n in net.items() if n < 0), key=lambda x: -x[1])
    if debtors:
        print("\nSettlement plan:")
        i = j = 0
        while i < len(debtors) and j < len(creditors):
            du, dn = debtors[i]
            cu, cn = creditors[j]
            pay = min(dn, cn)
            print(f"  {du} pays {cu} ${pay:g}")
            debtors[i] = (du, dn - pay)
            creditors[j] = (cu, cn - pay)
            if debtors[i][1] == 0:
                i += 1
            if creditors[j][1] == 0:
                j += 1
