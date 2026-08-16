/* Accuracy scoring, by comparing inked pixels rather than paths.
 *
 * Both the target and the player's attempt are rasterized to binary masks, and
 * each gets a distance field. Three numbers come out of that:
 *
 *   precision - how close the player's ink sits to the target's
 *   coverage  - how much of the target's ink the player actually reached
 *   economy   - whether they got there without drawing far further than the
 *               signature is long
 *
 * Precision and coverage combine as a harmonic mean, then economy scales the
 * result. All three are needed. Precision and coverage alone were exploitable:
 * scribbling back and forth across the card covers nearly every target pixel,
 * because signatures are mostly horizontal ink, and keeps a respectable average
 * distance simply by staying inside the signature's own bounding box. That
 * scored a pass. Precision is a *mean* over the player's ink and is therefore
 * blind to how much ink there is - drawing ten times as much at the same
 * average closeness scores identically. Economy supplies that dimension.
 *
 * Economy measures pen *travel*, not inked area. Area was tried first and was
 * wrong: a wavering hand covers more area without drawing any further, so it
 * punished shaky tracing, which precision already accounts for. Distance
 * travelled is invariant to that wobble and is exactly what a scribble spends
 * without limit.
 *
 * Working in pixels rather than along paths also means stroke order, stroke
 * direction and the number of pen lifts are all irrelevant - which matters,
 * because the corpus stores traced contours, not the original pen path.
 */
(function (SG) {
  'use strict';

  var geom = SG.geom, ink = SG.ink;

  var RES = 384;          // long side of the grading raster
  var TOL_FRAC = 0.030;   // falloff distance, as a fraction of the diagonal
  var MAX_SPAN = 2.6;     // clamp on how far outside the card we bother to score

  // Precision falls off as a power curve rather than linearly, so drifting
  // costs progressively more: at half the tolerance a pixel keeps a quarter of
  // its credit, not half. Coverage stays linear - it asks whether the player
  // reached the ink at all, which is a yes-or-no sort of question.
  var SHARPNESS = 2;

  // How much further than the signature the player may draw before economy
  // starts to bite, and how sharply it bites after that. Because the penalty
  // grows with the *square* of the excess, the grace can sit well below the
  // cases it needs to tolerate: at twice the signature's length - going over
  // the whole thing again, or tracing an outline down both edges rather than
  // its middle - the cost is still only a few points, while the coarse
  // horizontal scribbles that used to squeak past are charged properly.
  var TRAVEL_GRACE = 1.6;
  var TRAVEL_BITE = 1.0;

  // Radius around the target ink inside which the player is not marked down at
  // all, as a fraction of the pen width. It exists to forgive the player's own
  // stroke thickness. Half a pen width is the geometrically "correct" value -
  // the strokes are then just touching - but it proved too generous on fat
  // hands like Rembrandt's, where it let a line drawn a whole pen width off the
  // mark still score perfectly.
  var FREE_FRAC = 0.3;

  // A stroke whose average point is this far off the line is not part of an
  // attempt at the signature.
  var STRAY_MEAN = 0.55;
  var STRAY_COST = 0.06;   // scored penalty per stray stroke
  var STRAY_FLOOR = 0.55;  // ...but stray strokes alone cannot zero a score

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

  /* How far a pen has to travel to draw this signature once.
   *
   * An "outline" level stores the contour around the ink, which runs down both
   * sides of every stroke, so the pen itself travels about half that. A
   * "centerline" level stores the pen path directly. Cached on the level. */
  function targetTravel(level) {
    if (level._travel != null) return level._travel;
    var cs = level._contours || (level._contours = geom.contours(level));
    var L = 0;
    for (var i = 0; i < cs.length; i++) {
      L += geom.strokeLength(cs[i]);
      if (level.kind !== 'centerline' && cs[i].length > 2) {
        var a = cs[i][0], b = cs[i][cs[i].length - 1];
        L += Math.hypot(a.x - b.x, a.y - b.y);      // the closing segment
      }
    }
    level._travel = level.kind === 'centerline' ? L : L / 2;
    return level._travel;
  }

  function falloff(d, free, tol) {
    if (d <= free) return 1;
    if (d >= tol) return 0;
    return 1 - (d - free) / (tol - free);
  }

  function score(level, strokes) {
    var empty = {
      accuracy: 0, precision: 0, coverage: 0, economy: 0, errors: [], ok: false
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

    var free = FREE_FRAC * level.pen * scale;
    var tol = free + TOL_FRAC * geom.diagonal(level) * scale;

    var i, sum = 0;
    for (i = 0; i < user.length; i++) {
      if (user[i]) sum += Math.pow(falloff(dTarget[i], free, tol), SHARPNESS);
    }
    var precision = sum / user.count;

    sum = 0;
    for (i = 0; i < target.length; i++) {
      if (target[i]) sum += falloff(dUser[i], free, tol);
    }
    var coverage = sum / target.count;

    // How far did the pen travel to get there? A scribble covers a signature
    // handsomely, but only by drawing several times its length.
    var travelled = 0;
    for (i = 0; i < strokes.length; i++) travelled += geom.strokeLength(strokes[i]);
    var ratio = travelled / (targetTravel(level) || 1);
    var over = Math.max(0, ratio / TRAVEL_GRACE - 1);
    var economy = 1 / (1 + TRAVEL_BITE * over * over);

    var errors = pointErrors(strokes, dTarget, W, H, t, free, tol);
    var stray = strayStrokes(errors);
    var strayFactor = Math.max(STRAY_FLOOR, 1 - STRAY_COST * stray);

    var base = (precision + coverage > 0)
      ? (2 * precision * coverage) / (precision + coverage)
      : 0;
    var accuracy = base * economy * strayFactor;

    return {
      accuracy: Math.round(accuracy * 100),
      precision: Math.round(precision * 100),
      coverage: Math.round(coverage * 100),
      economy: Math.round(economy * 100),
      travelRatio: Math.round(ratio * 100) / 100,
      stray: stray,
      errors: errors,
      ok: true
    };
  }

  /* Strokes that spend most of their length away from the signature. Extra ink
   * is already charged for by economy; this names the specific case of adding
   * whole strokes that were never an attempt at the line. */
  function strayStrokes(errors) {
    var n = 0;
    for (var i = 0; i < errors.length; i++) {
      var row = errors[i];
      if (row.length < 2) continue;
      var sum = 0;
      for (var j = 0; j < row.length; j++) sum += row[j];
      if (sum / row.length >= STRAY_MEAN) n++;
    }
    return n;
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

  SG.grade = {
    score: score,
    RES: RES,
    TOL_FRAC: TOL_FRAC,
    SHARPNESS: SHARPNESS,
    TRAVEL_GRACE: TRAVEL_GRACE,
    targetTravel: targetTravel
  };
})(window.SG || (window.SG = {}));
