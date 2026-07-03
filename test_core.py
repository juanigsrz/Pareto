"""In-process unit tests for pareto_core and serialize (no subprocess)."""
import pareto_core as C
import serialize as S


SWAP = "alice: (1for1) A -> B\nbob: (1for1) B -> A\n"

MONEY = (
    "item C1 owner bob ask 10\n"
    "bid alice C1 20\n"
)


def test_parse_swap():
    inst = C.parse(SWAP)
    assert inst.users == {"alice", "bob"}, inst.users
    assert len(inst.wishes) == 2, inst.wishes
    # giving an item implies owning it
    a = C.intern_lookup(inst, "A")
    assert inst.owner[a] == "alice", inst.owner


def test_parse_money():
    inst = C.parse(MONEY)
    c1 = C.intern_lookup(inst, "C1")
    assert inst.owner[c1] == "bob"
    assert inst.ask[c1] == 10
    assert inst.bids[("alice", c1)] == 20


def test_parse_json_input():
    obj = '{"items":[{"name":"A","owner":"alice"}],' \
          '"wishes":[{"user":"alice","give":["A"],"take":["B"]}]}'
    inst = C.parse(obj)                     # auto-detects JSON from leading '{'
    assert inst.users == {"alice"}, inst.users
    assert len(inst.wishes) == 1, inst.wishes


def test_parse_bad_latitude():
    try:
        C.parse("location alice 999 0\nalice: (1for1) A -> B\n")
    except ValueError as e:
        assert "latitude" in str(e)
    else:
        raise AssertionError("expected ValueError")


def test_kpi_list_validation():
    assert C.parse_kpi_list("trades,users") == ["trades", "users"]
    for bad in ("trades,bogus", "trades,trades", "trades,,users", "distance"):
        try:
            C.parse_kpi_list(bad)
        except Exception:
            pass
        else:
            raise AssertionError(f"expected rejection of {bad!r}")


def test_solve_swap_trades():
    sol = C.solve(SWAP, kpi=["trades"], want_stats=True)
    assert sol.status == "Optimal", sol.status
    pairs = {(t["give"], t["take"]) for t in sol.result["trades"]}
    assert ("A", "B") in pairs and ("B", "A") in pairs, sol.result["trades"]
    assert sol.result["stats"]["swap_vars"] == 2, sol.result["stats"]
    assert sol.result["stats"]["kpi"] == {"trades": 2}, sol.result["stats"]
    assert sol.result["cash_summary"] == []     # pure barter -> no money section


def test_solve_money_buy():
    sol = C.solve(MONEY, kpi=["trades"], want_stats=True)
    items = {p["item"] for p in sol.result["cash_purchases"]}
    assert "C1" in items, sol.result["cash_purchases"]
    summ = {r["user"]: r for r in sol.result["cash_summary"]}
    assert summ["alice"]["spent"] == 10 and summ["alice"]["net"] == 10
    assert any(p["from"] == "alice" and p["to"] == "bob"
               for p in sol.result["payments"]), sol.result["payments"]


def test_to_dict_money():
    d = S.to_dict(C.solve(MONEY, kpi=["trades"], want_stats=True))
    assert d["status"] == "Optimal"
    # meta stamped onto every document
    assert {"version", "gurobi_version", "input_checksum", "result_checksum"} <= set(d), d.keys()
    assert {"trades", "combos", "cash_purchases", "cash_summary",
            "payments", "settlement", "kpi", "stats"} <= set(d), d.keys()
    assert any(p["item"] == "C1" for p in d["cash_purchases"]), d
    assert d["stats"]["kpi"] == {"trades": 1}, d["stats"]


def test_to_dict_barter():
    d = S.to_dict(C.solve(SWAP, kpi=["trades"]))
    assert d["cash_purchases"] == [] and d["cash_summary"] == []
    assert {(t["give"], t["take"]) for t in d["trades"]} == {("A", "B"), ("B", "A")}


if __name__ == "__main__":
    for _name, _fn in sorted(list(globals().items())):
        if _name.startswith("test_"):
            _fn()
    print("OK: core tests passed")
