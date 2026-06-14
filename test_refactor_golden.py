"""to_text(solve(text)) must reproduce the captured CLI golden exactly."""
import os
import pareto_core as C
import serialize as S

HERE = os.path.dirname(os.path.abspath(__file__))
TC = os.path.join(HERE, "testcases")
GOLD = os.path.join(TC, "golden")


def cases():
    for dirpath, _, names in os.walk(TC):
        if os.path.abspath(dirpath).startswith(os.path.abspath(GOLD)):
            continue
        for n in sorted(names):
            if n.endswith(".txt"):
                rel = os.path.relpath(os.path.join(dirpath, n), TC)
                yield os.path.join(dirpath, n), rel[:-4].replace(os.sep, "__")


def test_golden():
    failures = []
    for path, name in sorted(cases()):
        with open(path) as f:
            text = f.read()
        got = S.to_text(C.solve(text, kpi=["trades"]))
        with open(os.path.join(GOLD, name + ".out")) as f:
            want = f.read()
        if got != want:
            failures.append(name)
    assert not failures, f"golden mismatch: {failures}"


if __name__ == "__main__":
    test_golden()
    print("OK: golden text matches baseline")
