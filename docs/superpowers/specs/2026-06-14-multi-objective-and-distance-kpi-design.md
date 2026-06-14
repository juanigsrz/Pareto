# Multi-objective KPIs + geographical-distance KPI

Date: 2026-06-14
File touched: `main.py` (plus one testcase, one test, README).

## Goal

Two related changes to the objective layer of the Pareto MIP solver:

1. **Multi-objective `--kpi`.** Accept a comma-separated list, e.g.
   `--kpi trades,users`, and optimize them lexicographically: leftmost is the
   primary objective, the next is secondary, and so on. Gurobi supports this
   natively via hierarchical multi-objective.

2. **New `distance` KPI.** A new objective that minimizes the total
   geographical shipping distance of the chosen trades. User locations come from
   a new input directive:

   ```
   location <user> <lat> <lng>      # e.g. location trader01 -61.3902 34.2251
   ```

## Semantics

### Distance = per item-move shipping distance

Every active move ships one item from its **current owner** to its
**receiver**:

- a swap receive-leg (simple swap take, or any combo take item), or
- a cash buy.

For each such move the cost is `haversine(loc[owner], loc[receiver])`, rounded
to integer kilometres. The `distance` objective minimizes the sum over all
active moves. Each item that changes hands is received exactly once, so each
move is counted exactly once.

If either endpoint has no `location`, or the owner is unknown, the move
contributes 0 (no distance information ⇒ no penalty).

### Distance metric

Haversine great-circle distance, `R = 6371 km`, result `round()`-ed to an
integer. Integer coefficients are deliberate: fractional objective coefficients
defeat Gurobi's integer-bound rounding and slow proving optimality (see the
existing note at `main.py` lines 259-260). Distances cached per unordered
user-pair.

### Lexicographic combination

- `model.ModelSense = GRB.MAXIMIZE`.
- Each KPI builds an expression in **maximize form**:
  - `trades`   → `+(sum(swaps) + sum(buys))`
  - `users`    → `+sum(traded)`   (per-user "≥1 trade" indicators)
  - `distance` → `-sum(dist_term)` (minimize distance ≡ maximize its negation)
- Single KPI: `model.setObjective(expr, GRB.MAXIMIZE)`.
- Multiple KPIs: for the k-th KPI in the list (0-based),
  `model.setObjectiveN(expr, index=k, priority=len(kpis) - k)`.
  Higher priority is optimized first, so list position = priority order.
  Strict lexicographic (Gurobi default `ObjNRelTol = ObjNAbsTol = 0`): each
  higher-priority objective is held at its optimum while the next is optimized.

## Changes in `main.py`

1. **`--kpi` parsing** (currently lines 107-109).
   Replace `choices=[...]` with a custom `type` function: split on `,`, strip,
   reject empty tokens, validate each against `{"trades","users","distance"}`,
   preserve order, reject duplicates. Default `["trades"]`.

2. **`location` directive** (in `parse_file`).
   Add a regex matching `location <user> <lat> <lng>` with optionally-signed
   decimal lat/lng. Store in `location = {}  # user -> (lat, lng)`. Add the user
   to `users`. Validate `lat ∈ [-90, 90]` and `lng ∈ [-180, 180]`; raise
   `ValueError` otherwise (catches swapped lat/lng and typos).

3. **`haversine(a, b)`** helper plus a per-pair cache; returns integer km.

4. **Distance terms.** Build from existing structures — no new edge vars:
   - swap legs: `spend_swap[user]` already holds `(take_iid, var)` for both
     simple swaps and combos.
   - cash legs: `buy[(u, iid)]`.
   For each, `term = dist(loc[owner[take_iid]], loc[receiver]) * var`, skipping
   any with missing owner/location.

5. **Objective build** (replace the block at lines 270-294).
   - Build `participation` / `traded` vars when `"users" in kpis` (currently
     gated on `_args.kpi == "users"`); also keep building them when
     `PARETO_STATS` is set, as today.
   - Assemble the maximize-form expression per KPI and wire up
     `setObjective` (single) or `setObjectiveN` (multiple) as above.

6. **Stats reporting** (`PARETO_STATS`, lines 302-312).
   With multiple objectives `model.ObjVal` is ambiguous; report per-objective
   `ObjNVal` (iterate `ObjNumber`) when more than one KPI, else keep `ObjVal`.
   Trade/cash output (lines 318+) is unchanged.

## Testing

- New testcase file under `testcases/` containing `location` lines plus a small
  set of competing swaps where a geographically closer counterpart exists.
- A self-contained subprocess test in the style of `test_dupcap.py`:
  - `--kpi trades,distance`: with trade count tied, the closer-user trade is
    chosen.
  - lexicographic ordering: a configuration where `trades,distance` and
    `distance,trades` yield different outcomes, confirming priority by position.
- Run `python test_dupcap.py` to confirm no regression.

## Docs

README updates:
- `--kpi` row: note comma-separated lexicographic list; add `distance`.
- Input format: document the `location` directive.
- Features / "How it works": mention the distance objective and that missing
  locations contribute 0.

## Out of scope

- Per-user-pair distance (counting a trading pair once) — rejected in favour of
  per-item-move shipping.
- Any change to barter/cash/budget/dupcap modelling.
- Distance units other than integer km; non-haversine metrics.
