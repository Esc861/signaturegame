/* Accuracy scoring, by comparing inked pixels rather than paths.
 *
 * Both the target and the player's attempt are rasterized to binary masks, and
 * each gets a distance field. Two numbers come out of that:
 *
 *   precision - how close the player's ink sits to the target's
 *   coverage  - how much of the target's ink the player actually reached
 *
 * The score is their harmonic mean, which is what makes it hard to cheat:
 * scribbling over the whole card wrecks precision, and carefully tracing one
 * flourish while skipping the rest wrecks coverage. Neither shortcut scores.
 *
 * Working in pixels rather than along paths also means stroke order, stroke
 * direction and the number of pen lifts are all irrelevant - which matters,
 * because the corpus stores traced contours, not the original pen path.
 */
(function (SG) {
  'use strict';

  var geom = SG.geom, ink = SG.ink;

  var RES = 384;          // long side of the grading raster
  var TOL_FRAC = 0.045;   // falloff distance, as a fraction of the diagonal
  var MAX_SPAN = 2.6;     // clamp on how far outside the card we bother to score

  function bounds(level, strokes) {
    var pad = level.pen;
    var x0 = -pad, y0 = -pad, x1 = level.w + pad, y1 = level.h + pad;
    for (var i = 0; i < strokes.length; i++) {
      for (var j = 0; j < strokes[i].length; j++) {
        var p = strokes[i][j];
        if (p.x - pad < x0) x0 = p.x - pad;
        if (p.y - pad < y0) y0 = p.y - pad;
        if (p.x + pad > x1) x1 = p.x + pad;
        if (p.y + pad > y1) y1 = p.y + pad;
      }
    }
    // Ink far off the card still has to count against precision, but a stray
    // flick shouldn't blow the raster up to an unusable size.
    var limit = MAX_SPAN * Math.max(level.w, level.h);
    var cx = level.w / 2, cy = level.h / 2;
    x0 = Math.max(x0, cx - limit); x1 = Math.min(x1, cx + limit);
    y0 = Math.max(y0, cy - limit); y1 = Math.min(y1, cy + limit);
    return { x0: x0, y0: y0, x1: x1, y1: y1 };
  }

  function falloff(d, free, tol) {
    if (d <= free) return 1;
    if (d >= tol) return 0;
    return 1 - (d - free) / (tol - free);
  }

  function score(level, strokes) {
    var empty = {
      accuracy: 0, precision: 0, coverage: 0, errors: [], ok: false
    };
    if (!strokes || !strokes.length) return empty;

    var b = bounds(level, strokes);
    var spanW = b.x1 - b.x0, spanH = b.y1 - b.y0;
    if (spanW <= 0 || spanH <= 0) return empty;

    var scale = RES / Math.max(spanW, spanH);
    var W = Math.max(2, Math.round(spanW * scale));
    var H = Math.max(2, Math.round(spanH * scale));
    var t = { scale: scale, ox: -b.x0 * scale, oy: -b.y0 * scale };

    var tc = ink.scratch(W, H);
    ink.paintSignature(tc.getContext('2d'), level, t, '#000');
    var target = ink.maskOf(tc);

    var uc = ink.scratch(W, H);
    ink.paintStrokes(uc.getContext('2d'), strokes, level.pen, t, '#000');
    var user = ink.maskOf(uc);

    if (!target.count || !user.count) return empty;

    var dTarget = ink.distanceField(target, W, H);
    var dUser = ink.distanceField(user, W, H);

    // Forgive the player's own stroke thickness: ink whose centre is within
    // half a pen width of the target is overlapping it, not missing it.
    var free = 0.5 * level.pen * scale;
    var tol = free + TOL_FRAC * geom.diagonal(level) * scale;

    var i, sum = 0;
    for (i = 0; i < user.length; i++) {
      if (user[i]) sum += falloff(dTarget[i], free, tol);
    }
    var precision = sum / user.count;

    sum = 0;
    for (i = 0; i < target.length; i++) {
      if (target[i]) sum += falloff(dUser[i], free, tol);
    }
    var coverage = sum / target.count;

    var accuracy = (precision + coverage > 0)
      ? (2 * precision * coverage) / (precision + coverage)
      : 0;

    return {
      accuracy: Math.round(accuracy * 100),
      precision: Math.round(precision * 100),
      coverage: Math.round(coverage * 100),
      errors: pointErrors(strokes, dTarget, W, H, t, free, tol),
      ok: true
    };
  }

  /* Per-point error in 0..1, so the result screen can recolour the player's
   * own line green-to-red instead of just quoting a number at them. */
  function pointErrors(strokes, dTarget, W, H, t, free, tol) {
    var out = [];
    for (var i = 0; i < strokes.length; i++) {
      var pts = strokes[i], row = new Array(pts.length);
      for (var j = 0; j < pts.length; j++) {
        var px = Math.round(t.ox + pts[j].x * t.scale);
        var py = Math.round(t.oy + pts[j].y * t.scale);
        if (px < 0 || py < 0 || px >= W || py >= H) {
          row[j] = 1;
        } else {
          row[j] = 1 - falloff(dTarget[py * W + px], free, tol);
        }
      }
      out.push(row);
    }
    return out;
  }

  SG.grade = { score: score, RES: RES, TOL_FRAC: TOL_FRAC };
})(window.SG || (window.SG = {}));
