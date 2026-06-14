"""Unit tests for the request validator (no Modal import side effects)."""
from modal_app import validate_request, ValidationError


def ok(payload):
    return validate_request(payload)


def bad(payload, needle):
    try:
        validate_request(payload)
    except ValidationError as e:
        assert needle in str(e), (needle, str(e))
    else:
        raise AssertionError(f"expected ValidationError for {payload}")


def test_minimal_ok():
    p = ok({"instance": "alice: (1for1) A -> B\n"})
    assert p["kpi"] == ["trades"]
    assert p["time_limit"] is None and p["mipgap"] is None
    assert p["want_stats"] is False


def test_full_ok():
    p = ok({"instance": "x", "kpi": "trades,users",
            "time_limit": 30, "mipgap": 0.01, "stats": True})
    assert p["kpi"] == ["trades", "users"]
    assert p["time_limit"] == 30 and p["want_stats"] is True


def test_missing_instance():
    bad({}, "instance")
    bad({"instance": ""}, "instance")


def test_bad_kpi():
    bad({"instance": "x", "kpi": "trades,bogus"}, "bogus")


def test_time_limit_cap():
    bad({"instance": "x", "time_limit": 99999}, "time_limit")
    bad({"instance": "x", "time_limit": -1}, "time_limit")


def test_instance_too_big():
    bad({"instance": "x" * (1024 * 1024 + 1)}, "too large")


def test_bad_mipgap():
    bad({"instance": "x", "mipgap": -0.5}, "mipgap")


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("OK: validation tests passed")
