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


if __name__ == "__main__":
    test_takecap_uncapped_gets_three()
    test_takecap_n1_caps_at_one()
    test_takecap_n2_caps_at_two()
    test_dupcap_alias_equals_takecap_one()
    print("OK: takecap tests passed")
