import re
import math
import argparse
from dataclasses import dataclass, field

import gurobipy as gp
from gurobipy import GRB


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


@dataclass
class Instance:
    item_to_id: dict = field(default_factory=dict)
    id_to_item: dict = field(default_factory=dict)
    wishes: list = field(default_factory=list)   # (user, give, take, N, M)
    users: set = field(default_factory=set)
    budget: dict = field(default_factory=dict)
    owner: dict = field(default_factory=dict)
    ask: dict = field(default_factory=dict)
    bids: dict = field(default_factory=dict)
    dup_groups: list = field(default_factory=list)
    location: dict = field(default_factory=dict)

    def intern(self, token):
        if token not in self.item_to_id:
            self.item_to_id[token] = len(self.item_to_id)
            self.id_to_item[self.item_to_id[token]] = token
        return self.item_to_id[token]


def intern_lookup(inst, token):
    """Test helper: id of an already-interned token."""
    return inst.item_to_id[token]


def _set_owner(inst, iid, u, line):
    if iid in inst.owner and inst.owner[iid] != u:
        raise ValueError(
            f"Item '{inst.id_to_item[iid]}' has conflicting owners "
            f"'{inst.owner[iid]}' and '{u}': {line}")
    inst.owner[iid] = u


def _parse_wish_body(inst, body, line):
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
            raise ValueError(
                f"Option must be in 'NforM' format, e.g., '2for1': {line}")
        N = int(match.group(1))
        M = int(match.group(2))
    give = [inst.intern(t) for t in groups[0]]
    take = [inst.intern(t) for t in groups[1]]
    return give, take, N, M


def parse(text):
    """Parse instance text into an Instance. Raises ValueError on bad input."""
    inst = Instance()
    for raw in text.splitlines():
        line = raw.partition('#')[0].strip()
        if not line:
            continue
        m_user = re.fullmatch(r'user\s+(\S+)\s+budget\s+(\d+)', line)
        m_item = re.fullmatch(
            r'item\s+(\S+)\s+owner\s+(\S+)(?:\s+ask\s+(\d+))?', line)
        m_bid = re.fullmatch(r'bid\s+(\S+)\s+(\S+)\s+(\d+)', line)
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
            _set_owner(inst, iid, u, raw)
            if m_item.group(3) is not None:
                inst.ask[iid] = int(m_item.group(3))
        elif m_bid:
            u = m_bid.group(1)
            inst.users.add(u)
            iid = inst.intern(m_bid.group(2))
            inst.bids[(u, iid)] = int(m_bid.group(3))
        elif m_dup:
            u = m_dup.group(1)
            inst.users.add(u)
            inst.dup_groups.append(
                (u, [inst.intern(t) for t in m_dup.group(2).split()]))
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
            give, take, N, M = _parse_wish_body(inst, body.strip(), raw)
            for g in give:
                _set_owner(inst, g, u, raw)
            inst.wishes.append((u, give, take, N, M))
        else:
            raise ValueError(f"Unrecognized line: {raw}")
    return inst


@dataclass
class Build:
    model: object
    env: object
    kpi: list
    edge_vars: dict
    combo_records: list
    spend_swap: dict
    buy: dict
    spend_data: dict
    earn_data: dict
    participation: dict
    swaps: list
    buys: list
    real_item_count: int


@dataclass
class Result:
    status: str
    money_present: bool
    has_solution: bool = True
    stats: dict = field(default_factory=dict)
    swaps: list = field(default_factory=list)
    combo_trades: list = field(default_factory=list)
    cash_purchases: list = field(default_factory=list)
    cash_summary: list = field(default_factory=list)
    payments: list = field(default_factory=list)
    settlement: list = field(default_factory=list)


def _silent_env():
    """A default-license Gurobi Env with the startup banner suppressed."""
    env = gp.Env(empty=True)
    env.setParam("OutputFlag", 0)
    env.start()
    return env


def build(inst, kpi, time_limit=None, mipgap=None, *,
          env=None, threads=8, want_stats=False):
    """Construct the MIP. Returns a Build carrying the model + var handles."""
    owned_env = None
    if env is None:
        env = owned_env = _silent_env()
    model = gp.Model(env=env)
    model.Params.OutputFlag = 0
    if threads:
        model.Params.Threads = threads

    edge_vars = {}        # (i, j) -> binary var
    combo_records = []    # list of (in_pairs, out_pairs); pair = (item_id, var)
    spend_swap = {}       # user -> list of (take_iid, take_var): swap receipt legs
    in_terms = {}         # item_id -> vars where the item is given away in a swap
    out_terms = {}        # item_id -> vars where the item is received in a swap

    combo_node_id = len(inst.item_to_id)

    def add_edge(i, j, var):
        edge_vars[(i, j)] = var
        out_terms.setdefault(i, []).append(var)
        in_terms.setdefault(j, []).append(var)

    # Swap / combo variables, recording per-user swap cash legs
    for user, send_ids, take_ids, N, M in inst.wishes:
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

        # No individual edge is active unless the whole combo is active
        active = model.addVar(vtype=GRB.BINARY)
        for v in out_vars:
            model.addConstr(v <= active)
        for v in in_vars:
            model.addConstr(v <= active)

        # Total outgoing (sent) <= N if active; at least 1 item is sent
        model.addConstr(gp.quicksum(out_vars) <= N * active)
        model.addConstr(active <= gp.quicksum(out_vars))

        # Total incoming (received) >= M if combo is active
        model.addConstr(M * active <= gp.quicksum(in_vars))

        combo_records.append((in_pairs, out_pairs))

    # Cash purchase variables: only for bids that clear the ask and aren't self-buys
    buy = {}
    for (u, iid), y in inst.bids.items():
        o = inst.owner.get(iid)
        if o is None:
            raise ValueError(
                f"Cannot bid on item '{inst.id_to_item[iid]}' with no declared owner")
        if u == o:
            continue  # don't buy your own item
        if iid not in inst.ask:
            continue          # no ask -> not for sale
        if y < inst.ask[iid]:
            continue  # bid doesn't clear the ask -> edge filtered out
        buy[(u, iid)] = model.addVar(vtype=GRB.BINARY)

    # A wish's take-item may also be acquired with cash (implicit bid at the ask),
    # funded by the net budget. Gated on money being present so pure-barter
    # instances keep strict swap reciprocity.
    money_present = bool(inst.ask) or bool(inst.budget) or bool(inst.bids)
    if money_present:
        for user, send_ids, take_ids, N, M in inst.wishes:
            for t in take_ids:
                if t not in inst.ask:
                    continue          # can't implicitly buy an unlisted item
                if user == inst.owner.get(t) or (user, t) in buy:
                    continue
                buy[(user, t)] = model.addVar(vtype=GRB.BINARY)

    buy_terms = {}  # item_id -> list of buy vars
    for (u, iid), v in buy.items():
        buy_terms.setdefault(iid, []).append(v)

    real_item_ids = set(inst.item_to_id.values())

    # Duplicate protection: a user receives at most one copy of a protected game,
    # counting swap receipts and cash buys together.
    for u, iids in inst.dup_groups:
        grp = set(iids)
        terms = [v for (it, v) in spend_swap.get(u, []) if it in grp]
        terms += [buy[(u, it)] for it in grp if (u, it) in buy]
        if len(terms) > 1:
            model.addConstr(gp.quicksum(terms) <= 1)

    # Swap balance kept; cash competes for the same single slot
    for node in real_item_ids:
        ins = in_terms.get(node, [])
        outs = out_terms.get(node, [])
        if ins and outs:
            model.addConstr(gp.quicksum(ins) == gp.quicksum(outs))
        elif ins:
            model.addConstr(gp.quicksum(ins) == 0)   # given but no swap wants it
        elif outs:
            model.addConstr(gp.quicksum(outs) == 0)  # wanted but never offered
        if ins:
            model.addConstr(gp.quicksum(ins) <= 1)
        if node in buy_terms:
            model.addConstr(gp.quicksum(outs) + gp.quicksum(buy_terms[node]) <= 1)

    # Bucket buys and items by user once (linear budget build)
    buys_by_user = {}    # user -> list of (item_id, var)
    for (u, iid), v in buy.items():
        buys_by_user.setdefault(u, []).append((iid, v))
    items_by_owner = {}  # user -> list of item_id
    for iid, o in inst.owner.items():
        items_by_owner.setdefault(o, []).append(iid)

    # Per-user net budget: spend (receipts + buys) - earnings (own items leaving) <= X_u
    spend_data = {}  # user -> list of (coeff, var) for reporting
    earn_data = {}   # user -> list of (coeff, var) for reporting
    for u in inst.users:
        spend = [(inst.ask.get(iid, 0), v) for (iid, v) in spend_swap.get(u, [])
                 if inst.ask.get(iid, 0)]
        spend += [(inst.ask.get(iid, 0), v) for (iid, v) in buys_by_user.get(u, [])
                  if inst.ask.get(iid, 0)]
        earn = []
        for iid in items_by_owner.get(u, []):
            z = inst.ask.get(iid, 0)
            if z:
                earn += [(z, v) for v in in_terms.get(iid, [])]
                earn += [(z, v) for v in buy_terms.get(iid, [])]
        spend_data[u] = spend
        earn_data[u] = earn
        if u in inst.budget and (spend or earn):
            lhs = gp.quicksum(c * v for c, v in spend) - gp.quicksum(c * v for c, v in earn)
            model.addConstr(lhs <= inst.budget[u])

    swaps = list(edge_vars.values())
    buys = list(buy.values())

    if time_limit:
        model.Params.TimeLimit = float(time_limit)
    if mipgap is not None:
        model.Params.MIPGap = float(mipgap)

    # Per-user participation vars: a user participates if they receive any item
    # (swap take or cash buy) or give an owned item away. Used for the 'users'
    # KPI and the users_traded report; skip the work when neither is requested.
    need_participation = ("users" in kpi) or want_stats
    participation = {}
    if need_participation:
        for u in inst.users:
            part = [v for _, v in spend_swap.get(u, [])]      # receive via swap
            part += [v for _, v in buys_by_user.get(u, [])]   # receive via cash
            for j in items_by_owner.get(u, []):               # give: owned item leaves
                part += in_terms.get(j, [])
                part += buy_terms.get(j, [])
            if part:
                participation[u] = part

    # 'users' KPI: one binary per user that can be 1 only with >= 1 trade.
    traded = []
    if "users" in kpi:
        for u, part in participation.items():
            t = model.addVar(vtype=GRB.BINARY)
            model.addConstr(t <= gp.quicksum(part))
            traded.append(t)

    dist_cache = {}

    def haversine_km(a, b):
        """Great-circle distance in integer km between (lat, lng) points."""
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

        for u, legs in spend_swap.items():          # swap receive-legs
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

    # Lexicographic multi-objective: leftmost KPI = highest priority. All share
    # ModelSense; min-objectives (distance) are negated into maximize form.
    model.ModelSense = GRB.MAXIMIZE
    if len(kpi) == 1:
        model.setObjective(kpi_expr(kpi[0]), GRB.MAXIMIZE)
    else:
        n = len(kpi)
        for k, name in enumerate(kpi):
            model.setObjectiveN(kpi_expr(name), index=k, priority=n - k)

    return Build(
        model=model, env=owned_env, kpi=kpi, edge_vars=edge_vars,
        combo_records=combo_records, spend_swap=spend_swap, buy=buy,
        spend_data=spend_data, earn_data=earn_data, participation=participation,
        swaps=swaps, buys=buys, real_item_count=len(real_item_ids),
    )


_STATUS = {GRB.OPTIMAL: "Optimal", GRB.TIME_LIMIT: "TimeLimit",
           GRB.INFEASIBLE: "Infeasible"}


def _active(var):
    return var.X > 0.5


def collect(inst, b, kpi, want_stats):
    """Read the optimized model into a Result."""
    model = b.model
    status = _STATUS.get(model.Status, f"Status{model.Status}")
    have = model.SolCount > 0
    show_money = bool(b.buy) or bool(inst.ask) or bool(inst.budget)
    res = Result(status=status, money_present=show_money, has_solution=have)

    if want_stats:
        users_traded = (sum(1 for part in b.participation.values()
                            if any(v.X > 0.5 for v in part)) if have else 0)
        if len(kpi) == 1:
            obj = round(model.ObjVal) if have else None
            gap = round(model.MIPGap, 4) if have else None
        else:
            obj = {}
            for k, name in enumerate(kpi):
                model.params.ObjNumber = k
                obj[name] = round(model.ObjNVal) if have else None
            gap = None
        res.stats = {
            "swap_vars": len(b.swaps), "buy_vars": len(b.buys),
            "combos": len(b.combo_records), "items": b.real_item_count,
            "users_traded": users_traded, "total_users": len(inst.users),
            "status": status, "obj": obj, "gap": gap,
            "runtime": round(model.Runtime, 3),
        }

    if not have:
        return res

    for (i, j), var in b.edge_vars.items():
        if _active(var) and i in inst.id_to_item and j in inst.id_to_item:
            res.swaps.append({"give": inst.id_to_item[j],
                              "receive": inst.id_to_item[i]})

    for in_pairs, out_pairs in b.combo_records:
        if any(_active(v) for _, v in in_pairs + out_pairs):
            res.combo_trades.append({
                "sent": [inst.id_to_item[s] for s, v in out_pairs if _active(v)],
                "taken": [inst.id_to_item[t] for t, v in in_pairs if _active(v)],
            })

    if not show_money:
        return res

    for (u, iid), v in b.buy.items():
        if _active(v):
            res.cash_purchases.append({
                "item": inst.id_to_item[iid], "from": inst.owner[iid],
                "to": u, "price": inst.ask.get(iid, 0)})

    net = {}
    for u in sorted(inst.users):
        spent = sum(c for c, v in b.spend_data[u] if _active(v))
        earned = sum(c for c, v in b.earn_data[u] if _active(v))
        net[u] = spent - earned
        cap = inst.budget[u] if u in inst.budget else "inf"
        direction = ("owes" if net[u] > 0 else
                     "receives" if net[u] < 0 else "even")
        res.cash_summary.append({
            "user": u, "spent": spent, "earned": earned,
            "net": net[u], "direction": direction, "cap": cap})
    assert sum(net.values()) == 0, "cash nets must balance to zero"

    flows = {}

    def add_flow(payer, payee, amt):
        if amt and payer != payee:
            flows[(payer, payee)] = flows.get((payer, payee), 0) + amt

    for u, legs in b.spend_swap.items():
        for iid, v in legs:
            if _active(v):
                add_flow(u, inst.owner[iid], inst.ask.get(iid, 0))
    for (u, iid), v in b.buy.items():
        if _active(v):
            add_flow(u, inst.owner[iid], inst.ask.get(iid, 0))

    printed = set()
    for (a, bb) in list(flows):
        if (a, bb) in printed or (bb, a) in printed:
            continue
        pair_net = flows.get((a, bb), 0) - flows.get((bb, a), 0)
        if pair_net > 0:
            res.payments.append({"payer": a, "payee": bb, "amount": pair_net})
        elif pair_net < 0:
            res.payments.append({"payer": bb, "payee": a, "amount": -pair_net})
        printed.add((a, bb))
        printed.add((bb, a))

    debtors = sorted(((u, n) for u, n in net.items() if n > 0), key=lambda x: -x[1])
    creditors = sorted(((u, -n) for u, n in net.items() if n < 0), key=lambda x: -x[1])
    i = j = 0
    while i < len(debtors) and j < len(creditors):
        du, dn = debtors[i]
        cu, cn = creditors[j]
        pay = min(dn, cn)
        res.settlement.append({"payer": du, "payee": cu, "amount": pay})
        debtors[i] = (du, dn - pay)
        creditors[j] = (cu, cn - pay)
        if debtors[i][1] == 0:
            i += 1
        if creditors[j][1] == 0:
            j += 1
    return res


def solve(text, kpi=("trades",), time_limit=None, mipgap=None, *,
          env=None, threads=8, want_stats=False):
    """Parse, build, optimize, collect. Returns a Result."""
    kpi = list(kpi)
    inst = parse(text)
    b = build(inst, kpi, time_limit, mipgap,
              env=env, threads=threads, want_stats=want_stats)
    b.model.optimize()
    return collect(inst, b, kpi, want_stats)
