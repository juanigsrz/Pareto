"""Unit tests for pareto_io pure helpers (no gurobipy, run with any python)."""
import hashlib
from pareto_io import canonical_json, checksum, detect_input_format


def test_canonical_json_sorts_keys():
    assert canonical_json({"b": 1, "a": 2}) == b'{"a":2,"b":1}'


def test_canonical_json_is_order_independent_for_keys():
    assert canonical_json({"a": 1, "b": 2}) == canonical_json({"b": 2, "a": 1})


def test_canonical_json_no_whitespace():
    assert b" " not in canonical_json({"x": [1, 2, 3]})


def test_checksum_prefix_and_hex():
    obj = {"a": 1}
    expected = "sha256:" + hashlib.sha256(canonical_json(obj)).hexdigest()
    assert checksum(obj) == expected


def test_checksum_stable_across_key_order():
    assert checksum({"a": 1, "b": 2}) == checksum({"b": 2, "a": 1})


def test_detect_text_directive():
    assert detect_input_format("alice : (1for1) A -> B\n") == "text"


def test_detect_json_object():
    assert detect_input_format('{"wishes": []}\n') == "json"


def test_detect_json_after_comments_and_blanks():
    assert detect_input_format("# a comment\n\n   {\n  \"wishes\": []}\n") == "json"


def test_detect_empty_is_text():
    assert detect_input_format("\n\n# only comments\n") == "text"


if __name__ == "__main__":  # self-contained runner, no framework needed (see README)
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
    print(f"{len(fns)} passed")
