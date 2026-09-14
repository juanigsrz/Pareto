"""Text output carries a version + checksum header, and check.py still accepts it."""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN = os.path.join(HERE, "main.py")
CHECK = os.path.join(HERE, "check.py")
SWAP = os.path.join(HERE, "testcases/money/swap.txt")


def run_text(path):
    return subprocess.run([sys.executable, MAIN, path],
                          capture_output=True, text=True, check=True).stdout


def _header_lines(out):
    return [l for l in out.splitlines() if l.startswith("#")]


def test_header_present():
    lines = _header_lines(run_text(SWAP))
    assert any(l.startswith("# pareto 1.1.0") for l in lines)
    assert any(l.startswith("# input_checksum") and "sha256:" in l for l in lines)
    assert any(l.startswith("# result_checksum") and "sha256:" in l for l in lines)


def test_check_py_accepts_headered_output():
    out = run_text(SWAP)
    proc = subprocess.run([sys.executable, CHECK, SWAP, "-"],
                          input=out, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr


if __name__ == "__main__":  # self-contained runner, no framework needed (see README)
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
    print(f"{len(fns)} passed")
