"""Render a pareto_core.Result as CLI text or a structured JSON dict."""


def to_text(res):
    """Render a Result as the original CLI stdout (byte-identical)."""
    out = []
    out.append("\nTrade Results:")
    for s in res.swaps:
        out.append(f"{s['give']} -> {s['receive']}")
    for c in res.combo_trades:
        out.append(" ".join(c["sent"]) + " -> " + " ".join(c["taken"]))

    if res.money_present:
        if res.cash_purchases:
            out.append("\nCash Purchases:")
            for p in res.cash_purchases:
                out.append(f"{p['item']}: {p['from']} -> {p['to']}  "
                           f"({p['to']} pays {p['from']} ${p['price']})")
        out.append("\nCash Summary:")
        for r in res.cash_summary:
            cap = r["cap"]
            out.append(f"  {r['user']}: spent ${r['spent']:g}, "
                       f"earned ${r['earned']:g}, net ${r['net']:g} "
                       f"({r['direction']}) (cap ${cap})")
        if res.payments:
            out.append("\nPayments:")
            for p in res.payments:
                out.append(f"  {p['payer']} pays {p['payee']} ${p['amount']:g}")
        if res.settlement:
            out.append("\nSettlement plan:")
            for p in res.settlement:
                out.append(f"  {p['payer']} pays {p['payee']} ${p['amount']:g}")

    return "\n".join(out) + "\n"
