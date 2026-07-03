"""The in-process solve->render path must reproduce the CLI golden output.

Cross-checks pareto_core.solve + serialize.render_text against the same
testcases/golden/*.out fixtures that test_golden.py pins for the subprocess CLI.
render_text is called without a checksum, matching the headerless golden bodies
(the CLI prepends a checksum header that test_golden's body() strips)."""
import os

import pareto_core as C
import serialize as S

HERE = os.path.dirname(os.path.abspath(__file__))

CASES = {
    "money__swap": "testcases/money/swap.txt",
    "money__cashchain": "testcases/money/cashchain.txt",
    "2for11for2": "testcases/2for11for2.txt",
    "distance": "testcases/distance.txt",
}


def body(text):
    """Everything from the first 'Trade Results:' line onward -- the same slice
    test_golden applies to CLI stdout, so both sides compare the same bytes."""
    lines = text.splitlines(keepends=True)
    for i, line in enumerate(lines):
        if line.strip() == "Trade Results:":
            return "".join(lines[i:])
    raise AssertionError("no 'Trade Results:' marker in output")


def test_golden():
    failures = []
    for name, path in sorted(CASES.items()):
        with open(os.path.join(HERE, path)) as f:
            text = f.read()
        sol = C.solve(text, kpi=["trades"])      # goldens captured at the CLI default
        got = body(S.render_text(sol.result))    # headerless body, matches golden
        with open(os.path.join(HERE, "testcases/golden", name + ".out")) as f:
            want = f.read()
        if got != want:
            failures.append(name)
    assert not failures, f"golden mismatch: {failures}"


if __name__ == "__main__":
    test_golden()
    print("OK: in-process golden matches CLI baseline")
