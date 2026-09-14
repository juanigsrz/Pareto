"""check.py enforces the solver's exact NforM semantics: a move backed by a
'(2for1)' wish must give exactly 2 and receive exactly 1. 'Up to N' / 'at least
M' outputs are NOT legal (the solver never produces them: sum(out) == N*active).

Runs check.py as a subprocess (no Gurobi needed)."""
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
CHECK = os.path.join(HERE, "check.py")

INSTANCE = """\
u: (2for1) A B -> X
x: (1for2) X -> A B
"""

EXACT = """\
Trade Results:
A B -> X
X -> A B
"""

# u gives only A for X: fewer than the 2 the wish commits to.
UNDER_GIVE = """\
Trade Results:
A -> X
X -> A
"""


def run_check(instance, output):
    with tempfile.TemporaryDirectory() as d:
        inp = os.path.join(d, "in.txt")
        out = os.path.join(d, "out.txt")
        with open(inp, "w") as f:
            f.write(instance)
        with open(out, "w") as f:
            f.write(output)
        r = subprocess.run([sys.executable, CHECK, inp, out],
                           capture_output=True, text=True)
    return r.returncode, r.stdout


def test_exact_n_m_is_legal():
    code, out = run_check(INSTANCE, EXACT)
    assert code == 0, out
    assert out.startswith("OK:"), out


def test_giving_fewer_than_n_is_a_violation():
    code, out = run_check(INSTANCE, UNDER_GIVE)
    assert code == 1, out
    assert "not backed by any wish of 'u'" in out, out
    assert "not backed by any wish of 'x'" in out, out


def test_shipping_plan_lines_are_not_moves():
    """The text output's 'City -> City: N items' lines live under a 'Shipping plan'
    header the checker must skip, or they would parse as swap moves."""
    output = EXACT + """
Shipping plan (hub CABA, box >= 5 items):
  Direct boxes: 1 (5 items bypass the hub)
    Cordoba -> Mendoza: 5 items
  Via hub: 0 items
  Local hand-offs: 0 items
"""
    code, out = run_check(INSTANCE, output)
    assert code == 0, out
    assert "2 swap moves" in out, out


if __name__ == "__main__":  # self-contained runner, no framework needed (see README)
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
    print(f"{len(fns)} passed")
