import re
import math
import argparse
from dataclasses import dataclass, field

import gurobipy as gp
from gurobipy import GRB


ALLOWED_KPIS = ("trades", "users", "distance")


def parse_kpi_list(s):
    """Comma-separated KPIs in priority order, e.g. 'trades,users'."""
    kpis = []
    for tok in s.split(","):
        tok = tok.strip()
        if not tok:
            raise argparse.ArgumentTypeError("empty KPI in --kpi list")
        if tok not in ALLOWED_KPIS:
            raise argparse.ArgumentTypeError(
                f"invalid KPI '{tok}' (choose from {', '.join(ALLOWED_KPIS)})")
        if tok in kpis:
            raise argparse.ArgumentTypeError(f"duplicate KPI '{tok}'")
        kpis.append(tok)
    return kpis


@dataclass
class Instance:
    item_to_id: dict = field(default_factory=dict)
    id_to_item: dict = field(default_factory=dict)
    wishes: list = field(default_factory=list)   # (user, give, take, N, M)
    users: set = field(default_factory=set)
    budget: dict = field(default_factory=dict)
    owner: dict = field(default_factory=dict)
    ask: dict = field(default_factory=dict)
    bids: dict = field(default_factory=dict)
    dup_groups: list = field(default_factory=list)
    location: dict = field(default_factory=dict)

    def intern(self, token):
        if token not in self.item_to_id:
            self.item_to_id[token] = len(self.item_to_id)
            self.id_to_item[self.item_to_id[token]] = token
        return self.item_to_id[token]


def intern_lookup(inst, token):
    """Test helper: id of an already-interned token."""
    return inst.item_to_id[token]


def _set_owner(inst, iid, u, line):
    if iid in inst.owner and inst.owner[iid] != u:
        raise ValueError(
            f"Item '{inst.id_to_item[iid]}' has conflicting owners "
            f"'{inst.owner[iid]}' and '{u}': {line}")
    inst.owner[iid] = u


def _parse_wish_body(inst, body, line):
    if not body.startswith('('):
        raise ValueError(f"Missing options: {line}")
    r = body.find(')')
    if r == -1:
        raise ValueError(f"Missing closing ')': {line}")
    if '->' not in body:
        raise ValueError(f"Missing '->': {line}")
    options = body[1:r].strip().split()
    rest = body[r + 1:].strip()
    groups = [part.strip().split() for part in rest.split("->")]
    if len(groups) > 2:
        raise ValueError(f"Non supported amount of groups (max 2): {line}")
    N, M = len(groups[0]), len(groups[1])
    for opt in options:
        match = re.fullmatch(r'(\d+)for(\d+)', opt)
        if not match:
            raise ValueError(
                f"Option must be in 'NforM' format, e.g., '2for1': {line}")
        N = int(match.group(1))
        M = int(match.group(2))
    give = [inst.intern(t) for t in groups[0]]
    take = [inst.intern(t) for t in groups[1]]
    return give, take, N, M


def parse(text):
    """Parse instance text into an Instance. Raises ValueError on bad input."""
    inst = Instance()
    for raw in text.splitlines():
        line = raw.partition('#')[0].strip()
        if not line:
            continue
        m_user = re.fullmatch(r'user\s+(\S+)\s+budget\s+(\d+)', line)
        m_item = re.fullmatch(
            r'item\s+(\S+)\s+owner\s+(\S+)(?:\s+ask\s+(\d+))?', line)
        m_bid = re.fullmatch(r'bid\s+(\S+)\s+(\S+)\s+(\d+)', line)
        m_dup = re.fullmatch(r'dupcap\s+(\S+)\s+(.+)', line)
        m_loc = re.fullmatch(
            r'location\s+(\S+)\s+(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)', line)
        if m_user:
            inst.users.add(m_user.group(1))
            inst.budget[m_user.group(1)] = int(m_user.group(2))
        elif m_item:
            iid = inst.intern(m_item.group(1))
            u = m_item.group(2)
            inst.users.add(u)
            _set_owner(inst, iid, u, raw)
            if m_item.group(3) is not None:
                inst.ask[iid] = int(m_item.group(3))
        elif m_bid:
            u = m_bid.group(1)
            inst.users.add(u)
            iid = inst.intern(m_bid.group(2))
            inst.bids[(u, iid)] = int(m_bid.group(3))
        elif m_dup:
            u = m_dup.group(1)
            inst.users.add(u)
            inst.dup_groups.append(
                (u, [inst.intern(t) for t in m_dup.group(2).split()]))
        elif m_loc:
            u = m_loc.group(1)
            lat = float(m_loc.group(2))
            lng = float(m_loc.group(3))
            if not (-90 <= lat <= 90):
                raise ValueError(f"latitude out of range [-90, 90]: {raw}")
            if not (-180 <= lng <= 180):
                raise ValueError(f"longitude out of range [-180, 180]: {raw}")
            inst.users.add(u)
            inst.location[u] = (lat, lng)
        elif ':' in line:
            u, _, body = line.partition(':')
            u = u.strip()
            inst.users.add(u)
            give, take, N, M = _parse_wish_body(inst, body.strip(), raw)
            for g in give:
                _set_owner(inst, g, u, raw)
            inst.wishes.append((u, give, take, N, M))
        else:
            raise ValueError(f"Unrecognized line: {raw}")
    return inst
