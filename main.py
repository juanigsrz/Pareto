"""Command-line front end: read an instance, solve it, print text or JSON.

A thin wrapper over pareto_core.solve + serialize; all solver logic and all
diagnostic stderr output live in pareto_core so the Modal service shares them.
"""
import sys
import os
import argparse

import pareto_core as C
import serialize as S


def _read_source(src):
    if src == "-":
        return sys.stdin.read()
    with open(src, "r") as f:
        return f.read()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("--kpi", type=C.parse_kpi_list, default=["trades"],
                    help="comma-separated objectives in priority order "
                         "(leftmost optimized first), e.g. 'trades,users'. "
                         "Choices: 'trades' = max total trades (default); "
                         "'users' = max users with >= 1 trade; "
                         "'distance' = min total shipping distance (km).")
    ap.add_argument("--format", choices=("text", "json"), default="text",
                    help="output format (default: text).")
    ap.add_argument("--in-format", choices=("auto", "text", "json"), default="auto",
                    help="input format; 'auto' peeks the first character (default). "
                         "Read '-' for stdin.")
    args = ap.parse_args()

    raw = _read_source(args.file)
    want_stats = bool(os.environ.get("PARETO_STATS"))

    # text mode lets Gurobi log to stdout (quiet=False); json mode stays silent so
    # stdout is a single clean JSON document. time_limit/mipgap come from the
    # PARETO_* env vars inside pareto_core.build (params left None here).
    sol = C.solve(raw, kpi=args.kpi, in_format=args.in_format,
                  quiet=(args.format == "json"), want_stats=want_stats)

    if not sol.has_solution:
        print("No solution found.", file=sys.stderr)
        if args.format == "json":
            print(S.render_json(sol.result, sol.input_checksum), end="")
        sys.exit(0)

    if args.format == "json":
        print(S.render_json(sol.result, sol.input_checksum), end="")
    else:
        print(S.render_text(sol.result, sol.input_checksum), end="")


if __name__ == "__main__":
    main()
