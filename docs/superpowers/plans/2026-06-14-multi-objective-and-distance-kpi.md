# Multi-objective KPIs + Distance KPI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let `--kpi` take a comma-separated lexicographic list (e.g. `trades,users`) and add a new `distance` KPI that minimizes total shipping distance between item owners and receivers, driven by a new `location` input directive.

**Architecture:** All work is in the objective layer of `main.py`. KPIs combine via Gurobi hierarchical multi-objective (`setObjectiveN`, priority = list position). The `distance` objective reuses the existing `spend_swap` and `buy` structures — each receive-leg is one item move `owner -> receiver`, costed by haversine km (integer). No new decision variables for distance.

**Tech Stack:** Python 3, `gurobipy`, `math` (stdlib, haversine). Tests are subprocess end-to-end runs of `main.py`, matching `test_dupcap.py`.

---

## Background for the implementer

`main.py` is a single script. It parses CLI args and the input file at module top-level, builds the MIP, optimizes, and prints. Key existing structures you will reuse:

- `owner` — `item_id -> user` (original owner of each item).
- `spend_swap` — `user -> [(take_iid, var)]`: every swap receive-leg (simple swaps **and** combo takes). `var` is 1 when that user receives `take_iid` via swap.
- `buy` — `(user, item_id) -> var`: cash purchase legs. `var` is 1 when `user` buys `item_id` from its owner.
- `swaps = list(edge_vars.values())`, `buys = list(buy.values())` (defined ~line 261-262).
- `participation` / traded indicators — built for the `users` KPI (~lines 270-294).

A receive-leg means the item ships from `owner[take_iid]` to the receiving user. Summing distance over `spend_swap` legs + `buy` legs counts each item move exactly once (each item is received once).

Tests run `main.py` as a subprocess on a temp input file and parse stdout, exactly like `test_dupcap.py`. New tests go in a new file `test_kpi_distance.py` with a `__main__` block that calls each test and prints `OK: ...`.

---

## Task 1: Multi-objective `--kpi` list

**Files:**
- Modify: `main.py` (argument parsing ~lines 105-110; objective build ~lines 270-294)
- Test: `test_kpi_distance.py` (create)

- [ ] **Step 1: Write the failing test**

Create `test_kpi_distance.py`:

```python
"""Multi-objective --kpi list and the distance KPI. Runs main.py as a
subprocess, like test_dupcap.py."""
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN = os.path.join(HERE, "main.py")


def run(text, kpi=None, check=True):
    """Run main.py on `text`. Returns CompletedProcess (stdout/stderr/code)."""
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
        f.write(text)
        path = f.name
    try:
        cmd = [sys.executable, MAIN, path]
        if kpi is not None:
            cmd += ["--kpi", kpi]
        return subprocess.run(cmd, capture_output=True, text=True, check=check)
    finally:
        os.unlink(path)


# Plain reciprocal 1-for-1 swap: both moves happen regardless of KPI.
SWAP = """\
alice: (1for1) A -> B
bob: (1for1) B -> A
"""


def test_multi_kpi_runs():
    out = run(SWAP, kpi="trades,users").stdout
    assert "A -> B" in out and "B -> A" in out, out


def test_invalid_kpi_rejected():
    p = run(SWAP, kpi="trades,bogus", check=False)
    assert p.returncode != 0
    assert "bogus" in (p.stderr + p.stdout)


def test_duplicate_kpi_rejected():
    p = run(SWAP, kpi="trades,trades", check=False)
    assert p.returncode != 0


if __name__ == "__main__":
    test_multi_kpi_runs()
    test_invalid_kpi_rejected()
    test_duplicate_kpi_rejected()
    print("OK: kpi list tests passed")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python test_kpi_distance.py`
Expected: FAIL — `test_multi_kpi_runs` errors because argparse `choices=["trades","users"]` rejects the value `trades,users` (non-zero exit, `subprocess.run(check=True)` raises `CalledProcessError`).

- [ ] **Step 3: Replace the `--kpi` argument with a list parser**

In `main.py`, replace the argument definition (currently lines 107-109):

```python
_argp.add_argument("--kpi", choices=["trades", "users"], default="trades",
                   help="objective: 'trades' = max total trades (default); "
                        "'users' = max number of users with >= 1 trade")
```

with:

```python
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
```

`_args.kpi` is now a list. Note: `ALLOWED_KPIS` already includes `distance`; the objective code for it lands in Task 2 — running `--kpi distance` before then would raise inside `kpi_expr`, which is fine (no test exercises it yet).

- [ ] **Step 4: Rewrite the objective build to consume the list**

Replace the block currently at lines 270-294 (from the `# Per-user participation vars:` comment through `model.setObjective(...)` in the `else` branch, i.e. everything up to but not including `model.optimize()`):

```python
# Per-user participation vars: a user participates if they receive any item (swap take or cash
# buy) or give an owned item away. Needed for the 'users' KPI and the users_traded report.
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
```

Note: `distance_terms()` is defined in Task 2. For Task 1, add a temporary stub just above this block so the single-KPI `trades`/`users` paths run (it is never called until a `distance` KPI is used):

```python
def distance_terms():
    return []
```

The stub is replaced by the real implementation in Task 2 Step 3 — do not leave both.

- [ ] **Step 5: Run test to verify it passes**

Run: `python test_kpi_distance.py`
Expected: PASS — `OK: kpi list tests passed`.

- [ ] **Step 6: Run the existing test to confirm no regression**

Run: `python test_dupcap.py`
Expected: `OK: dupcap tests passed` (the single-KPI default path still works).

- [ ] **Step 7: Commit**

```bash
git add main.py test_kpi_distance.py
git commit -m "feat: --kpi accepts a lexicographic list of objectives"
```

---

## Task 2: `location` directive + `distance` KPI

**Files:**
- Modify: `main.py` (imports; globals; `parse_file`; add `haversine_km` + `distance_terms`)
- Test: `test_kpi_distance.py`

- [ ] **Step 1: Write the failing tests**

Append to `test_kpi_distance.py`, before the `__main__` block:

```python
# alice wants one copy of game G; bob (near) and carol (far) each have one.
# dupcap caps her at one copy. With trades primary the count is 1 either way,
# so the distance KPI decides WHICH seller ships.
TWO_SELLERS = """\
item C1 owner bob ask 10
item C2 owner carol ask 10
bid alice C1 20
bid alice C2 20
dupcap alice C1 C2
location alice 0 0
location bob 0 1
location carol 0 5
"""

# One optional cash buy. Trades wants the move; distance wants no move (0 km).
ONE_OPTIONAL = """\
item C1 owner bob ask 10
bid alice C1 20
location alice 0 0
location bob 0 5
"""


def test_distance_picks_closer_seller():
    out = run(TWO_SELLERS, kpi="trades,distance").stdout
    assert "C1: bob -> alice" in out, out      # near seller chosen
    assert "C2: carol -> alice" not in out, out  # far seller not chosen


def test_lexicographic_order_changes_outcome():
    # trades first -> the buy happens.
    trades_first = run(ONE_OPTIONAL, kpi="trades,distance").stdout
    assert "pays" in trades_first, trades_first
    # distance first -> optimum is 0 km (no move); trades is held subordinate.
    distance_first = run(ONE_OPTIONAL, kpi="distance,trades").stdout
    assert "pays" not in distance_first, distance_first


def test_bad_latitude_rejected():
    bad = "location alice 999 0\nalice: (1for1) A -> B\n"
    p = run(bad, check=False)
    assert p.returncode != 0
    assert "latitude" in (p.stderr + p.stdout)
```

And add these calls inside the `__main__` block (before the final `print`):

```python
    test_distance_picks_closer_seller()
    test_lexicographic_order_changes_outcome()
    test_bad_latitude_rejected()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python test_kpi_distance.py`
Expected: FAIL — `location` lines hit the `else: raise ValueError(f"Unrecognized line: ...")` branch in `parse_file`, so every new test errors out.

- [ ] **Step 3: Add `math` import, `location` global, haversine + distance terms**

In `main.py`:

Add to the imports at the top (after `import re`):

```python
import math
```

Add to the globals block (after the `dup_groups = []` line, ~line 16):

```python
location = {}  # user -> (lat, lng) in degrees
```

Add the `else: distance_terms` stub from Task 1 is now replaced. Define the real helpers. Put them just after the `buy_terms` / `real_item_ids` setup and the dupcap loop — i.e. immediately before the objective block from Task 1 (so `owner`, `location`, `spend_swap`, `buy` all exist). Replace the temporary stub:

```python
def distance_terms():
    return []
```

with:

```python
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
```

- [ ] **Step 4: Parse the `location` directive**

In `parse_file`, add the regex alongside the other `m_*` matches (after the `m_dup = ...` line, ~line 72):

```python
            m_loc = re.fullmatch(
                r'location\s+(\S+)\s+(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)', line)
```

and add a branch before the `elif ':' in line:` wish branch (~line 93):

```python
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
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python test_kpi_distance.py`
Expected: PASS — `OK: kpi list tests passed`.

- [ ] **Step 6: Confirm no regression**

Run: `python test_dupcap.py`
Expected: `OK: dupcap tests passed`.

- [ ] **Step 7: Commit**

```bash
git add main.py test_kpi_distance.py
git commit -m "feat: distance KPI minimizes shipping distance via location lines"
```

---

## Task 3: `PARETO_STATS` reporting for multi-objective

**Files:**
- Modify: `main.py` (the `PARETO_STATS` block, ~lines 302-312)
- Test: `test_kpi_distance.py`

- [ ] **Step 1: Write the failing test**

Append to `test_kpi_distance.py` before the `__main__` block:

```python
def test_stats_multi_objective():
    p = subprocess_with_stats(SWAP, "trades,users")
    assert "STATS" in p.stderr, p.stderr
    assert "obj[trades=" in p.stderr and "users=" in p.stderr, p.stderr


def subprocess_with_stats(text, kpi):
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
        f.write(text)
        path = f.name
    try:
        env = dict(os.environ, PARETO_STATS="1")
        return subprocess.run(
            [sys.executable, MAIN, path, "--kpi", kpi],
            capture_output=True, text=True, check=True, env=env,
        )
    finally:
        os.unlink(path)
```

Add to the `__main__` block before the final print:

```python
    test_stats_multi_objective()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python test_kpi_distance.py`
Expected: FAIL — current stats line prints a single `obj=<n>` (from `model.ObjVal`), so `obj[trades=` is absent.

- [ ] **Step 3: Report per-objective values when multiple KPIs**

In `main.py`, replace the `PARETO_STATS` block (currently lines 302-312):

```python
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
```

with:

```python
if os.environ.get("PARETO_STATS"):
    have = model.SolCount > 0
    gap = model.MIPGap if have else float("nan")
    users_traded = (sum(1 for part in participation.values() if any(v.X > 0.5 for v in part))
                    if have else 0)
    if len(_args.kpi) == 1:
        obj_str = f"obj={model.ObjVal:.0f}" if have else "obj=nan"
    else:
        # Per-objective values; distance is reported negated (maximize form).
        parts = []
        for k, kpi in enumerate(_args.kpi):
            model.params.ObjNumber = k
            val = f"{model.ObjNVal:.0f}" if have else "nan"
            parts.append(f"{kpi}={val}")
        obj_str = "obj[" + ",".join(parts) + "]"
    print(
        f"STATS swap_vars={len(swaps)} buy_vars={len(buys)} combos={len(combo_records)} "
        f"items={len(real_item_ids)} users_traded={users_traded}/{len(users)} "
        f"status={status} {obj_str} gap={gap:.4f} runtime={model.Runtime:.3f}",
        file=sys.stderr,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python test_kpi_distance.py`
Expected: PASS — `OK: kpi list tests passed`.

- [ ] **Step 5: Commit**

```bash
git add main.py test_kpi_distance.py
git commit -m "feat: PARETO_STATS reports per-objective values for multi-KPI"
```

---

## Task 4: Example testcase + documentation

**Files:**
- Create: `testcases/distance.txt`
- Modify: `README.md`

- [ ] **Step 1: Create the example testcase**

Create `testcases/distance.txt`:

```
# Distance KPI demo. alice wants one copy of game G, available from bob (near)
# or carol (far); dupcap caps her at one. With --kpi trades,distance the solver
# maximizes trades (1) then ships from the nearer seller (bob).
# Run: python main.py testcases/distance.txt --kpi trades,distance
item C1 owner bob ask 10
item C2 owner carol ask 10
bid alice C1 20
bid alice C2 20
dupcap alice C1 C2
location alice 0 0
location bob 0 1
location carol 0 5
```

- [ ] **Step 2: Verify the example runs as documented**

Run: `python main.py testcases/distance.txt --kpi trades,distance`
Expected: output contains `C1: bob -> alice` and does NOT contain `C2: carol -> alice`.

- [ ] **Step 3: Update the Usage table in `README.md`**

Replace the two `--kpi` rows (currently lines 54-55):

```
| `--kpi trades` | Objective: maximize total trades (default). |
| `--kpi users` | Objective: maximize number of users with ≥ 1 trade. |
```

with:

```
| `--kpi <list>` | Comma-separated objectives in priority order (leftmost first), e.g. `--kpi trades,users`. Choices: `trades` = max total trades (default); `users` = max users with ≥ 1 trade; `distance` = min total shipping distance (km). |
```

- [ ] **Step 4: Document the `location` directive in `README.md`**

In the "Items, asks, bids, budgets" code block (currently lines 84-88), add a `location` line:

```
item <name> owner <user> [ask <price>]   # declare ownership; optional sale price
bid  <user> <item> <max_price>           # user will pay up to max_price in cash
user <name> budget <amount>              # net-spend cap (absent => unlimited)
location <user> <lat> <lng>              # user location for the distance KPI
```

Then add a new subsection after the "Duplicate cap" subsection (after line 100):

```markdown
### Locations (distance KPI)

```
location <user> <lat> <lng>     # e.g. location trader01 -61.3902 34.2251
```

Used only by `--kpi distance`. Each item move ships the item from its owner to
the receiver; the `distance` objective minimizes the sum of those great-circle
distances (haversine, integer km). A move whose owner or receiver has no
`location` contributes 0.
```

- [ ] **Step 5: Update the Features and "How it works" lists in `README.md`**

In the Features list, replace the "Two objectives" bullet (lines 25-26):

```
- **Two objectives** — maximize total trades (default) or maximize the number of
  users who get at least one trade.
```

with:

```
- **Lexicographic objectives** — `--kpi` takes a priority-ordered list (e.g.
  `trades,users`); each objective is optimized in turn. Available KPIs: total
  trades, participating users, and total shipping `distance` (minimized).
```

In the "How it works" list, replace the objective bullet (lines 152-153):

```
- The objective maximizes total moves (`--kpi trades`) or distinct participating
  users (`--kpi users`).
```

with:

```
- KPIs combine lexicographically (Gurobi hierarchical multi-objective): `trades`
  maximizes total moves, `users` maximizes distinct participants, `distance`
  minimizes total owner→receiver shipping km. List order sets priority.
```

- [ ] **Step 6: Commit**

```bash
git add testcases/distance.txt README.md
git commit -m "docs: document --kpi list, location directive, distance KPI"
```

---

## Final verification

- [ ] Run `python test_kpi_distance.py` → `OK: kpi list tests passed`
- [ ] Run `python test_dupcap.py` → `OK: dupcap tests passed`
- [ ] Run `python main.py testcases/distance.txt --kpi trades,distance` → shows `C1: bob -> alice`
- [ ] Run `python main.py testcases/1for1.txt` → unchanged single-KPI default still works

---

## Self-review notes (coverage vs spec)

- Spec §"Distance = per item-move shipping" → Task 2 `distance_terms()`.
- Spec §"Distance metric / integer km" → Task 2 `haversine_km()` with `round()`.
- Spec §"Lexicographic combination" → Task 1 `kpi_expr` + `setObjectiveN`.
- Spec §"`--kpi` parsing" → Task 1 `parse_kpi_list`.
- Spec §"`location` directive + range validation" → Task 2 Step 4.
- Spec §"Objective build / participation gate" → Task 1 Step 4.
- Spec §"Stats reporting `ObjNVal`" → Task 3.
- Spec §"Testing" → Tasks 1-3 tests + Task 4 testcase.
- Spec §"Docs" → Task 4.
