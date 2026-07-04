"""Self-trade guard + hub/takecap reporting soundness.

A user must never barter-receive an item they already own: a self-loop
(A -> A) or a self-cycle across two of their own wishes moves nothing but
used to count as trades, inflating every KPI. The cash side always refused
self-buys; these tests lock the barter mirror.

Also locks the hub compaction gate: the hub's <= 1 receipt cap comes from an
n == 1 takecap row, so an n >= 2 takecap must NOT merge wishes into a hub
(the merged output printed a multi-give bundle line that no single 1for1
wish backs, and check.py rejected the solver's own output).

Runs main.py / check.py as subprocesses.
"""
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN = os.path.join(HERE, "main.py")
CHECK = os.path.join(HERE, "check.py")

SELF_LOOP = """\
alice: (1for1) A -> A
"""

SELF_CYCLE = """\
alice: (1for1) A -> B
alice: (1for1) B -> A
"""

# alice's take list mixes her own A (must be dropped) with bob's X (real trade).
OWN_AMONG_OTHERS = """\
alice: (1for1) A -> X A
bob: (1for1) X -> A
"""

# Two 1for1 wishes sharing a take set, capped at n=2: must NOT merge into a hub.
# Naive build trades all four items; the output must pass check.py.
HUB_TAKECAP2 = """\
u: (1for1) A -> X1 X2
u: (1for1) B -> X1 X2
takecap u 2 X1 X2
v: (1for1) X1 -> A
w: (1for1) X2 -> B
"""

# The intended hub pattern (dupcap = takecap n 1) must still work end-to-end.
HUB_DUPCAP = """\
u: (1for1) A -> X1 X2
u: (1for1) B -> X1 X2
dupcap u X1 X2
v: (1for1) X1 -> A
"""


def solve(text):
    """Returns (trade lines, full stdout, instance path). Caller unlinks path."""
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
        f.write(text)
        path = f.name
    out = subprocess.run([sys.executable, MAIN, path],
                         capture_output=True, text=True, check=True).stdout
    lines = out.splitlines()
    start = lines.index("Trade Results:") + 1
    trades = [l for l in lines[start:] if "->" in l]
    return trades, out, path


def check_ok(instance_path, output_text):
    """Run check.py on the solver's own output; return (exit_code, stdout)."""
    with tempfile.NamedTemporaryFile("w", suffix=".out", delete=False) as f:
        f.write(output_text)
        out_path = f.name
    try:
        r = subprocess.run([sys.executable, CHECK, instance_path, out_path],
                           capture_output=True, text=True)
        return r.returncode, r.stdout
    finally:
        os.unlink(out_path)


def run_case(text):
    trades, out, path = solve(text)
    try:
        code, msg = check_ok(path, out)
    finally:
        os.unlink(path)
    return trades, code, msg


def test_self_loop_is_not_a_trade():
    trades, code, msg = run_case(SELF_LOOP)
    assert trades == [], f"phantom self-loop trade: {trades}"
    assert code == 0, msg


def test_self_cycle_is_not_a_trade():
    trades, code, msg = run_case(SELF_CYCLE)
    assert trades == [], f"phantom self-cycle trades: {trades}"
    assert code == 0, msg


def test_own_item_dropped_but_real_trade_survives():
    trades, code, msg = run_case(OWN_AMONG_OTHERS)
    assert sorted(trades) == ["A -> X", "X -> A"], trades
    assert code == 0, msg


def test_takecap2_not_hubbed_output_passes_checker():
    trades, code, msg = run_case(HUB_TAKECAP2)
    assert len(trades) == 4, trades
    assert code == 0, msg


def test_dupcap_hub_still_works():
    trades, code, msg = run_case(HUB_DUPCAP)
    # u receives one of {X1, X2}: v's X1 for u's A closes the only 2-cycle.
    assert len(trades) == 2, trades
    assert code == 0, msg


def test_checker_flags_phantom_self_trade():
    """check.py must reject a hand-written self-trade even though it balances."""
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
        f.write(SELF_LOOP)
        path = f.name
    try:
        code, _ = check_ok(path, "Trade Results:\nA -> A\n")
        assert code == 1, "checker accepted a phantom self-trade"
    finally:
        os.unlink(path)


if __name__ == "__main__":  # self-contained runner, no framework needed
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
    print(f"OK: {len(fns)} self-trade/hub tests passed")
