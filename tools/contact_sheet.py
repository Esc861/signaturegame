"""Render the built corpus to PNG contact sheets for visual QA.

    python tools/contact_sheet.py [theme_id]

Writes tools/sheet_<theme>.png, one per track, signatures in difficulty order.
Lets the corpus be checked by eye without a browser in the loop.
"""

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import raster

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CELL_W, CELL_H = 380, 150
COLS = 3
PAD = 10
MARGIN = 12
SS = 2                       # supersampling factor

BG = (244, 239, 228)
CARD = (255, 253, 247)
INK = (43, 33, 24)
RULE = (203, 191, 168)


def level_contours(level):
    """Flat [x,y,x,y,...] arrays back into point lists."""
    return [[(st[i], st[i + 1]) for i in range(0, len(st), 2)]
            for st in level["strokes"]]


def render_cell(level):
    """Anti-aliased alpha map sized CELL_W x CELL_H."""
    W, H = CELL_W * SS, CELL_H * SS
    cov = bytearray(W * H)
    w, h = level["w"], level["h"]
    if w > 0 and h > 0:
        m = MARGIN * SS
        scale = min((W - 2 * m) / w, (H - 2 * m) / h)
        ox, oy = (W - w * scale) / 2.0, (H - h * scale) / 2.0
        placed = [[(ox + x * scale, oy + y * scale) for x, y in c]
                  for c in level_contours(level)]
        if level.get("kind") == "centerline":
            raster.stroke_polylines(cov, W, H, placed,
                                    raster.STROKE_FRAC * max(w, h) * scale)
        else:
            raster.fill_polygons(cov, W, H, placed, level.get("rule", "nonzero"))
    return raster.downsample(cov, W, H, SS)


def blit(px, W, alpha, aw, ah, ox, oy, color):
    for y in range(ah):
        drow = ((oy + y) * W + ox) * 3
        srow = y * aw
        for x in range(aw):
            a = alpha[srow + x]
            if not a:
                continue
            i = drow + x * 3
            inv = 255 - a
            px[i] = (px[i] * inv + color[0] * a) // 255
            px[i + 1] = (px[i + 1] * inv + color[1] * a) // 255
            px[i + 2] = (px[i + 2] * inv + color[2] * a) // 255


def rect(px, W, x0, y0, w, h, color):
    for y in range(y0, y0 + h):
        i = (y * W + x0) * 3
        for _ in range(w):
            px[i] = color[0]; px[i + 1] = color[1]; px[i + 2] = color[2]
            i += 3


def build_sheet(levels, path):
    rows = (len(levels) + COLS - 1) // COLS
    W = COLS * CELL_W + (COLS + 1) * PAD
    H = rows * CELL_H + (rows + 1) * PAD
    px = bytearray(bytes(BG) * (W * H))

    for i, lv in enumerate(levels):
        cx = PAD + (i % COLS) * (CELL_W + PAD)
        cy = PAD + (i // COLS) * (CELL_H + PAD)
        rect(px, W, cx, cy, CELL_W, CELL_H, CARD)
        rect(px, W, cx, cy, CELL_W, 1, RULE)
        rect(px, W, cx, cy + CELL_H - 1, CELL_W, 1, RULE)
        alpha, aw, ah = render_cell(lv)
        blit(px, W, alpha, aw, ah, cx, cy, INK)

    raster.write_png(path, W, H, px)


def load_corpus():
    with open(os.path.join(ROOT, "data", "signatures.js"), "r", encoding="utf-8") as f:
        text = f.read().strip()
    return json.loads(re.sub(r"^window\.SIGNATURES=", "", text).rstrip(";"))


def main():
    corpus = load_corpus()
    only = sys.argv[1] if len(sys.argv) > 1 else None
    for theme in corpus["themes"]:
        if only and theme["id"] != only:
            continue
        out = os.path.join(ROOT, "tools", "sheet_%s.png" % theme["id"])
        build_sheet(theme["levels"], out)
        print("%s -> %s" % (theme["name"], os.path.basename(out)))
        for i, lv in enumerate(theme["levels"]):
            print("   %2d. %-32s diff %3d  pass %d%%  %s"
                  % (i + 1, lv["name"], lv["difficulty"], lv["pass"],
                     lv.get("kind", "")))


if __name__ == "__main__":
    main()
