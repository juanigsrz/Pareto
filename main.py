import sys
import os
import argparse

import pareto_core as C
from serialize import to_text


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("--kpi", type=C.parse_kpi_list, default=["trades"],
                    help="comma-separated objectives in priority order "
                         "(leftmost optimized first), e.g. 'trades,users'. "
                         "Choices: 'trades' = max total trades (default); "
                         "'users' = max users with >= 1 trade; "
                         "'distance' = min total shipping distance (km).")
    args = ap.parse_args()

    with open(args.file) as f:
        text = f.read()

    time_limit = os.environ.get("PARETO_TIME_LIMIT")
    mipgap = os.environ.get("PARETO_MIPGAP")
    want_stats = bool(os.environ.get("PARETO_STATS"))

    res = C.solve(
        text, kpi=args.kpi,
        time_limit=float(time_limit) if time_limit else None,
        mipgap=float(mipgap) if mipgap else None,
        threads=0,                # 0 = Gurobi default (match pre-refactor CLI)
        want_stats=want_stats,
    )

    if res.status != "Optimal":
        print(f"WARNING: solver status is {res.status}", file=sys.stderr)

    if want_stats:
        s = res.stats
        if isinstance(s["obj"], dict):
            parts = ",".join(f"{k}={'nan' if v is None else v}"
                             for k, v in s["obj"].items())
            obj_str = f"obj[{parts}]"
            gap = "nan"
        else:
            obj_str = f"obj={'nan' if s['obj'] is None else s['obj']}"
            gap = "nan" if s["gap"] is None else f"{s['gap']:.4f}"
        print(
            f"STATS swap_vars={s['swap_vars']} buy_vars={s['buy_vars']} "
            f"combos={s['combos']} items={s['items']} "
            f"users_traded={s['users_traded']}/{s['total_users']} "
            f"status={s['status']} {obj_str} gap={gap} "
            f"runtime={s['runtime']:.3f}",
            file=sys.stderr,
        )

    if not res.has_solution:
        print("No solution found.", file=sys.stderr)
        sys.exit(0)

    print(to_text(res), end="")


if __name__ == "__main__":
    main()
