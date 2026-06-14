# Pareto

A Mixed-Integer-Programming (MIP) solver for **complex math trades**.

Classic math-trade tools (e.g. TradeMaximizer) maximize the number of items
that change hands in pure barter cycles. Pareto trades raw speed for
expressiveness: on top of plain swaps it understands **N-to-M bundle trades**,
**cash bids/asks settled through a clearinghouse**, **per-user budgets**, and
**duplicate protection**. It models the whole instance as a single MIP and
solves it to proven optimality with [Gurobi](https://www.gurobi.com/).


## Features

- **Swaps** — ordinary `give -> take` trade cycles across users.
- **N-to-M bundles** — give *at most* N items to receive *at least* M (e.g.
  `2for1`, `1for2`).
- **Cash** — items can carry an *ask* price; users place *bids*; a global
  clearinghouse nets everyone out. Cash and barter compete for the same item.
- **Net budgets** — a user's spend minus earnings from items sold stays under a
  cap, enabling cash *pass-through chains* (sell one game to fund buying
  another).
- **Duplicate protection (`dupcap`)** — a user receives at most one copy of a
  given game, counting swap receipts and cash buys together.
- **Lexicographic objectives** — `--kpi` takes a priority-ordered list (e.g.
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


## Input format

One directive per line. `#` starts a comment; blank lines are ignored.

### Wishes

```
<user> : (<options>) <give items...> -> <take items...>
```

`<options>` is an `NforM` token meaning **give at most N, receive at least M**.
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

### Duplicate cap

```
dupcap <user> <item...>     # user receives at most ONE of these copies
```

Use it when several listed items are copies of the *same* game and the user
wants only one, regardless of whether it arrives by swap or by cash.

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
  clearinghouse — both discharge the same net balances.


## How it works

Pareto builds one MIP:

- Each swap is a binary edge; bundles route through a virtual combo node so a
  whole `NforM` group activates together.
- Per item: at most one slot — it can leave via swap *or* be sold for cash, not
  both. Swap in-flow equals out-flow (you only give an item if you receive one).
- Per user: `spend (swap receipts + buys, at ask) − earnings (own items sold) ≤
  budget`. Because earnings count, a user can fund a purchase by selling
  something in the same plan (cash chains).
- `dupcap` adds one constraint summing a user's swap-receive and buy indicators
  over the protected copies to ≤ 1.
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


## Testing

`dupcap` has a self-contained subprocess test (no test framework needed):

```bash
python test_dupcap.py
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
