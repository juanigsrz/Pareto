"""Multi-objective --kpi list and the distance KPI. Runs main.py as a
subprocess, like test_dupcap.py."""
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN = os.path.join(HERE, "main.py")


def run(text, kpi=None, check=True):
    """Run main.py on `text`. Returns CompletedProcess (stdout/stderr/code)."""
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
        f.write(text)
        path = f.name
    try:
        cmd = [sys.executable, MAIN, path]
        if kpi is not None:
            cmd += ["--kpi", kpi]
        return subprocess.run(cmd, capture_output=True, text=True, check=check)
    finally:
        os.unlink(path)


# Plain reciprocal 1-for-1 swap: both moves happen regardless of KPI.
SWAP = """\
alice: (1for1) A -> B
bob: (1for1) B -> A
"""


def test_multi_kpi_runs():
    out = run(SWAP, kpi="trades,users").stdout
    assert "A -> B" in out and "B -> A" in out, out


def test_invalid_kpi_rejected():
    p = run(SWAP, kpi="trades,bogus", check=False)
    assert p.returncode != 0
    assert "bogus" in (p.stderr + p.stdout)


def test_duplicate_kpi_rejected():
    p = run(SWAP, kpi="trades,trades", check=False)
    assert p.returncode != 0


# alice wants one copy of game G; bob (near) and carol (far) each have one.
# dupcap caps her at one copy. With trades primary the count is 1 either way,
# so the distance KPI decides WHICH seller ships.
TWO_SELLERS = """\
item C1 owner bob ask 10
item C2 owner carol ask 10
bid alice C1 20
bid alice C2 20
dupcap alice C1 C2
location alice 0 0
location bob 0 1
location carol 0 5
"""

# One optional cash buy. Trades wants the move; distance wants no move (0 km).
ONE_OPTIONAL = """\
item C1 owner bob ask 10
bid alice C1 20
location alice 0 0
location bob 0 5
"""


def test_distance_picks_closer_seller():
    out = run(TWO_SELLERS, kpi="trades,distance").stdout
    assert "C1: bob -> alice" in out, out      # near seller chosen
    assert "C2: carol -> alice" not in out, out  # far seller not chosen


def test_lexicographic_order_changes_outcome():
    # trades first -> the buy happens.
    trades_first = run(ONE_OPTIONAL, kpi="trades,distance").stdout
    assert "pays" in trades_first, trades_first
    # distance first -> optimum is 0 km (no move); trades is held subordinate.
    distance_first = run(ONE_OPTIONAL, kpi="distance,trades").stdout
    assert "pays" not in distance_first, distance_first


def test_bad_latitude_rejected():
    bad = "location alice 999 0\nalice: (1for1) A -> B\n"
    p = run(bad, check=False)
    assert p.returncode != 0
    assert "latitude" in (p.stderr + p.stdout)


if __name__ == "__main__":
    test_multi_kpi_runs()
    test_invalid_kpi_rejected()
    test_duplicate_kpi_rejected()
    test_distance_picks_closer_seller()
    test_lexicographic_order_changes_outcome()
    test_bad_latitude_rejected()
    print("OK: kpi list tests passed")
