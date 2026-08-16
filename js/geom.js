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
    strokeLength: strokeLength
  };
})(window.SG || (window.SG = {}));
