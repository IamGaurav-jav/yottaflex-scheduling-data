"""Half-open interval [a, b) primitives."""
from bisect import bisect_right


def merge(ivs):
    """Sort and merge overlapping or touching intervals."""
    out = []
    for a, b in sorted(ivs):
        if b <= a:
            continue
        if out and a <= out[-1][1]:
            if b > out[-1][1]:
                out[-1][1] = b
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


def subtract(ivs, cuts):
    """Remove `cuts` from merged `ivs`."""
    cuts = merge(cuts)
    out = []
    for a, b in ivs:
        cur = a
        for c, d in cuts:
            if d <= cur:
                continue
            if c >= b:
                break
            if c > cur:
                out.append((cur, c))
            cur = max(cur, d)
            if cur >= b:
                break
        if cur < b:
            out.append((cur, b))
    return out


def clip(ivs, lo, hi):
    return [(max(a, lo), min(b, hi)) for a, b in ivs if min(b, hi) > max(a, lo)]


def covering(ivs, s, e):
    """Return the merged interval containing [s, e), else None. `ivs` must be merged."""
    starts = [a for a, _ in ivs]
    i = bisect_right(starts, s) - 1
    if i >= 0 and ivs[i][0] <= s and e <= ivs[i][1]:
        return ivs[i]
    return None


def overlaps(a1, b1, a2, b2):
    return a1 < b2 and a2 < b1
