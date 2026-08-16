/* Painting signatures and player strokes onto a 2D context, plus the
 * rasterize-and-measure primitives the grader needs.
 *
 * All painting goes through here so the guide the player sees and the mask the
 * grader scores are produced by the same code. If those two ever drifted apart
 * the score would stop matching what is on screen.
 */
(function (SG) {
  'use strict';

  var geom = SG.geom;

  /* --------------------------------------------------------------------
   * painting
   * ------------------------------------------------------------------ */

  function tracePath(ctx, contours, t, close) {
    ctx.beginPath();
    for (var i = 0; i < contours.length; i++) {
      var pts = contours[i];
      if (!pts.length) continue;
      ctx.moveTo(t.ox + pts[0].x * t.scale, t.oy + pts[0].y * t.scale);
      for (var j = 1; j < pts.length; j++) {
        ctx.lineTo(t.ox + pts[j].x * t.scale, t.oy + pts[j].y * t.scale);
      }
      if (close) ctx.closePath();
    }
  }

  /* The target signature. "outline" files store the contour of the ink and get
   * filled; "centerline" files store the pen path itself and get stroked. */
  function paintSignature(ctx, level, t, style) {
    var cs = level._contours || (level._contours = geom.contours(level));
    if (level.kind === 'centerline') {
      tracePath(ctx, cs, t, false);
      ctx.strokeStyle = style;
      ctx.lineWidth = Math.max(1, level.pen * t.scale);
      ctx.lineCap = 'round';
      ctx.lineJoin = 'round';
      ctx.stroke();
    } else {
      tracePath(ctx, cs, t, true);
      ctx.fillStyle = style;
      ctx.fill(level.rule === 'evenodd' ? 'evenodd' : 'nonzero');
    }
  }

  /* Player strokes, inked at a given width in signature units. */
  function paintStrokes(ctx, strokes, width, t, style) {
    ctx.strokeStyle = style;
    ctx.lineWidth = Math.max(1, width * t.scale);
    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';
    for (var i = 0; i < strokes.length; i++) {
      var pts = strokes[i];
      if (!pts.length) continue;
      ctx.beginPath();
      if (pts.length === 1) {
        // A tap still leaves a dot; round caps need a zero-length segment.
        ctx.moveTo(t.ox + pts[0].x * t.scale, t.oy + pts[0].y * t.scale);
        ctx.lineTo(t.ox + pts[0].x * t.scale, t.oy + pts[0].y * t.scale);
      } else {
        ctx.moveTo(t.ox + pts[0].x * t.scale, t.oy + pts[0].y * t.scale);
        for (var j = 1; j < pts.length; j++) {
          ctx.lineTo(t.ox + pts[j].x * t.scale, t.oy + pts[j].y * t.scale);
        }
      }
      ctx.stroke();
    }
  }

  /* Recolour the player's own line by how far off it was, so the result
   * screen shows *where* they drifted rather than only quoting a number.
   * Segments are drawn one at a time, taking the worse of their two ends. */
  function errorColor(e) {
    // green -> amber -> oxblood
    var stops = [[44, 92, 44], [176, 122, 20], [140, 47, 34]];
    var i = e < 0.5 ? 0 : 1;
    var f = e < 0.5 ? e * 2 : (e - 0.5) * 2;
    var a = stops[i], b = stops[i + 1];
    return 'rgb(' + Math.round(a[0] + (b[0] - a[0]) * f) + ','
                  + Math.round(a[1] + (b[1] - a[1]) * f) + ','
                  + Math.round(a[2] + (b[2] - a[2]) * f) + ')';
  }

  function paintErrors(ctx, strokes, errors, width, t) {
    ctx.lineWidth = Math.max(1, width * t.scale);
    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';
    for (var i = 0; i < strokes.length; i++) {
      var pts = strokes[i], errs = (errors && errors[i]) || [];
      if (pts.length === 1) {
        ctx.strokeStyle = errorColor(errs[0] || 0);
        ctx.beginPath();
        ctx.moveTo(t.ox + pts[0].x * t.scale, t.oy + pts[0].y * t.scale);
        ctx.lineTo(t.ox + pts[0].x * t.scale, t.oy + pts[0].y * t.scale);
        ctx.stroke();
        continue;
      }
      for (var j = 1; j < pts.length; j++) {
        ctx.strokeStyle = errorColor(Math.max(errs[j - 1] || 0, errs[j] || 0));
        ctx.beginPath();
        ctx.moveTo(t.ox + pts[j - 1].x * t.scale, t.oy + pts[j - 1].y * t.scale);
        ctx.lineTo(t.ox + pts[j].x * t.scale, t.oy + pts[j].y * t.scale);
        ctx.stroke();
      }
    }
  }

  /* --------------------------------------------------------------------
   * rasterizing to masks
   * ------------------------------------------------------------------ */

  function scratch(w, h) {
    var c = document.createElement('canvas');
    c.width = w;
    c.height = h;
    return c;
  }

  /* Binary ink mask. Threshold at half alpha so antialiased edges do not
   * inflate the ink and skew both scores. */
  function maskOf(canvas) {
    var w = canvas.width, h = canvas.height;
    var data = canvas.getContext('2d').getImageData(0, 0, w, h).data;
    var mask = new Uint8Array(w * h);
    var n = 0;
    for (var i = 0, p = 3; i < mask.length; i++, p += 4) {
      if (data[p] >= 128) { mask[i] = 1; n++; }
    }
    mask.count = n;
    return mask;
  }

  /* Two-pass chamfer distance transform, in pixels.
   *
   * Exact Euclidean distance would cost more and buy nothing here: the scores
   * feed a falloff many pixels wide, where chamfer's ~2% error is invisible. */
  var INF = 1e9;
  function distanceField(mask, w, h) {
    var d = new Float32Array(w * h);
    var i;
    for (i = 0; i < d.length; i++) d[i] = mask[i] ? 0 : INF;

    var D1 = 1, D2 = 1.41421356;
    var x, y, k, v;
    for (y = 0; y < h; y++) {
      for (x = 0; x < w; x++) {
        k = y * w + x;
        v = d[k];
        if (v === 0) continue;
        if (x > 0 && d[k - 1] + D1 < v) v = d[k - 1] + D1;
        if (y > 0) {
          if (d[k - w] + D1 < v) v = d[k - w] + D1;
          if (x > 0 && d[k - w - 1] + D2 < v) v = d[k - w - 1] + D2;
          if (x < w - 1 && d[k - w + 1] + D2 < v) v = d[k - w + 1] + D2;
        }
        d[k] = v;
      }
    }
    for (y = h - 1; y >= 0; y--) {
      for (x = w - 1; x >= 0; x--) {
        k = y * w + x;
        v = d[k];
        if (v === 0) continue;
        if (x < w - 1 && d[k + 1] + D1 < v) v = d[k + 1] + D1;
        if (y < h - 1) {
          if (d[k + w] + D1 < v) v = d[k + w] + D1;
          if (x < w - 1 && d[k + w + 1] + D2 < v) v = d[k + w + 1] + D2;
          if (x > 0 && d[k + w - 1] + D2 < v) v = d[k + w - 1] + D2;
        }
        d[k] = v;
      }
    }
    return d;
  }

  SG.ink = {
    tracePath: tracePath,
    paintSignature: paintSignature,
    paintStrokes: paintStrokes,
    paintErrors: paintErrors,
    errorColor: errorColor,
    scratch: scratch,
    maskOf: maskOf,
    distanceField: distanceField
  };
})(window.SG || (window.SG = {}));
