"""Scanline rasterizer and PNG writer, standard library only.

Shared by the corpus builder (which measures the *rendered* ink to judge a
signature) and the contact-sheet tool (which draws it for a human). Reasoning
about contour winding proved unreliable for telling a hairline trace from a
solid one; rendering it and counting pixels is exact.
"""

import math
import struct
import zlib


# --------------------------------------------------------------------------
# filling
# --------------------------------------------------------------------------

def fill_polygons(cov, W, H, contours, rule="nonzero"):
    """Scanline-fill closed contours into a binary coverage buffer."""
    edges = []
    for pts in contours:
        n = len(pts)
        for i in range(n):
            x0, y0 = pts[i]
            x1, y1 = pts[(i + 1) % n]
            if y0 != y1:
                edges.append((y0, y1, x0, x1))
    if not edges:
        return

    ymin = max(0, int(min(min(e[0], e[1]) for e in edges)))
    ymax = min(H - 1, int(max(max(e[0], e[1]) for e in edges)) + 1)

    for y in range(ymin, ymax + 1):
        yc = y + 0.5
        xs = []
        for y0, y1, x0, x1 in edges:
            if (y0 <= yc < y1) or (y1 <= yc < y0):
                t = (yc - y0) / (y1 - y0)
                xs.append((x0 + t * (x1 - x0), 1 if y1 > y0 else -1))
        if not xs:
            continue
        xs.sort()
        row = y * W
        if rule == "evenodd":
            for i in range(0, len(xs) - 1, 2):
                a = max(0, int(xs[i][0] + 0.5))
                b = min(W, int(xs[i + 1][0] + 0.5))
                for x in range(a, b):
                    cov[row + x] = 1
        else:
            wind = 0
            for i in range(len(xs) - 1):
                wind += xs[i][1]
                if wind != 0:
                    a = max(0, int(xs[i][0] + 0.5))
                    b = min(W, int(xs[i + 1][0] + 0.5))
                    for x in range(a, b):
                        cov[row + x] = 1


def stroke_polylines(cov, W, H, polylines, width):
    """Thick round-capped strokes, for centerline signatures.

    Each quad and cap is filled on its own rather than as one path: coverage is
    binary, so overlapping pieces union for free and we never have to reason
    about consistent winding between segments.
    """
    r = max(width, 0.6) / 2.0
    disc = [(math.cos(2 * math.pi * i / 12) * r, math.sin(2 * math.pi * i / 12) * r)
            for i in range(12)]
    for pts in polylines:
        for i in range(len(pts) - 1):
            (x0, y0), (x1, y1) = pts[i], pts[i + 1]
            dx, dy = x1 - x0, y1 - y0
            L = math.hypot(dx, dy)
            if L < 1e-9:
                continue
            nx, ny = -dy / L * r, dx / L * r
            fill_polygons(cov, W, H,
                          [[(x0 + nx, y0 + ny), (x1 + nx, y1 + ny),
                            (x1 - nx, y1 - ny), (x0 - nx, y0 - ny)]], "evenodd")
        for x, y in pts:
            fill_polygons(cov, W, H,
                          [[(x + ddx, y + ddy) for ddx, ddy in disc]], "evenodd")


STROKE_FRAC = 0.011      # centerline pen width, as a fraction of the long side


def rasterize(contours, w, h, kind="outline", rule="nonzero",
              long_side=400, margin=0):
    """Render contours to a binary coverage buffer.

    Returns (cov, W, H, scale) where scale converts source units to pixels.
    """
    if w <= 0 or h <= 0:
        return bytearray(), 0, 0, 1.0
    scale = (long_side - 2.0 * margin) / max(w, h)
    W = max(1, int(round(w * scale)) + 2 * margin)
    H = max(1, int(round(h * scale)) + 2 * margin)
    cov = bytearray(W * H)
    placed = [[(margin + x * scale, margin + y * scale) for x, y in c]
              for c in contours]
    if kind == "centerline":
        stroke_polylines(cov, W, H, placed, STROKE_FRAC * max(w, h) * scale)
    else:
        fill_polygons(cov, W, H, placed, rule)
    return cov, W, H, scale


def shapes_to_contours(shapes):
    return [s["points"] for s in shapes]


def _distance_inside(cov, W, H):
    """Chamfer distance from each ink pixel to the nearest background pixel.

    Two passes, 3-4 neighbourhood. The ~2% error against true Euclidean is
    irrelevant here - the result feeds a ratio, not a measurement.
    """
    INF = 1e9
    d = [0.0] * (W * H)
    for i in range(W * H):
        d[i] = INF if cov[i] else 0.0

    D1, D2 = 1.0, 1.41421356
    for y in range(H):
        base = y * W
        for x in range(W):
            k = base + x
            v = d[k]
            if v == 0.0:
                continue
            if x > 0 and d[k - 1] + D1 < v: v = d[k - 1] + D1
            if y > 0:
                if d[k - W] + D1 < v: v = d[k - W] + D1
                if x > 0 and d[k - W - 1] + D2 < v: v = d[k - W - 1] + D2
                if x < W - 1 and d[k - W + 1] + D2 < v: v = d[k - W + 1] + D2
            d[k] = v
    for y in range(H - 1, -1, -1):
        base = y * W
        for x in range(W - 1, -1, -1):
            k = base + x
            v = d[k]
            if v == 0.0:
                continue
            if x < W - 1 and d[k + 1] + D1 < v: v = d[k + 1] + D1
            if y < H - 1:
                if d[k + W] + D1 < v: v = d[k + W] + D1
                if x < W - 1 and d[k + W + 1] + D2 < v: v = d[k + W + 1] + D2
                if x > 0 and d[k + W - 1] + D2 < v: v = d[k + W - 1] + D2
            d[k] = v
    return d


def fattest_point(cov, W, H):
    """Radius of the largest disc that fits inside the ink, in pixels.

    Compared against the signature's average half-width, this is what separates
    a broad pen from a botched trace. A pen of any width draws a ribbon, so the
    biggest disc that fits inside it is about half the nib; when a trace
    collapses a letterform into a filled blob, the disc that fits is far larger.
    Neither stroke width nor contour roundness distinguishes those two cases -
    both rank honest broad hands like Rembrandt's above the bad traces.
    """
    d = _distance_inside(cov, W, H)
    return max(d) if d else 0.0


def components(cov, W, H, min_frac=0.02):
    """Separate pieces of ink, and how many of them are small.

    A name written in one connected flow is far easier to trace than the same
    name broken into a dozen disconnected marks - the hand can keep going
    instead of lifting, re-siting and re-starting for each piece. The count of
    *small* pieces matters on its own: those are the fiddly bits.

    Returns (total pieces, pieces smaller than min_frac of the ink).
    """
    seen = bytearray(W * H)
    total = small = 0
    sizes = []
    for start in range(W * H):
        if not cov[start] or seen[start]:
            continue
        stack = [start]
        seen[start] = 1
        size = 0
        while stack:
            i = stack.pop()
            size += 1
            x, y = i % W, i // W
            for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1),
                           (x - 1, y - 1), (x + 1, y - 1),
                           (x - 1, y + 1), (x + 1, y + 1)):
                if 0 <= nx < W and 0 <= ny < H:
                    j = ny * W + nx
                    if cov[j] and not seen[j]:
                        seen[j] = 1
                        stack.append(j)
        sizes.append(size)
        total += 1

    ink = sum(sizes) or 1
    for s in sizes:
        if s / ink < min_frac:
            small += 1
    return total, small


def enclosed_ratio(cov, W, H):
    """Area of enclosed background over area of ink.

    Used to catch files that draw the *outline* of a signature rather than the
    signature: there, every pen stroke is bounded by two hairlines, so each
    stroke's whole interior counts as enclosed background.

    Read it as a rough measure of how much empty space the ink encircles, not
    as a clean hollow/solid test. Genuinely loopy signatures score high too --
    Armstrong's underline loop reaches 1.5 and Gauss's flourishes 1.6, both
    perfectly solid -- so only egregious values mean outline art. Stroke width
    is no help at all here: Voltaire's real signature is *thinner* than the
    outline art we want to reject.
    """
    if not W or not H:
        return 0.0
    seen = bytearray(W * H)
    stack = []

    def push(i):
        if not cov[i] and not seen[i]:
            seen[i] = 1
            stack.append(i)

    for x in range(W):
        push(x)
        push((H - 1) * W + x)
    for y in range(H):
        push(y * W)
        push(y * W + W - 1)

    while stack:
        i = stack.pop()
        x, y = i % W, i // W
        if x > 0:
            push(i - 1)
        if x < W - 1:
            push(i + 1)
        if y > 0:
            push(i - W)
        if y < H - 1:
            push(i + W)

    ink = 0
    holes = 0
    for i in range(W * H):
        if cov[i]:
            ink += 1
        elif not seen[i]:
            holes += 1
    return holes / ink if ink else 0.0


# --------------------------------------------------------------------------
# output
# --------------------------------------------------------------------------

def downsample(cov, W, H, ss):
    """ss x ss box filter over a binary buffer -> alpha 0..255."""
    ow, oh = W // ss, H // ss
    out = bytearray(ow * oh)
    denom = ss * ss
    for y in range(oh):
        base = y * ss * W
        orow = y * ow
        for x in range(ow):
            s = 0
            bx = x * ss
            for dy in range(ss):
                r = base + dy * W + bx
                for dx in range(ss):
                    s += cov[r + dx]
            out[orow + x] = (s * 255) // denom
    return out, ow, oh


def write_png(path, w, h, pixels):
    """pixels: bytearray of w*h*3, RGB."""
    raw = bytearray()
    stride = w * 3
    for y in range(h):
        raw.append(0)                      # filter type: none
        raw += pixels[y * stride:(y + 1) * stride]

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")
        f.write(chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)))
        f.write(chunk(b"IDAT", zlib.compress(bytes(raw), 6)))
        f.write(chunk(b"IEND", b""))
