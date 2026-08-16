"""Build the signature corpus for the game.

    python tools/build_corpus.py

Sources deceased public figures with SVG signatures from Wikidata, downloads
the artwork from Wikimedia Commons, flattens it to plain polylines, scores each
signature's complexity, and writes:

    docs/data/signatures.js   the corpus the game loads (one global assignment)
    tools/preview.html        a QA contact sheet for eyeballing the results

Standard library only, and everything network-touching is cached under
tools/.cache, so re-runs are fast and offline.
"""

import argparse
import datetime
import json
import math
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import geometry
import raster
import sources
import svgpath

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# The shipped game lives in docs/ so GitHub Pages can serve it straight from the
# repo without a workflow -- root and docs/ are the only two folders it offers.
APP = os.path.join(ROOT, "docs")

BOX = 1000.0          # normalized long side
RDP_EPS = 1.1         # simplification tolerance, in BOX units
PER_THEME = 16        # levels per track
TRY_PER_THEME = 60    # candidates to attempt, to survive conversion failures
RASTER_PX = 360       # resolution used to measure rendered ink
HOLLOW_PX = 900       # higher res, so hairlines stay connected when flooding
# Deliberately loose. Loopy-but-solid signatures reach 1.5-2.1 (Armstrong,
# Gauss, Leonov), so only a clear outlier means outline art. Anything hollow
# that slips past this goes in sources.BLACKLIST after a look at the sheets.
MAX_ENCLOSED = 2.5
# Largest inscribed disc allowed, as a fraction of the long side. A radius of
# 5% means a blob a tenth of the signature across, which no pen makes.
MAX_BLOB = 0.05


# --------------------------------------------------------------------------
# per-signature conversion
# --------------------------------------------------------------------------

class Rejected(Exception):
    pass


def convert(svg_text):
    """SVG text -> normalized, simplified shapes + metrics.

    Raises Rejected when the file is not usable as a signature.
    """
    shapes = svgpath.parse_svg(svg_text)
    shapes = [s for s in shapes if len(s["points"]) >= 2]
    if not shapes:
        raise Rejected("no geometry")

    shapes = geometry.prune(shapes, min_frac=0.003)
    if not shapes:
        raise Rejected("nothing left after pruning")

    shapes, w, h = geometry.normalize(shapes, BOX)
    if w <= 0 or h <= 0:
        raise Rejected("degenerate bounds")

    aspect = max(w, h) / max(min(w, h), 1e-6)
    if aspect > 30:
        raise Rejected("implausible aspect %.1f" % aspect)

    for s in shapes:
        s["points"] = geometry.dedupe(geometry.rdp(s["points"], RDP_EPS))
    shapes = [s for s in shapes if len(s["points"]) >= 3]
    if not shapes:
        raise Rejected("nothing left after simplifying")

    n_points = sum(len(s["points"]) for s in shapes)
    if n_points < 24:
        raise Rejected("too simple (%d points)" % n_points)
    if n_points > 6000:
        raise Rejected("too noisy (%d points)" % n_points)

    # Centerline files draw the pen path itself and must be stroked; outline
    # files trace the edge of the ink and must be filled. Getting this backwards
    # turns a signature into a smear of filled blobs, so it decides both the
    # rendering and which of the checks below even apply.
    ink_by_kind = {}
    for s in shapes:
        L = geometry.polyline_length(s["points"])
        ink_by_kind[s["kind"]] = ink_by_kind.get(s["kind"], 0.0) + L
    kind = max(ink_by_kind, key=ink_by_kind.get)

    m = geometry.metrics(shapes, w, h)
    if m["ink_ratio"] < 0.8:
        raise Rejected("too little ink")

    rule = shapes[0].get("rule", "nonzero")

    # Measure the ink as it will actually be drawn. Inferring this from contour
    # winding does not work: signed areas cancel across unnested contours that
    # nonetheless fill solid, and a hairline trace is indistinguishable from a
    # hollow ring on paper. Rendering it and counting pixels settles both.
    cov, RW, RH, rscale = raster.rasterize(
        raster.shapes_to_contours(shapes), w, h, kind, rule, long_side=RASTER_PX)
    ink_px = sum(cov)
    if not ink_px:
        raise Rejected("renders blank")

    ink_area = ink_px / (rscale * rscale)          # back to normalized units
    solidity = ink_area / (w * h)
    perim = sum(geometry.polyline_length(s["points"]) for s in shapes)
    # Mean pen width, from area over path length. A filled outline runs down
    # both sides of the ribbon, so its contour length is twice the pen's
    # travel; a centerline path is the travel itself.
    if perim <= 0:
        stroke_w = 0.0
    elif kind == "outline":
        stroke_w = 2.0 * ink_area / perim
    else:
        stroke_w = ink_area / perim

    # A fine hand is harder to trace accurately than a broad one: the target is
    # narrower, so the same wobble of the finger costs far more. Measured
    # against the signature's own size, not in absolute units - a broad nib on
    # a large signature is what "thick" actually means to the hand tracing it.
    diag = math.hypot(w, h)
    m["fineness"] = diag / max(stroke_w, 0.5)

    # Mean curvature: how tightly the line turns per unit travelled. Big
    # sweeping curves are easy to follow and score low here; a hand made of
    # small fiddly turns scores high.
    travel = perim / 2.0 if kind == "outline" else perim
    turn_total = sum(geometry.turning(s["points"]) for s in shapes)
    m["curl"] = 1000.0 * turn_total / max(travel, 1e-6)

    # Crude traces collapse letterforms into filled blobs. The giveaway is the
    # largest disc that fits inside the ink: a pen of any width draws a ribbon,
    # so the disc is about half the nib, while a collapsed bowl swallows a disc
    # many times that. Measured absolutely rather than against the mean width,
    # because the ratio ranks honest hands (Amundsen's period ink blot, 14x)
    # above the bad traces, whereas the absolute size separates them cleanly.
    fattest = raster.fattest_point(cov, RW, RH) / rscale
    if fattest > MAX_BLOB * BOX:
        raise Rejected("blobby trace (fits a disc %.0f units across)" % (2 * fattest))

    enclosed = 0.0
    if kind == "outline":
        if solidity > 0.42:
            raise Rejected("too solid (%.0f%% of box)" % (100 * solidity))

        # Reject drawings of the *outline* of a signature, where every stroke
        # is bounded by two hairlines and the ink is the edge rather than the
        # stroke. Filling those yields wiry rings and would have the player
        # tracing the boundary of the ink.
        hcov, HW, HH, _ = raster.rasterize(
            raster.shapes_to_contours(shapes), w, h, kind, rule,
            long_side=HOLLOW_PX)
        enclosed = raster.enclosed_ratio(hcov, HW, HH)
        if enclosed > MAX_ENCLOSED:
            raise Rejected("outline art (encloses %.1fx its own ink)" % enclosed)

    return {
        "shapes": shapes,
        "w": w, "h": h,
        "metrics": m,
        "points": n_points,
        "kind": kind,
        "rule": rule,
        "solidity": solidity,
        "stroke_w": stroke_w,
        "enclosed": enclosed,
    }


def years(person):
    b, d = person.get("born"), person.get("died")
    def fmt(y):
        return "%d BC" % -y if y and y < 0 else str(y)
    if b and d:
        return "%s–%s" % (fmt(b), fmt(d))
    if d:
        return "d. %s" % fmt(d)
    return ""


# --------------------------------------------------------------------------
# difficulty
# --------------------------------------------------------------------------

# Ranked as percentiles across the whole corpus, then combined. Absolute values
# are meaningless on their own -- only how a signature compares to the others.
#
# Fineness carries real weight because difficulty here is not only about the
# shape: van Gogh's broad, blunt hand is forgiving no matter how it loops, while
# Curie's hairline punishes a millimetre. Judging on shape alone put fat, showy
# signatures above fine, plain ones that are markedly harder to trace.
WEIGHTS = {
    "fineness":  0.32,   # how narrow the pen is, relative to the signature
    "ink_ratio": 0.20,   # pen travel packed into the space
    "fragments": 0.16,   # small disconnected marks to re-site the hand for
    "curl":      0.10,   # how tightly the line turns as it travels
    "contours":  0.10,   # pen lifts and counters
    "turning":   0.06,   # curliness
    "corners":   0.06,   # abrupt direction changes
}


def percentile_ranks(values):
    """Map each value to its rank in [0,1], averaging ties."""
    n = len(values)
    if n < 2:
        return [0.5] * n
    order = sorted(range(n), key=lambda i: values[i])
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg = (i + j) / 2.0 / (n - 1)
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def score_difficulty(entries):
    """Attach a 0-100 difficulty to every entry, in place."""
    ranked = {}
    for key in WEIGHTS:
        ranked[key] = percentile_ranks([e["metrics"][key] for e in entries])
    used = set()
    for i, e in enumerate(entries):
        d = 100.0 * sum(WEIGHTS[k] * ranked[k][i] for k in WEIGHTS)
        nudge = sources.DIFFICULTY_NUDGE.get(e["person"]["name"], 0)
        if nudge:
            used.add(e["person"]["name"])
        e["difficulty"] = int(round(max(0.0, min(100.0, d + nudge))))
    missing = sorted(set(sources.DIFFICULTY_NUDGE) - used)
    if missing:
        print("  note: difficulty nudges matched nobody: %s" % ", ".join(missing))


def pass_mark(difficulty):
    """Accuracy needed to clear a level.

    Calibrated against the scorer rather than guessed, by running every level in
    dev/grade-test.html against a realistic traced attempt and against the ways
    people try to cheat a tracing game. Those two populations separate cleanly:
    a decent attempt scores no lower than 70, and the best scribble anywhere
    reaches 65. The band below sits between them, so no scribble clears any
    level and no honest attempt fails one.

    Still eased against difficulty, so a dense, tangled hand is not a wall - but
    over a much narrower range than the spread of the scores themselves, which
    turns out not to track difficulty closely.
    """
    return int(round(max(66.0, min(76.0, 76.0 - 0.12 * difficulty))))


# --------------------------------------------------------------------------
# build
# --------------------------------------------------------------------------

def gather(args):
    print("Querying Wikidata...")
    people = sources.fetch_candidates()
    print("  %d deceased public figures with an SVG signature" % len(people))

    sources.occupation_index()

    by_theme = {t["id"]: [] for t in sources.THEMES}
    for p in people.values():
        if p["name"] in sources.EXCLUDE_NAMES:
            continue
        theme = sources.assign_theme(p["occupations"], p["name"])
        if theme:
            by_theme[theme].append(p)

    # Fame first, but the hand-picked signatures jump the queue: sitelinks
    # measure how well known the person is, not how well known their signature
    # is, and for a few the two come apart badly.
    for rows in by_theme.values():
        rows.sort(key=lambda r: (r["name"] not in sources.PRIORITY, -r["sitelinks"]))

    missing = sources.unmatched_overrides()
    if missing:
        print("  note: theme overrides matched nobody: %s" % ", ".join(missing))
    absent = sorted(sources.PRIORITY - {p["name"] for p in people.values()})
    if absent:
        print("  note: wanted but not in the pool (no SVG signature on Wikidata): %s"
              % ", ".join(absent))

    print("\nConverting artwork...")
    kept = {}
    for theme in sources.THEMES:
        tid = theme["id"]
        kept[tid] = []
        rejects = []
        for person in by_theme[tid][:args.tries]:
            if len(kept[tid]) >= args.per_theme:
                break
            if person["file"] in sources.BLACKLIST:
                rejects.append("%s (blacklisted)" % person["name"])
                continue
            try:
                entry = convert(sources.download_svg(person["file"]))
            except Rejected as e:
                rejects.append("%s (%s)" % (person["name"], e))
                continue
            except Exception as e:
                rejects.append("%s (%s: %s)" % (person["name"], type(e).__name__, e))
                if args.debug:
                    traceback.print_exc()
                continue
            entry["person"] = person
            kept[tid].append(entry)
        print("  %-28s kept %2d / tried %2d" % (theme["name"], len(kept[tid]),
                                                min(args.tries, len(by_theme[tid]))))
        if rejects and args.verbose:
            for r in rejects:
                print("      skipped: %s" % r)
    return kept


def level_up(kept, args):
    """Trim every track to the same, even number of levels.

    Tracks draw from pools of very different sizes - there are far more
    politicians with a signature on file than mountaineers - so without this the
    counts come out ragged, and a track that happened to convert badly would be
    visibly short. Entries are still in fame order here, so trimming from the
    end drops the least famous.
    """
    counts = {tid: len(rows) for tid, rows in kept.items()}
    n = min(counts.values())
    n -= n % 2                      # an even number reads better on the cards
    if n < args.per_theme:
        short = [t for t, c in counts.items() if c < args.per_theme]
        print("  levelled to %d (wanted %d; short: %s)"
              % (n, args.per_theme, ", ".join(sorted(short))))
    for tid in kept:
        del kept[tid][n:]
    return kept


FRAGMENT_PX = 1000    # resolution for counting genuinely separate marks


def add_fragment_metric(entries):
    """Count the small, separate marks in each signature.

    A name written in one connected flow is far easier than the same name broken
    into a dozen little marks, each needing the hand lifted, re-sited and
    re-started. Only pieces under a fiftieth of the ink count: the handful of
    large pieces in any signature are just its words.

    Measured at high resolution, and only on the levels that made the cut,
    because it is slow. At the resolution used elsewhere a hairline breaks into
    hundreds of "pieces" that are raster gaps rather than pen lifts - Voltaire
    read as 211 at 340px and 18 at 1000px - which would have measured thinness
    a second time under another name.
    """
    for e in entries:
        cov, W, H, _ = raster.rasterize(
            raster.shapes_to_contours(e["shapes"]), e["w"], e["h"],
            e["kind"], e["rule"], long_side=FRAGMENT_PX)
        total, small = raster.components(cov, W, H, min_frac=0.02)
        e["metrics"]["fragments"] = float(small)
        e["pieces"] = total


def build(args):
    kept = level_up(gather(args), args)

    flat = [e for rows in kept.values() for e in rows]
    print("\nCounting separate marks...")
    add_fragment_metric(flat)
    if not flat:
        print("No signatures survived conversion.")
        return 1
    score_difficulty(flat)

    print("\nFetching licence metadata...")
    meta = sources.commons_metadata({e["person"]["file"] for e in flat})

    themes_out = []
    for theme in sources.THEMES:
        rows = sorted(kept[theme["id"]], key=lambda e: e["difficulty"])
        levels = []
        for e in rows:
            p = e["person"]
            credit = meta.get(p["file"], {})
            levels.append({
                "id": p["qid"],
                "name": p["name"],
                "years": years(p),
                # English Wikipedia article, so a player can read about whoever
                # they have just been tracing.
                "wiki": p.get("wiki", ""),
                "difficulty": e["difficulty"],
                "pass": pass_mark(e["difficulty"]),
                "w": round(e["w"], 1),
                "h": round(e["h"], 1),
                "rule": e["rule"],
                # "outline" contours get filled; "centerline" pen paths get
                # stroked. The renderer must branch on this.
                "kind": e["kind"],
                # Measured mean pen width, in the same units as the strokes.
                # The grader inks the player's line to match, so that precision
                # and coverage stay symmetric on both fat and fine signatures.
                "pen": round(max(5.0, min(34.0, e["stroke_w"])), 1),
                # Flat [x,y,x,y,...] per contour: half the punctuation of
                # nested pairs, and the renderer walks it in twos anyway.
                "strokes": [[int(round(v)) for xy in s["points"] for v in xy]
                            for s in e["shapes"]],
                "credit": {
                    "file": p["file"],
                    "author": credit.get("author", "unknown"),
                    "license": credit.get("license", "unknown"),
                    "url": credit.get("url", ""),
                },
            })
        themes_out.append({
            "id": theme["id"],
            "name": theme["name"],
            "blurb": theme["blurb"],
            "levels": levels,
        })

    corpus = {
        "version": 1,
        "generated": datetime.date.today().isoformat(),
        "source": "Wikidata (P109) + Wikimedia Commons",
        "themes": themes_out,
    }

    nowiki = [lv["name"] for t in themes_out for lv in t["levels"] if not lv["wiki"]]
    if nowiki:
        print("  note: no English Wikipedia article for: %s" % ", ".join(nowiki))

    write_corpus(corpus)
    write_preview(corpus, kept)
    return 0


def write_corpus(corpus):
    path = os.path.join(APP, "data", "signatures.js")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # ensure_ascii so the file is byte-identical regardless of how the browser
    # guesses the encoding -- it is loaded over file:// as often as over http.
    body = json.dumps(corpus, separators=(",", ":"), ensure_ascii=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("window.SIGNATURES=")
        f.write(body)
        f.write(";\n")
    n = sum(len(t["levels"]) for t in corpus["themes"])
    print("\nWrote docs/data/signatures.js  (%d levels, %.0f KB)"
          % (n, os.path.getsize(path) / 1024.0))


# --------------------------------------------------------------------------
# QA contact sheet
# --------------------------------------------------------------------------

PREVIEW_CSS = """
body{margin:0;padding:24px;background:#f4efe4;color:#2b2118;
     font:14px/1.5 -apple-system,Segoe UI,Roboto,sans-serif}
h1{font-size:20px;margin:0 0 4px}
h2{font-size:15px;margin:32px 0 10px;padding-bottom:6px;
   border-bottom:1px solid #cbbfa8;letter-spacing:.08em;text-transform:uppercase}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:14px}
.card{background:#fffdf7;border:1px solid #ddd2bb;border-radius:6px;padding:10px}
.card svg{width:100%;height:110px;display:block}
.n{font-weight:600}
.m{color:#7d6d57;font-size:11.5px;font-family:ui-monospace,Consolas,monospace}
.d{float:right;font-weight:700;color:#8c2f22}
.bar{height:3px;background:#e5dcc8;border-radius:2px;margin:6px 0 4px}
.bar>i{display:block;height:100%;background:#8c2f22;border-radius:2px}
"""


STROKE_FRAC = 0.011      # centerline pen width, as a fraction of the long side


def _svg_markup(level):
    w, h = level["w"], level["h"]
    centerline = level.get("kind") == "centerline"
    d = []
    for st in level["strokes"]:
        pts = ["%g %g" % (st[i], st[i + 1]) for i in range(0, len(st), 2)]
        d.append("M" + " ".join(pts) + ("" if centerline else "Z"))
    d = " ".join(d)
    if centerline:
        paint = ('fill="none" stroke="#2b2118" stroke-width="%g" '
                 'stroke-linecap="round" stroke-linejoin="round"'
                 % (STROKE_FRAC * max(w, h)))
    else:
        paint = 'fill="#2b2118" fill-rule="%s"' % level["rule"]
    return ('<svg viewBox="0 0 %g %g" preserveAspectRatio="xMidYMid meet">'
            '<path d="%s" %s/></svg>' % (w, h, d, paint))


def write_preview(corpus, kept):
    by_qid = {e["person"]["qid"]: e for rows in kept.values() for e in rows}
    out = ["<!doctype html><meta charset=utf-8><title>Signature corpus QA</title>",
           "<style>%s</style>" % PREVIEW_CSS,
           "<h1>Signature corpus &mdash; %d levels</h1>" % sum(
               len(t["levels"]) for t in corpus["themes"]),
           "<p class=m>Generated %s. Ordered by difficulty within each track."
           % corpus["generated"]]

    for theme in corpus["themes"]:
        out.append("<h2>%s &mdash; %d</h2><div class=grid>"
                   % (theme["name"], len(theme["levels"])))
        for lv in theme["levels"]:
            e = by_qid.get(lv["id"], {})
            m = e.get("metrics", {})
            out.append(
                '<div class=card>%s'
                '<div><span class=d>%d</span><span class=n>%s</span> '
                '<span class=m>%s</span></div>'
                '<div class=bar><i style="width:%d%%"></i></div>'
                '<div class=m>pass %d%% &middot; %s &middot; %d pts &middot; %d contours'
                ' &middot; ink %.1f &middot; tangle %.1f &middot; encl %.2f</div>'
                '<div class=m>%s</div></div>'
                % (_svg_markup(lv), lv["difficulty"], lv["name"], lv["years"],
                   lv["difficulty"], lv["pass"], lv.get("kind", "?"),
                   e.get("points", 0),
                   len(lv["strokes"]), m.get("ink_ratio", 0),
                   m.get("tangle", 0), e.get("enclosed", 0),
                   lv["credit"]["file"][:52]))
        out.append("</div>")

    path = os.path.join(ROOT, "tools", "preview.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(out))
    print("Wrote tools/preview.html")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--per-theme", type=int, default=PER_THEME)
    ap.add_argument("--tries", type=int, default=TRY_PER_THEME)
    ap.add_argument("-v", "--verbose", action="store_true",
                    help="list every rejected signature and why")
    ap.add_argument("--debug", action="store_true")
    return build(ap.parse_args())


if __name__ == "__main__":
    sys.exit(main())
