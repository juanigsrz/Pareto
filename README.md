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
  trades, participating users, and total shipping `distance` (minimized).


## Requirements

- Python 3
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
| `--kpi <list>` | Comma-separated objectives in priority order (leftmost first), e.g. `--kpi trades,users`. Choices: `trades` = max total trades (default); `users` = max users with ≥ 1 trade; `distance` = min total shipping distance (km). |
| `PARETO_TIME_LIMIT` | Solver time limit, seconds. |
| `PARETO_MIPGAP` | Accept a solution within this relative MIP gap. |
| `PARETO_STATS` | Print a `STATS …` line (vars, objective, gap, runtime) to stderr. |
| `PARETO_FAST` | Aggressive pruning just to get a valid solution. Set it to the min accepted float in the LP relaxation. |
| `PARETO_NOHUB` | Turn off HUB Optimization (groups up `A -> List`, `B -> List`, `dupcap List`) |


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
u1    : (2for1) A B -> X         # give up to 2 of {A, B}, receive ≥ 1 of {X}
u5    : (1for2) Catan -> Azul TTR  # give Catan, receive ≥ 2 of {Azul, TTR}
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


## Output

Pareto prints the chosen trades, then (when money is involved) the cash side:

```
Trade Results:
A -> B
B -> A
```

`X -> Y` reads "Y is given so that X is received" for each active move; bundle
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

Objective coefficients are kept integer on purpose: fractional tie-breaks defeat
Gurobi's integer-bound rounding and make proving optimality much slower.


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
checker and solver agree.


## Testing

The caps have self-contained subprocess tests (no test framework needed):

```bash
python test_dupcap.py
python test_takecap.py
```


## Benchmarking

Generate random instances and sweep solver scaling:

```bash
python generate_testcase.py --users 100 --money 0.5 --bundle 0.4 --out inst.txt
python benchmark.py --money 0.6 --users 10 50 100 200 --time-limit 60
```

`generate_testcase.py` builds cross-user trade cycles with tunable money/bundle
density; `benchmark.py` runs the sweep and tabulates variable counts, solver
runtime, and wall time, stopping once a solve no longer proves optimality in
time.
