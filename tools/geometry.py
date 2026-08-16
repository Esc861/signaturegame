"""Polyline cleanup, normalization and complexity metrics.

Every Commons signature we sampled turned out to be a filled *outline* rather
than a stroked centerline, so the metrics here are written to read sensibly
against outlines: what we measure is the traced contour, and a single pen
stroke shows up as a long thin ribbon with two sides.
"""

import math


# --------------------------------------------------------------------------
# basics
# --------------------------------------------------------------------------

def bbox(shapes):
    xs0 = ys0 = float("inf")
    xs1 = ys1 = float("-inf")
    for s in shapes:
        for x, y in s["points"]:
            if x < xs0: xs0 = x
            if x > xs1: xs1 = x
            if y < ys0: ys0 = y
            if y > ys1: ys1 = y
    if xs0 == float("inf"):
        return (0.0, 0.0, 0.0, 0.0)
    return (xs0, ys0, xs1, ys1)


def polyline_length(pts):
    L = 0.0
    for i in range(1, len(pts)):
        L += math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1])
    return L


def polygon_area(pts):
    """Shoelace. Sign indicates winding; callers take abs."""
    a = 0.0
    n = len(pts)
    for i in range(n):
        x0, y0 = pts[i]
        x1, y1 = pts[(i + 1) % n]
        a += x0 * y1 - x1 * y0
    return a / 2.0


# --------------------------------------------------------------------------
# simplification
# --------------------------------------------------------------------------

def rdp(pts, eps):
    """Ramer-Douglas-Peucker, iterative.

    The source files flatten to hundreds of thousands of points; recursion
    would blow the stack, and uniform resampling would keep far more points
    than the shape needs. RDP spends points where the curve actually bends.
    """
    n = len(pts)
    if n < 3:
        return list(pts)
    keep = [False] * n
    keep[0] = keep[n - 1] = True
    stack = [(0, n - 1)]
    e2 = eps * eps

    while stack:
        i, j = stack.pop()
        if j <= i + 1:
            continue
        ax, ay = pts[i]
        bx, by = pts[j]
        dx, dy = bx - ax, by - ay
        d2 = dx * dx + dy * dy
        best = -1.0
        bi = -1
        for k in range(i + 1, j):
            px, py = pts[k]
            if d2 == 0.0:
                ex, ey = px - ax, py - ay
            else:
                t = ((px - ax) * dx + (py - ay) * dy) / d2
                t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
                ex, ey = px - (ax + t * dx), py - (ay + t * dy)
            dist2 = ex * ex + ey * ey
            if dist2 > best:
                best = dist2
                bi = k
        if best > e2:
            keep[bi] = True
            stack.append((i, bi))
            stack.append((bi, j))

    return [pts[i] for i in range(n) if keep[i]]


def dedupe(pts, tol=1e-9):
    out = []
    for p in pts:
        if not out or abs(p[0] - out[-1][0]) > tol or abs(p[1] - out[-1][1]) > tol:
            out.append(p)
    return out


# --------------------------------------------------------------------------
# normalization
# --------------------------------------------------------------------------

def normalize(shapes, box=1000.0):
    """Uniformly scale + translate so the ink fits a `box`-square, origin at 0.

    Aspect ratio is preserved, so the returned view is `box` on its long side.
    """
    x0, y0, x1, y1 = bbox(shapes)
    w, h = x1 - x0, y1 - y0
    if w <= 0 and h <= 0:
        return shapes, 0.0, 0.0
    scale = box / max(w, h)
    for s in shapes:
        s["points"] = [((x - x0) * scale, (y - y0) * scale) for x, y in s["points"]]
    return shapes, w * scale, h * scale


def prune(shapes, min_frac=0.005):
    """Drop specks: contours contributing under `min_frac` of total perimeter.

    Auto-traced scans routinely carry stray dots and paper-speckle artifacts
    that would otherwise count as pen lifts and skew the difficulty score.
    """
    lengths = [polyline_length(s["points"]) for s in shapes]
    total = sum(lengths)
    if total <= 0:
        return []
    return [s for s, L in zip(shapes, lengths) if L / total >= min_frac]


# --------------------------------------------------------------------------
# complexity metrics
# --------------------------------------------------------------------------

def _edges(shapes):
    out = []
    for s in shapes:
        p = s["points"]
        for i in range(len(p) - 1):
            out.append((p[i], p[i + 1]))
        if s.get("closed") and len(p) > 2 and p[0] != p[-1]:
            out.append((p[-1], p[0]))
    return out


def turning(pts):
    """Total absolute exterior angle, in radians."""
    total = 0.0
    for i in range(1, len(pts) - 1):
        ax = pts[i][0] - pts[i - 1][0]
        ay = pts[i][1] - pts[i - 1][1]
        bx = pts[i + 1][0] - pts[i][0]
        by = pts[i + 1][1] - pts[i][1]
        if (ax or ay) and (bx or by):
            total += abs(math.atan2(ax * by - ay * bx, ax * bx + ay * by))
    return total


def sharp_corners(pts, min_deg=60.0, min_seg=4.0):
    """Count direction changes past `min_deg`.

    Short segments are skipped: on a simplified contour a pair of tiny
    neighbouring segments can meet at a steep angle without the shape
    actually having a corner there.
    """
    thresh = math.radians(min_deg)
    n = 0
    for i in range(1, len(pts) - 1):
        ax = pts[i][0] - pts[i - 1][0]
        ay = pts[i][1] - pts[i - 1][1]
        bx = pts[i + 1][0] - pts[i][0]
        by = pts[i + 1][1] - pts[i][1]
        if math.hypot(ax, ay) < min_seg or math.hypot(bx, by) < min_seg:
            continue
        ang = abs(math.atan2(ax * by - ay * bx, ax * bx + ay * by))
        if ang > thresh:
            n += 1
    return n


def scanline_tangle(shapes, samples=72):
    """Mean number of pen strokes a horizontal line passes through.

    This is the best single indicator of how tangled a signature is. A plain
    cursive name crosses a given scanline two or three times; one loaded with
    loops, underlines and a struck-through flourish crosses it far more. Since
    we are measuring filled outlines, each pen stroke contributes two contour
    crossings, hence the halving.
    """
    edges = _edges(shapes)
    if not edges:
        return 0.0
    _, y0, _, y1 = bbox(shapes)
    if y1 <= y0:
        return 0.0
    counts = []
    for i in range(samples):
        y = y0 + (i + 0.5) * (y1 - y0) / samples
        c = 0
        for (ax, ay), (bx, by) in edges:
            if (ay <= y < by) or (by <= y < ay):
                c += 1
        if c:
            counts.append(c)
    if not counts:
        return 0.0
    return (sum(counts) / len(counts)) / 2.0


def metrics(shapes, view_w, view_h):
    """Raw complexity numbers for one signature.

    These are absolute; build_corpus turns them into percentile ranks across
    the whole corpus before combining, because only the relative ordering is
    meaningful.
    """
    perim = sum(polyline_length(s["points"]) for s in shapes)
    diag = math.hypot(view_w, view_h) or 1.0
    turn = sum(turning(s["points"]) for s in shapes)
    corners = sum(sharp_corners(s["points"]) for s in shapes)
    long_side = max(view_w, view_h) or 1.0
    short_side = max(min(view_w, view_h), 1e-6)

    return {
        # Half the contour perimeter approximates actual pen travel, measured
        # against the diagonal so it is a density rather than a raw size.
        "ink_ratio": (perim / 2.0) / diag,
        "turning": turn / (2 * math.pi),
        "tangle": scanline_tangle(shapes),
        # Normalized by length so a big signature isn't penalized for having
        # more corners simply by virtue of being longer.
        "corners": corners / max(perim / 1000.0, 1e-6),
        "contours": float(len(shapes)),
        "aspect": long_side / short_side,
    }
