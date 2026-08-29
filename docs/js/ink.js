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

  /* The nib. A real pen lands and leaves the paper rather than switching on, so
   * a stroke is thin for the first and last moment of its travel.
   *
   * This is a *display* flourish and the grader must not see it: the score is
   * computed from a line of one flat width, which is what makes precision and
   * coverage symmetric. Hence the opt-in argument - pad.js passes it, grade.js
   * does not. The two only disagree over a nib-and-a-half at each end, and only
   * by the drawn line being slightly thinner than the scored one, so what is on
   * screen is never wider than what was actually credited. */
  var TAPER_NIB = 1.6;        // ramp length, in nib widths
  var TAPER_TIP = 0.34;       // width at the very tip, as a fraction of the nib
  var TAPER_MAX = 0.28;       // ...but never more than this much of the stroke
  var CORE_ALPHA = 0.3;       // a wetter, darker centre to the line
  var CORE_FRAC = 0.45;

  function widthAt(s, len, ramp, w) {
    if (ramp <= 0) return w;
    var d = s < len - s ? s : len - s;
    if (d >= ramp) return w;
    // sqrt so the nib swells quickly and then holds, the way a pen bites.
    return w * (TAPER_TIP + (1 - TAPER_TIP) * Math.sqrt(d / ramp));
  }

  /* One stroke, in view coordinates, with tapered ends.
   *
   * Only the two ramps are drawn segment by segment; everything between them
   * goes down as a single full-width path. Stroking every segment separately
   * would be a few hundred paths per stroke on every frame of live drawing. */
  function taperedStroke(ctx, v, w) {
    var n = v.length, seg = new Array(n - 1), len = 0, j, d;
    for (j = 1; j < n; j++) {
      d = Math.hypot(v[j].x - v[j - 1].x, v[j].y - v[j - 1].y);
      seg[j - 1] = d;
      len += d;
    }
    if (len < 1e-6) { dot(ctx, v[0], w); return; }

    var ramp = Math.min(TAPER_NIB * w, len * TAPER_MAX);
    var step = Math.max(1.5, w * 0.45);
    var body = null, s = 0;

    function flush() {
      if (!body || body.length < 2) { body = null; return; }
      ctx.lineWidth = w;
      ctx.beginPath();
      ctx.moveTo(body[0].x, body[0].y);
      for (var k = 1; k < body.length; k++) ctx.lineTo(body[k].x, body[k].y);
      ctx.stroke();
      body = null;
    }

    for (j = 1; j < n; j++) {
      var a = v[j - 1], b = v[j], L = seg[j - 1];
      if (s >= ramp && s + L <= len - ramp) {
        if (!body) body = [a];
        body.push(b);
      } else {
        flush();
        var parts = Math.max(1, Math.ceil(L / step));
        for (var k = 0; k < parts; k++) {
          var f0 = k / parts, f1 = (k + 1) / parts;
          ctx.lineWidth = widthAt(s + L * (f0 + f1) / 2, len, ramp, w);
          ctx.beginPath();
          ctx.moveTo(a.x + (b.x - a.x) * f0, a.y + (b.y - a.y) * f0);
          ctx.lineTo(a.x + (b.x - a.x) * f1, a.y + (b.y - a.y) * f1);
          ctx.stroke();
        }
      }
      s += L;
    }
    flush();
  }

  function dot(ctx, p, w) {
    // A tap still leaves a dot; round caps need a zero-length segment.
    ctx.lineWidth = w;
    ctx.beginPath();
    ctx.moveTo(p.x, p.y);
    ctx.lineTo(p.x, p.y);
    ctx.stroke();
  }

  /* Player strokes, inked at a given width in signature units.
   *
   * `opts` is the pen dressing: {taper: true, core: '#150e07'}. Absent, the line
   * is a flat band of one width - which is exactly what the grader wants. */
  function paintStrokes(ctx, strokes, width, t, style, opts) {
    var w = Math.max(1, width * t.scale);
    var taper = opts && opts.taper;
    ctx.strokeStyle = style;
    ctx.lineWidth = w;
    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';
    for (var i = 0; i < strokes.length; i++) {
      var pts = strokes[i];
      if (!pts.length) continue;
      var v = new Array(pts.length);
      for (var j = 0; j < pts.length; j++) {
        v[j] = { x: t.ox + pts[j].x * t.scale, y: t.oy + pts[j].y * t.scale };
      }
      if (v.length === 1) { dot(ctx, v[0], w); continue; }
      if (!taper) {
        ctx.lineWidth = w;
        ctx.beginPath();
        ctx.moveTo(v[0].x, v[0].y);
        for (j = 1; j < v.length; j++) ctx.lineTo(v[j].x, v[j].y);
        ctx.stroke();
        continue;
      }
      taperedStroke(ctx, v, w);
      // A darker, narrower core down the middle, so the line reads as ink
      // soaking into paper rather than as a flat band of colour.
      if (opts.core) {
        var alpha = ctx.globalAlpha;
        ctx.strokeStyle = opts.core;
        ctx.globalAlpha = alpha * CORE_ALPHA;
        taperedStroke(ctx, v, w * CORE_FRAC);
        ctx.globalAlpha = alpha;
        ctx.strokeStyle = style;
      }
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

  /* Centre of the inked pixels. Both sides of a comparison must be measured
   * this same way: the centroid of a target's stored contour points is a
   * different quantity, weighted by however densely that file happened to be
   * traced, and using one against the other pulls a correctly placed attempt
   * off the mark. */
  function maskCentroid(mask, w) {
    var sx = 0, sy = 0, n = 0;
    for (var i = 0; i < mask.length; i++) {
      if (!mask[i]) continue;
      sx += i % w;
      sy += (i / w) | 0;
      n++;
    }
    return n ? { x: sx / n, y: sy / n, count: n } : null;
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
    maskCentroid: maskCentroid,
    distanceField: distanceField
  };
})(window.SG || (window.SG = {}));
