#!/usr/bin/env python3
"""Independent legality checker for Pareto solver output.

Reads a Pareto instance and a solver output, then verifies the output is a
*valid* solution from the perspective of a regular user -- without depending on
the solver in any way. It re-implements the constraint checks itself, so it can
catch solver bugs.

    python check.py INPUT.txt OUTPUT.txt
    python main.py in.txt | python check.py in.txt -    # OUTPUT '-' reads stdin

Exit 0 and print an OK line when valid; exit 1 and print one
'VIOLATION: ...' line per problem when invalid.

Checks (legality only -- not optimality):
  A. every swap move is backed by a real wish (right owner, give/take subset, NforM)
  B. no item moves twice; swap balance g == r (something received for what is given)
  C. every cash sale is a real, clearing, non-self purchase at the ask price
  D. takecap / givecap receive/give limits
  E. per-user budget: cash spend - cash earnings <= cap (swaps are moneyless)

Deliberately NOT checked: optimality, the distance KPI, and the cash
summary / payments / settlement prose.
"""
import re
import sys


# --------------------------------------------------------------------------- #
# Input parsing (own minimal parser, name space; trusts the instance is valid) #
# --------------------------------------------------------------------------- #
def parse_input(path):
    owner = {}            # item name -> user
    ask = {}              # item name -> price
    raw_bids = []         # (user, item, max_price)
    wishes = []           # (user, give[], take[], N, M)
    take_groups = []      # (user, N, items[])
    give_groups = []      # (user, N, items[])
    budget = {}           # user -> cap

    def set_owner(item, u):
        owner[item] = u   # last writer wins; the instance is trusted

    with open(path) as f:
        for raw in f:
            line = raw.partition('#')[0].strip()
            if not line:
                continue

            m_user = re.fullmatch(r'user\s+(\S+)\s+budget\s+(\d+)', line)
            m_item = re.fullmatch(r'item\s+(\S+)\s+owner\s+(\S+)(?:\s+ask\s+(\d+))?', line)
            m_bid = re.fullmatch(r'bid\s+(\S+)\s+(\S+)\s+(\d+)', line)
            m_take = re.fullmatch(r'takecap\s+(\S+)\s+(\d+)\s+(.+)', line)
            m_give = re.fullmatch(r'givecap\s+(\S+)\s+(\d+)\s+(.+)', line)
            m_dup = re.fullmatch(r'dupcap\s+(\S+)\s+(.+)', line)
            m_loc = re.fullmatch(
                r'location\s+(\S+)\s+(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)', line)

            if m_user:
                budget[m_user.group(1)] = int(m_user.group(2))
            elif m_item:
                set_owner(m_item.group(1), m_item.group(2))
                if m_item.group(3) is not None:
                    ask[m_item.group(1)] = int(m_item.group(3))
            elif m_bid:
                raw_bids.append((m_bid.group(1), m_bid.group(2), int(m_bid.group(3))))
            elif m_take:
                take_groups.append((m_take.group(1), int(m_take.group(2)),
                                    m_take.group(3).split()))
            elif m_give:
                give_groups.append((m_give.group(1), int(m_give.group(2)),
                                    m_give.group(3).split()))
            elif m_dup:
                take_groups.append((m_dup.group(1), 1, m_dup.group(2).split()))
            elif m_loc:
                pass  # locations only feed the distance KPI -- irrelevant to legality
            elif ':' in line:
                user, _, body = line.partition(':')
                give, take, N, M = parse_wish_body(body.strip())
                for g in give:
                    set_owner(g, user.strip())  # giving an item implies owning it
                wishes.append((user.strip(), give, take, N, M))
            # anything else: not our concern (we are not an input validator)

    # A bid creates a real cash edge only when it clears the ask and isn't a self-buy.
    bids = set()
    for u, item, price in raw_bids:
        if item in ask and price >= ask[item] and owner.get(item) != u:
            bids.add((u, item))

    return {
        "owner": owner, "ask": ask, "bids": bids, "wishes": wishes,
        "take_groups": take_groups, "give_groups": give_groups, "budget": budget,
    }


def parse_wish_body(body):
    """'(NforM) give... -> take...' -> (give[], take[], N, M)."""
    r = body.find(')')
    options = body[1:r].split()
    rest = body[r + 1:].strip()
    groups = [part.split() for part in rest.split("->")]
    give, take = groups[0], groups[1]
    N, M = len(give), len(take)
    for opt in options:
        mm = re.fullmatch(r'(\d+)for(\d+)', opt)
        if mm:
            N, M = int(mm.group(1)), int(mm.group(2))
    return give, take, N, M


# --------------------------------------------------------------------------- #
# Output parsing (section-aware)                                              #
# --------------------------------------------------------------------------- #
CASH_RE = re.compile(r'^(\S+):\s+(\S+)\s+->\s+(\S+)\s+\((\S+)\s+pays\s+(\S+)\s+\$(\d+)\)$')


def parse_output(text):
    swap_moves = []   # (given[], received[])
    cash_moves = []   # (item, seller, buyer, price)
    section = None
    for raw in text.splitlines():
        line = raw.strip()
        if line in ("Trade Results:", "Cash Purchases:"):
            section = line
            continue
        if line in ("Cash Summary:", "Payments:", "Settlement plan:"):
            section = "ignore"
            continue
        if not line:
            continue
        if section == "Trade Results:" and "->" in line:
            given, _, received = line.partition("->")
            swap_moves.append((given.split(), received.split()))
        elif section == "Cash Purchases:":
            m = CASH_RE.match(line)
            if m:
                cash_moves.append((m.group(1), m.group(2), m.group(3), int(m.group(6))))
    return swap_moves, cash_moves


# --------------------------------------------------------------------------- #
# Checks                                                                       #
# --------------------------------------------------------------------------- #
def check(inst, swap_moves, cash_moves):
    owner = inst["owner"]
    ask = inst["ask"]
    bids = inst["bids"]
    wishes = inst["wishes"]
    budget = inst["budget"]
    v = []  # violations

    # Per-item move counts: g=swap-given, r=swap-received, c=cash-sold.
    g_count, r_count, c_count = {}, {}, {}
    # Per-user received / given item sets (for caps), from swaps and cash.
    recv_by_user, give_by_user = {}, {}

    def bump(d, k):
        d[k] = d.get(k, 0) + 1

    # ---- A. swap backing + tally g / r and per-user receive/give ---- #
    for given, received in swap_moves:
        owners = {owner.get(it) for it in given}
        u = next(iter(owners)) if len(owners) == 1 else None
        if u is None:
            v.append(f"swap move {given} -> {received}: items given are not all "
                     f"owned by one user (owners {sorted(map(str, owners))})")
        elif not _backing_wish(wishes, u, given, received):
            v.append(f"swap move {given} -> {received}: not backed by any wish of '{u}'")

        for it in received:
            if it not in owner:
                v.append(f"swap move {given} -> {received}: received item '{it}' "
                         f"has no declared owner")
        for it in given:
            bump(g_count, it)
        for it in received:
            bump(r_count, it)
        if u is not None:
            recv_by_user.setdefault(u, set()).update(received)
            give_by_user.setdefault(u, set()).update(given)

    # ---- C. cash legality + tally c and per-user receive/give ---- #
    for item, seller, buyer, price in cash_moves:
        if owner.get(item) != seller:
            v.append(f"cash sale of '{item}': seller '{seller}' is not the owner "
                     f"('{owner.get(item)}')")
        if item not in ask:
            v.append(f"cash sale of '{item}': item has no ask price")
        elif price != ask[item]:
            v.append(f"cash sale of '{item}': paid ${price} but ask is ${ask[item]}")
        if seller == buyer:
            v.append(f"cash sale of '{item}': buyer and seller are both '{seller}'")
        clears = (buyer, item) in bids or _wished_by(wishes, buyer, item)
        if not clears:
            v.append(f"cash sale of '{item}': no clearing bid from '{buyer}' "
                     f"(no explicit bid >= ask, not a wished item)")
        bump(c_count, item)
        recv_by_user.setdefault(buyer, set()).add(item)
        give_by_user.setdefault(seller, set()).add(item)

    # ---- B. single-use + swap balance ---- #
    for it in set(g_count) | set(r_count) | set(c_count):
        g, r, c = g_count.get(it, 0), r_count.get(it, 0), c_count.get(it, 0)
        if g > 1:
            v.append(f"item '{it}' is given via swap {g} times (max 1)")
        if r > 1:
            v.append(f"item '{it}' is received via swap {r} times (max 1)")
        if c > 1:
            v.append(f"item '{it}' is cash-sold {c} times (max 1)")
        if g != r:
            v.append(f"item '{it}' broken swap balance: given {g} time(s) but "
                     f"received {r} time(s) (must be equal -- nothing for nothing)")
        if g and c:
            v.append(f"item '{it}' is both swapped and cash-sold (same copy "
                     f"cannot move twice)")

    # ---- D. caps ---- #
    for u, n, items in inst["take_groups"]:
        got = recv_by_user.get(u, set()) & set(items)
        if len(got) > n:
            v.append(f"takecap: '{u}' receives {len(got)} of {items} "
                     f"({sorted(got)}) but cap is {n}")
    for u, n, items in inst["give_groups"]:
        gave = give_by_user.get(u, set()) & set(items)
        if len(gave) > n:
            v.append(f"givecap: '{u}' gives {len(gave)} of {items} "
                     f"({sorted(gave)}) but cap is {n}")

    # ---- E. budget (swaps moneyless: only cash moves money) ---- #
    spend, earn = {}, {}
    for item, seller, buyer, price in cash_moves:
        spend[buyer] = spend.get(buyer, 0) + price
        earn[seller] = earn.get(seller, 0) + price
    for u, cap in budget.items():
        net = spend.get(u, 0) - earn.get(u, 0)
        if net > cap:
            v.append(f"budget: '{u}' net cash spend ${net} exceeds cap ${cap}")

    return v


def _backing_wish(wishes, u, given, received):
    g, r = set(given), set(received)
    for wu, wgive, wtake, N, M in wishes:
        if wu == u and g <= set(wgive) and r <= set(wtake) and len(given) <= N and len(received) >= M:
            return True
    return False


def _wished_by(wishes, u, item):
    return any(wu == u and item in wtake for wu, _, wtake, _, _ in wishes)


# --------------------------------------------------------------------------- #
# Main                                                                          #
# --------------------------------------------------------------------------- #
def main(argv):
    if len(argv) != 3:
        sys.exit("usage: check.py INPUT.txt OUTPUT.txt   (OUTPUT '-' = stdin)")
    inst = parse_input(argv[1])
    text = sys.stdin.read() if argv[2] == "-" else open(argv[2]).read()
    swap_moves, cash_moves = parse_output(text)
    violations = check(inst, swap_moves, cash_moves)
    if violations:
        for msg in violations:
            print(f"VIOLATION: {msg}")
        return 1
    print(f"OK: {len(swap_moves)} swap moves, {len(cash_moves)} cash moves, "
          f"all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
