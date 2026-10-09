"""Exact, bounded missing-evidence antichains; no evidence or truth promotion.

Only changed premise families wake dependent rules. Integer masks encode sets
within this operation; zero is an empty obligation set, not an empty family.
"""
from collections import deque


class _Truncated(Exception):
    pass


class _Antichain:
    def __init__(self, cap, masks=()):
        self.cap = cap
        self.masks = set(masks)
        self.by_size = {}
        for mask in self.masks:
            self.by_size.setdefault(mask.bit_count(), set()).add(mask)
        self._ordered = None

    def dominates(self, mask):
        if mask in self.masks:
            return True
        size = mask.bit_count()
        return any(old & mask == old for count, bucket in self.by_size.items()
                   if count < size for old in bucket)

    def add(self, mask):
        if self.dominates(mask):
            return False
        size = mask.bit_count()
        removed = [old for count, bucket in self.by_size.items() if count > size
                   for old in bucket if old & mask == mask]
        if len(self.masks) - len(removed) + 1 > self.cap:
            raise _Truncated('max_blocker_sets exceeded')
        for old in removed:
            self.masks.remove(old)
            bucket = self.by_size[old.bit_count()]
            bucket.remove(old)
            if not bucket:
                del self.by_size[old.bit_count()]
        self.masks.add(mask)
        self.by_size.setdefault(size, set()).add(mask)
        self._ordered = None
        return True

    def ordered(self):
        if self._ordered is None:
            self._ordered = tuple(sorted(self.masks, key=lambda mask: (mask.bit_count(), mask)))
        return self._ordered


def missing_families(nodes, edges, closure, direct, relevant_edges, blocked_rules, limits):
    """Return exact families, actual candidate work and explicit incompleteness.

Pruning is set inclusion only. If an existing head set H is contained in a
partial AND union P, every extension of P is already dominated by H. Dropping
that branch preserves the upward closure even if a later smaller H replaces it.
Duplicate premise families can be skipped because F AND F = F for monotone DNF.
No cache survives this call, a retraction or a changed receipt audit.
"""
    active = [edge for edge in edges if edge['id'] in relevant_edges
              and edge['status'] != 'CONTRADICTED' and edge['conclusion'] not in closure
              and nodes[edge['conclusion']]['status'] != 'CONTRADICTED']
    if not active:
        return ({ident: {frozenset()} if ident in closure else
                 {frozenset({'node:' + ident})} if ident in direct else set()
                 for ident in nodes}, 0, False, None)
    proposed = {e['id'] for e in active if e['status'] == 'PROPOSED' or e['id'] in blocked_rules}
    used = {edge['conclusion'] for edge in active} | {tail for edge in active for tail in edge['premises']}
    atoms = sorted(['node:' + ident for ident in direct & used] + ['rule:' + ident for ident in proposed])
    bits = {atom: 1 << i for i, atom in enumerate(atoms)}
    cap = limits['max_blocker_sets']
    families = {ident: _Antichain(cap, (0,) if ident in closure else
                (bits['node:' + ident],) if ident in direct else ()) for ident in used}
    waiting = {}
    for i, edge in enumerate(active):
        for tail in edge['premises']:
            waiting.setdefault(tail, []).append(i)
    pending, queued = deque(range(len(active))), set(range(len(active)))
    combinations, truncated, reason = 0, False, None

    def spend():
        nonlocal combinations
        if combinations >= limits['max_combinations']:
            raise _Truncated('max_combinations exceeded')
        combinations += 1

    try:
        while pending:
            i = pending.popleft()
            queued.remove(i)
            edge = active[i]
            target = families[edge['conclusion']]
            seed = bits['rule:' + edge['id']] if edge['id'] in proposed else 0
            if target.dominates(seed):
                continue
            premises = [families[tail].ordered() for tail in edge['premises']]
            if any(not family for family in premises):
                continue
            # Narrow factors first; exact repeated families are idempotent.
            premises = sorted(dict.fromkeys(family for family in premises if family != (0,)), key=len)
            changed = False
            if len(premises) <= 1:
                # The usual chain needs no temporary product antichains.
                for right in premises[0] if premises else (0,):
                    if premises:
                        spend()
                    plan = seed | right
                    if not target.dominates(plan):
                        spend()
                        changed |= target.add(plan)
            else:
                plans = _Antichain(cap, (seed,))
                for family in premises:
                    joined = _Antichain(cap)
                    for left in plans.ordered():
                        for right in family:
                            spend()
                            union = left | right
                            if not target.dominates(union):
                                joined.add(union)
                    plans = joined
                    if not plans.masks:
                        break
                for plan in plans.ordered():
                    spend()
                    changed |= target.add(plan)
            if changed:
                # A replacement by a smaller set matters even at unchanged size.
                for dependent in waiting.get(edge['conclusion'], ()):
                    if dependent not in queued:
                        pending.append(dependent)
                        queued.add(dependent)
    except _Truncated as exc:
        truncated, reason = True, str(exc)

    decoded = {}

    def decode(mask):
        if mask not in decoded:
            rest, values = mask, []
            while rest:
                bit = rest & -rest
                values.append(atoms[bit.bit_length() - 1])
                rest ^= bit
            decoded[mask] = frozenset(values)
        return decoded[mask]

    result = {ident: {decode(mask) for mask in families[ident].masks} if ident in families else
              {frozenset()} if ident in closure else {frozenset({'node:' + ident})} if ident in direct else set()
              for ident in nodes}
    return result, combinations, truncated, reason
