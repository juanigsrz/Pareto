"""'hubload' KPI: minimize the items a hub city must process after the event.
Items shipped city->city in a direct box of >= boxmin items bypass the hub; local
hand-offs never touch it; anything to/from the hub (or with an unknown city) is
hub-processed. Locks the KPI, the derived shipping plan, --kpi-tol, --blend, the
new directives in text and JSON, and input_checksum stability.

Runs main.py as a subprocess (see README)."""
import json
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN = os.path.join(HERE, "main.py")
CHECK = os.path.join(HERE, "check.py")
sys.path.insert(0, HERE)
import pareto_io  # noqa: E402

CITIES = "\n".join([f"city c{i} Cordoba" for i in range(1, 6)]
                   + [f"city m{i} Mendoza" for i in range(1, 6)])

# 5 Cordoba<->Mendoza pairs: both directions fill a 5-box, nothing touches CABA.
BOX = f"""\
hub CABA
boxmin 5
{CITIES}
c1: (1for1) C1 -> M1
c2: (1for1) C2 -> M2
c3: (1for1) C3 -> M3
c4: (1for1) C4 -> M4
c5: (1for1) C5 -> M5
m1: (1for1) M1 -> C1
m2: (1for1) M2 -> C2
m3: (1for1) M3 -> C3
m4: (1for1) M4 -> C4
m5: (1for1) M5 -> C5
"""

# 4 Cordoba<->Mendoza pairs (flows 4/4: one short of a box) plus c5/m5, who can
# either complete both boxes with each other (+2 trades, hub load 0) or each trade
# with a CABA user instead (+4 trades, but then all 12 items go through the hub).
TRADEOFF = f"""\
hub CABA
boxmin 5
{CITIES}
city y CABA
city w CABA
c1: (1for1) C1 -> M1
c2: (1for1) C2 -> M2
c3: (1for1) C3 -> M3
c4: (1for1) C4 -> M4
m1: (1for1) M1 -> C1
m2: (1for1) M2 -> C2
m3: (1for1) M3 -> C3
m4: (1for1) M4 -> C4
c5: (1for1) C5 -> M5 Y
m5: (1for1) M5 -> C5 W
y: (1for1) Y -> C5
w: (1for1) W -> M5
"""

# Local hand-off (a1<->a2, same city), hub endpoint (a3<->z), unknown city (n<->a4).
MIXED = """\
hub CABA
city a1 Cordoba
city a2 Cordoba
city a3 Cordoba
city a4 Cordoba
city z CABA
a1: (1for1) A1 -> A2
a2: (1for1) A2 -> A1
a3: (1for1) A3 -> Z
z: (1for1) Z -> A3
a4: (1for1) A4 -> N
n: (1for1) N -> A4
"""


def run(text, *extra, fmt="json", env=None):
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
        f.write(text)
        path = f.name
    try:
        r = subprocess.run([sys.executable, MAIN, path, "--format", fmt, *extra],
                           capture_output=True, text=True,
                           env={**os.environ, **env} if env else None)
    finally:
        os.unlink(path)
    return r


def model_cols(text, *extra):
    """Columns Gurobi is handed, straight off its log -- each box gadget adds 2."""
    r = run(text, *extra, fmt="text")
    assert r.returncode == 0, r.stderr
    m = re.search(r"Optimize a model with \d+ rows, (\d+) columns", r.stdout)
    assert m, r.stdout
    return int(m.group(1))


def solve(text, *extra):
    r = run(text, *extra)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


# Six Mendoza users all chasing the SAME two Cordoba copies. The Cordoba -> Mendoza
# pair has 12 candidate legs but only 2 copies behind them, and a copy moves once, so
# no box can ever leave: sizing the gadget off legs builds one that cannot fire.
FEW_COPIES = "\n".join(
    ["hub CABA", "boxmin 5", "city a1 Cordoba", "city a2 Cordoba"]
    + [f"city b{i} Mendoza" for i in range(1, 7)]
    + ["a1: (1for1) A1 -> B1", "a2: (1for1) A2 -> B2"]
    + [f"b{i}: (1for1) B{i} -> A1 A2" for i in range(1, 7)]) + "\n"


def boxes(doc):
    return sorted((b["from_city"], b["to_city"], len(b["items"]))
                  for b in doc["shipping"]["boxes"])


def test_boxes_form_when_flow_reaches_boxmin():
    d = solve(BOX, "--kpi", "trades,hubload")
    assert d["kpi"] == {"trades": 10, "hubload": 0}
    assert boxes(d) == [("Cordoba", "Mendoza", 5), ("Mendoza", "Cordoba", 5)]
    assert d["shipping"]["hub_items"] == 0 and d["shipping"]["local_items"] == 0
    assert d["shipping"]["boxed_items"] == 10


def test_boxmin_above_flow_sends_everything_via_hub():
    d = solve(BOX.replace("boxmin 5", "boxmin 6"), "--kpi", "trades,hubload")
    assert d["kpi"] == {"trades": 10, "hubload": 10}
    assert boxes(d) == [] and d["shipping"]["hub_items"] == 10
    assert d["shipping"]["box_min"] == 6


def test_plan_is_derived_even_without_the_kpi():
    d = solve(BOX)
    assert d["kpi"] == {"trades": 10}
    assert boxes(d) == [("Cordoba", "Mendoza", 5), ("Mendoza", "Cordoba", 5)]


def test_local_hub_and_unknown_routes():
    d = solve(MIXED, "--kpi", "trades,hubload")
    assert d["kpi"] == {"trades": 6, "hubload": 4}   # A3/Z via hub + A4/N unknown city
    s = d["shipping"]
    assert s["local_items"] == 2 and s["hub_items"] == 4 and s["boxes"] == []
    r = run(MIXED, "--kpi", "trades,hubload")
    assert "user 'n' has no 'city'" in r.stderr


def test_strict_lexicographic_keeps_max_trades():
    d = solve(TRADEOFF, "--kpi", "trades,hubload")
    assert d["kpi"] == {"trades": 12, "hubload": 12}
    assert boxes(d) == []


def test_tolerance_gives_up_trades_for_boxes():
    d = solve(TRADEOFF, "--kpi", "trades,hubload", "--kpi-tol", "0.2")
    assert d["kpi"] == {"trades": 10, "hubload": 0}
    assert boxes(d) == [("Cordoba", "Mendoza", 5), ("Mendoza", "Cordoba", 5)]


def test_blend_prices_the_exchange_rate():
    # 3:1 -> two boxes (10*3 - 0 = 30) beat two extra trades (12*3 - 12 = 24).
    d = solve(TRADEOFF, "--kpi", "trades,hubload", "--blend", "3,1")
    assert d["kpi"] == {"trades": 10, "hubload": 0}
    # 10:1 -> a trade is worth ten hub items, so max trades wins (120-12 > 100).
    d = solve(TRADEOFF, "--kpi", "trades,hubload", "--blend", "10,1")
    assert d["kpi"] == {"trades": 12, "hubload": 12}


def test_json_input_and_text_input_agree():
    doc = {
        "hub": "CABA", "box_min": 5,
        "cities": [{"user": f"c{i}", "city": "Cordoba"} for i in range(1, 6)]
                  + [{"user": f"m{i}", "city": "Mendoza"} for i in range(1, 6)],
        "wishes": [{"user": f"c{i}", "give": [f"C{i}"], "take": [f"M{i}"]} for i in range(1, 6)]
                  + [{"user": f"m{i}", "give": [f"M{i}"], "take": [f"C{i}"]} for i in range(1, 6)],
    }
    dj = solve(json.dumps(doc), "--kpi", "trades,hubload")
    dt = solve(BOX, "--kpi", "trades,hubload")
    assert dj["kpi"] == dt["kpi"] == {"trades": 10, "hubload": 0}
    assert dj["input_checksum"] == dt["input_checksum"]
    # A consumer holding only pareto_io.py can recompute the solver's input_checksum.
    assert pareto_io.checksum(pareto_io.normalize_instance(doc)) == dj["input_checksum"]


def test_input_checksum_is_stable_for_instances_without_cities():
    """Pin the checksum contract: adding optional sections must not change the
    checksum of instances that never use them (value from pareto 1.0.0)."""
    with open(os.path.join(HERE, "testcases/1for1.txt")) as f:
        d = solve(f.read())
    assert d["input_checksum"] == \
        "sha256:a36f3b882e164bc92101e152501c28be330fbac44b4e309d29ddd3ca3ef5b164"
    assert "shipping" not in d


def test_pair_short_on_copies_builds_no_box_gadget():
    d = solve(FEW_COPIES, "--kpi", "trades,hubload")
    assert d["kpi"] == {"trades": 4, "hubload": 4}
    assert boxes(d) == [] and d["shipping"]["hub_items"] == 4
    # At boxmin 2 both pairs can fill a box and each gets a gadget (2 columns apiece);
    # at boxmin 5 neither can, so neither may be built. Counting legs instead of copies
    # would build one for the 12-leg pair and leave only a 2-column gap.
    wide = FEW_COPIES.replace("boxmin 5", "boxmin 2")
    assert model_cols(wide, "--kpi", "trades,hubload") \
        == model_cols(FEW_COPIES, "--kpi", "trades,hubload") + 4
    d = solve(wide, "--kpi", "trades,hubload")
    assert d["kpi"] == {"trades": 4, "hubload": 0}
    assert boxes(d) == [("Cordoba", "Mendoza", 2), ("Mendoza", "Cordoba", 2)]


def test_blend_qualifies_for_the_absolute_gap_shortcut():
    """--blend is ONE Gurobi objective, so it may stop ~1 objective unit short; a
    lexicographic list may not (the gap can zero out the primary objective)."""
    env = {"PARETO_GAPABS_MINVARS": "1"}    # default 20000 vars, far above these tests
    r = run(FEW_COPIES, "--kpi", "trades,hubload", "--blend", "3,1", fmt="text", env=env)
    assert "MIPGapAbs" in r.stdout, r.stdout
    r = run(FEW_COPIES, "--kpi", "trades,hubload", fmt="text", env=env)
    assert "MIPGapAbs" not in r.stdout, r.stdout


def test_hubload_first_is_rejected():
    r = run(TRADEOFF, "--kpi", "hubload")
    assert r.returncode == 2 and "cannot be the first/only lexicographic objective" in r.stderr
    r = run(TRADEOFF, "--kpi", "hubload,trades")
    assert r.returncode == 2


def test_hubload_without_hub_is_rejected():
    r = run(TRADEOFF.replace("hub CABA\n", ""), "--kpi", "trades,hubload")
    assert r.returncode == 2 and "needs the instance to name the hub city" in r.stderr


def test_flag_combinations_are_validated():
    r = run(TRADEOFF, "--kpi", "trades,hubload", "--blend", "3")
    assert r.returncode == 2 and "one weight per --kpi entry" in r.stderr
    r = run(TRADEOFF, "--kpi", "trades,hubload", "--blend", "3,1", "--kpi-tol", "0.1")
    assert r.returncode == 2 and "mutually exclusive" in r.stderr
    r = run(TRADEOFF, "--kpi", "trades,hubload", "--kpi-tol", "0.1,0.1")
    assert r.returncode == 2 and "at most len(--kpi)-1" in r.stderr


def test_text_output_has_plan_and_passes_checker():
    r = run(TRADEOFF, "--kpi", "trades,hubload", "--blend", "3,1", fmt="text")
    assert r.returncode == 0, r.stderr
    assert "Shipping plan (hub CABA, box >= 5 items):" in r.stdout
    assert "Cordoba -> Mendoza: 5 items" in r.stdout
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
        f.write(TRADEOFF)
        path = f.name
    try:
        c = subprocess.run([sys.executable, CHECK, path, "-"], input=r.stdout,
                           capture_output=True, text=True)
    finally:
        os.unlink(path)
    assert c.returncode == 0, c.stdout
    assert "10 swap moves" in c.stdout, c.stdout


if __name__ == "__main__":  # self-contained runner, no framework needed (see README)
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
    print(f"OK: {len(fns)} hubload tests passed")
