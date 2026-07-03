"""Characterization test: main.py stdout must match testcases/golden/*.out.
Locks the output refactor (Task 3). Golden fixtures contain only the program's
own output (from 'Trade Results:' onward) -- the Gurobi solver log that
OutputFlag=1 writes ahead of it is stripped by body() below, on both sides."""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN = os.path.join(HERE, "main.py")

CASES = {
    "money__swap": "testcases/money/swap.txt",
    "money__cashchain": "testcases/money/cashchain.txt",
    "2for11for2": "testcases/2for11for2.txt",
    "distance": "testcases/distance.txt",
}


def body(text):
    """Return the program's own output: everything from the first
    'Trade Results:' line onward. Skips the Gurobi solver log that
    OutputFlag=1 writes to stdout ahead of it, and any Task-5 header."""
    lines = text.splitlines(keepends=True)
    for i, line in enumerate(lines):
        if line.strip() == "Trade Results:":
            return "".join(lines[i:])
    raise AssertionError("no 'Trade Results:' marker in output")


def run(path):
    out = subprocess.run([sys.executable, MAIN, os.path.join(HERE, path)],
                         capture_output=True, text=True, check=True).stdout
    return body(out)


def test_golden_outputs_match():
    for name, path in CASES.items():
        with open(os.path.join(HERE, "testcases/golden", name + ".out")) as f:
            expected = f.read()
        assert run(path) == expected, f"{name} output changed"


if __name__ == "__main__":  # self-contained runner, no framework needed (see README)
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
    print(f"{len(fns)} passed")
