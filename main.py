import sys
import os
import re
import math
import argparse
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
dup_groups = []  # list of (user, [item_id, ...]); user receives <=1 of these copies
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
            elif m_dup:
                u = m_dup.group(1)
                users.add(u)
                dup_groups.append((u, [intern(t) for t in m_dup.group(2).split()]))
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
model.Params.OutputFlag = 0

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


# Build swap / combo variables (unchanged barter structure), recording per-user swap cash legs
for user, send_ids, take_ids, N, M in wishes:
    if len(send_ids) == len(take_ids) == 1:
        e = model.addVar(vtype=GRB.BINARY)
        add_edge(take_ids[0], send_ids[0], e)
        spend_swap.setdefault(user, []).append((take_ids[0], e))
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
for u, iids in dup_groups:
    grp = set(iids)
    terms = [v for (it, v) in spend_swap.get(u, []) if it in grp]
    terms += [buy[(u, it)] for it in grp if (u, it) in buy]
    if len(terms) > 1:
        model.addConstr(gp.quicksum(terms) <= 1)

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
swaps = list(edge_vars.values())
buys = list(buy.values())

_time_limit = os.environ.get("PARETO_TIME_LIMIT")
if _time_limit:
    model.Params.TimeLimit = float(_time_limit)
if os.environ.get("PARETO_MIPGAP"):
    model.Params.MIPGap = float(os.environ["PARETO_MIPGAP"])

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
model.optimize()

_STATUS = {GRB.OPTIMAL: "Optimal", GRB.TIME_LIMIT: "TimeLimit", GRB.INFEASIBLE: "Infeasible"}
status = _STATUS.get(model.Status, f"Status{model.Status}")
if status != "Optimal":
    print(f"WARNING: solver status is {status}", file=sys.stderr)

if os.environ.get("PARETO_STATS"):
    obj = model.ObjVal if model.SolCount > 0 else float("nan")
    gap = model.MIPGap if model.SolCount > 0 else float("nan")
    users_traded = (sum(1 for part in participation.values() if any(v.X > 0.5 for v in part))
                    if model.SolCount > 0 else 0)
    print(
        f"STATS swap_vars={len(swaps)} buy_vars={len(buys)} combos={len(combo_records)} "
        f"items={len(real_item_ids)} users_traded={users_traded}/{len(users)} "
        f"status={status} obj={obj:.0f} gap={gap:.4f} runtime={model.Runtime:.3f}",
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
