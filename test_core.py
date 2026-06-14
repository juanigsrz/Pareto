"""In-process unit tests for pareto_core and serialize (no subprocess)."""
import pareto_core as C


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


def test_parse_bad_latitude():
    try:
        C.parse("location alice 999 0\nalice: (1for1) A -> B\n")
    except ValueError as e:
        assert "latitude" in str(e)
    else:
        raise AssertionError("expected ValueError")


def test_kpi_list_validation():
    assert C.parse_kpi_list("trades,users") == ["trades", "users"]
    for bad in ("trades,bogus", "trades,trades", "trades,,users"):
        try:
            C.parse_kpi_list(bad)
        except Exception:
            pass
        else:
            raise AssertionError(f"expected rejection of {bad!r}")


def test_solve_swap_trades():
    res = C.solve(SWAP, kpi=["trades"], want_stats=True)
    assert res.status == "Optimal", res.status
    pairs = {(s["give"], s["receive"]) for s in res.swaps}
    assert ("A", "B") in pairs and ("B", "A") in pairs, res.swaps
    assert res.stats["swap_vars"] == 2, res.stats
    assert res.stats["obj"] == 2, res.stats
    assert res.money_present is False


def test_solve_money_buy():
    res = C.solve(MONEY, kpi=["trades"], want_stats=True)
    assert res.money_present is True
    items = {p["item"] for p in res.cash_purchases}
    assert "C1" in items, res.cash_purchases
    summ = {r["user"]: r for r in res.cash_summary}
    assert summ["alice"]["spent"] == 10 and summ["alice"]["net"] == 10
    assert any(p["payer"] == "alice" and p["payee"] == "bob"
               for p in res.payments), res.payments


if __name__ == "__main__":
    test_parse_swap()
    test_parse_money()
    test_parse_bad_latitude()
    test_kpi_list_validation()
    test_solve_swap_trades()
    test_solve_money_buy()
    print("OK: parse tests passed")
