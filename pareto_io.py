"""Pure, dependency-free I/O helpers for Pareto: canonical serialization,
checksums, and input-format detection. Deliberately imports nothing from
gurobipy and has no import-time side effects, so it is fast to unit-test."""
import hashlib
import json


def canonical_json(obj):
    """Deterministic bytes for `obj`: sorted keys, no whitespace, UTF-8.
    The single canonicalization rule used for every checksum."""
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def checksum(obj):
    """`'sha256:<hex>'` over `canonical_json(obj)`."""
    return "sha256:" + hashlib.sha256(canonical_json(obj)).hexdigest()


def detect_input_format(raw):
    """Peek the first meaningful character of `raw` (blank lines and `#`
    comments skipped). Leading `{` ⇒ 'json', otherwise 'text'."""
    for line in raw.splitlines():
        line = line.partition("#")[0].strip()
        if not line:
            continue
        return "json" if line[0] == "{" else "text"
    return "text"
