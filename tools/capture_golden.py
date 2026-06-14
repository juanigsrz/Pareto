"""Regenerate testcases/golden/<name>.out from the current main.py CLI.

Run BEFORE refactoring to snapshot behaviour, and only re-run intentionally
when output is meant to change. Golden files are the refactor contract.
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
MAIN = os.path.join(ROOT, "main.py")
TC = os.path.join(ROOT, "testcases")
OUT = os.path.join(TC, "golden")


def cases():
    for dirpath, _, names in os.walk(TC):
        if os.path.abspath(dirpath).startswith(os.path.abspath(OUT)):
            continue
        for n in sorted(names):
            if n.endswith(".txt"):
                yield os.path.join(dirpath, n)


MARKER = "\nTrade Results:"


def strip_banner(stdout):
    """Drop the Gurobi env-startup banner that precedes Pareto's output.

    Gurobi prints license/parameter lines to stdout when the environment
    starts; that noise is not part of Pareto's output contract. The real
    output always begins with the MARKER. A no-op once build() silences the
    env (refactored output already starts at the marker).
    """
    i = stdout.find(MARKER)
    return stdout[i:] if i != -1 else stdout


def main():
    os.makedirs(OUT, exist_ok=True)
    for path in sorted(cases()):
        rel = os.path.relpath(path, TC)[:-4].replace(os.sep, "__")
        r = subprocess.run([sys.executable, MAIN, path],
                           capture_output=True, text=True, check=True)
        with open(os.path.join(OUT, rel + ".out"), "w") as f:
            f.write(strip_banner(r.stdout))
        print("wrote", rel + ".out")


if __name__ == "__main__":
    main()
