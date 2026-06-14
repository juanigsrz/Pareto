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


if __name__ == "__main__":
    test_multi_kpi_runs()
    test_invalid_kpi_rejected()
    test_duplicate_kpi_rejected()
    print("OK: kpi list tests passed")
