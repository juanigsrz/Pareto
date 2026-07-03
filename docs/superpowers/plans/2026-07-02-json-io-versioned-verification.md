# JSON I/O + Versioned Verification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add JSON input/output and versioned, checksummed results to `main.py` so the solver is easy to drive programmatically and users can re-run locally to verify a hosted result instead of trusting it blindly.

**Architecture:** Extract the I/O edges of `main.py` (currently top-level parse + print) into functions around an unchanged MIP core. A new dependency-free module `pareto_io.py` holds the pure, unit-tested primitives (canonical JSON, sha256 checksum, input-format detection). `main.py` gains a JSON input parser that fills the same globals as the text parser, a `build_result()` that returns one result dict, and two serializers (`render_text`, `render_json`) that both consume it — so text and JSON describe the same solution by construction.

**Tech Stack:** Python 3 (stdlib `json`, `hashlib`, `argparse`), gurobipy (unchanged), subprocess-style tests (no framework), matching existing `test_takecap.py`.

## Global Constraints

- **Solver determinism is a non-goal.** Do NOT set `Threads`, `Seed`, or any solver param to force a particular optimum. Alternate optima remain the user's problem to reconcile (via `kpi` + `gurobi_version`).
- **Output serialization determinism IS a goal (distinct from the above).** `build_result()` MUST return every list in a **canonical sorted order** so that a *given* solution serializes identically — same trades ⇒ same bytes ⇒ same `result_checksum` — regardless of Gurobi variable-creation order / Python hash seed. Sort keys: `trades` by `(give, take)`; each `combos` entry's `sent`/`taken` sorted, then `combos` sorted by `(sent, taken)`; `cash_purchases` by `(item, from, to)`; `payments` and `settlement` by `(from, to)`; `cash_summary` stays `sorted(users)`. This also makes text/JSON output order deterministic, so tests need no `PYTHONHASHSEED` pinning. (`canonical_json` only sorts dict keys, not list elements — the ordering must come from `build_result`.)
- **The Gurobi solver log must never reach a JSON consumer.** `main.py` sets `OutputFlag=1`, which writes the solver log to **stdout**. That is tolerated in text mode (historical; `check.py` ignores pre-`Trade Results:` lines) but would corrupt `--format json` (log-then-JSON breaks `json.loads`). In JSON mode, build the model under a silent Gurobi env (see Task 4) so stdout is pure JSON. **Never capture the solver log into a committed fixture** — it contains license identifiers and machine details and is version/host-specific.
- `input_checksum` hashes the **canonical normalized instance**, NOT raw source bytes — text and JSON describing the same instance MUST produce the same hash.
- `result_checksum` hashes the **canonical result dict EXCLUDING** the `version`, `gurobi_version`, `input_checksum`, `result_checksum` fields.
- Checksum wire format: `"sha256:" + lowercase-hex`.
- One canonicalization rule, defined once in `pareto_io.canonical_json`: `json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")`.
- `__version__ = "1.0.0"` in `main.py`.
- Backward compatibility: default output stays text; text `Trade Results:` / `Cash Purchases:` / `Cash Summary:` / `Payments:` / `Settlement plan:` sections keep their exact current wording and formatting (`check.py` and the golden files parse them). Only an additive `#`-comment header is allowed on top.
- Run all tests with the repo venv: `venv/bin/python` (global python lacks gurobipy).
- Tests follow the existing self-contained subprocess style (`test_takecap.py`): `subprocess.run([sys.executable, MAIN, path], ...)`. No pytest fixtures, no new deps.
- **Every new test file MUST end with a self-contained runner block** so it runs framework-free per the README, matching `test_dupcap.py`:
  ```python
  if __name__ == "__main__":  # self-contained runner, no framework needed (see README)
      fns = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
      for fn in fns:
          fn()
      print(f"{len(fns)} passed")
  ```
  Verify each test file with `venv/bin/python test_<name>.py` (framework-free). `pytest` remains usable but is not required; prefer the direct invocation in RED/GREEN evidence.

---

### Task 1: `pareto_io.py` — canonical JSON + checksum

**Files:**
- Create: `pareto_io.py`
- Test: `test_pareto_io.py`

**Interfaces:**
- Produces: `canonical_json(obj) -> bytes`, `checksum(obj) -> str`. `checksum` returns `"sha256:<hex>"`. Both are pure, import-safe, no gurobipy.

- [ ] **Step 1: Write the failing test**

Create `test_pareto_io.py`:

```python
"""Unit tests for pareto_io pure helpers (no gurobipy, run with any python)."""
import hashlib
from pareto_io import canonical_json, checksum


def test_canonical_json_sorts_keys():
    assert canonical_json({"b": 1, "a": 2}) == b'{"a":2,"b":1}'


def test_canonical_json_is_order_independent_for_keys():
    assert canonical_json({"a": 1, "b": 2}) == canonical_json({"b": 2, "a": 1})


def test_canonical_json_no_whitespace():
    assert b" " not in canonical_json({"x": [1, 2, 3]})


def test_checksum_prefix_and_hex():
    obj = {"a": 1}
    expected = "sha256:" + hashlib.sha256(canonical_json(obj)).hexdigest()
    assert checksum(obj) == expected


def test_checksum_stable_across_key_order():
    assert checksum({"a": 1, "b": 2}) == checksum({"b": 2, "a": 1})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python -m pytest test_pareto_io.py -v` (or `venv/bin/python -c "import pareto_io"`)
Expected: FAIL — `ModuleNotFoundError: No module named 'pareto_io'`

- [ ] **Step 3: Write minimal implementation**

Create `pareto_io.py`:

```python
"""Pure, dependency-free I/O helpers for Pareto: canonical serialization,
checksums, and input-format detection. Deliberately imports nothing from
gurobipy and has no import-time side effects, so it is fast to unit-test."""
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `venv/bin/python -m pytest test_pareto_io.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add pareto_io.py test_pareto_io.py
git commit -m "feat: pareto_io canonical_json + checksum helpers"
```

---

### Task 2: `pareto_io.py` — input-format detection

**Files:**
- Modify: `pareto_io.py`
- Test: `test_pareto_io.py`

**Interfaces:**
- Produces: `detect_input_format(raw: str) -> str` returning `"json"` or `"text"`. Skips blank lines and `#` comments; `{` as the first meaningful char ⇒ `"json"`, else `"text"`; empty input ⇒ `"text"`.

- [ ] **Step 1: Write the failing test**

Append to `test_pareto_io.py`:

```python
from pareto_io import detect_input_format


def test_detect_text_directive():
    assert detect_input_format("alice : (1for1) A -> B\n") == "text"


def test_detect_json_object():
    assert detect_input_format('{"wishes": []}\n') == "json"


def test_detect_json_after_comments_and_blanks():
    assert detect_input_format("# a comment\n\n   {\n  \"wishes\": []}\n") == "json"


def test_detect_empty_is_text():
    assert detect_input_format("\n\n# only comments\n") == "text"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python -m pytest test_pareto_io.py -k detect -v`
Expected: FAIL — `ImportError: cannot import name 'detect_input_format'`

- [ ] **Step 3: Write minimal implementation**

Add to `pareto_io.py`:

```python
def detect_input_format(raw):
    """Peek the first meaningful character of `raw` (blank lines and `#`
    comments skipped). Leading `{` ⇒ 'json', otherwise 'text'."""
    for line in raw.splitlines():
        line = line.partition("#")[0].strip()
        if not line:
            continue
        return "json" if line[0] == "{" else "text"
    return "text"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `venv/bin/python -m pytest test_pareto_io.py -v`
Expected: PASS (9 passed)

- [ ] **Step 5: Commit**

```bash
git add pareto_io.py test_pareto_io.py
git commit -m "feat: pareto_io.detect_input_format"
```

---

### Task 3: Refactor text output into `build_result` + `render_text`

> **REVISION (supersedes the code below where they conflict):**
> 1. **Sort every result list in `build_result()`** per the Global Constraints ordering (`trades` by `(give,take)`; combos' `sent`/`taken` sorted then combos sorted; `cash_purchases` by `(item,from,to)`; `payments`/`settlement` by `(from,to)`). This makes output deterministic — so it is NOT strictly "byte-for-byte identical to the old nondeterministic order," it is byte-for-byte identical to the *sorted* output. That is the intended behavior change (the old order was hash-seed dependent).
> 2. **The golden test compares only the program's own output**, anchored on the first `Trade Results:` line — NOT the Gurobi solver log that precedes it on stdout and NOT the Task-5 header. `body(text)` returns everything from the line whose strip == `"Trade Results:"` onward. Golden `.out` files contain ONLY that body (they start with `Trade Results:`).
> 3. **No `PYTHONHASHSEED` pinning** anywhere — sorting removes the need. Plain `subprocess.run([sys.executable, MAIN, path], ...)`.
> 4. **Never write the solver log into a golden file.** Generate golden bodies by piping through the anchor (e.g. capture stdout, then keep from `Trade Results:`). Do not commit license/log lines.

Extract the module-level output block (`main.py:619-699+`) into a `build_result()` that returns a result dict (lists **sorted**) and a `render_text(result)` that formats it. Add a golden characterization test locking the trade/cash body.

**Files:**
- Modify: `main.py` (extract lines ~615-700 into functions; call them at the end)
- Create: `test_golden.py`

**Interfaces:**
- Produces: `build_result() -> dict` with keys:
  - `status: str` (from `_STATUS` map, e.g. `"Optimal"`)
  - `kpi: dict[str, int]` (per requested KPI; `distance` reported as the real minimized km, i.e. positive)
  - `trades: list[{"give": str, "take": str}]` — one per active simple swap edge (`edge_vars[(i,j)]` active with both `i,j` real items); `give = id_to_item[j]`, `take = id_to_item[i]` (matches text `j -> i`)
  - `combos: list[{"sent": [str], "taken": [str]}]`
  - `cash_purchases: list[{"item": str, "from": str, "to": str, "price": int}]`
  - `cash_summary: list[{"user": str, "spent": int, "earned": int, "net": int, "cap": int|None}]` (`cap` = `None` when unbounded)
  - `payments: list[{"from": str, "to": str, "amount": int}]`
  - `settlement: list[{"from": str, "to": str, "amount": int}]`
  - `has_money: bool` (mirrors current `show_money` gate so `render_text` omits cash sections when false)
- Produces: `render_text(result) -> str` — the exact current stdout (no header yet).

- [ ] **Step 1: Capture the current golden output (pre-refactor baseline)**

The golden files exist but may be stale. Regenerate the ones this test uses, from the CURRENT (pre-refactor) code, so the test asserts "refactor changed nothing":

Run:
```bash
for t in money/swap money/cashchain 2for11for2 distance; do
  name=$(echo $t | tr '/' '_' | sed 's/money_/money__/')
  venv/bin/python main.py testcases/$t.txt > testcases/golden/$name.out
done
```
Expected: files written; `git diff --stat testcases/golden/` shows they match what's committed (if a file differs, the golden was stale — inspect, and keep the freshly generated version as the baseline).

- [ ] **Step 2: Write the failing test**

Create `test_golden.py`:

```python
"""Characterization test: main.py stdout must match testcases/golden/*.out.
Locks the output refactor (Task 3) and, once a header is added (Task 5),
compares only the body below the '#' header lines."""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN = os.path.join(HERE, "main.py")

CASES = {
    "money__swap": "testcases/money/swap.txt",
    "money__cashchain": "testcases/money/cashchain.txt",
    "2for11for2": "testcases/2for11for2.txt",
    "distance": "testcases/distance.txt",
}


def body(text):
    """Return the program's own output: everything from the first
    'Trade Results:' line onward. Skips the Gurobi solver log (which
    OutputFlag=1 writes to stdout ahead of it) and the Task-5 header."""
    lines = text.splitlines(keepends=True)
    for i, line in enumerate(lines):
        if line.strip() == "Trade Results:":
            return "".join(lines[i:])
    raise AssertionError("no 'Trade Results:' marker in output")


def run(path):
    out = subprocess.run([sys.executable, MAIN, os.path.join(HERE, path)],
                         capture_output=True, text=True, check=True).stdout
    return body(out)


def test_golden_outputs_match():
    for name, path in CASES.items():
        with open(os.path.join(HERE, "testcases/golden", name + ".out")) as f:
            expected = f.read()
        assert run(path) == expected, f"{name} output changed"
```

- [ ] **Step 3: Run test to verify it passes on current code (baseline green)**

Run: `venv/bin/python -m pytest test_golden.py -v`
Expected: PASS — confirms the harness matches the un-refactored code before you touch `main.py`.

- [ ] **Step 4: Extract `build_result()` and `render_text()`**

In `main.py`, replace the top-level output block (currently `print("\nTrade Results:")` through the end of the settlement loop, ~lines 619-700) with two functions plus a call. `build_result()` gathers the data the old prints computed inline; `render_text()` formats it identically. Keep the exact format strings.

```python
def build_result():
    trades = []
    for (i, j), var in edge_vars.items():
        if active(var) and i in id_to_item and j in id_to_item:
            trades.append({"give": id_to_item[j], "take": id_to_item[i]})

    combos = []
    for in_pairs, out_pairs in combo_records:
        if any(active(v) for _, v in in_pairs + out_pairs):
            combos.append({
                "sent": [id_to_item[s] for s, v in out_pairs if active(v)],
                "taken": [id_to_item[t] for t, v in in_pairs if active(v)],
            })

    has_money = bool(buy) or bool(ask) or bool(budget)
    cash_purchases, cash_summary, payments, settlement = [], [], [], []
    if has_money:
        for (u, iid), v in buy.items():
            if active(v):
                cash_purchases.append({
                    "item": id_to_item[iid], "from": owner[iid],
                    "to": u, "price": ask.get(iid, 0)})
        net = {}
        for u in sorted(users):
            spent = sum(c for c, v in spend_data[u] if active(v))
            earned = sum(c for c, v in earn_data[u] if active(v))
            net[u] = spent - earned
            cash_summary.append({
                "user": u, "spent": spent, "earned": earned, "net": net[u],
                "cap": budget[u] if u in budget else None})
        assert sum(net.values()) == 0, "cash nets must balance to zero"

        flows = {}
        def add_flow(payer, payee, amt):
            if amt and payer != payee:
                flows[(payer, payee)] = flows.get((payer, payee), 0) + amt
        for (u, iid), v in buy.items():
            if active(v):
                add_flow(u, owner[iid], ask.get(iid, 0))
        printed = set()
        for (a, b) in list(flows):
            if (a, b) in printed or (b, a) in printed:
                continue
            pair_net = flows.get((a, b), 0) - flows.get((b, a), 0)
            if pair_net > 0:
                payments.append({"from": a, "to": b, "amount": pair_net})
            elif pair_net < 0:
                payments.append({"from": b, "to": a, "amount": -pair_net})
            printed.add((a, b)); printed.add((b, a))

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
    # Gurobi variable-creation order / Python hash seed (see Global Constraints).
    trades.sort(key=lambda t: (t["give"], t["take"]))
    for c in combos:
        c["sent"].sort(); c["taken"].sort()
    combos.sort(key=lambda c: (c["sent"], c["taken"]))
    cash_purchases.sort(key=lambda p: (p["item"], p["from"], p["to"]))
    payments.sort(key=lambda p: (p["from"], p["to"]))
    settlement.sort(key=lambda p: (p["from"], p["to"]))
    # cash_summary already built in sorted(users) order.

    return {
        "status": status,
        "kpi": kpi_values(),
        "trades": trades, "combos": combos,
        "cash_purchases": cash_purchases, "cash_summary": cash_summary,
        "payments": payments, "settlement": settlement,
        "has_money": has_money,
    }
```

> **Note on the settlement loop:** copy the exact index-advance logic from the current `main.py:693-699` (shown above collapsed); verify against the source so the greedy pairing is unchanged. `kpi_values()` is added in the next step.
> **Note on sorting `settlement`:** the greedy pairing determines the *amounts*; sorting the resulting rows by `(from, to)` only stabilizes display/checksum order and does not change which transfers occur.

Add `kpi_values()` (reuses the `PARETO_STATS` logic at `main.py:592-602`, but returns real minimized distance as positive):

```python
def kpi_values():
    if model.SolCount == 0:
        return {k: None for k in _args.kpi}
    vals = {}
    if len(_args.kpi) == 1:
        raw = model.ObjVal
        vals[_args.kpi[0]] = int(round(-raw if _args.kpi[0] == "distance" else raw))
    else:
        for k, kpi in enumerate(_args.kpi):
            model.params.ObjNumber = k
            raw = model.ObjNVal
            vals[kpi] = int(round(-raw if kpi == "distance" else raw))
    return vals
```

Add `render_text(result)` reproducing the exact current output:

```python
def render_text(result):
    out = ["\nTrade Results:"]
    for t in result["trades"]:
        out.append(f"{t['give']} -> {t['take']}")
    for c in result["combos"]:
        out.append(" ".join(c["sent"]) + " -> " + " ".join(c["taken"]))
    if result["has_money"]:
        if result["cash_purchases"]:
            out.append("\nCash Purchases:")
            for p in result["cash_purchases"]:
                out.append(f"{p['item']}: {p['from']} -> {p['to']}  "
                           f"({p['to']} pays {p['from']} ${p['price']})")
        out.append("\nCash Summary:")
        for s in result["cash_summary"]:
            cap = "inf" if s["cap"] is None else f"{s['cap']}"
            direction = "owes" if s["net"] > 0 else "receives" if s["net"] < 0 else "even"
            out.append(f"  {s['user']}: spent ${s['spent']:g}, earned ${s['earned']:g}, "
                       f"net ${s['net']:g} ({direction}) (cap ${cap})")
        if result["payments"]:
            out.append("\nPayments:")
            for p in result["payments"]:
                out.append(f"  {p['from']} pays {p['to']} ${p['amount']:g}")
        if result["settlement"]:
            out.append("\nSettlement plan:")
            for p in result["settlement"]:
                out.append(f"  {p['from']} pays {p['to']} ${p['amount']:g}")
    return "\n".join(out) + "\n"
```

Replace the old print block with:

```python
_result = build_result()
print(render_text(_result), end="")
```

- [ ] **Step 5: Run the golden test to verify the refactor changed nothing**

Run: `venv/bin/python -m pytest test_golden.py -v`
Expected: PASS. If a case differs, diff `venv/bin/python main.py testcases/... ` against the golden and fix the format string until byte-identical.

- [ ] **Step 6: Commit**

```bash
git add main.py test_golden.py testcases/golden
git commit -m "refactor: extract build_result + render_text; golden characterization test"
```

---

### Task 4: JSON output — `--format json`, version, checksums

> **REVISION — pure-JSON stdout:** `main.py` sets `OutputFlag=1`, so Gurobi's log goes to **stdout** and would precede the JSON, breaking `json.loads`. Since `--format` is parsed before the model is built (args at `main.py:189`, `gp.Model()` at `:192`), build the model under a silent env in JSON mode so NOTHING but the JSON reaches stdout:
> ```python
> if _args.format == "json":
>     _env = gp.Env(empty=True)
>     _env.setParam("OutputFlag", 0)
>     _env.start()
>     model = gp.Model(env=_env)
> else:
>     model = gp.Model()
> ```
> Replace the current `model = gp.Model()` (`main.py:192`) with this. The empty env suppresses the license/param/log lines too (they otherwise print during `gp.Model()` construction). Text mode is unchanged (log still on stdout, `check.py`-tolerated). Add a test asserting `--format json` stdout is parseable AND has no `Set parameter`/`Gurobi Optimizer` lines. `PARETO_STATS` and warnings stay on stderr, unaffected.
>
> **REVISION — checksum stability rests on Task 3's sorting.** `result_checksum` = `checksum(result)` over the sorted `build_result()` dict; do not re-sort here. The test `test_result_checksum_excludes_metadata_and_is_stable` stays valid because the arrays are already canonically ordered.

Add `__version__`, the `--format` flag, `normalized_input()`, `render_json()`, and wire checksums. Input is still text only here.

**Files:**
- Modify: `main.py` (add `import` of `pareto_io`, `__version__`, argparse `--format`, functions, dispatch at end)
- Test: `test_json_output.py`

**Interfaces:**
- Consumes: `pareto_io.checksum`, `build_result` (Task 3).
- Produces: `normalized_input() -> dict` (canonical instance from globals, item ids resolved to names). Produces `render_json(result) -> str` (a JSON document string ending in `\n`).

- [ ] **Step 1: Write the failing test**

Create `test_json_output.py`:

```python
"""--format json emits a well-formed, versioned, checksummed result that
agrees with the text output."""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN = os.path.join(HERE, "main.py")
SWAP = os.path.join(HERE, "testcases/money/swap.txt")


def run_json(path, *extra):
    out = subprocess.run([sys.executable, MAIN, path, "--format", "json", *extra],
                         capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def test_json_has_metadata():
    d = run_json(SWAP)
    assert d["version"] == "1.0.0"
    assert d["input_checksum"].startswith("sha256:")
    assert d["result_checksum"].startswith("sha256:")
    assert "gurobi_version" in d
    assert d["status"] == "Optimal"


def test_json_trades_match_swap():
    d = run_json(SWAP)
    pairs = {(t["give"], t["take"]) for t in d["trades"]}
    assert pairs == {("A", "B"), ("B", "A")}


def test_result_checksum_excludes_metadata_and_is_stable():
    d = run_json(SWAP)
    import pareto_io
    stripped = {k: v for k, v in d.items()
                if k not in ("version", "gurobi_version",
                             "input_checksum", "result_checksum")}
    assert pareto_io.checksum(stripped) == d["result_checksum"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python -m pytest test_json_output.py -v`
Expected: FAIL — `--format` is an unrecognized argument (argparse error) / KeyError.

- [ ] **Step 3: Implement**

Near the top of `main.py` (after imports):

```python
import pareto_io

__version__ = "1.0.0"
```

Add the flag next to the existing `--kpi` argument (`main.py:183`):

```python
_argp.add_argument("--format", choices=("text", "json"), default="text",
                   help="output format (default: text).")
```

Add `normalized_input()` (build the canonical instance from the globals; resolve ids to names; sort for stability):

```python
def normalized_input():
    """Canonical, format-independent view of the parsed instance for hashing.
    Item ids are resolved to names; lists are sorted where order is semantically
    irrelevant so equal instances hash equally regardless of source ordering."""
    def name(iid):
        return id_to_item[iid]
    wishes_out = sorted(
        ({"user": u, "give": sorted(name(g) for g in give),
          "take": sorted(name(t) for t in take), "n": N, "m": M}
         for (u, give, take, N, M) in wishes),
        key=lambda w: (w["user"], w["give"], w["take"], w["n"], w["m"]))
    items_out = sorted(
        ({"name": name(iid), "owner": o, **({"ask": ask[iid]} if iid in ask else {})}
         for iid, o in owner.items()),
        key=lambda it: it["name"])
    bids_out = sorted(
        ({"user": u, "item": name(iid), "max_price": y}
         for (u, iid), y in bids.items()),
        key=lambda b: (b["user"], b["item"]))
    budgets_out = sorted(
        ({"user": u, "budget": b} for u, b in budget.items()),
        key=lambda x: x["user"])
    locations_out = sorted(
        ({"user": u, "lat": lat, "lng": lng} for u, (lat, lng) in location.items()),
        key=lambda x: x["user"])
    takecaps_out = sorted(
        ({"user": u, "n": n, "items": sorted(name(i) for i in iids)}
         for (u, n, iids) in take_groups),
        key=lambda x: (x["user"], x["n"], x["items"]))
    givecaps_out = sorted(
        ({"user": u, "n": n, "items": sorted(name(i) for i in iids)}
         for (u, n, iids) in give_groups),
        key=lambda x: (x["user"], x["n"], x["items"]))
    return {"wishes": wishes_out, "items": items_out, "bids": bids_out,
            "budgets": budgets_out, "locations": locations_out,
            "takecaps": takecaps_out, "givecaps": givecaps_out}
```

Add metadata + `render_json`:

```python
def _gurobi_version():
    try:
        return ".".join(str(x) for x in gp.gurobi.version())
    except Exception:
        return "unknown"


def _meta(result, input_checksum):
    return {"version": __version__, "gurobi_version": _gurobi_version(),
            "input_checksum": input_checksum,
            "result_checksum": pareto_io.checksum(result)}


def render_json(result, input_checksum):
    doc = {**_meta(result, input_checksum), **result}
    doc.pop("has_money", None)  # internal render flag, not part of the payload
    return json.dumps(doc, indent=2) + "\n"
```

> `has_money` is dropped from the JSON payload but is present in the `result` dict that `result_checksum` is computed over. Keep it consistent: compute `result_checksum` on the SAME dict `render_json`/`test` reconstruct. **Simplest: drop `has_money` from `build_result`'s return and instead recompute it inside `render_text` as `bool(result["cash_summary"]) or ...`.** To avoid ambiguity, change `build_result` to NOT include `has_money`; have `render_text` gate on `result["cash_summary"] or result["cash_purchases"]` (equivalent, since with money present `cash_summary` is always populated for all users). Update Task 3's `render_text` gate accordingly during this task and re-run `test_golden.py`.

Compute the input checksum right after parsing and dispatch output at the end (replace the `print(render_text(_result), end="")` from Task 3):

```python
_input_checksum = pareto_io.checksum(normalized_input())
...
_result = build_result()
if _args.format == "json":
    print(render_json(_result, _input_checksum), end="")
else:
    print(render_text(_result, _input_checksum), end="")  # header added in Task 5
```

> For this task `render_text` may ignore the second arg (or accept `_input_checksum=None`); Task 5 uses it. Give `render_text` signature `render_text(result, input_checksum=None)` now to avoid churn.

Place `_input_checksum = pareto_io.checksum(normalized_input())` immediately after `parse_file(_args.file)` (`main.py:190`) so it hashes the instance regardless of solve outcome.

- [ ] **Step 4: Run tests**

Run: `venv/bin/python -m pytest test_json_output.py test_golden.py -v`
Expected: PASS (golden still green after the `has_money` gate change).

- [ ] **Step 5: Commit**

```bash
git add main.py test_json_output.py
git commit -m "feat: --format json with version, input/result checksums"
```

---

### Task 5: Text output verification header

Prepend a `#`-comment header (version + both checksums) to text output so text users get the same verification metadata. `check.py` ignores lines before `Trade Results:`, so this is safe.

**Files:**
- Modify: `main.py` (`render_text`)
- Test: `test_text_header.py`

**Interfaces:**
- Consumes: `render_text(result, input_checksum)` (Task 4 gave it the param).

- [ ] **Step 1: Write the failing test**

Create `test_text_header.py`:

```python
"""Text output carries a version + checksum header, and check.py still accepts it."""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN = os.path.join(HERE, "main.py")
CHECK = os.path.join(HERE, "check.py")
SWAP = os.path.join(HERE, "testcases/money/swap.txt")


def run_text(path):
    return subprocess.run([sys.executable, MAIN, path],
                          capture_output=True, text=True, check=True).stdout


def test_header_present():
    lines = run_text(SWAP).splitlines()
    assert lines[0].startswith("# pareto 1.0.0")
    assert any(l.startswith("# input_checksum") and "sha256:" in l for l in lines)
    assert any(l.startswith("# result_checksum") and "sha256:" in l for l in lines)


def test_check_py_accepts_headered_output():
    out = run_text(SWAP)
    proc = subprocess.run([sys.executable, CHECK, SWAP, "-"],
                          input=out, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python -m pytest test_text_header.py -v`
Expected: FAIL — first line is `""` / `Trade Results:`, not `# pareto ...`.

- [ ] **Step 3: Implement**

In `render_text`, build the header from the metadata and prepend it:

```python
def render_text(result, input_checksum=None):
    header = []
    if input_checksum is not None:
        m = _meta(result, input_checksum)
        header = [f"# pareto {m['version']}  gurobi {m['gurobi_version']}",
                  f"# input_checksum  {m['input_checksum']}",
                  f"# result_checksum {m['result_checksum']}"]
    out = header + ["\nTrade Results:"]
    # ... rest unchanged ...
    return "\n".join(out) + "\n"
```

- [ ] **Step 4: Run tests**

Run: `venv/bin/python -m pytest test_text_header.py test_golden.py -v`
Expected: PASS. `test_golden.py` still passes because its `body()` strips leading `#` lines.

- [ ] **Step 5: Commit**

```bash
git add main.py test_text_header.py
git commit -m "feat: version + checksum header on text output"
```

---

### Task 6: JSON input + `--in-format` auto-detect + stdin

Parse JSON instances into the same globals as the text parser, auto-detect the format, and support `-` for stdin. Prove the headline claim: JSON and text of the same instance produce the same `input_checksum`.

**Files:**
- Modify: `main.py` (refactor `parse_file` into `parse_text(raw)`; add `parse_json_input(obj)`, `load_input(src, in_format)`; add `--in-format`; call `load_input` instead of `parse_file`)
- Test: `test_json_input.py`

**Interfaces:**
- Consumes: `pareto_io.detect_input_format`, existing `intern`, `set_owner`, `parse_wish_body`, validation.
- Produces: `parse_text(raw: str)` (the current per-line loop, reading a string not a file), `parse_json_input(obj: dict)`, `load_input(src: str, in_format: str)`.

- [ ] **Step 1: Write the failing test**

Create `test_json_input.py`. It converts `testcases/money/swap.txt` to the equivalent JSON and asserts equal `input_checksum` and identical trades:

```python
"""JSON input parses into the same instance as the equivalent text, proving
input_checksum is format-independent."""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN = os.path.join(HERE, "main.py")
SWAP_TXT = os.path.join(HERE, "testcases/money/swap.txt")

# Equivalent to testcases/money/swap.txt.
SWAP_JSON = {
    "budgets": [{"user": "alice", "budget": 0}, {"user": "bob", "budget": 0}],
    "items": [{"name": "A", "owner": "alice", "ask": 5},
              {"name": "B", "owner": "bob", "ask": 5}],
    "wishes": [{"user": "alice", "give": ["A"], "take": ["B"], "n": 1, "m": 1},
               {"user": "bob", "give": ["B"], "take": ["A"], "n": 1, "m": 1}],
}


def run_json_out(path, *extra):
    out = subprocess.run([sys.executable, MAIN, path, "--format", "json", *extra],
                         capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def test_json_input_matches_text_checksum():
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(SWAP_JSON, f)
        jpath = f.name
    try:
        d_txt = run_json_out(SWAP_TXT)
        d_json = run_json_out(jpath)               # auto-detected as JSON
        assert d_json["input_checksum"] == d_txt["input_checksum"]
        assert {(t["give"], t["take"]) for t in d_json["trades"]} == \
               {(t["give"], t["take"]) for t in d_txt["trades"]}
    finally:
        os.unlink(jpath)


def test_stdin_json_with_explicit_format():
    payload = json.dumps(SWAP_JSON)
    out = subprocess.run(
        [sys.executable, MAIN, "-", "--in-format", "json", "--format", "json"],
        input=payload, capture_output=True, text=True, check=True).stdout
    assert json.loads(out)["status"] == "Optimal"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python -m pytest test_json_input.py -v`
Expected: FAIL — `--in-format` unrecognized / JSON file parsed as text raises `Unrecognized line`.

- [ ] **Step 3: Implement**

Refactor the reader. Change `parse_file` (`main.py:76-140`) so the per-line loop lives in `parse_text(raw)`:

```python
def parse_text(raw):
    for line in raw.splitlines():
        line = line.partition('#')[0].strip()
        if not line:
            continue
        # ... existing directive-matching body unchanged ...
```

Keep the post-loop cap-owner warning block (`main.py:142-152`) in a shared `_post_parse_checks()` called by both parsers.

Add the JSON parser (fills the same globals, reuses `intern`/`set_owner` and the same validation):

```python
def parse_json_input(obj):
    for u in obj.get("budgets", []):
        users.add(u["user"]); budget[u["user"]] = int(u["budget"])
    for it in obj.get("items", []):
        iid = intern(it["name"]); users.add(it["owner"])
        set_owner(iid, it["owner"], f"item {it['name']}")
        if "ask" in it:
            ask[iid] = int(it["ask"])
    for b in obj.get("bids", []):
        users.add(b["user"])
        bids[(b["user"], intern(b["item"]))] = int(b["max_price"])
    for lo in obj.get("locations", []):
        lat, lng = float(lo["lat"]), float(lo["lng"])
        if not (-90 <= lat <= 90):
            raise ValueError(f"latitude out of range [-90, 90]: {lo}")
        if not (-180 <= lng <= 180):
            raise ValueError(f"longitude out of range [-180, 180]: {lo}")
        users.add(lo["user"]); location[lo["user"]] = (lat, lng)
    for c in obj.get("takecaps", []):
        users.add(c["user"])
        take_groups.append((c["user"], int(c["n"]),
                            [intern(t) for t in c["items"]]))
    for c in obj.get("givecaps", []):
        users.add(c["user"])
        give_groups.append((c["user"], int(c["n"]),
                            [intern(t) for t in c["items"]]))
    for w in obj.get("wishes", []):
        u = w["user"]; users.add(u)
        give = [intern(t) for t in w["give"]]
        take = [intern(t) for t in w["take"]]
        N = int(w.get("n", len(give))); M = int(w.get("m", len(take)))
        if N > len(give) or M > len(take):
            warn(f"combo can never activate: give {N}/{len(give)}, take {M}/{len(take)}: {w}")
        for g in give:
            set_owner(g, u, f"wish {u}")
        wishes.append((u, give, take, N, M))
    _post_parse_checks()
```

Add the dispatcher and read helpers:

```python
def _read_source(src):
    if src == "-":
        return sys.stdin.read()
    with open(src, "r") as f:
        return f.read()


def load_input(src, in_format):
    raw = _read_source(src)
    if in_format == "auto":
        in_format = pareto_io.detect_input_format(raw)
    if in_format == "json":
        parse_json_input(json.loads(raw))
    else:
        parse_text(raw)
```

Make `parse_file`'s old body delegate (so any lingering caller works): `def parse_file(_file): parse_text(_read_source(_file))`.

Add the flag and swap the call site (`main.py:189-190`):

```python
_argp.add_argument("--in-format", choices=("auto", "text", "json"), default="auto",
                   help="input format; 'auto' peeks the first character (default).")
_args = _argp.parse_args()
load_input(_args.file, _args.in_format)
```

- [ ] **Step 4: Run tests**

Run: `venv/bin/python -m pytest test_json_input.py test_golden.py test_json_output.py test_text_header.py -v`
Expected: PASS. The equivalence test proving text/JSON share an `input_checksum` is the key green.

- [ ] **Step 5: Commit**

```bash
git add main.py test_json_input.py
git commit -m "feat: JSON input, --in-format auto-detect, stdin support"
```

---

### Task 7: Documentation

Document JSON I/O, `--format`/`--in-format`, the version, the checksums, and the determinism caveat in the README.

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Update the Usage table and add sections**

In the flag table (`README.md:54-62`) add rows:

```markdown
| `--format <text\|json>` | Output format. `text` (default) prints the sections below plus a `#` verification header; `json` prints one structured document. |
| `--in-format <auto\|text\|json>` | Input format. `auto` (default) detects JSON by a leading `{`. Read `-` for stdin. |
```

After the "Output" section, add:

```markdown
## Versioning & verifying results

Every run reports the software `version`, the solver's `gurobi_version`, an
`input_checksum`, and a `result_checksum`. Text output carries them as a `#`
header; JSON output as top-level fields. To verify a result the website
published, run the same `version` locally on the same instance and compare
checksums.

- `input_checksum` hashes the *canonical, normalized* instance, not the raw
  file — text and JSON that describe the same instance share a checksum, and
  comments / whitespace / line order never change it.
- `result_checksum` hashes the canonical result (trades + cash), excluding the
  metadata fields themselves.

**Determinism caveat.** Gurobi may return a different but equally-optimal
solution across machines, versions, or thread counts. A matching
`result_checksum` proves identical plans; a *differing* one whose `kpi` values
match is a benign alternate optimum, not a wrong answer. This is why `kpi` and
`gurobi_version` are reported. The solver is not pinned to one thread for
reproducibility (the speed cost is not worth it).

## JSON input/output

Pass a JSON instance (auto-detected, or `--in-format json`):

```json
{
  "wishes":    [{"user": "alice", "give": ["A"], "take": ["B"], "n": 1, "m": 1}],
  "items":     [{"name": "A", "owner": "alice", "ask": 20}],
  "bids":      [{"user": "bob", "item": "A", "max_price": 25}],
  "budgets":   [{"user": "alice", "budget": 50}],
  "locations": [{"user": "u", "lat": -61.39, "lng": 34.22}],
  "takecaps":  [{"user": "u", "n": 1, "items": ["A", "AB"]}],
  "givecaps":  [{"user": "u", "n": 1, "items": ["A"]}]
}
```

All keys are optional. `wishes[].n`/`.m` default to the give/take list lengths.
Get JSON output with `--format json`.
```

- [ ] **Step 2: Verify the documented JSON runs**

Run:
```bash
printf '{"wishes":[{"user":"a","give":["A"],"take":["B"]},{"user":"b","give":["B"],"take":["A"]}]}' \
  | venv/bin/python main.py - --format json
```
Expected: a JSON document with `version`, both checksums, and two trades `A->B`, `B->A`.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: JSON I/O, versioning, and result verification"
```

---

## Self-Review

**Spec coverage:**
- Version constant + `gurobi_version` → Task 4 (`__version__`, `_gurobi_version`), doc Task 7. ✓
- JSON input, normalized schema, auto-detect, `--in-format`, stdin → Task 6. ✓
- JSON output schema, `--format` → Task 4. ✓
- `input_checksum` canonical-normalized → Task 4 (`normalized_input` + `checksum`), equivalence proven Task 6. ✓
- `result_checksum` excluding metadata → Task 4 (test asserts exclusion). ✓
- Text header → Task 5; check.py compatibility asserted. ✓
- `kpi` in output → Task 3 (`kpi_values`). ✓
- Determinism non-goal, no solver-param changes → honored throughout; documented Task 7. ✓
- Refactor of I/O edges only, MIP core untouched → Tasks 3/6. ✓
- Testing per spec (equivalence, parity, stability, auto-detect, no-solution) → Tasks 1-6.
  - **Gap noted:** the spec lists a no-solution JSON path test. Covered informally (empty arrays + `status`), but no dedicated test. Acceptable: the tiny testcases are always feasible, and constructing a guaranteed-infeasible-yet-model-building instance is fiddly; `kpi_values()`/`build_result` already guard `SolCount == 0`. If desired, add later.

**Placeholder scan:** No TBD/TODO/"handle edge cases"; every code step shows real code. The settlement-loop and directive-matching bodies reference exact existing line ranges to copy verbatim (not re-typed to avoid transcription drift), with the collapsed logic shown for orientation.

**Type consistency:** `build_result` keys (`trades`/`combos`/`cash_purchases`/`cash_summary`/`payments`/`settlement`/`kpi`/`status`) are used identically in `render_text`, `render_json`, and tests. `checksum` returns `"sha256:…"` everywhere. `render_text(result, input_checksum=None)` signature fixed in Task 4, used in Task 5. `has_money` removed from the payload dict (Task 4 note) so `result_checksum` covers the same dict the test reconstructs — resolved consistently.
