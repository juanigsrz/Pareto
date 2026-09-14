"""Compare ways of trading off trade volume against hub congestion on ONE instance.

Runs main.py several times on the same file and tabulates, per strategy: total
trades, items processed at the hub ('hubload'), direct boxes and the items they
carry, local hand-offs, and wall time. Strategies:

    trades            baseline: max trades, no logistics objective (plan still derived)
    lex               --kpi trades,hubload           strict: hub load only breaks ties
    tol=X             --kpi trades,hubload --kpi-tol X   give up <= X of the trades
    blend=T:H         --kpi trades,hubload --blend T,H   one objective: T*trades - H*hubload

Usage:
    python hubload_tradeoff.py INSTANCE.txt
    python hubload_tradeoff.py INSTANCE.txt --tols 0.02,0.05,0.1 --blends 5:1,3:1,2:1,1:1
    PARETO_TIME_LIMIT=120 python hubload_tradeoff.py big.txt      # env passes through

The instance needs 'hub <city>' and 'city <user> <city>' lines (see README).
"""
import argparse
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN = os.path.join(HERE, "main.py")


def solve(instance, extra):
    t0 = time.perf_counter()
    r = subprocess.run([sys.executable, MAIN, instance, "--format", "json", *extra],
                       capture_output=True, text=True)
    wall = time.perf_counter() - t0
    if r.returncode != 0:
        sys.exit(f"main.py failed for {extra}:\n{r.stderr}")
    return json.loads(r.stdout), wall


def row(label, doc, wall):
    k = doc["kpi"]
    s = doc.get("shipping") or {}
    return {
        "strategy": label,
        "trades": k.get("trades"),
        "hubload": s.get("hub_items"),
        "boxes": len(s.get("boxes", [])),
        "boxed": s.get("boxed_items"),
        "local": s.get("local_items"),
        "status": doc["status"],
        "wall": wall,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("instance")
    ap.add_argument("--tols", default="0.02,0.05,0.1",
                    help="comma-separated --kpi-tol values to try ('' to skip)")
    ap.add_argument("--blends", default="5:1,3:1,2:1,1:1",
                    help="comma-separated T:H weight pairs to try ('' to skip)")
    a = ap.parse_args()

    runs = [("trades", []), ("lex", ["--kpi", "trades,hubload"])]
    for t in filter(None, a.tols.split(",")):
        runs.append((f"tol={t}", ["--kpi", "trades,hubload", "--kpi-tol", t]))
    for b in filter(None, a.blends.split(",")):
        tw, hw = b.split(":")
        runs.append((f"blend={tw}:{hw}", ["--kpi", "trades,hubload", "--blend", f"{tw},{hw}"]))

    rows = []
    for label, extra in runs:
        doc, wall = solve(a.instance, extra)
        rows.append(row(label, doc, wall))

    base = rows[0]["trades"] or 0
    hdr = (f"{'strategy':<12} {'trades':>7} {'Δtrades':>8} {'hubload':>8} {'boxes':>6} "
           f"{'boxed':>6} {'local':>6} {'status':>9} {'wall_s':>7}")
    print(f"# {a.instance}")
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        d = "" if r["trades"] is None else f"{r['trades'] - base:+d}"
        print(f"{r['strategy']:<12} {r['trades']!s:>7} {d:>8} {r['hubload']!s:>8} "
              f"{r['boxes']:>6} {r['boxed']!s:>6} {r['local']!s:>6} {r['status']:>9} "
              f"{r['wall']:>7.1f}")


if __name__ == "__main__":
    main()
