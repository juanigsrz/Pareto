"""JSON input parses into the same instance as the equivalent text, proving
input_checksum is format-independent."""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN = os.path.join(HERE, "main.py")
SWAP_TXT = os.path.join(HERE, "testcases/money/swap.txt")

# Equivalent to testcases/money/swap.txt.
SWAP_JSON = {
    "budgets": [{"user": "alice", "budget": 0}, {"user": "bob", "budget": 0}],
    "items": [{"name": "A", "owner": "alice", "ask": 5},
              {"name": "B", "owner": "bob", "ask": 5}],
    "wishes": [{"user": "alice", "give": ["A"], "take": ["B"], "n": 1, "m": 1},
               {"user": "bob", "give": ["B"], "take": ["A"], "n": 1, "m": 1}],
}


def run_json_out(path, *extra):
    out = subprocess.run([sys.executable, MAIN, path, "--format", "json", *extra],
                         capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def test_json_input_matches_text_checksum():
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(SWAP_JSON, f)
        jpath = f.name
    try:
        d_txt = run_json_out(SWAP_TXT)
        d_json = run_json_out(jpath)               # auto-detected as JSON
        assert d_json["input_checksum"] == d_txt["input_checksum"]
        assert {(t["give"], t["take"]) for t in d_json["trades"]} == \
               {(t["give"], t["take"]) for t in d_txt["trades"]}
    finally:
        os.unlink(jpath)


def test_stdin_json_with_explicit_format():
    payload = json.dumps(SWAP_JSON)
    out = subprocess.run(
        [sys.executable, MAIN, "-", "--in-format", "json", "--format", "json"],
        input=payload, capture_output=True, text=True, check=True).stdout
    assert json.loads(out)["status"] == "Optimal"


if __name__ == "__main__":  # self-contained runner, no framework needed (see README)
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
    print(f"{len(fns)} passed")
