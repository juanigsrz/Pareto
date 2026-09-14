# Pareto

A Mixed-Integer-Programming (MIP) solver for **complex math trades**.

Classic math-trade tools (e.g. TradeMaximizer) maximize the number of items
that change hands in pure barter cycles. Pareto trades raw speed for
expressiveness: on top of plain swaps it understands **N-to-M bundle trades**,
**cash bids/asks settled through a clearinghouse**, **per-user budgets**, and
**duplicate protection**. It models the whole instance as a single MIP and
solves it to proven optimality with [Gurobi](https://www.gurobi.com/).


## Features

- **Swaps**: ordinary `give -> take` trade cycles across users.
- **N-for-M trades**: give *any* N items to receive *any* M (e.g.
  `2for1`, `1for2`).
- **Cash**: items can carry an *ask* price; users place *bids*; a global
  clearinghouse nets everyone out. Cash and barter compete for the same item.
- **Net budgets**: a user's spend minus earnings from items sold stays under a
  cap, enabling cash *pass-through chains* (sell one game to fund buying
  another).
- **Take / give caps (`takecap` / `givecap`)**: bound how many of a listed set
  of copies a user may **receive** (`takecap`) or **give** (`givecap`),
  counting swaps and cash together. `dupcap` is the legacy `takecap … 1` alias.
- **Lexicographic objectives**: `--kpi` takes a priority-ordered list (e.g.
  `trades,users`); each objective is optimized in turn. Available KPIs: total
  trades, participating users, total shipping `distance` (minimized), and
  `hubload` (minimized, below).
- **Hub-aware logistics (`hubload`)**: for centralized events where everything
  not shipped city-to-city is processed at one hub city, minimize the items the
  hub has to handle. Items between two non-hub cities bypass the hub when the
  pair fills a *direct box* of at least `boxmin` items; same-city trades are
  local hand-offs. Every run with a `hub` prints the derived shipping plan.
- **Controlled trade-offs**: `--kpi-tol` lets a higher-priority objective give
  up a fraction of its optimum for the ones after it; `--blend` replaces the
  list with one weighted objective (an explicit exchange rate).


## Requirements

- Python ≥ 3.10 (the pinned `gurobipy` publishes no wheels for older versions)
- [`gurobipy`](https://pypi.org/project/gurobipy/) (pinned in `requirements.txt`)
- A Gurobi license. The bundled `gurobipy` ships a size-limited trial; larger
  instances need a full or [free academic](https://www.gurobi.com/academia/)
  license.

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```


## Usage

```bash
python main.py INPUT.txt
```

Options and environment variables:

| Flag / Env | Effect |
|---|---|
| `--kpi <list>` | Comma-separated objectives in priority order (leftmost first), e.g. `--kpi trades,users`. Choices: `trades` = max total trades (default); `users` = max users with ≥ 1 trade; `distance` = min total shipping distance (km); `hubload` = min items processed at the hub city (needs `hub` and `city` directives). A minimized KPI cannot come first unless `--blend` is used. |
| `--kpi-tol <list>` | Lexicographic only. Relative tolerances aligned with `--kpi`: objective *k* may degrade by at most this fraction of its optimum while the objectives after it are optimized. `--kpi trades,hubload --kpi-tol 0.05` gives up ≤ 5 % of the trades to cut hub load. Default strict (0). |
| `--blend <list>` | Replace the lexicographic list with **one** weighted objective: positive integer weights aligned with `--kpi`, minimized KPIs negated. `--kpi trades,hubload --blend 3,1` values a trade at 3 and each hub-processed item at −1, so a trade is only dropped when it saves more than three hub items. Mutually exclusive with `--kpi-tol`. |
| `--format <text\|json>` | Output format. `text` (default) prints the sections below plus a `#` verification header; `json` prints one structured document. |
| `--in-format <auto\|text\|json>` | Input format. `auto` (default) detects JSON by a leading `{`. Read `-` for stdin. |
| `PARETO_TIME_LIMIT` | Solver time limit, seconds. |
| `PARETO_MIPGAP` | Accept a solution within this relative MIP gap. |
| `PARETO_STATS` | Print a `STATS …` line (vars, objective, gap, runtime) to stderr. |
| `PARETO_FAST` | Aggressive pruning just to get a valid solution. Set it to the min accepted float in the LP relaxation. |
| `PARETO_NOHUB` | Turn off HUB Optimization (groups up `A -> List`, `B -> List`, `dupcap List`) |
| `PARETO_MIPFOCUS` | Gurobi `MIPFocus`. `1` chases incumbents instead of the bound — the right setting for `hubload`, whose root bound barely moves (see below). |
| `PARETO_NORELHEUR` | Seconds of Gurobi's NoRel heuristic before the root relaxation. Finds a first solution on instances whose root LP is itself slow. |
| `PARETO_METHOD` | Gurobi `Method` for the root LP (`1` = dual simplex, avoiding the costly barrier ordering). |
| `PARETO_GAPABS_MINVARS` | Var-count threshold past which a single-objective solve may stop ~1 objective unit short (default 20000; `0` disables). |


## Input format

One directive per line. `#` starts a comment; blank lines are ignored.

### Wishes

```
<user> : (<options>) <give items...> -> <take items...>
```

`<options>` is an `NforM` token meaning **give any N, receive any M**.
The simplest case is a one-for-one swap:

```
alice : (1for1) A -> B          # alice gives A, wants B
u1    : (2for1) A B -> X         # give any 2 of {A, B}, receive any 1 of {X}
u5    : (1for2) Catan -> Azul TTR  # give Catan, receive any 2 of {Azul, TTR}
```

Listing an item on the *give* side declares the user as its owner.

### Items, asks, bids, budgets

```
item <name> owner <user> [ask <price>]   # declare ownership; optional sale price
bid  <user> <item> <max_price>           # user will pay up to max_price in cash
user <name> budget <amount>              # net-spend cap (absent => unlimited)
location <user> <lat> <lng>              # user location for the distance KPI
```

A bid creates a cash edge only when it clears the ask (`max_price >= ask`) and
the bidder is not the owner.

### Take / give caps

```
takecap <user> <N> <item...>   # user RECEIVES at most N of these copies
givecap <user> <N> <item...>   # user GIVES   at most N of these copies
dupcap  <user> <item...>       # legacy alias for: takecap <user> 1 <item...>
```

Both count swaps and cash together. `takecap` is receiver-side duplicate
protection: list copies of the same game so the user ends up with at most N
regardless of whether they arrive by swap or cash. `givecap` is the give-side
mirror over the user's **own** copies, list a physical item alongside every
combo/bundle item that contains it so it can leave at most N times in total
(e.g. `givecap u 1 A AB` lets `A` go out standalone *or* inside combo `AB`, not
both). Every `givecap` item must be owned by the named user.

### Locations (distance KPI)

```
location <user> <lat> <lng>     # e.g. location trader01 -61.3902 34.2251
```

Used only by `--kpi distance`. Each item move ships the item from its owner to
the receiver; the `distance` objective minimizes the sum of those great-circle
distances (haversine, integer km). A move whose owner or receiver has no
`location` contributes 0.

### Cities and hub (hubload KPI)

```
city <user> <city name>        # free text after the user, e.g. city ana Santa Fe
hub <city name>                # the city that processes everything not boxed
boxmin <N>                     # min items for a direct city->city box (default 5)
```

Models a centralized event: after the solve, each item either changes hands
locally (owner and receiver in the same city), travels in a **direct box** from
its owner's city to the receiver's city, or is sent to the hub, sorted there,
and forwarded. A direct box between two non-hub cities is only sent when at
least `boxmin` items go that way; anything to or from the hub, any pair that
falls short of a box, and any move with an unknown city is *hub-processed*.
`--kpi hubload` minimizes the hub-processed count. A user without a `city`
line is warned about and counts as hub-processed.


## Output

Pareto prints the chosen trades, then (when money is involved) the cash side:

```
Trade Results:
A -> B
B -> A
```

`X -> Y` reads "X is given so that Y is received" for each active move; bundle
trades print as `sent... -> taken...`. With cash:

```
Cash Purchases:
B_GAME: B -> A  (A pays B $20)
C_GAME: C -> B  (B pays C $20)

Cash Summary:
  A: spent $20, earned $0, net $20 (owes) (cap $50)
  B: spent $20, earned $20, net $0 (even) (cap $0)
  C: spent $0, earned $20, net $-20 (receives) (cap $0)

Payments:
  A pays B $20
  B pays C $20

Settlement plan:
  A pays C $20
```

- **Payments** reconstructs who owes whom from the actual item flows.
- **Settlement plan** is an equivalent, minimal-transfer settlement through the
  clearinghouse, both discharge the same net balances.

When the instance names a `hub`, every run (with or without `--kpi hubload`)
also prints the derived logistics:

```
Shipping plan (hub CABA, box >= 5 items):
  Direct boxes: 2 (10 items bypass the hub)
    Cordoba -> Mendoza: 5 items
    Mendoza -> Cordoba: 5 items
  Via hub: 12 items
  Local hand-offs: 4 items
```

JSON output carries the same under `shipping` with per-item detail (`boxes`,
`via_hub`, `local`, each move with its users and cities).


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
  "givecaps":  [{"user": "u", "n": 1, "items": ["A"]}],
  "cities":    [{"user": "u", "city": "CABA"}],
  "hub":       "CABA",
  "box_min":   5
}
```

All keys are optional. `wishes[].n`/`.m` default to the give/take list lengths.
Get JSON output with `--format json`. `pareto_io.normalize_instance(doc)` is the
canonical form that `input_checksum` hashes; it is pure Python (no Gurobi), so
a consumer can vendor `pareto_io.py` and recompute the checksum itself.


## How it works

Pareto builds one MIP:

- Each swap is a binary edge; bundles route through a virtual combo node so a
  whole `NforM` group activates together.
- Per item: at most one slot, it can leave via swap *or* be sold for cash, not
  both. Swap in-flow equals out-flow (you only give an item if you receive one).
- Per user: `cash spend (buys, at ask), cash earnings (own items sold for cash)
  ≤ budget`. Only cash moves money, a barter swap is free even when the item
  carries an ask (the ask is just the *cash* price), so swap legs never touch the
  budget. Because cash earnings count, a user can fund a purchase by *selling* a
  game for cash in the same plan (cash chains), but not by bartering one away.
- `takecap` / `givecap` each add one constraint per group: `takecap` sums a
  user's swap-receive and buy indicators over the listed copies to ≤ N;
  `givecap` sums the swap-supply and cash-sale indicators of the user's own
  copies to ≤ N.
- KPIs combine lexicographically (Gurobi hierarchical multi-objective): `trades`
  maximizes total moves, `users` maximizes distinct participants, `distance`
  minimizes total owner→receiver shipping km. List order sets priority.
  `--kpi-tol` maps to Gurobi's per-objective relative tolerance; `--blend`
  builds a single weighted objective instead.
- `hubload`: each move's owner city → receiver city is classified once. Same
  city: nothing. Hub at either end, or unknown city: one hub item. Otherwise the
  move joins its city pair's flow; per pair the model has a binary *box is sent*
  `B`, an integer `boxed ≤ flow`, `boxed ≤ |candidates|·B`, and `flow ≥ boxmin·B`.
  The pair's hub load is `flow − boxed`, so minimizing hub load sends the box
  whenever the flow reaches `boxmin`. Pairs with fewer candidate moves than
  `boxmin` get no variables at all.

Objective coefficients are kept integer on purpose: fractional tie-breaks defeat
Gurobi's integer-bound rounding and make proving optimality much slower. That is
also why `--blend` takes integer weights.


## Trading volume against hub congestion

`hubload` after `trades` in a strict lexicographic list only rearranges *which*
maximal set of trades happens; it never gives one up. To decongest the hub you
have to allow fewer trades, and there are two ways to say how much:

- `--kpi-tol X` fixes a **floor on trades** (≥ (1−X) of the optimum) and then
  minimizes hub load. It is a guarantee on volume, but the solver will spend the
  whole allowance even on exchanges that save one hub item per trade lost.
- `--blend T,H` fixes an **exchange rate**: a trade is dropped only if it saves
  more than T/H hub items. Nothing is spent on bad exchanges, but the trade
  count is not bounded in advance.

`hubload_tradeoff.py INSTANCE` runs both families on one instance and tabulates
them. On a generated 120-user instance with Argentina-like population weights
(45 % in the hub city, 6 wants per user):

| strategy | trades | hub items | direct boxes | items in boxes |
|---|---|---|---|---|
| `trades` only | 197 | 175 | 2 | 10 |
| `trades,hubload` strict | 197 | 172 | 2 | 10 |
| `--kpi-tol 0.02` | 194 | 154 | 5 | 25 |
| `--kpi-tol 0.05` | 188 | 146 | 5 | 26 |
| `--kpi-tol 0.1` | 178 | 128 | 7 | 36 |
| `--blend 5,1` | 195 | 161 | 4 | 20 |
| `--blend 3,1` | 193 | 154 | 5 | 25 |
| `--blend 2,1` | 191 | 149 | 5 | 26 |
| `--blend 1,1` | 175 | 125 | 7 | 37 |

Giving up 2 % of the trades removed 12 % of the hub's workload and more than
doubled the boxed items; each further trade bought progressively less. On two
sparser instances (≈ 0.6 trades per user) no city pair ever reached five items,
so no box could form and every tolerance was spent one-for-one, a pure loss.
Two practical consequences: start with `--blend 3,1` (or `--kpi-tol 0.02` when
the organizer wants a hard promise on volume), and remember that box formation
depends on flow density far more than on the objective. Lower `boxmin` if the
carrier allows it, and encourage more wants per participant.

### Why `hubload` is slow, and what the gap means

Expect `hubload` to take far longer than the same instance under `trades`, and
expect the reported gap to look terrible long after the answer has stopped
improving. A pair costs `flow` below `boxmin` and nothing at or above it, and
the lower convex envelope of that step is flat zero — so in the root relaxation
`B` goes fractional, `boxed` rises to meet `flow`, and every pair relaxes to
zero load. The bound therefore starts at the forced-hub count and hardly moves;
branch-and-bound closes the gap by exhausting the tree, not by the bound rising.
No cut in these variables can fix that, it is the shape of the objective.

In practice: run it with `PARETO_MIPFOCUS=1` and a `PARETO_TIME_LIMIT`, then
take the incumbent and read the shipping plan. `PARETO_MIPGAP` is not useful
here — it is measured against the bound that never moves.


## Examples

Ready-to-run instances live in `testcases/` (barter) and `testcases/money/`
(cash, with annotated expected results):

```bash
python main.py testcases/2for11for2.txt
python main.py testcases/money/cashchain.txt
```


## Checking a solution

`check.py` independently verifies that a solver output is a **legal** solution,
without trusting the solver. It re-derives every constraint from the instance:
each swap is backed by a real wish, no item moves twice (swap or cash), every
given item has something received in exchange, cash sales clear the ask, and
`takecap` / `givecap` / budgets hold. It checks legality only (not optimality).

```bash
python check.py INPUT.txt OUTPUT.txt
python main.py in.txt | python check.py in.txt -      # OUTPUT '-' reads stdin
```

Exit `0` prints an `OK` line; exit `1` prints one `VIOLATION: …` per problem.
Budgets are checked as pure barter, only cash moves money, so a swap-received
item with an ask is free to the receiver. The solver enforces the same rule, so
checker and solver agree. `NforM` is checked as **exactly** N given and exactly
M received, the solver's semantics. The `Shipping plan` section is derived
reporting and is skipped. The checker reads text output only; JSON output is
not parsed (yet).


## Testing

Every `test_*.py` is a self-contained subprocess test (no framework needed; the
files also run unchanged under `pytest`):

```bash
for t in test_*.py; do python "$t" || break; done
```

`test_hubload.py` covers the `hubload` KPI, `--kpi-tol`, `--blend`, and the
checksum contract; `test_check_exact.py` locks the checker's exact-NforM rule.


## Importing a FastTradeMaximizer instance

`ftm_to_pareto.py` converts an FTM wants file, plus a `name;user;location`
CSV, into a Pareto instance carrying the `city` / `hub` / `boxmin` directives
`hubload` needs:

```bash
python ftm_to_pareto.py wants.txt users.csv out.txt [out_items.csv] [--cities=A,B]
python verify_conversion.py wants.txt users.csv out.txt   # must print "equivalent"
```

`--cities` keeps only participants from the named cities, which is how you cut
a full event down to something a size-limited licence will solve.

FTM **dummy items are not copied across**. A dummy is owned by the user who
lists it, so `A -> %G` is a take-leg on an item its own wisher owns — exactly
what the sanitizer above drops, which would delete every want group in the
file. They are rewritten instead as the equivalent pattern Pareto models
natively, one 1for1 wish per (offered item, group) plus a `dupcap`:

```
(U) A : %G ...            U : (1for1) A -> X1 X2 ...
(U) %G : X1 X2 ...   ->   U : (1for1) B -> X1 X2 ...
(U) B : %G ...            dupcap U X1 X2 ...
```

Both readings say "at most one copy out of the group reaches U, and it costs U
one of the items that asked for it", and this is the shape the hub-and-spoke
compaction collapses, so the expanded wish list costs no extra variables.

Copies that can never move are pruned: a copy whose owner filed no want line
can never be given, so nothing can be given for it either, and that cascades.
`verify_conversion.py` re-derives the FTM graph and asserts that every surviving
item may receive exactly the copies it could before, that no want group was
lost, and that every pruned copy really was unreachable.

## Benchmarking

Generate random instances and sweep solver scaling:

```bash
python generate_testcase.py --users 100 --money 0.5 --bundle 0.4 --out inst.txt
python benchmark.py --money 0.6 --users 10 50 100 200 --time-limit 60
```

`generate_testcase.py` builds cross-user trade cycles with tunable money/bundle
density; `benchmark.py` runs the sweep and tabulates variable counts, solver
runtime, and wall time, stopping once a solve no longer proves optimality in
time. `--cities "CABA:45,Cordoba:12,..." --hub CABA --boxmin 5` places users by
population weight for `hubload` experiments:

```bash
python generate_testcase.py --users 120 --items 3 --wants 6 \
  --cities "CABA:45,GBA:15,Cordoba:12,SantaFe:8,Mendoza:7,Tucuman:5,Salta:4,Neuquen:4" \
  --hub CABA --boxmin 5 --out arg.txt
python hubload_tradeoff.py arg.txt --tols 0.02,0.05,0.1 --blends 5:1,3:1,2:1
```
