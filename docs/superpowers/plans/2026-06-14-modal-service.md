# Pareto as a Modal.com service — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor the top-level `main.py` script into an importable solver package and host it on Modal as a proxy-auth'd HTTP endpoint with async submit/poll, backed by a Gurobi WLS license.

**Architecture:** `pareto_core.py` holds the pure solver (`parse → build → solve → Result`); `serialize.py` renders a `Result` as today's text (CLI) or as structured JSON (service); `main.py` becomes a thin CLI wrapper; `modal_app.py` runs a `cpu=8` / `Threads=8` worker behind a FastAPI ASGI endpoint that spawns jobs and polls them through Modal's `FunctionCall`.

**Tech Stack:** Python 3, `gurobipy==13.0.2`, Modal, FastAPI. Tests follow the existing convention: plain `assert` scripts with an `if __name__ == "__main__"` runner, run via `python test_<x>.py` (no pytest).

**Spec:** `docs/superpowers/specs/2026-06-14-modal-service-design.md`

---

## File structure

| File | Responsibility | Status |
|---|---|---|
| `pareto_core.py` | `Instance`, `Build`, `Result` dataclasses; `parse`, `build`, `solve`, `collect`, `parse_kpi_list`, `ALLOWED_KPIS` | create |
| `serialize.py` | `to_text(Result) -> str`, `to_dict(Result) -> dict` | create |
| `main.py` | thin CLI: flags/env → `solve()` → `to_text()` (back-compat) | rewrite |
| `modal_app.py` | Modal worker + FastAPI ASGI endpoint (`POST /solve`, `GET /result/{id}`); `validate_request` | create |
| `testcases/golden/*.out` | captured CLI stdout baseline (the refactor contract) | create |
| `test_refactor_golden.py` | `to_text(solve(...))` == golden, per testcase | create |
| `test_core.py` | unit tests for `parse` / `solve` / `to_dict` | create |
| `test_validate.py` | unit tests for `validate_request` | create |
| `tools/capture_golden.py` | regenerates `testcases/golden/*.out` from current CLI | create |
| `README.md` | document the Modal service + deploy | modify |
| `requirements.txt` | unchanged runtime pin (note `modal` is deploy-only) | inspect |

**Determinism note:** Gurobi is deterministic for a fixed thread count, version, and platform. Golden files are captured AND verified in the same environment. If a golden test fails after the refactor, suspect a behavioural change in the move, not solver noise — investigate before regenerating.

---

## Task 1: Capture the golden CLI baseline

Locks the current behaviour of `main.py` as committed text files **before** any refactor, so later tasks can prove byte-identical output.

**Files:**
- Create: `tools/capture_golden.py`
- Create: `testcases/golden/*.out` (generated)

- [ ] **Step 1: Write the capture tool**

Create `tools/capture_golden.py`:

```python
"""Regenerate testcases/golden/<name>.out from the current main.py CLI.

Run BEFORE refactoring to snapshot behaviour, and only re-run intentionally
when output is meant to change. Golden files are the refactor contract.
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
MAIN = os.path.join(ROOT, "main.py")
TC = os.path.join(ROOT, "testcases")
OUT = os.path.join(TC, "golden")


def cases():
    for dirpath, _, names in os.walk(TC):
        if os.path.abspath(dirpath).startswith(os.path.abspath(OUT)):
            continue
        for n in sorted(names):
            if n.endswith(".txt"):
                yield os.path.join(dirpath, n)


def main():
    os.makedirs(OUT, exist_ok=True)
    for path in sorted(cases()):
        rel = os.path.relpath(path, TC)[:-4].replace(os.sep, "__")
        r = subprocess.run([sys.executable, MAIN, path],
                           capture_output=True, text=True, check=True)
        with open(os.path.join(OUT, rel + ".out"), "w") as f:
            f.write(r.stdout)
        print("wrote", rel + ".out")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Generate the golden files against the ORIGINAL main.py**

Run: `python tools/capture_golden.py`
Expected: one `wrote <name>.out` line per `.txt` in `testcases/` (incl. `money/`), e.g. `1for1`, `money__swap`, etc. Files land in `testcases/golden/`.

- [ ] **Step 3: Sanity-check a golden file**

Run: `cat testcases/golden/money__cashchain.out`
Expected: the familiar `Trade Results` / `Cash Purchases` / `Cash Summary` / `Settlement plan` text — non-empty.

- [ ] **Step 4: Commit**

```bash
git add tools/capture_golden.py testcases/golden
git commit -m "test: capture golden CLI baseline before Modal refactor"
```

---

## Task 2: `pareto_core` — `Instance` + `parse()`

Move parsing off module globals into a returned `Instance`. No model building yet.

**Files:**
- Create: `pareto_core.py`
- Create: `test_core.py`

- [ ] **Step 1: Write the failing parse test**

Create `test_core.py`:

```python
"""In-process unit tests for pareto_core and serialize (no subprocess)."""
import pareto_core as C


SWAP = "alice: (1for1) A -> B\nbob: (1for1) B -> A\n"

MONEY = (
    "item C1 owner bob ask 10\n"
    "bid alice C1 20\n"
)


def test_parse_swap():
    inst = C.parse(SWAP)
    assert inst.users == {"alice", "bob"}, inst.users
    assert len(inst.wishes) == 2, inst.wishes
    # giving an item implies owning it
    a = C.intern_lookup(inst, "A")
    assert inst.owner[a] == "alice", inst.owner


def test_parse_money():
    inst = C.parse(MONEY)
    c1 = C.intern_lookup(inst, "C1")
    assert inst.owner[c1] == "bob"
    assert inst.ask[c1] == 10
    assert inst.bids[("alice", c1)] == 20


def test_parse_bad_latitude():
    try:
        C.parse("location alice 999 0\nalice: (1for1) A -> B\n")
    except ValueError as e:
        assert "latitude" in str(e)
    else:
        raise AssertionError("expected ValueError")


def test_kpi_list_validation():
    assert C.parse_kpi_list("trades,users") == ["trades", "users"]
    for bad in ("trades,bogus", "trades,trades", "trades,,users"):
        try:
            C.parse_kpi_list(bad)
        except Exception:
            pass
        else:
            raise AssertionError(f"expected rejection of {bad!r}")


if __name__ == "__main__":
    test_parse_swap()
    test_parse_money()
    test_parse_bad_latitude()
    test_kpi_list_validation()
    print("OK: parse tests passed")
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python test_core.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'pareto_core'`.

- [ ] **Step 3: Create `pareto_core.py` with `Instance` + `parse()`**

Create `pareto_core.py`. Port the parsing logic from the current `main.py` (lines 9–116 and the `parse_kpi_list`/`ALLOWED_KPIS` block at 121–137), converting module globals into an `Instance` and reading from a string:

```python
import re
import math
import argparse
from dataclasses import dataclass, field


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
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python test_core.py`
Expected: `OK: parse tests passed`.

- [ ] **Step 5: Commit**

```bash
git add pareto_core.py test_core.py
git commit -m "feat: pareto_core.parse builds an Instance from text"
```

---

## Task 3: `pareto_core` — `build()`, `solve()`, `collect()`, `Result`

Move the MIP build, optimize, and result collection into functions. `solve()` returns a structured `Result`.

**Files:**
- Modify: `pareto_core.py`
- Modify: `test_core.py`

- [ ] **Step 1: Add the failing solve test**

Append to `test_core.py` (and add the calls in `__main__`):

```python
def test_solve_swap_trades():
    res = C.solve(SWAP, kpi=["trades"], want_stats=True)
    assert res.status == "Optimal", res.status
    pairs = {(s["give"], s["receive"]) for s in res.swaps}
    assert ("A", "B") in pairs and ("B", "A") in pairs, res.swaps
    assert res.stats["swap_vars"] == 2, res.stats
    assert res.stats["obj"] == 2, res.stats
    assert res.money_present is False


def test_solve_money_buy():
    res = C.solve(MONEY, kpi=["trades"], want_stats=True)
    assert res.money_present is True
    items = {p["item"] for p in res.cash_purchases}
    assert "C1" in items, res.cash_purchases
    summ = {r["user"]: r for r in res.cash_summary}
    assert summ["alice"]["spent"] == 10 and summ["alice"]["net"] == 10
    assert any(p["payer"] == "alice" and p["payee"] == "bob"
               for p in res.payments), res.payments
```

Add to the `__main__` block: `test_solve_swap_trades()` and `test_solve_money_buy()`.

- [ ] **Step 2: Run to verify it fails**

Run: `python test_core.py`
Expected: FAIL — `AttributeError: module 'pareto_core' has no attribute 'solve'`.

- [ ] **Step 3: Add `Build`, `Result`, `build`, `collect`, `solve` to `pareto_core.py`**

Add `import gurobipy as gp` and `from gurobipy import GRB` at the top. Append the following.

`build()` ports `main.py` lines 149–389 (variable/constraint construction, participation vars, KPI objective, lexicographic setup). Apply this mechanical substitution while moving:

| In old `main.py` | Becomes |
|---|---|
| `item_to_id`, `id_to_item` | `inst.item_to_id`, `inst.id_to_item` |
| `wishes`, `users`, `budget`, `owner`, `ask`, `bids`, `dup_groups`, `location` | `inst.<name>` |
| `_args.kpi` | `kpi` (param) |
| `os.environ.get("PARETO_TIME_LIMIT")` / `_MIPGAP` | `time_limit` / `mipgap` params |
| `os.environ.get("PARETO_STATS")` | `want_stats` param |
| `gp.Model()` | `gp.Model(env=env) if env else gp.Model()` |
| (new) | `model.Params.Threads = threads` after `OutputFlag = 0` |

All locals the build creates that collection needs (`edge_vars`, `combo_records`, `spend_swap`, `buy`, `spend_data`, `earn_data`, `participation`, `swaps`, `buys`) are returned in a `Build`. The objective wiring (lines 369–389) and `haversine_km`/`distance_terms`/`kpi_expr` helpers (lines 330–377) move verbatim into `build`, reading `inst`.

```python
@dataclass
class Build:
    model: object
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
    stats: dict = field(default_factory=dict)
    swaps: list = field(default_factory=list)
    combo_trades: list = field(default_factory=list)
    cash_purchases: list = field(default_factory=list)
    cash_summary: list = field(default_factory=list)
    payments: list = field(default_factory=list)
    settlement: list = field(default_factory=list)


def build(inst, kpi, time_limit=None, mipgap=None, *,
          env=None, threads=8, want_stats=False):
    """Construct the MIP. Returns a Build carrying the model + var handles.

    Body is main.py lines 149-389 with globals replaced per the substitution
    table in the plan. Key changes from the original:
      - model = gp.Model(env=env) if env is not None else gp.Model()
      - model.Params.Threads = threads   (after OutputFlag = 0)
      - time_limit / mipgap / want_stats come from params, not os.environ
      - participation is built when ('users' in kpi) or want_stats
    Returns Build(model, kpi, edge_vars, combo_records, spend_swap, buy,
                  spend_data, earn_data, participation, swaps, buys,
                  real_item_count=len(real_item_ids)).
    """
    ...  # see substitution table; ends by returning the Build above
```

`collect()` is **new** code — it replaces the print section (`main.py` 391–517) with data construction, reading `var.X` after optimize:

```python
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
    res = Result(status=status, money_present=show_money)

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
```

> While moving the build body, delete the now-dead `os`/`sys`/`argparse._args` references — `build` is import-safe and must not read `os.environ` or `sys.argv`.

- [ ] **Step 4: Run the test to verify it passes**

Run: `python test_core.py`
Expected: `OK: parse tests passed` (and the two new solve tests pass silently before it).

- [ ] **Step 5: Commit**

```bash
git add pareto_core.py test_core.py
git commit -m "feat: pareto_core.solve returns a structured Result"
```

---

## Task 4: `serialize.to_text` + golden test

Reproduce the current stdout byte-for-byte from a `Result`.

**Files:**
- Create: `serialize.py`
- Create: `test_refactor_golden.py`

- [ ] **Step 1: Write the failing golden test**

Create `test_refactor_golden.py`:

```python
"""to_text(solve(text)) must reproduce the captured CLI golden exactly."""
import os
import pareto_core as C
import serialize as S

HERE = os.path.dirname(os.path.abspath(__file__))
TC = os.path.join(HERE, "testcases")
GOLD = os.path.join(TC, "golden")


def cases():
    for dirpath, _, names in os.walk(TC):
        if os.path.abspath(dirpath).startswith(os.path.abspath(GOLD)):
            continue
        for n in sorted(names):
            if n.endswith(".txt"):
                rel = os.path.relpath(os.path.join(dirpath, n), TC)
                yield os.path.join(dirpath, n), rel[:-4].replace(os.sep, "__")


def test_golden():
    failures = []
    for path, name in sorted(cases()):
        with open(path) as f:
            text = f.read()
        got = S.to_text(C.solve(text, kpi=["trades"]))
        with open(os.path.join(GOLD, name + ".out")) as f:
            want = f.read()
        if got != want:
            failures.append(name)
    assert not failures, f"golden mismatch: {failures}"


if __name__ == "__main__":
    test_golden()
    print("OK: golden text matches baseline")
```

- [ ] **Step 2: Run to verify it fails**

Run: `python test_refactor_golden.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'serialize'`.

- [ ] **Step 3: Write `serialize.to_text`**

Create `serialize.py`. `to_text` mirrors `main.py` lines 429–517 exactly, but reads a `Result` instead of live vars. **Match the original whitespace** — leading `\n` before `Trade Results:`, two-space indents in summaries, `:g` number formatting:

```python
def to_text(res):
    """Render a Result as the original CLI stdout (byte-identical)."""
    out = []
    out.append("\nTrade Results:")
    for s in res.swaps:
        out.append(f"{s['give']} -> {s['receive']}")
    for c in res.combo_trades:
        out.append(" ".join(c["sent"]) + " -> " + " ".join(c["taken"]))

    if res.money_present:
        if res.cash_purchases:
            out.append("\nCash Purchases:")
            for p in res.cash_purchases:
                out.append(f"{p['item']}: {p['from']} -> {p['to']}  "
                           f"({p['to']} pays {p['from']} ${p['price']})")
        out.append("\nCash Summary:")
        for r in res.cash_summary:
            cap = r["cap"]
            out.append(f"  {r['user']}: spent ${r['spent']:g}, "
                       f"earned ${r['earned']:g}, net ${r['net']:g} "
                       f"({r['direction']}) (cap ${cap})")
        if res.payments:
            out.append("\nPayments:")
            for p in res.payments:
                out.append(f"  {p['payer']} pays {p['payee']} ${p['amount']:g}")
        if res.settlement:
            out.append("\nSettlement plan:")
            for p in res.settlement:
                out.append(f"  {p['payer']} pays {p['payee']} ${p['amount']:g}")

    return "\n".join(out) + "\n"
```

> Verify the trailing-newline shape against a golden file: original used `print()` per line, so output ends with a newline and section headers are preceded by a blank line via the leading `\n`. Adjust join/terminator only if a diff appears.

- [ ] **Step 4: Run the golden test**

Run: `python test_refactor_golden.py`
Expected: `OK: golden text matches baseline`.
If a single case diffs, run `diff <(python -c "import pareto_core as C, serialize as S; print(S.to_text(C.solve(open('testcases/<f>.txt').read())), end='')") testcases/golden/<name>.out` and fix the whitespace in `to_text`.

- [ ] **Step 5: Commit**

```bash
git add serialize.py test_refactor_golden.py
git commit -m "feat: serialize.to_text reproduces CLI output; golden test"
```

---

## Task 5: Rewrite `main.py` as a thin CLI

Replace the script body with a wrapper over `solve()` + `to_text()`, preserving every flag, env var, and stderr message.

**Files:**
- Rewrite: `main.py`

- [ ] **Step 1: Replace `main.py` entirely**

```python
import sys
import os
import argparse

import pareto_core as C
from serialize import to_text


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("--kpi", type=C.parse_kpi_list, default=["trades"],
                    help="comma-separated objectives in priority order "
                         "(leftmost optimized first), e.g. 'trades,users'. "
                         "Choices: 'trades' = max total trades (default); "
                         "'users' = max users with >= 1 trade; "
                         "'distance' = min total shipping distance (km).")
    args = ap.parse_args()

    with open(args.file) as f:
        text = f.read()

    time_limit = os.environ.get("PARETO_TIME_LIMIT")
    mipgap = os.environ.get("PARETO_MIPGAP")
    want_stats = bool(os.environ.get("PARETO_STATS"))

    res = C.solve(
        text, kpi=args.kpi,
        time_limit=float(time_limit) if time_limit else None,
        mipgap=float(mipgap) if mipgap else None,
        threads=0,                # 0 = Gurobi default (match pre-refactor CLI)
        want_stats=want_stats,
    )

    if res.status != "Optimal":
        print(f"WARNING: solver status is {res.status}", file=sys.stderr)

    if want_stats:
        s = res.stats
        if isinstance(s["obj"], dict):
            parts = ",".join(f"{k}={'nan' if v is None else v}"
                             for k, v in s["obj"].items())
            obj_str = f"obj[{parts}]"
            gap = "nan"
        else:
            obj_str = f"obj={'nan' if s['obj'] is None else s['obj']}"
            gap = "nan" if s["gap"] is None else f"{s['gap']:.4f}"
        print(
            f"STATS swap_vars={s['swap_vars']} buy_vars={s['buy_vars']} "
            f"combos={s['combos']} items={s['items']} "
            f"users_traded={s['users_traded']}/{s['total_users']} "
            f"status={s['status']} {obj_str} gap={gap} "
            f"runtime={s['runtime']:.3f}",
            file=sys.stderr,
        )

    # No solution: original printed this to stderr and exited 0 before output.
    if res.status != "Optimal" and not res.swaps and not res.combo_trades \
            and not res.cash_purchases:
        # collect() returns empty lists when SolCount == 0
        if not res.cash_summary:
            print("No solution found.", file=sys.stderr)
            sys.exit(0)

    print(to_text(res), end="")


if __name__ == "__main__":
    main()
```

> `threads=0` keeps the CLI on Gurobi's default thread count, matching the golden capture (the Modal worker overrides to 8). Confirm `build` treats `threads=0` as "don't constrain" — set `model.Params.Threads = threads` only when `threads`; i.e. guard with `if threads: model.Params.Threads = threads`.

- [ ] **Step 2: Adjust the threads guard in `pareto_core.build`**

In `build`, ensure:

```python
model.Params.OutputFlag = 0
if threads:
    model.Params.Threads = threads
```

- [ ] **Step 3: Verify the STATS / "No solution" parity**

The original's `No solution found.` path (main.py 420–422) fires when `SolCount == 0`. Simplify the CLI guard to match exactly by exposing it on the Result. Add to `Result`: `has_solution: bool = True`, set `res.has_solution = have` in `collect` right after computing `have`. Then replace the CLI's no-solution block with:

```python
    if not res.has_solution:
        print("No solution found.", file=sys.stderr)
        sys.exit(0)
```

(Remove the heuristic block from Step 1.)

- [ ] **Step 4: Run the full existing + new suite**

Run:
```bash
python test_refactor_golden.py && \
python test_core.py && \
python test_dupcap.py && \
python test_kpi_distance.py
```
Expected: each prints its `OK:` line. Golden still matches; legacy subprocess tests still green.

- [ ] **Step 5: Spot-check STATS parity**

Run: `PARETO_STATS=1 python main.py testcases/money/cashchain.txt 1>/dev/null`
Expected: a `STATS swap_vars=… status=Optimal obj=… gap=… runtime=…` line, same shape as before the refactor.

- [ ] **Step 6: Commit**

```bash
git add main.py pareto_core.py
git commit -m "refactor: main.py is a thin CLI over pareto_core + serialize"
```

---

## Task 6: `serialize.to_dict` + shape test

Structured JSON body for the service.

**Files:**
- Modify: `serialize.py`
- Modify: `test_core.py`

- [ ] **Step 1: Add the failing to_dict test**

Append to `test_core.py` and call from `__main__`:

```python
import serialize as S


def test_to_dict_money():
    d = S.to_dict(C.solve(MONEY, kpi=["trades"], want_stats=True))
    assert d["status"] == "Optimal"
    assert d["money_present"] is True
    assert {"swaps", "combo_trades", "cash_purchases", "cash_summary",
            "payments", "settlement", "stats"} <= set(d), d.keys()
    assert any(p["item"] == "C1" for p in d["cash_purchases"]), d
    assert d["stats"]["obj"] == 1, d["stats"]


def test_to_dict_barter():
    d = S.to_dict(C.solve(SWAP, kpi=["trades"]))
    assert d["money_present"] is False
    assert d["cash_purchases"] == [] and d["cash_summary"] == []
    assert {(s["give"], s["receive"]) for s in d["swaps"]} == {("A", "B"), ("B", "A")}
```

- [ ] **Step 2: Run to verify it fails**

Run: `python test_core.py`
Expected: FAIL — `AttributeError: module 'serialize' has no attribute 'to_dict'`.

- [ ] **Step 3: Add `to_dict` to `serialize.py`**

```python
from dataclasses import asdict


def to_dict(res):
    """Structured JSON-ready dict for the HTTP service."""
    d = asdict(res)
    d.pop("has_solution", None)   # internal flag, not part of the API
    return d
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python test_core.py`
Expected: `OK: parse tests passed`.

- [ ] **Step 5: Commit**

```bash
git add serialize.py test_core.py
git commit -m "feat: serialize.to_dict emits structured JSON body"
```

---

## Task 7: Request validation

Pure, import-safe validator for `POST /solve` payloads — testable without Modal.

**Files:**
- Create: `modal_app.py` (validation portion only this task)
- Create: `test_validate.py`

- [ ] **Step 1: Write the failing validation test**

Create `test_validate.py`:

```python
"""Unit tests for the request validator (no Modal import side effects)."""
from modal_app import validate_request, ValidationError


def ok(payload):
    return validate_request(payload)


def bad(payload, needle):
    try:
        validate_request(payload)
    except ValidationError as e:
        assert needle in str(e), (needle, str(e))
    else:
        raise AssertionError(f"expected ValidationError for {payload}")


def test_minimal_ok():
    p = ok({"instance": "alice: (1for1) A -> B\n"})
    assert p["kpi"] == ["trades"]
    assert p["time_limit"] is None and p["mipgap"] is None
    assert p["want_stats"] is False


def test_full_ok():
    p = ok({"instance": "x", "kpi": "trades,users",
            "time_limit": 30, "mipgap": 0.01, "stats": True})
    assert p["kpi"] == ["trades", "users"]
    assert p["time_limit"] == 30 and p["want_stats"] is True


def test_missing_instance():
    bad({}, "instance")
    bad({"instance": ""}, "instance")


def test_bad_kpi():
    bad({"instance": "x", "kpi": "trades,bogus"}, "bogus")


def test_time_limit_cap():
    bad({"instance": "x", "time_limit": 99999}, "time_limit")
    bad({"instance": "x", "time_limit": -1}, "time_limit")


def test_instance_too_big():
    bad({"instance": "x" * (1024 * 1024 + 1)}, "too large")


def test_bad_mipgap():
    bad({"instance": "x", "mipgap": -0.5}, "mipgap")


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("OK: validation tests passed")
```

- [ ] **Step 2: Run to verify it fails**

Run: `python test_validate.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'modal_app'`.

- [ ] **Step 3: Create `modal_app.py` with the validator at top**

Keep the validator above any Modal decorators so it imports cleanly in tests. Modal wiring is added in Task 8.

```python
import os
import pareto_core as C

MAX_INSTANCE_BYTES = int(os.environ.get("PARETO_MAX_INSTANCE_BYTES",
                                        str(1024 * 1024)))   # 1 MiB
MAX_TIME_LIMIT = float(os.environ.get("PARETO_MAX_TIME_LIMIT", "600"))


class ValidationError(Exception):
    """Raised for a malformed POST /solve payload (-> HTTP 400)."""


def validate_request(payload):
    """Validate + normalize a /solve JSON body. Returns a clean dict
    {instance, kpi, time_limit, mipgap, want_stats} or raises ValidationError.
    """
    if not isinstance(payload, dict):
        raise ValidationError("body must be a JSON object")

    instance = payload.get("instance")
    if not isinstance(instance, str) or not instance.strip():
        raise ValidationError("'instance' is required and must be non-empty")
    if len(instance.encode("utf-8")) > MAX_INSTANCE_BYTES:
        raise ValidationError(f"'instance' too large (max {MAX_INSTANCE_BYTES} bytes)")

    kpi_raw = payload.get("kpi", "trades")
    if isinstance(kpi_raw, list):
        kpi_raw = ",".join(kpi_raw)
    if not isinstance(kpi_raw, str):
        raise ValidationError("'kpi' must be a string or list of strings")
    try:
        kpi = C.parse_kpi_list(kpi_raw)
    except Exception as e:
        raise ValidationError(f"invalid 'kpi': {e}")

    time_limit = payload.get("time_limit")
    if time_limit is not None:
        if not isinstance(time_limit, (int, float)) or isinstance(time_limit, bool):
            raise ValidationError("'time_limit' must be a number")
        if not (0 < time_limit <= MAX_TIME_LIMIT):
            raise ValidationError(
                f"'time_limit' must be in (0, {MAX_TIME_LIMIT}]")

    mipgap = payload.get("mipgap")
    if mipgap is not None:
        if not isinstance(mipgap, (int, float)) or isinstance(mipgap, bool):
            raise ValidationError("'mipgap' must be a number")
        if mipgap < 0:
            raise ValidationError("'mipgap' must be >= 0")

    return {
        "instance": instance,
        "kpi": kpi,
        "time_limit": time_limit,
        "mipgap": mipgap,
        "want_stats": bool(payload.get("stats", False)),
    }
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python test_validate.py`
Expected: `OK: validation tests passed`.

- [ ] **Step 5: Commit**

```bash
git add modal_app.py test_validate.py
git commit -m "feat: request validator for the Modal /solve endpoint"
```

---

## Task 8: Modal app — worker + ASGI endpoint

Wire the worker and FastAPI endpoint on top of the validator. Not unit-tested (needs Modal auth + WLS secret); verified via `modal serve`.

**Files:**
- Modify: `modal_app.py`

- [ ] **Step 1: Append the Modal app to `modal_app.py`**

```python
import modal

image = (modal.Image.debian_slim()
         .pip_install("gurobipy==13.0.2", "fastapi[standard]")
         .add_local_python_source("pareto_core", "serialize"))

app = modal.App("pareto", image=image)

THREADS = int(os.environ.get("PARETO_THREADS", "8"))


def _gurobi_env():
    """Build a Gurobi WLS Env from the gurobi-wls Modal Secret."""
    import gurobipy as gp
    return gp.Env(params={
        "WLSACCESSID": os.environ["WLSACCESSID"],
        "WLSSECRET": os.environ["WLSSECRET"],
        "LICENSEID": int(os.environ["LICENSEID"]),
    })


@app.function(cpu=8, timeout=MAX_INSTANCE_BYTES and 900,
              secrets=[modal.Secret.from_name("gurobi-wls")])
def solve_job(req):
    """Worker: req is the validated dict from validate_request()."""
    import serialize as S
    try:
        env = _gurobi_env()
        res = C.solve(
            req["instance"], kpi=req["kpi"],
            time_limit=req["time_limit"], mipgap=req["mipgap"],
            env=env, threads=THREADS, want_stats=req["want_stats"],
        )
        return S.to_dict(res)
    except ValueError as e:               # parse / build errors
        return {"status": "error", "error": str(e)}


@app.function(image=image)
@modal.asgi_app(requires_proxy_auth=True)
def web():
    from fastapi import FastAPI, HTTPException, Request

    api = FastAPI(title="Pareto")

    @api.post("/solve", status_code=202)
    async def submit(request: Request):
        try:
            payload = await request.json()
        except Exception:
            raise HTTPException(400, "body must be valid JSON")
        try:
            req = validate_request(payload)
        except ValidationError as e:
            raise HTTPException(400, str(e))
        call = solve_job.spawn(req)
        return {"job_id": call.object_id}

    @api.get("/result/{job_id}")
    async def result(job_id: str):
        fc = modal.FunctionCall.from_id(job_id)
        try:
            out = fc.get(timeout=0)
        except TimeoutError:
            return {"status": "pending"}
        except modal.exception.NotFoundError:
            raise HTTPException(404, "unknown job_id")
        return {"status": "done", "result": out}

    return api
```

> `timeout=MAX_INSTANCE_BYTES and 900` is a quirky guard; replace with a plain `timeout=900` (15 min worker cap, comfortably above MAX_TIME_LIMIT=600). Set it as the literal `900`.

- [ ] **Step 2: Fix the worker timeout literal**

In the `@app.function` decorator for `solve_job`, set `timeout=900` (not the `MAX_INSTANCE_BYTES and 900` expression).

- [ ] **Step 3: Confirm imports still pass for the unit tests**

Run: `python test_validate.py`
Expected: `OK: validation tests passed` — importing `modal_app` (now importing `modal`) must still succeed. If `modal` isn't installed locally, run `pip install modal` first (deploy-time dep).

- [ ] **Step 4: Local smoke test against Modal**

Run (separate shell, needs `modal token new` once):
```bash
modal serve modal_app.py
```
Then, using the printed URL and proxy-auth token:
```bash
curl -s -X POST "$URL/solve" \
  -H "Modal-Key: $MODAL_KEY" -H "Modal-Secret: $MODAL_SECRET" \
  -H "Content-Type: application/json" \
  -d '{"instance":"alice: (1for1) A -> B\nbob: (1for1) B -> A\n","stats":true}'
# -> {"job_id":"fc-..."}
curl -s "$URL/result/fc-..." \
  -H "Modal-Key: $MODAL_KEY" -H "Modal-Secret: $MODAL_SECRET"
# -> {"status":"pending"}  then  {"status":"done","result":{...swaps...}}
```
Expected: a `job_id`, then a `done` result whose `swaps` contains both A->B and B->A. (Requires the `gurobi-wls` secret to exist — see Task 9.)

- [ ] **Step 5: Commit**

```bash
git add modal_app.py
git commit -m "feat: Modal ASGI endpoint with async submit/poll worker"
```

---

## Task 9: Docs, deploy notes, requirements

**Files:**
- Modify: `README.md`
- Inspect: `requirements.txt`

- [ ] **Step 1: Confirm `requirements.txt` stays runtime-only**

Run: `cat requirements.txt`
Expected: just `gurobipy==13.0.2`. Leave it — `modal` and `fastapi` are deploy/image deps, not runtime ones. (No change committed unless absent.)

- [ ] **Step 2: Add a "Modal service" section to `README.md`**

Append after the `Benchmarking` section:

````markdown
## Hosted service (Modal)

Pareto runs on [Modal](https://modal.com) as an HTTP service backed by a Gurobi
WLS license. The worker uses `cpu=8` and `Threads=8` (optimal for the
single-strategy solve; more threads only help when running multiple Gurobi
strategies). The endpoint requires Modal proxy auth.

### Deploy

```bash
pip install modal
modal token new                       # one-time auth
modal secret create gurobi-wls \      # your WLS credentials
  WLSACCESSID=<id> WLSSECRET=<secret> LICENSEID=<licid>
modal deploy modal_app.py
```

### API

Submit a job (async), then poll for the result. Both calls need proxy-auth
headers `Modal-Key` / `Modal-Secret`.

```bash
# POST /solve  -> 202 {"job_id": "..."}
curl -X POST "$URL/solve" -H "Modal-Key: …" -H "Modal-Secret: …" \
  -H "Content-Type: application/json" \
  -d '{"instance":"<instance text>","kpi":"trades,users","time_limit":60,"stats":true}'

# GET /result/{job_id} -> {"status":"pending"} | {"status":"done","result":{…}} | {"status":"error",…}
curl "$URL/result/<job_id>" -H "Modal-Key: …" -H "Modal-Secret: …"
```

Request fields: `instance` (required, the instance file text), `kpi`
(comma-separated or list; default `trades`), `time_limit` (seconds, ≤ 600),
`mipgap`, `stats` (bool). Response `result` mirrors the CLI output as JSON:
`swaps`, `combo_trades`, `cash_purchases`, `cash_summary`, `payments`,
`settlement`, plus `status`, `money_present`, and (when `stats`) `stats`.

Local development: `modal serve modal_app.py` gives a hot-reloading temporary URL.
````

- [ ] **Step 3: Run the whole suite once more**

Run:
```bash
python test_refactor_golden.py && python test_core.py && \
python test_validate.py && python test_dupcap.py && python test_kpi_distance.py
```
Expected: five `OK:` lines.

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: document the Modal service endpoint and deploy"
```

---

## Done criteria

- All five test scripts print `OK:`.
- `testcases/golden/*.out` unchanged by the refactor (byte-identical CLI).
- `modal serve` smoke test returns a `done` result for the sample swap.
- `main.py` CLI, `--kpi`, and `PARETO_*` env vars behave exactly as before.
```
