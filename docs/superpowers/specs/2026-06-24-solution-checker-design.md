# Solution Checker — Design

Date: 2026-06-24

## Purpose

A standalone tool that, given a Pareto instance (`INPUT.txt`) and a solver
output (`OUTPUT.txt`), verifies the output is a **legal / feasible** solution.
It does *not* check optimality. It re-implements the constraint checks
independently of `main.py` so it can catch solver bugs.

## Invocation

```bash
python check.py INPUT.txt OUTPUT.txt
python main.py in.txt | python check.py in.txt -      # OUTPUT '-' reads stdin
```

- Exit `0` and print `OK: <S> swap moves, <C> cash moves, all checks passed`
  when valid.
- Exit `1` and print one `VIOLATION: <message>` per problem (all violations
  collected, not just the first) when invalid.

## Scope

In scope: legality of swap moves, cash moves, single-use of items,
flow reciprocity, `takecap` / `givecap`, and per-user budgets.

Out of scope (deliberately not checked):
- **Optimality** — whether the trade count / KPI is maximal.
- **Distance / KPI** values and `location` lines.
- The **Cash Summary / Payments / Settlement plan** prose (derived reporting).

## Independence

`check.py` shares no code with `main.py`. It has its own input parser. This is
intentional: a checker that reused the solver's logic could not catch the
solver's bugs. (`main.py` is also not import-safe — it parses and solves at
import time.)

## Component 1 — Input parser

Own minimal parser over the input file, in item-**name** space (item names are
unique per physical copy). Builds:

- `owner: name -> user` — from `item <name> owner <user>` lines and from the
  give-side of every wish (giving an item implies owning it).
- `ask: name -> int` — from `item ... ask <price>`.
- `bids: set of (user, name)` for explicit `bid` lines whose `max_price >= ask`.
  (Bids that do not clear, or self-bids, are dropped — they create no edge.)
- `wishes: list of (user, give[], take[], N, M)`. `NforM` token overrides the
  default `N=len(give), M=len(take)`.
- `take_groups: list of (user, N, names[])` — from `takecap` and `dupcap`
  (`dupcap` => `N=1`).
- `give_groups: list of (user, N, names[])` — from `givecap`.
- `budget: user -> int` — from `user <name> budget <amount>`.

`location` lines are parsed-and-ignored. Lines that don't match are ignored
(the checker is not an input validator; it trusts the instance).

## Component 2 — Output parser

A section-aware line scan. Header lines switch the active section:

- `Trade Results:` → **swap-move** section.
- `Cash Purchases:` → **cash-move** section.
- `Cash Summary:`, `Payments:`, `Settlement plan:` → ignored sections.

Within the swap section, each non-empty line `G... -> R...` splits on `->` into
a list of **given** item names (left) and a list of **received** item names
(right). Simple swaps (1 give, 1 take), bundle combos, and hub merges all print
in this same form, so they parse uniformly.

Within the cash section, each line matches
`<item>: <seller> -> <buyer>  (<buyer> pays <seller> $<price>)` and yields a
`(item, seller, buyer, price)` tuple.

Produces:
- `swap_moves: list of (given[], received[])`
- `cash_moves: list of (item, seller, buyer, price)`

## Component 3 — Checks

All checks run; every violation is recorded.

### A. Swap backing

For each swap move `(G, R)`:
- Every name in `G` must have a declared owner, and they must all share the
  same owner `u`. Otherwise: `items given together but not all owned by one user`.
- There must exist a wish by `u` with `set(G) ⊆ give`, `set(R) ⊆ take`,
  `len(G) ≤ N`, and `len(R) ≥ M`. Otherwise: `swap move not backed by any wish`.
- Every name in `R` must have a declared owner (it is a real item).

### B. Single-use + reciprocity

Per item name, count over all moves:
- `g` = appearances on the **left** of a swap line (given via swap)
- `r` = appearances on the **right** of a swap line (received via swap)
- `c` = appearances as the sold item in a cash move

Assert:
- `g ≤ 1`, `r ≤ 1`, `c ≤ 1` — nothing moves more than once per channel.
- `g == r` — swap balance: an item is given via swap iff it is received via
  swap. This is the pseudo-cycle closing (every sent item lands with a wanter,
  every received item came from a giver).
- `g + c ≤ 1` — the same copy is not both swapped and cash-sold.

Together these are "no item sent twice" and "something is received in exchange".

### C. Cash legality

For each cash move `(item, seller, buyer, price)`:
- `owner[item] == seller`.
- `item` has an `ask`, and `price == ask[item]`.
- `buyer != seller`.
- A clearing edge exists: either `(buyer, item) in bids` (explicit clearing
  bid), **or** `item` appears in the take-side of some wish by `buyer`
  (implicit bid — the solver creates a buy var for any wished, asked item).
- `item` does not also appear in any swap move (enforced by B's `g + c ≤ 1`,
  restated here as a cash-specific message).

### D. Caps

Attribute each move to a user via ownership: in a swap move `(G, R)` the giver/
receiver is `u = owner(G)`; in a cash move the seller and buyer are explicit.

- `takecap (u, N, items)`: count distinct items from `items` that `u`
  **receives** — `R` of `u`'s swap moves plus cash moves with `buyer == u`.
  Assert `count ≤ N`.
- `givecap (u, N, items)`: count distinct items from `items` that `u`
  **gives** — `G` of `u`'s swap moves plus cash moves with `seller == u`.
  Assert `count ≤ N`.

### E. Budget (swaps treated as moneyless)

For each user `u` with a `budget`:
- `spend = Σ ask[i]` over items `i` that `u` **buys via cash**.
- `earn  = Σ ask[i]` over items `i` that `u` **sells via cash**.
- Assert `spend - earn ≤ budget[u]`.

> **Deliberate divergence from `main.py`.** The solver (line 354) also charges a
> swap-*received* item that carries an ask, and credits a swap-*given* asked
> item. This checker treats swaps as pure barter (no money on either side), per
> the decision that the intuitive rule is "only cash moves money." Consequence:
> the checker may flag a solution the solver considers within budget. That is
> intended — it surfaces the line-354 behavior for review rather than blessing
> it.

## Report format

```
VIOLATION: <message>          # zero or more, one per problem
OK: 5 swap moves, 2 cash moves, all checks passed   # only when none
```

Exit `1` if any violation, else `0`.

## Testing

The checker ships with its own checks; verification during implementation runs
it against every `testcases/` and `testcases/money/` golden output (which are
known-good) expecting exit `0`, plus a few hand-built broken outputs expecting
exit `1` with the right message. (The pytest harness that auto-runs the whole
suite through the checker is out of scope for this iteration — standalone CLI
only, per the form-factor decision.)
</content>
</invoke>
