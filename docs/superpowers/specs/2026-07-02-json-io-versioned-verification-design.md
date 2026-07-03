# JSON I/O + Versioned Verification — Design

Date: 2026-07-02

## Purpose

Two enhancements to the solver's input/output boundary:

1. **JSON input/output** — a structured, machine-friendly alternative to the
   line-oriented text format, so `main.py` is easier to drive programmatically
   (web backend, pipelines) without string-formatting directives.
2. **Versioned, checksummed results** — emit a software `version`, an
   `input_checksum`, and a `result_checksum` so a user who distrusts the hosted
   website can re-run the same version locally on the same input and confirm the
   published result, instead of blindly trusting it.

The MIP core (model build + solve) is untouched. Only the I/O edges change.

## Scope

In scope:
- JSON input parsing into the existing internal state (same globals the text
  parser fills).
- JSON output serialization of the full result (trades, combos, cash purchases,
  cash summary, payments, settlement) plus verification metadata.
- Version constant, input checksum, result checksum, in both output formats.
- Input-format auto-detection and a `--format` flag for output.
- A light refactor of `main.py`'s I/O edges into functions to make the above
  clean.

Out of scope (deliberately not changed):
- The MIP formulation, objective handling, solver params, KPIs.
- Forcing determinism (no `Threads=1`, no `Seed` pinning). See
  [Determinism](#determinism-non-goal).
- `check.py` gaining JSON support (may follow later; not this change).
- The `STATS` stderr line and `PARETO_*` env vars.

## Version

- `__version__ = "1.0.0"` constant near the top of `main.py`.
- The output also records `gurobi_version` (from `gp.gurobi.version()` as a
  dotted string, e.g. `"11.0.0"`). The solver version can change results, so it
  is part of the verification story.
- Semver. README documents: any change to the objective/formulation or solver
  behavior that can change results bumps at least the minor version. A pure I/O
  or docs change bumps patch.

## Input

### Selection

- Default `--in-format auto`: peek the first non-blank, non-comment
  (`#`-stripped) character of the source. `{` ⇒ JSON; anything else ⇒ text.
- `--in-format {auto,text,json}` overrides detection.
- Source may be a file path or `-` for stdin (text parsing already reads a
  file; stdin support is added alongside JSON).

### JSON schema (normalized object model)

Chosen over mirroring raw directive strings: it maps 1:1 to the internal state
and needs no re-parsing of embedded directive syntax. All top-level keys are
optional; an absent key means "none of those".

```json
{
  "wishes":    [{"user":"alice","give":["A"],"take":["B"],"n":1,"m":1}],
  "items":     [{"name":"A","owner":"alice","ask":20}],
  "bids":      [{"user":"bob","item":"A","max_price":25}],
  "users":     [{"name":"alice","budget":50}],
  "locations": [{"user":"u","lat":-61.39,"lng":34.22}],
  "takecaps":  [{"user":"u","n":1,"items":["A","AB"]}],
  "givecaps":  [{"user":"u","n":1,"items":["A"]}]
}
```

Field semantics mirror the text directives exactly:

- `wishes[].n` / `.m` default to `len(give)` / `len(take)` when omitted (same as
  the text form with no `NforM` option). Listing an item in `give` declares
  ownership (same `set_owner` call as text).
- `items[].ask` optional (absent ⇒ 0, i.e. barter-only).
- `users[].budget` optional here only in the sense that a user can also be
  introduced implicitly by appearing in any other field; a `users` entry with a
  `budget` sets the net-spend cap.
- `takecaps` / `givecaps` items are copy names; same owner-existence warning as
  text (`cap references item … with no declared owner`).

The same validation as the text path applies (owner conflicts raise, lat/lng
range checks, combo-can-never-activate warnings). Validation lives in shared
helpers so both parsers enforce identical rules.

## Output

### Selection

- `--format {text,json}`, default `text` (backward compatible).

### JSON schema

```json
{
  "version": "1.0.0",
  "gurobi_version": "11.0.0",
  "input_checksum":  "sha256:ab12…",
  "result_checksum": "sha256:cd34…",
  "status": "Optimal",
  "kpi": {"trades": 42, "users": 10},
  "trades":         [{"give":"B","take":"A"}],
  "combos":         [{"sent":["A","B"],"taken":["X"]}],
  "cash_purchases": [{"item":"B_GAME","from":"B","to":"A","price":20}],
  "cash_summary":   [{"user":"A","spent":20,"earned":0,"net":20,"cap":50}],
  "payments":       [{"from":"A","to":"B","amount":20}],
  "settlement":     [{"from":"A","to":"C","amount":20}]
}
```

- `trades[]`: `give`/`take` read the same as the text `take -> give` line
  (`give` is the item the receiver hands over, `take` is what they receive) —
  wording matches the README's "`X -> Y` reads 'Y is given so that X is
  received'". Concretely each entry is `{"give": <item given>, "take": <item
  received>}`.
- `combos[]`: `sent` and `taken` are the item lists of an activated bundle.
- Cash sections mirror the text `Cash Purchases`, `Cash Summary`, `Payments`,
  and `Settlement plan`. `cash_summary[].cap` is a number, or `null` for an
  unbounded budget (text prints `inf`).
- `kpi` reports each optimized objective's value (from `ObjNVal` /`ObjVal`),
  so two runs that pick different equal-optimal plans can still be compared on
  the optimum itself.
- When no solution: `status` set accordingly, solution arrays empty, and
  `result_checksum` computed over that empty result. (Text path keeps printing
  `No solution found.` to stderr and additionally emits the header.)

### Text output

Unchanged body. Add a header block (before `Trade Results:`) carrying the
verification metadata as comments so text users get the same guarantees:

```
# pareto 1.0.0  gurobi 11.0.0
# input_checksum  sha256:ab12…
# result_checksum sha256:cd34…
```

## Checksums

- **`input_checksum`** = `sha256` of the **canonical normalized input**, not the
  raw source bytes. After parsing (from either format) the internal state is
  re-serialized into one canonical JSON form (sorted keys, sorted arrays where
  order is semantically irrelevant, normalized number formatting) and hashed.
  Consequence: text and JSON inputs that describe the *same instance* produce the
  *same* `input_checksum`, and comments / whitespace / line ordering never
  affect it. This is what makes "download my text file, re-run, compare hash"
  robust.
- **`result_checksum`** = `sha256` of the **canonical result**: the same result
  object, canonicalized (sorted trades/combos/cash arrays, stable key order),
  **excluding** the `version`, `gurobi_version`, `input_checksum`, and
  `result_checksum` fields themselves (a hash cannot include itself, and the
  metadata is not part of "the answer").
- Serialized as `sha256:<lowercase-hex>`.
- One shared `canonical_json(obj) -> bytes` helper (`json.dumps(obj,
  sort_keys=True, separators=(",", ":"), ensure_ascii=False)` encoded UTF-8)
  feeds both, so the hashing rule is defined in exactly one place.

## Determinism (non-goal)

Explicitly **not** addressed. Gurobi can return different but equally-optimal
solutions across runs, thread counts, platforms, and versions. We do **not**
force `Threads=1` or pin a `Seed` — the performance cost is not worth it and the
user asked to keep the solver as-is.

Instead, honesty in the docs and the payload:
- `result_checksum` is canonical, so **given a solution** the hash is stable;
  the only source of mismatch is Gurobi picking a different optimum.
- Emitting `kpi` and `gurobi_version` lets a user whose local `result_checksum`
  differs confirm the runs are still equivalent (same optimum value, possibly a
  different plan). README documents this: a matching `result_checksum` proves
  identity; a differing one with a matching `kpi` is a benign alternate optimum.

## Refactor (I/O edges only)

`main.py` currently parses arguments, builds the model, solves, and prints — all
at module top level, and is not import-safe. This change keeps it a runnable
script but extracts the I/O boundary into functions:

- `load_input(src, in_format)` — dispatches to the existing text parser or the
  new JSON parser; both fill the same module globals.
- `parse_json_input(obj)` — the new JSON parser, sharing validation helpers with
  the text parser (owner conflicts, lat/lng ranges, cap-owner warnings).
- `normalized_input()` — builds the canonical input dict from the globals for
  hashing.
- `build_result()` — assembles the result dict (trades, combos, cash sections,
  kpi, status) from the solved model. Both serializers consume it.
- `render_text(result)` / `render_json(result)` — serialize the result dict.
- `canonical_json(obj)` and `checksum(obj)` — the single hashing rule.

The MIP construction between input and output is left in place. No unrelated
refactoring.

## Testing

- **Round-trip / equivalence**: for each existing `testcases/` instance, running
  the text input and a hand- or tool-converted JSON input yields the **same
  `input_checksum`** (proves the normalized-hash claim) and the same trades.
- **Format parity**: `--format json` and text output on the same solved model
  describe the same trades/cash (parse the JSON, compare to text lines).
- **Checksum stability**: re-serializing/re-hashing the same result twice is
  identical; reordering input lines / adding comments does not change
  `input_checksum`.
- **Auto-detect**: a file starting with `{` (after comments/blanks) is read as
  JSON; otherwise text; `--in-format` overrides.
- **No-solution path**: JSON output has empty arrays, a `status`, and a valid
  `result_checksum`.
- Follow the existing self-contained subprocess-test style
  (`test_takecap.py`), no new framework.

## Open questions

None outstanding. Decisions locked with the user:
- Input schema = normalized object model (not raw-directive mirror).
- `input_checksum` = canonical normalized (not raw bytes).
- Output format via `--format`; input auto-detected with `--in-format`
  override.
- No determinism forcing; plain canonical checksum, user handles alternate
  optima.
