"""Minimal SVG -> polyline converter, standard library only.

Commons signature files are a grab bag: some are centerline traces with a
stroke, some are filled outlines, most carry nested <g transform> wrappers from
whatever editor produced them. This module flattens all of that down to plain
lists of points in a single coordinate space.
"""

import math
import re
import xml.etree.ElementTree as ET

SVG_NS = "http://www.w3.org/2000/svg"

# Elements we can turn into geometry. Anything else is walked through (for its
# children and transform) or skipped entirely.
_SKIP_TAGS = {"defs", "clipPath", "mask", "pattern", "marker", "symbol",
              "filter", "style", "metadata", "title", "desc"}

IDENTITY = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


# --------------------------------------------------------------------------
# matrices
# --------------------------------------------------------------------------

def mat_mul(m, n):
    """Compose so that `n` is applied to a point first, then `m`."""
    a1, b1, c1, d1, e1, f1 = m
    a2, b2, c2, d2, e2, f2 = n
    return (a1 * a2 + c1 * b2,
            b1 * a2 + d1 * b2,
            a1 * c2 + c1 * d2,
            b1 * c2 + d1 * d2,
            a1 * e2 + c1 * f2 + e1,
            b1 * e2 + d1 * f2 + f1)


def mat_apply(m, x, y):
    a, b, c, d, e, f = m
    return (a * x + c * y + e, b * x + d * y + f)


_TRANSFORM_RE = re.compile(r"(matrix|translate|scale|rotate|skewX|skewY)\s*\(([^)]*)\)")
_NUM_LIST_RE = re.compile(r"[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?")


def parse_transform(s):
    if not s:
        return IDENTITY
    m = IDENTITY
    for name, args in _TRANSFORM_RE.findall(s):
        v = [float(t) for t in _NUM_LIST_RE.findall(args)]
        if name == "matrix" and len(v) >= 6:
            t = tuple(v[:6])
        elif name == "translate":
            t = (1, 0, 0, 1, v[0] if v else 0, v[1] if len(v) > 1 else 0)
        elif name == "scale":
            sx = v[0] if v else 1
            sy = v[1] if len(v) > 1 else sx
            t = (sx, 0, 0, sy, 0, 0)
        elif name == "rotate" and v:
            a = math.radians(v[0])
            ca, sa = math.cos(a), math.sin(a)
            r = (ca, sa, -sa, ca, 0, 0)
            if len(v) >= 3:
                # rotate about (cx, cy)
                t = mat_mul(mat_mul((1, 0, 0, 1, v[1], v[2]), r),
                            (1, 0, 0, 1, -v[1], -v[2]))
            else:
                t = r
        elif name == "skewX" and v:
            t = (1, 0, math.tan(math.radians(v[0])), 1, 0, 0)
        elif name == "skewY" and v:
            t = (1, math.tan(math.radians(v[0])), 0, 1, 0, 0)
        else:
            continue
        m = mat_mul(m, t)
    return m


# --------------------------------------------------------------------------
# path data scanner
# --------------------------------------------------------------------------

class _Scanner:
    """Cursor over a path `d` string.

    Arc flags need their own reader: the spec allows them to run together with
    the following number, so `a5 5 0 0150 0` means flags 0,1 then x=50 — a
    generic number tokenizer would swallow `0150` whole.
    """

    _NUM = re.compile(r"[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?")

    def __init__(self, s):
        self.s = s
        self.i = 0

    def _skip(self):
        while self.i < len(self.s) and self.s[self.i] in " \t\r\n,":
            self.i += 1

    def eof(self):
        self._skip()
        return self.i >= len(self.s)

    def peek_command(self):
        self._skip()
        if self.i < len(self.s) and self.s[self.i].isalpha():
            return self.s[self.i]
        return None

    def command(self):
        c = self.peek_command()
        if c:
            self.i += 1
        return c

    def number(self):
        self._skip()
        m = self._NUM.match(self.s, self.i)
        if not m or m.group() in ("+", "-", "."):
            raise ValueError("expected number at %d in %r" % (self.i, self.s[:80]))
        self.i = m.end()
        return float(m.group())

    def flag(self):
        self._skip()
        if self.i < len(self.s) and self.s[self.i] in "01":
            v = self.s[self.i] == "1"
            self.i += 1
            return v
        # Some files in the wild write flags as full numbers.
        return self.number() != 0


# --------------------------------------------------------------------------
# curve flattening
# --------------------------------------------------------------------------

def _steps(points, quality):
    """Segment count from control-polygon length.

    We resample to uniform spacing later, so erring generous here costs a
    little memory during the build and nothing in the shipped data.
    """
    L = 0.0
    for i in range(1, len(points)):
        L += math.hypot(points[i][0] - points[i - 1][0], points[i][1] - points[i - 1][1])
    return max(2, min(120, int(L / quality) + 2))


def _cubic(out, p0, p1, p2, p3, quality):
    n = _steps([p0, p1, p2, p3], quality)
    for i in range(1, n + 1):
        t = i / n
        u = 1 - t
        x = (u * u * u * p0[0] + 3 * u * u * t * p1[0]
             + 3 * u * t * t * p2[0] + t * t * t * p3[0])
        y = (u * u * u * p0[1] + 3 * u * u * t * p1[1]
             + 3 * u * t * t * p2[1] + t * t * t * p3[1])
        out.append((x, y))


def _quad(out, p0, p1, p2, quality):
    n = _steps([p0, p1, p2], quality)
    for i in range(1, n + 1):
        t = i / n
        u = 1 - t
        x = u * u * p0[0] + 2 * u * t * p1[0] + t * t * p2[0]
        y = u * u * p0[1] + 2 * u * t * p1[1] + t * t * p2[1]
        out.append((x, y))


def _arc(out, x1, y1, rx, ry, phi_deg, large, sweep, x2, y2, quality):
    """Endpoint -> center parameterization, per SVG spec F.6.5."""
    if rx == 0 or ry == 0 or (x1 == x2 and y1 == y2):
        out.append((x2, y2))
        return
    rx, ry = abs(rx), abs(ry)
    phi = math.radians(phi_deg % 360.0)
    cosp, sinp = math.cos(phi), math.sin(phi)

    dx, dy = (x1 - x2) / 2.0, (y1 - y2) / 2.0
    x1p = cosp * dx + sinp * dy
    y1p = -sinp * dx + cosp * dy

    lam = (x1p * x1p) / (rx * rx) + (y1p * y1p) / (ry * ry)
    if lam > 1:
        s = math.sqrt(lam)
        rx *= s
        ry *= s

    den = rx * rx * y1p * y1p + ry * ry * x1p * x1p
    num = rx * rx * ry * ry - den
    co = math.sqrt(max(0.0, num / den)) if den else 0.0
    if large == sweep:
        co = -co
    cxp = co * rx * y1p / ry
    cyp = -co * ry * x1p / rx
    cx = cosp * cxp - sinp * cyp + (x1 + x2) / 2.0
    cy = sinp * cxp + cosp * cyp + (y1 + y2) / 2.0

    def angle(ux, uy, vx, vy):
        n = math.hypot(ux, uy) * math.hypot(vx, vy)
        if n == 0:
            return 0.0
        c = max(-1.0, min(1.0, (ux * vx + uy * vy) / n))
        a = math.acos(c)
        return -a if (ux * vy - uy * vx) < 0 else a

    ux, uy = (x1p - cxp) / rx, (y1p - cyp) / ry
    vx, vy = (-x1p - cxp) / rx, (-y1p - cyp) / ry
    th1 = angle(1, 0, ux, uy)
    dth = angle(ux, uy, vx, vy)
    if not sweep and dth > 0:
        dth -= 2 * math.pi
    elif sweep and dth < 0:
        dth += 2 * math.pi

    n = max(4, min(180, int(abs(dth) * max(rx, ry) / quality) + 4))
    for i in range(1, n + 1):
        th = th1 + dth * (i / n)
        ct, st = math.cos(th), math.sin(th)
        out.append((cosp * rx * ct - sinp * ry * st + cx,
                    sinp * rx * ct + cosp * ry * st + cy))


# --------------------------------------------------------------------------
# path data -> subpaths
# --------------------------------------------------------------------------

def path_to_subpaths(d, quality=1.0):
    """Return [(points, closed), ...] in the path's own user space."""
    sc = _Scanner(d)
    subpaths = []
    cur = []
    x = y = 0.0
    sx = sy = 0.0          # subpath start, for Z
    prev_cubic = None      # reflection control point for S
    prev_quad = None       # reflection control point for T
    cmd = None

    def close_current(closed):
        if len(cur) > 1:
            subpaths.append((list(cur), closed))

    while not sc.eof():
        c = sc.peek_command()
        if c is not None:
            cmd = sc.command()
        elif cmd is None:
            break
        else:
            # Repeated coordinate set: M/m implicitly continues as L/l.
            if cmd == "M":
                cmd = "L"
            elif cmd == "m":
                cmd = "l"

        rel = cmd.islower()
        C = cmd.upper()

        try:
            if C == "M":
                close_current(False)
                cur = []
                nx, ny = sc.number(), sc.number()
                x, y = (x + nx, y + ny) if rel else (nx, ny)
                sx, sy = x, y
                cur.append((x, y))
                prev_cubic = prev_quad = None

            elif C == "L":
                nx, ny = sc.number(), sc.number()
                x, y = (x + nx, y + ny) if rel else (nx, ny)
                cur.append((x, y))
                prev_cubic = prev_quad = None

            elif C == "H":
                nx = sc.number()
                x = x + nx if rel else nx
                cur.append((x, y))
                prev_cubic = prev_quad = None

            elif C == "V":
                ny = sc.number()
                y = y + ny if rel else ny
                cur.append((x, y))
                prev_cubic = prev_quad = None

            elif C in ("C", "S"):
                if C == "C":
                    a = sc.number(), sc.number()
                    b = sc.number(), sc.number()
                    if rel:
                        a = (x + a[0], y + a[1])
                        b = (x + b[0], y + b[1])
                else:
                    a = (2 * x - prev_cubic[0], 2 * y - prev_cubic[1]) if prev_cubic else (x, y)
                    b = sc.number(), sc.number()
                    if rel:
                        b = (x + b[0], y + b[1])
                e = sc.number(), sc.number()
                if rel:
                    e = (x + e[0], y + e[1])
                if not cur:
                    cur.append((x, y))
                _cubic(cur, (x, y), a, b, e, quality)
                prev_cubic = b
                prev_quad = None
                x, y = e

            elif C in ("Q", "T"):
                if C == "Q":
                    a = sc.number(), sc.number()
                    if rel:
                        a = (x + a[0], y + a[1])
                else:
                    a = (2 * x - prev_quad[0], 2 * y - prev_quad[1]) if prev_quad else (x, y)
                e = sc.number(), sc.number()
                if rel:
                    e = (x + e[0], y + e[1])
                if not cur:
                    cur.append((x, y))
                _quad(cur, (x, y), a, e, quality)
                prev_quad = a
                prev_cubic = None
                x, y = e

            elif C == "A":
                rx, ry = sc.number(), sc.number()
                rot = sc.number()
                large, sweep = sc.flag(), sc.flag()
                e = sc.number(), sc.number()
                if rel:
                    e = (x + e[0], y + e[1])
                if not cur:
                    cur.append((x, y))
                _arc(cur, x, y, rx, ry, rot, large, sweep, e[0], e[1], quality)
                prev_cubic = prev_quad = None
                x, y = e

            elif C == "Z":
                if cur:
                    cur.append((sx, sy))
                    close_current(True)
                    cur = []
                x, y = sx, sy
                prev_cubic = prev_quad = None

            else:
                break
        except ValueError:
            break

    close_current(False)
    return subpaths


# --------------------------------------------------------------------------
# document walk
# --------------------------------------------------------------------------

def _style_map(el):
    """Presentation attributes, with inline style= winning."""
    keys = ("fill", "stroke", "stroke-width", "fill-rule")
    out = {}
    for k in keys:
        v = el.get(k)
        if v is not None:
            out[k] = v.strip()
    style = el.get("style")
    if style:
        for decl in style.split(";"):
            if ":" in decl:
                k, v = decl.split(":", 1)
                k = k.strip()
                if k in keys:
                    out[k] = v.strip()
    return out


def _kind(style):
    """Is this shape a stroked centerline or a filled outline?

    SVG's default fill is black, so a shape with neither attribute set is
    filled. That default matters: plenty of Commons traces set nothing at all.
    """
    fill = style.get("fill", "").lower()
    stroke = style.get("stroke", "").lower()
    has_fill = fill not in ("none", "transparent")
    has_stroke = bool(stroke) and stroke not in ("none", "transparent")
    if has_stroke and not has_fill:
        return "centerline"
    return "outline"


def _local_tag(el):
    t = el.tag
    return t.split("}", 1)[1] if "}" in t else t


def _num(el, name, default=0.0):
    v = el.get(name)
    if v is None:
        return default
    m = _NUM_LIST_RE.search(v)
    return float(m.group()) if m else default


def _shape_subpaths(el, tag, quality):
    """Geometry for a single leaf element, in its own user space."""
    if tag == "path":
        d = el.get("d")
        return path_to_subpaths(d, quality) if d else []

    if tag in ("polygon", "polyline"):
        v = [float(t) for t in _NUM_LIST_RE.findall(el.get("points", ""))]
        pts = list(zip(v[0::2], v[1::2]))
        if len(pts) < 2:
            return []
        if tag == "polygon":
            pts.append(pts[0])
            return [(pts, True)]
        return [(pts, False)]

    if tag == "line":
        return [([(_num(el, "x1"), _num(el, "y1")),
                  (_num(el, "x2"), _num(el, "y2"))], False)]

    if tag == "rect":
        x, y = _num(el, "x"), _num(el, "y")
        w, h = _num(el, "width"), _num(el, "height")
        if w <= 0 or h <= 0:
            return []
        return [([(x, y), (x + w, y), (x + w, y + h), (x, y + h), (x, y)], True)]

    if tag in ("circle", "ellipse"):
        cx, cy = _num(el, "cx"), _num(el, "cy")
        if tag == "circle":
            rx = ry = _num(el, "r")
        else:
            rx, ry = _num(el, "rx"), _num(el, "ry")
        if rx <= 0 or ry <= 0:
            return []
        n = max(12, min(120, int(math.pi * (rx + ry) / quality)))
        pts = [(cx + rx * math.cos(2 * math.pi * i / n),
                cy + ry * math.sin(2 * math.pi * i / n)) for i in range(n + 1)]
        return [(pts, True)]

    return []


def parse_svg(svg_text, quality=1.0):
    """Flatten an SVG document to a list of shape dicts.

    Each shape is {points, closed, kind, rule}. Points are in the root user
    space with every ancestor transform applied. The root viewBox->viewport
    mapping is deliberately ignored: it is a uniform scale for every signature
    we care about, and the caller normalizes by bounding box afterwards anyway.
    """
    # Strip a DOCTYPE/entity preamble that ElementTree refuses to parse.
    svg_text = re.sub(r"<!DOCTYPE[^>[]*(\[[^\]]*\])?[^>]*>", "", svg_text, count=1)
    root = ET.fromstring(svg_text)

    shapes = []

    def walk(el, mat, inherited):
        tag = _local_tag(el)
        if tag in _SKIP_TAGS:
            return
        mat = mat_mul(mat, parse_transform(el.get("transform")))
        style = dict(inherited)
        style.update(_style_map(el))

        rule = style.get("fill-rule", "nonzero").lower()
        for pts, closed in _shape_subpaths(el, tag, quality):
            shapes.append({
                "points": [mat_apply(mat, px, py) for px, py in pts],
                "closed": closed,
                "kind": _kind(style),
                "rule": "evenodd" if rule == "evenodd" else "nonzero",
            })

        for child in el:
            walk(child, mat, style)

    walk(root, IDENTITY, {})
    return shapes
