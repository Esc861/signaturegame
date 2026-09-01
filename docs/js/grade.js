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
 * traveled is invariant to that wobble and is exactly what a scribble spends
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
  // its credit, not half.
  var SHARPNESS = 2;

  // Coverage stays linear. Sharpening it was tried, to stop a scribble
  // claiming near-perfect coverage by crossing the ink often, and measured
  // worse: it cost honest traces several points and barely touched the
  // scribbles, which lean on precision and travel instead.
  var COVER_SHARPNESS = 1;

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
  // attempt at the signature. Measured, not guessed: across the corpus an
  // honestly traced stroke never averages worse than 0.25 even with a shaky
  // hand, while the strokes of a scribble sit at 0.4-0.7. The old threshold of
  // 0.55 sat inside the scribbles' own range and so caught almost none of them
  // - on a dense, fat hand like Rembrandt's, where a line drawn anywhere is
  // near some ink, it caught 1 stroke in 20.
  var STRAY_MEAN = 0.35;
  var STRAY_COST = 0.06;   // scored penalty per stray stroke
  var STRAY_FLOOR = 0.55;  // ...but stray strokes alone cannot zero a score

  // How far the whole attempt may be nudged to meet the target, as a fraction
  // of the diagonal. A document examiner does not care where on the page a
  // signature sits - they compare its form, and an exact positional match to a
  // known specimen is evidence of tracing, not of authenticity. A trace that is
  // the right shape but a few units low was being marked down for the one thing
  // nobody judges. Capped, so this forgives an offset rather than allowing a
  // signature drawn anywhere on the card.
  var MAX_SHIFT = 0.035;

  // Line quality: the examiner's first and best tell. A genuine signature is
  // written fast and automatically, so it runs smooth and continuous; forgeries
  // betray themselves with tremor, hesitation and patching. This measures how
  // much a stroke shortens when smoothed - a fluent curve barely moves, a shaky
  // one collapses - and it is scored hard, because it is the thing that
  // separates a practiced hand from a careful copy.
  // Sampled at a fine, fixed spatial scale rather than a multiple of the pen.
  // Tremor is a high-frequency wobble; genuine curvature is low-frequency. Step
  // coarsely and smoothing eats the curvature too, which read van Gogh's broad
  // sweeping hand as though it were shaking.
  var FLUENCY_STEP = 0.004; // resample interval, as a fraction of the diagonal
  // Measured across the corpus: a fluent traced line shortens by about 0.01-0.02
  // when smoothed, a visibly shaky one by 0.05 and up. The band is set from
  // those, so honest curvature costs nothing and tremor costs steeply.
  var FLUENCY_FREE = 0.018; // shortening below this is just honest curvature
  var FLUENCY_FULL = 0.075; // ...and at this much, the line is all tremor
  var FLUENCY_FLOOR = 0.45; // worst multiplier tremor alone can inflict

  function bounds(level, strokes, extra) {
    var pad = level.pen + (extra || 0);
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

  /* How much a stroke shortens when smoothed: 0 is a fluent line, higher is
   * tremor. Measured at a fixed spatial step so it reads the shape of the line
   * rather than how many points the device happened to report. */
  function shakiness(level, strokes) {
    var step = Math.max(2, FLUENCY_STEP * geom.diagonal(level));
    var raw = 0, smooth = 0;
    for (var i = 0; i < strokes.length; i++) {
      var pts = geom.resample(strokes[i], step);
      if (pts.length < 5) continue;      // too short to say anything about
      raw += geom.strokeLength(pts);
      smooth += geom.strokeLength(smoothPath(pts));
    }
    if (raw <= 0) return 0;
    return Math.max(0, 1 - smooth / raw);
  }

  /* One pass of a 1-2-1 kernel, ends pinned. */
  function smoothPath(pts) {
    var out = new Array(pts.length);
    out[0] = pts[0];
    out[pts.length - 1] = pts[pts.length - 1];
    for (var i = 1; i < pts.length - 1; i++) {
      out[i] = {
        x: (pts[i - 1].x + 2 * pts[i].x + pts[i + 1].x) / 4,
        y: (pts[i - 1].y + 2 * pts[i].y + pts[i + 1].y) / 4
      };
    }
    return out;
  }

  function falloff(d, free, tol) {
    if (d <= free) return 1;
    if (d >= tol) return 0;
    return 1 - (d - free) / (tol - free);
  }

  function score(level, drawn) {
    var empty = {
      accuracy: 0, precision: 0, coverage: 0, economy: 0, fluency: 0,
      errors: [], ok: false
    };
    if (!drawn || !drawn.length) return empty;

    var strokes = drawn;
    var cap = MAX_SHIFT * geom.diagonal(level);
    var b = bounds(level, strokes, cap);
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
    var uctx = uc.getContext('2d');
    ink.paintStrokes(uctx, strokes, level.pen, t, '#000');
    var user = ink.maskOf(uc);

    if (!target.count || !user.count) return empty;

    var dTarget = ink.distanceField(target, W, H);
    var free = FREE_FRAC * level.pen * scale;
    var tol = free + TOL_FRAC * geom.diagonal(level) * scale;

    function precisionOf(mask) {
      var s = 0;
      for (var k = 0; k < mask.length; k++) {
        if (mask[k]) s += Math.pow(falloff(dTarget[k], free, tol), SHARPNESS);
      }
      return s / mask.count;
    }

    // Judged on form, not placement. A document examiner compares how a
    // signature is built, not where on the page it landed, so the attempt may
    // be nudged onto the target, up to a capped distance.
    //
    // Both centers come from the rendered ink - the target's stored contour
    // points are a different quantity and dragged attempts off the mark. Even
    // then the nudge is only kept if it scores better: a traced centerline's
    // ink does not sit exactly where the filled target's does, so centring
    // blindly made a *correct* attempt worse, which is the opposite of
    // forgiving an offset. Alignment can only ever help.
    var precision = precisionOf(user);
    var dx = 0, dy = 0;
    var tCentre = ink.maskCentroid(target, W);
    var uCentre = ink.maskCentroid(user, W);
    if (tCentre && uCentre) {
      var ddx = tCentre.x - uCentre.x, ddy = tCentre.y - uCentre.y;
      var dist = Math.hypot(ddx, ddy), capPx = cap * scale;
      if (dist > capPx) { ddx *= capPx / dist; ddy *= capPx / dist; }
      if (Math.abs(ddx) >= 0.5 || Math.abs(ddy) >= 0.5) {
        var shifted = { scale: scale, ox: t.ox + ddx, oy: t.oy + ddy };
        uctx.clearRect(0, 0, W, H);
        ink.paintStrokes(uctx, strokes, level.pen, shifted, '#000');
        var shiftedMask = ink.maskOf(uc);
        if (shiftedMask.count) {
          var shiftedPrecision = precisionOf(shiftedMask);
          if (shiftedPrecision > precision) {
            precision = shiftedPrecision;
            user = shiftedMask;
            t = shifted;
            dx = ddx; dy = ddy;
          }
        }
      }
    }

    var dUser = ink.distanceField(user, W, H);

    var i, sum = 0;
    for (i = 0; i < target.length; i++) {
      if (target[i]) sum += Math.pow(falloff(dUser[i], free, tol), COVER_SHARPNESS);
    }
    var coverage = sum / target.count;

    // How far did the pen travel to get there? A scribble covers a signature
    // handsomely, but only by drawing several times its length.
    var traveled = 0;
    for (i = 0; i < strokes.length; i++) traveled += geom.strokeLength(strokes[i]);
    var ratio = traveled / (targetTravel(level) || 1);
    var over = Math.max(0, ratio / TRAVEL_GRACE - 1);
    var economy = 1 / (1 + TRAVEL_BITE * over * over);

    var errors = pointErrors(strokes, dTarget, W, H, t, free, tol);
    var stray = strayStrokes(errors);
    var strayFactor = Math.max(STRAY_FLOOR, 1 - STRAY_COST * stray);

    // Line quality. Scored on the strokes as drawn - shifting them cannot make
    // a shaky line steady, but measuring the un-nudged version keeps it honest.
    var shake = shakiness(level, drawn);
    var fluency = 1 - Math.min(1, Math.max(0, shake - FLUENCY_FREE)
                                  / (FLUENCY_FULL - FLUENCY_FREE));
    var fluencyFactor = FLUENCY_FLOOR + (1 - FLUENCY_FLOOR) * fluency;

    var base = (precision + coverage > 0)
      ? (2 * precision * coverage) / (precision + coverage)
      : 0;
    var accuracy = base * economy * strayFactor * fluencyFactor;

    return {
      accuracy: Math.round(accuracy * 100),
      precision: Math.round(precision * 100),
      coverage: Math.round(coverage * 100),
      economy: Math.round(economy * 100),
      fluency: Math.round(fluency * 100),
      shake: Math.round(shake * 1000) / 1000,
      shifted: Math.round(Math.hypot(dx, dy) / scale),
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

  /* Calibration hook. The constants above are only defensible against measured
   * behavior, so dev/grade-test.html needs to sweep them without editing this
   * file between runs. Not used by the game itself. */
  function tune(o) {
    if (o.tol != null) TOL_FRAC = o.tol;
    if (o.sharp != null) SHARPNESS = o.sharp;
    if (o.coverSharp != null) COVER_SHARPNESS = o.coverSharp;
    if (o.grace != null) TRAVEL_GRACE = o.grace;
    if (o.bite != null) TRAVEL_BITE = o.bite;
    if (o.free != null) FREE_FRAC = o.free;
    if (o.strayMean != null) STRAY_MEAN = o.strayMean;
    if (o.strayCost != null) STRAY_COST = o.strayCost;
    if (o.maxShift != null) MAX_SHIFT = o.maxShift;
    if (o.fluencyFree != null) FLUENCY_FREE = o.fluencyFree;
    if (o.fluencyFull != null) FLUENCY_FULL = o.fluencyFull;
    if (o.fluencyFloor != null) FLUENCY_FLOOR = o.fluencyFloor;
    return current();
  }

  function current() {
    return { tol: TOL_FRAC, sharp: SHARPNESS, coverSharp: COVER_SHARPNESS,
             grace: TRAVEL_GRACE, bite: TRAVEL_BITE, free: FREE_FRAC,
             strayMean: STRAY_MEAN, strayCost: STRAY_COST,
             maxShift: MAX_SHIFT, fluencyFree: FLUENCY_FREE,
             fluencyFull: FLUENCY_FULL, fluencyFloor: FLUENCY_FLOOR };
  }

  SG.grade = {
    score: score,
    RES: RES,
    targetTravel: targetTravel,
    tune: tune,
    current: current
  };
})(window.SG || (window.SG = {}));
