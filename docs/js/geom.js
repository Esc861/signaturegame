/* Geometry helpers shared by the renderer and the grader.
 *
 * Two coordinate spaces are in play throughout the app:
 *   signature space - the units the corpus is stored in, long side 1000
 *   view space      - CSS pixels on whatever canvas we are drawing into
 * A "fit" carries the transform between them. Player strokes are always
 * recorded in signature space so a score does not depend on screen size.
 */
(function (SG) {
  'use strict';

  function fit(w, h, viewW, viewH, margin) {
    margin = margin || 0;
    var scale = Math.min((viewW - 2 * margin) / w, (viewH - 2 * margin) / h);
    if (!isFinite(scale) || scale <= 0) scale = 1;
    return {
      scale: scale,
      ox: (viewW - w * scale) / 2,
      oy: (viewH - h * scale) / 2
    };
  }

  function toView(t, x, y) {
    return { x: t.ox + x * t.scale, y: t.oy + y * t.scale };
  }

  function toSig(t, x, y) {
    return { x: (x - t.ox) / t.scale, y: (y - t.oy) / t.scale };
  }

  /* Corpus strokes ship as flat [x,y,x,y,...] to keep the payload small. */
  function contours(level) {
    var out = [];
    for (var i = 0; i < level.strokes.length; i++) {
      var flat = level.strokes[i];
      var pts = new Array(flat.length >> 1);
      for (var j = 0, k = 0; j < flat.length; j += 2, k++) {
        pts[k] = { x: flat[j], y: flat[j + 1] };
      }
      out.push(pts);
    }
    return out;
  }

  function diagonal(level) {
    return Math.sqrt(level.w * level.w + level.h * level.h);
  }

  /* Walk a polyline emitting a point every `step` units of arc length.
   *
   * Anything judging the *shape* of a stroke has to work at a fixed spatial
   * scale, or it measures how densely the points happen to have been recorded
   * instead - a slow finger reports far more points over the same curve. */
  function resample(pts, step) {
    if (!pts || pts.length < 2) return (pts || []).slice();
    var out = [pts[0]], carry = 0;
    for (var i = 1; i < pts.length; i++) {
      var a = pts[i - 1], b = pts[i];
      var dx = b.x - a.x, dy = b.y - a.y;
      var seg = Math.hypot(dx, dy);
      if (seg <= 0) continue;
      var t = 0;
      while (carry + (seg - t) >= step) {
        t += step - carry;
        out.push({ x: a.x + dx * (t / seg), y: a.y + dy * (t / seg) });
        carry = 0;
      }
      carry += seg - t;
    }
    return out;
  }

  /* Length-weighted centre of a set of polylines: where the ink sits. */
  function inkCentroid(strokes) {
    var sx = 0, sy = 0, w = 0;
    for (var i = 0; i < strokes.length; i++) {
      var pts = strokes[i];
      for (var j = 1; j < pts.length; j++) {
        var a = pts[j - 1], b = pts[j];
        var L = Math.hypot(b.x - a.x, b.y - a.y);
        if (!L) continue;
        sx += (a.x + b.x) / 2 * L;
        sy += (a.y + b.y) / 2 * L;
        w += L;
      }
      if (pts.length === 1) { sx += pts[0].x; sy += pts[0].y; w += 1; }
    }
    return w ? { x: sx / w, y: sy / w, weight: w } : null;
  }

  function shift(strokes, dx, dy) {
    if (!dx && !dy) return strokes;
    return strokes.map(function (pts) {
      return pts.map(function (p) { return { x: p.x + dx, y: p.y + dy }; });
    });
  }

  function strokeLength(pts) {
    var L = 0;
    for (var i = 1; i < pts.length; i++) {
      L += Math.hypot(pts[i].x - pts[i - 1].x, pts[i].y - pts[i - 1].y);
    }
    return L;
  }

  SG.geom = {
    fit: fit,
    toView: toView,
    toSig: toSig,
    contours: contours,
    diagonal: diagonal,
    strokeLength: strokeLength,
    resample: resample,
    inkCentroid: inkCentroid,
    shift: shift
  };
})(window.SG || (window.SG = {}));
