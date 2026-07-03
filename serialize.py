"""Render a pareto_core result dict as CLI text or a versioned JSON document.

Both renderers take the plain result dict plus the input checksum, so the CLI
and the Modal service share one formatting path. `_meta` stamps the pareto /
gurobi versions and a result checksum onto every emitted document.
"""
import json

import gurobipy as gp

import pareto_io
from pareto_core import __version__


def _gurobi_version():
    try:
        return ".".join(str(x) for x in gp.gurobi.version())
    except Exception:
        return "unknown"


def _meta(result, input_checksum):
    return {"version": __version__, "gurobi_version": _gurobi_version(),
            "input_checksum": input_checksum,
            "result_checksum": pareto_io.checksum(result)}


def render_text(result, input_checksum=None):
    out = []
    if input_checksum is not None:
        m = _meta(result, input_checksum)
        out += [f"# pareto {m['version']}  gurobi {m['gurobi_version']}",
                f"# input_checksum  {m['input_checksum']}",
                f"# result_checksum {m['result_checksum']}"]
    out.append("\nTrade Results:")
    for t in result["trades"]:
        out.append(f"{t['give']} -> {t['take']}")
    for c in result["combos"]:
        out.append(" ".join(c["sent"]) + " -> " + " ".join(c["taken"]))
    if result["cash_summary"] or result["cash_purchases"]:
        if result["cash_purchases"]:
            out.append("\nCash Purchases:")
            for p in result["cash_purchases"]:
                out.append(f"{p['item']}: {p['from']} -> {p['to']}  "
                           f"({p['to']} pays {p['from']} ${p['price']})")
        out.append("\nCash Summary:")
        for s in result["cash_summary"]:
            cap = "inf" if s["cap"] is None else f"{s['cap']}"
            direction = "owes" if s["net"] > 0 else "receives" if s["net"] < 0 else "even"
            out.append(f"  {s['user']}: spent ${s['spent']:g}, earned ${s['earned']:g}, "
                       f"net ${s['net']:g} ({direction}) (cap ${cap})")
        if result["payments"]:
            out.append("\nPayments:")
            for p in result["payments"]:
                out.append(f"  {p['from']} pays {p['to']} ${p['amount']:g}")
        if result["settlement"]:
            out.append("\nSettlement plan:")
            for p in result["settlement"]:
                out.append(f"  {p['from']} pays {p['to']} ${p['amount']:g}")
    return "\n".join(out) + "\n"


def render_json(result, input_checksum):
    doc = {**_meta(result, input_checksum), **result}
    return json.dumps(doc, indent=2) + "\n"


# --- Solution convenience wrappers (used by the Modal service) ---------------

def to_text(solution):
    """CLI-style text for a pareto_core.Solution (with the checksum header)."""
    return render_text(solution.result, solution.input_checksum)


def to_dict(solution):
    """Versioned, checksummed JSON-ready dict for the HTTP service."""
    return {**_meta(solution.result, solution.input_checksum), **solution.result}
