"""takecap directive: a user receives at most N of the listed copies, counting
swaps and cash buys together. dupcap is the legacy N=1 alias. Runs main.py as a
subprocess, like test_dupcap.py."""
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN = os.path.join(HERE, "main.py")

# alice can buy three copies of game G (C1/C2/C3 from three sellers).
BUY3 = """\
item C1 owner bob ask 10
item C2 owner carol ask 10
item C3 owner dave ask 10
bid alice C1 20
bid alice C2 20
bid alice C3 20
"""


def run(text):
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
        f.write(text)
        path = f.name
    try:
        return subprocess.run(
            [sys.executable, MAIN, path],
            capture_output=True, text=True, check=True,
        ).stdout
    finally:
        os.unlink(path)


def alice_buys(out):
    """Copies alice acquires by cash buy."""
    return sum(1 for l in out.splitlines() if "-> alice" in l and "pays" in l)


def test_takecap_n1_caps_at_one():
    assert alice_buys(run(BUY3 + "takecap alice 1 C1 C2 C3\n")) == 1


def test_takecap_n2_caps_at_two():
    assert alice_buys(run(BUY3 + "takecap alice 2 C1 C2 C3\n")) == 2


def test_takecap_uncapped_gets_three():
    assert alice_buys(run(BUY3)) == 3


def test_dupcap_alias_equals_takecap_one():
    assert alice_buys(run(BUY3 + "dupcap alice C1 C2 C3\n")) == 1


# u owns A and B; two other users each want one of them via swap.
GIVE_SWAP = """\
item A owner u ask 0
item B owner u ask 0
item P owner p ask 0
item Q owner q ask 0
u : (1for1) A -> P
u : (1for1) B -> Q
p : (1for1) P -> A
q : (1for1) Q -> B
"""

# u owns A (cash-sellable to buyer) and B (swap-wanted by q).
GIVE_CASH = """\
item A owner u ask 10
item B owner u ask 0
item Q owner q ask 0
bid buyer A 20
u : (1for1) B -> Q
q : (1for1) Q -> B
"""

# givecap names P, which is owned by p, not u -> must raise.
GIVE_BAD_OWNER = """\
item A owner u ask 0
item P owner p ask 0
u : (1for1) A -> P
p : (1for1) P -> A
givecap u 1 P
"""


def run_raw(text):
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
        f.write(text)
        path = f.name
    try:
        return subprocess.run(
            [sys.executable, MAIN, path], capture_output=True, text=True,
        )
    finally:
        os.unlink(path)


def u_gives_swap(out):
    """Times one of u's items (A or B) is given in a swap (left of '->')."""
    return sum(1 for l in out.splitlines()
               if l.strip().startswith(("A ", "B ")) and " -> " in l)


def u_gives_cash(out):
    """u's gives counting a cash sale of A and a swap give of B."""
    n = 0
    for l in out.splitlines():
        s = l.strip()
        if s.startswith("A:") and "u ->" in s:   # cash sale of A by u
            n += 1
        if s.startswith("B ") and " -> " in s:    # swap give of B
            n += 1
    return n


def test_givecap_swap_uncapped_gives_two():
    assert u_gives_swap(run(GIVE_SWAP)) == 2


def test_givecap_swap_caps_at_one():
    assert u_gives_swap(run(GIVE_SWAP + "givecap u 1 A B\n")) == 1


def test_givecap_counts_cash_sale():
    assert u_gives_cash(run(GIVE_CASH)) == 2
    assert u_gives_cash(run(GIVE_CASH + "givecap u 1 A B\n")) == 1


def test_givecap_bad_owner_raises():
    res = run_raw(GIVE_BAD_OWNER)
    assert res.returncode != 0
    assert "givecap" in res.stderr


if __name__ == "__main__":
    test_takecap_uncapped_gets_three()
    test_takecap_n1_caps_at_one()
    test_takecap_n2_caps_at_two()
    test_dupcap_alias_equals_takecap_one()
    test_givecap_swap_uncapped_gives_two()
    test_givecap_swap_caps_at_one()
    test_givecap_counts_cash_sale()
    test_givecap_bad_owner_raises()
    print("OK: takecap/givecap tests passed")
