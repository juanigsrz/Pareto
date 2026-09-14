"""--format json emits a well-formed, versioned, checksummed result that
agrees with the text output."""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN = os.path.join(HERE, "main.py")
SWAP = os.path.join(HERE, "testcases/money/swap.txt")


def run_json_raw(path, *extra):
    return subprocess.run([sys.executable, MAIN, path, "--format", "json", *extra],
                          capture_output=True, text=True, check=True).stdout


def run_json(path, *extra):
    return json.loads(run_json_raw(path, *extra))


def test_json_has_metadata():
    d = run_json(SWAP)
    assert d["version"] == "1.1.0"
    assert d["input_checksum"].startswith("sha256:")
    assert d["result_checksum"].startswith("sha256:")
    assert "gurobi_version" in d
    assert d["status"] == "Optimal"


def test_json_trades_match_swap():
    d = run_json(SWAP)
    pairs = {(t["give"], t["take"]) for t in d["trades"]}
    assert pairs == {("A", "B"), ("B", "A")}


def test_result_checksum_excludes_metadata_and_is_stable():
    d = run_json(SWAP)
    import pareto_io
    stripped = {k: v for k, v in d.items()
                if k not in ("version", "gurobi_version",
                             "input_checksum", "result_checksum")}
    assert pareto_io.checksum(stripped) == d["result_checksum"]


def test_json_stdout_is_pure():
    out = run_json_raw(SWAP)
    assert out.lstrip().startswith("{"), "stdout must start with JSON, not a solver log"
    for banned in ("Set parameter", "Gurobi Optimizer", "Academic license"):
        assert banned not in out, f"solver-log line {banned!r} leaked into JSON stdout"
    json.loads(out)  # must be parseable as a whole


def test_json_valid_on_no_solution():
    import os
    env = dict(os.environ, PARETO_TIME_LIMIT="0")
    out = subprocess.run([sys.executable, MAIN, SWAP, "--format", "json"],
                         capture_output=True, text=True, check=True, env=env).stdout
    d = json.loads(out)
    assert d["status"] != "Optimal"
    assert d["trades"] == [] and d["cash_purchases"] == []
    assert d["input_checksum"].startswith("sha256:")
    assert d["result_checksum"].startswith("sha256:")


if __name__ == "__main__":  # self-contained runner, no framework needed (see README)
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
    print(f"{len(fns)} passed")
