# Pareto as a Modal.com service — design

**Date:** 2026-06-14
**Branch:** `feat/modal-service`
**Status:** approved

## Goal

Expose the Pareto MIP solver as a hosted [Modal](https://modal.com) service so
instances can be solved remotely on Modal's compute, backed by a Gurobi WLS
(Web License Service) license configured on the platform. Modal allows up to 32
CPUs; experimentally ~8 threads is optimal for the single-strategy use case
(more threads only help when running multiple Gurobi strategies concurrently),
so the worker runs with `cpu=8` and `Threads=8`.

Today `main.py` is a single ~520-line script that executes everything at module
top level (argument parsing, model build, optimize, print). To call the solver
in-process it must first become importable functions. This work does that
refactor and adds the Modal app on top.

## Decisions (from brainstorming)

| Topic | Decision |
|---|---|
| Caller interface | HTTP web endpoint |
| Request format | JSON |
| Response format | Fully structured JSON |
| Auth | Modal proxy auth (Modal-Key / Modal-Secret) |
| Execution model | Async submit + poll (Modal `FunctionCall` as job store) |
| Refactor structure | Approach A — small package |

## Architecture

Four units, each with one purpose and a defined interface.

### `pareto_core.py` — pure solver

No I/O, no `argparse`, no top-level execution. Everything that runs today at
module scope moves into functions.

- `parse(text: str) -> Instance`
  - Port of current `parse_file`, reading from a string instead of a path.
  - `Instance` is a dataclass holding the parsed state currently kept in module
    state on an `Instance` dataclass (`wishes, users, budget, owner, ask, bids,
    take_groups, give_groups, location, item_to_id, id_to_item`) so many solves
    can share one process without leakage. Text and JSON input are both accepted
    (`parse(raw, in_format="auto")`).
  - Parse/validation errors raise `ValueError` (as today).
- `build(inst, kpi, *, env=None, quiet=True, threads=None, time_limit=None, mipgap=None, want_stats=False) -> Build`
  - Builds the MIP exactly as the CLI does: hub-and-spoke compaction, combos,
    cash buys, takecap/givecap, cash-only budgets, KPIs, lexicographic
    multi-objective, plus the `PARETO_*` env knobs (FAST, NOHUB, GAPABS, …).
  - Accepts an optional Gurobi `env` so the Modal worker can inject WLS
    credentials; the CLI passes `None` (default local license). `quiet` picks the
    silent env / `OutputFlag`; `time_limit`/`mipgap` fall back to `PARETO_*` env.
- `solve(raw, kpi=("trades",), *, in_format="auto", env=None, quiet=True, threads=None, time_limit=None, mipgap=None, want_stats=False) -> Solution`
  - Orchestrates parse → build → `optimize()` → `collect()`.

`Solution` dataclass: `result` (the dict below), `input_checksum`, `status`,
`has_solution`. The canonical result dict (identical to the CLI's `--format json`
body, minus the meta header) is:

```
status: str                 # "Optimal" | "TimeLimit" | "Infeasible" | "Status<n>"
kpi: dict                   # {kpi_name: value or null}; distance reported positive
trades: list                # {"give": <item>, "take": <item>}
combos: list                # {"sent": [<item>...], "taken": [<item>...]}
cash_purchases: list        # {"item", "from", "to", "price"}
cash_summary: list          # {"user", "spent", "earned", "net", "cap"}
payments: list              # {"from", "to", "amount"}
settlement: list            # {"from", "to", "amount"}
stats: dict                 # (only when want_stats) swap_vars, buy_vars, combos,
                            # items, users_traded, total_users, kpi, runtime
```

All lists are canonically sorted, so an equal solution serializes identically
regardless of Gurobi variable order or hash seed.

### `serialize.py` — output renderers

- `render_text(result, input_checksum=None) -> str` / `to_text(Solution)` —
  reproduces the CLI stdout **byte-for-byte** (`Trade Results`, combo lines,
  `Cash Purchases`, `Cash Summary`, `Payments`, `Settlement plan`), with an
  optional `# pareto … / # input_checksum / # result_checksum` header.
- `render_json(result, checksum)` / `to_dict(Solution) -> dict` — the structured
  JSON body: `{version, gurobi_version, input_checksum, result_checksum, **result}`.

### `main.py` — thin CLI (back-compat)

Keeps the existing surface so current tests and `benchmark.py` are untouched:

- `--kpi` flag (reusing `parse_kpi_list`).
- Env vars `PARETO_TIME_LIMIT`, `PARETO_MIPGAP`, `PARETO_STATS`.
- Calls `solve()`, prints `to_text()` to stdout, STATS line to stderr.
- Same warnings (`WARNING: solver status is …`, `No solution found.`).

### `modal_app.py` — Modal application

- **Image:** `modal.Image.debian_slim().pip_install("gurobipy==13.0.2", "fastapi")`.
- **License:** Modal Secret `gurobi-wls` provides env vars `WLSACCESSID`,
  `WLSSECRET`, `LICENSEID`. The worker constructs
  `gp.Env(params={"WLSACCESSID":…, "WLSSECRET":…, "LICENSEID":…})` and passes it
  to `solve(..., env=env)`.
- **Worker** `solve_job(payload: dict) -> dict`:
  - `@app.function(cpu=8, secrets=[Secret.from_name("gurobi-wls")])`.
  - Calls `solve(..., threads=8)` (Gurobi thread count, not a Modal arg),
    returns `to_dict(result)`.
  - Catches `ValueError` (parse/build errors) → `{"status": "error", "error": <msg>}`.
- **Web endpoint** (FastAPI ASGI, proxy auth required):
  - `POST /solve` body `{instance, kpi?, time_limit?, mipgap?, stats?}`:
    validate shape + knobs (fast `400` on bad input), then
    `call = solve_job.spawn(payload)` → `202 {"job_id": call.object_id}`.
  - `GET /result/{job_id}`: `FunctionCall.from_id(job_id).get(timeout=0)`:
    - ready → `{"status": "done", "result": {…to_dict…}}`
    - `TimeoutError` → `{"status": "pending"}`
    - failed → `{"status": "error", "error": <msg>}`

## Validation / limits

Enforced at `POST /solve` submit time (fast `400`, before spawning):

- `instance`: required, non-empty string, max size **1 MiB** (env-tunable).
- `kpi`: optional; each ∈ {`trades`, `users`, `distance`}, no duplicates
  (reuse `parse_kpi_list`). Default `["trades"]`.
- `time_limit`: optional, > 0, capped at **600 s** server-side (env-tunable).
- `mipgap`: optional, ≥ 0.
- `stats`: optional bool; when true, `result.stats` is populated.

Deep parse errors (bad directives in `instance`) surface later via
`GET /result/{job_id}` as `{"status": "error", …}`, since full parsing runs
inside the worker (where the Gurobi env lives).

## Error model

| Condition | Where | Response |
|---|---|---|
| Bad JSON / missing `instance` / bad knob | submit | `400` |
| Unknown `job_id` | poll | `404` |
| Parse error in instance | worker | `GET` → `{"status":"error", …}` |
| Solver infeasible / no solution | worker | `result.status` reflects it; `result` still returned |

## Data flow

```
client --POST /solve {instance,...}--> web fn --validate--> solve_job.spawn() --> {job_id}
client --GET /result/{job_id}-------> web fn --> FunctionCall.get(timeout=0)
                                                   ├─ pending
                                                   └─ done -> to_dict(Result)
worker: gp.Env(WLS) -> solve() -> Result -> to_dict
```

## Testing

- **Golden text test** (new, in-process): run `solve()` on each `testcases/`
  instance and assert `to_text()` is byte-identical to the current `main.py`
  stdout. Captures the saved expected output as the contract; this is what
  proves the refactor preserves behaviour.
- **`to_dict` shape test** (new): structured fields present and consistent with
  the text rendering for a money instance and a barter instance.
- **Existing tests** `test_dupcap.py`, `test_kpi_distance.py` (subprocess
  `python main.py`) must stay green — CLI surface is unchanged.
- **Modal smoke test:** documented `modal serve modal_app.py` + a `curl`
  submit/poll round-trip. Not wired into CI (needs Modal auth + WLS secret).

## Deployment

```bash
modal secret create gurobi-wls \
  WLSACCESSID=<id> WLSSECRET=<secret> LICENSEID=<licid>
modal deploy modal_app.py
```

Local dev: `modal serve modal_app.py` (hot-reload, temporary URL).
`modal` is a deploy-time dependency (not added to `requirements.txt`, which
stays the runtime `gurobipy` pin); document `pip install modal` for deployers.

## Out of scope (YAGNI)

- Per-request `threads` override (fixed at 8; revisit if multi-strategy lands).
- Persistent result storage beyond Modal's `FunctionCall` result retention.
- Rate limiting / quotas beyond proxy auth.
- A client SDK / wrapper library.
