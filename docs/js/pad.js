/* The drawing surface: a canvas the player traces on with a finger.
 *
 * Strokes are recorded in signature space, never in screen pixels, so a score
 * means the same thing on a phone and on a desktop.
 *
 * The player's line is drawn at exactly the width the grader inks it at. That
 * is deliberate - a prettier velocity-tapered nib would mean the line being
 * scored is not the line on screen, and near-misses would look unfair.
 */
(function (SG) {
  'use strict';

  var geom = SG.geom, ink = SG.ink;

  var MARGIN_FRAC = 0.07;   // breathing room around the signature
  var MIN_STEP = 1.2;       // px between recorded points, to drop jitter

  // The loupe: a magnified window on whatever is under the fingertip, shown
  // while drawing. A finger covers the very thing it is trying to trace, which
  // is the whole difficulty of the game on a phone rather than a desk.
  //
  // It rides just above the fingertip rather than sitting in a corner. A corner
  // loupe has to switch sides to stay out from under the hand, and that jump is
  // far more distracting than the thing it was avoiding.
  var LOUPE = 0.34;         // diameter, as a fraction of the pad's short edge
  var LOUPE_MAX = 124;      // ...but never bigger than this, in CSS px
  var LOUPE_ZOOM = 2.6;
  var LOUPE_GAP = 20;       // clear air between fingertip and glass
  var LOUPE_PAD = 6;        // keep it this far inside the card

  function Pad(canvas) {
    this.canvas = canvas;
    this.ctx = canvas.getContext('2d');
    this.level = null;
    this.strokes = [];
    this.showGuide = true;
    this.locked = false;
    this.onChange = null;
    this._pointer = null;
    this._fit = { scale: 1, ox: 0, oy: 0 };
    this._css = { w: 0, h: 0 };
    this._frame = 0;

    this._bind();
    this.resize();
  }

  Pad.prototype._bind = function () {
    var self = this, c = this.canvas;

    c.addEventListener('pointerdown', function (e) {
      if (self.locked || self._pointer !== null) return;
      e.preventDefault();
      self._pointer = e.pointerId;
      try { c.setPointerCapture(e.pointerId); } catch (err) { /* not fatal */ }
      self.strokes.push([]);
      self._add(e);
      self.draw();          // so the loupe is up before the finger moves
    });

    c.addEventListener('pointermove', function (e) {
      if (self._pointer !== e.pointerId) return;
      e.preventDefault();
      // A finger generates points faster than frames arrive; without the
      // coalesced buffer a quick flourish records as a few long chords. The
      // list can come back empty (synthetic events, and some browsers once
      // the event has been consumed), so fall back to the event itself.
      var events = e.getCoalescedEvents ? e.getCoalescedEvents() : null;
      if (!events || !events.length) events = [e];
      for (var i = 0; i < events.length; i++) self._add(events[i]);
      self.draw();
    });

    function end(e) {
      if (self._pointer !== e.pointerId) return;
      self._pointer = null;
      self._tip = null;
      try { c.releasePointerCapture(e.pointerId); } catch (err) { /* ignore */ }
      var last = self.strokes[self.strokes.length - 1];
      if (last && !last.length) self.strokes.pop();
      self.draw();
      self._changed();
    }
    c.addEventListener('pointerup', end);
    c.addEventListener('pointercancel', end);
  };

  Pad.prototype._add = function (e) {
    var r = this.canvas.getBoundingClientRect();
    var vx = e.clientX - r.left, vy = e.clientY - r.top;
    // Kept in view space for the loupe, and updated even when the point itself
    // is dropped as jitter, so the magnifier tracks smoothly.
    this._tip = { x: vx, y: vy };
    var stroke = this.strokes[this.strokes.length - 1];
    if (!stroke) return;
    if (stroke.length) {
      var prev = geom.toView(this._fit, stroke[stroke.length - 1].x,
                             stroke[stroke.length - 1].y);
      if (Math.hypot(vx - prev.x, vy - prev.y) < MIN_STEP) return;
    }
    stroke.push(geom.toSig(this._fit, vx, vy));
  };

  Pad.prototype._changed = function () {
    if (this.onChange) this.onChange(this);
  };

  Pad.prototype.setLevel = function (level) {
    this.level = level;
    this.strokes = [];
    this.locked = false;
    this.resize();
    this._changed();
  };

  Pad.prototype.clear = function () {
    this.strokes = [];
    this.draw();
    this._changed();
  };

  Pad.prototype.undo = function () {
    this.strokes.pop();
    this.draw();
    this._changed();
  };

  Pad.prototype.isEmpty = function () {
    for (var i = 0; i < this.strokes.length; i++) {
      if (this.strokes[i].length) return false;
    }
    return true;
  };

  Pad.prototype.resize = function () {
    var c = this.canvas;
    var r = c.getBoundingClientRect();
    var w = Math.max(1, Math.round(r.width));
    var h = Math.max(1, Math.round(r.height));
    var dpr = Math.min(window.devicePixelRatio || 1, 3);

    c.width = Math.round(w * dpr);
    c.height = Math.round(h * dpr);
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    this._css = { w: w, h: h };

    if (this.level) {
      this._fit = geom.fit(this.level.w, this.level.h, w, h,
                           Math.min(w, h) * MARGIN_FRAC);
    }
    this.draw();
  };

  /* Coalesce redraws to one per frame: pointermove can fire many times
   * between paints, especially with coalesced events expanded. */
  Pad.prototype.draw = function () {
    var self = this;
    if (this._frame) return;
    this._frame = requestAnimationFrame(function () {
      self._frame = 0;
      self._paint();
    });
  };

  Pad.prototype._paint = function () {
    var ctx = this.ctx, level = this.level;
    ctx.clearRect(0, 0, this._css.w, this._css.h);
    if (!level) return;

    var style = getComputedStyle(this.canvas);
    var guide = style.getPropertyValue('--guide').trim() || '#d9cfba';
    var inkc = style.getPropertyValue('--ink').trim() || '#2b2118';
    var card = style.getPropertyValue('--card').trim() || '#fffdf7';
    var rule = style.getPropertyValue('--rule').trim() || '#cbbfa8';

    this._scene(ctx, guide, inkc);
    if (this._tip) this._loupe(ctx, guide, inkc, card, rule);
  };

  /* Everything that lives on the card, at a given transform. Factored out so
     the loupe magnifies exactly what the pad draws rather than a lookalike.
     `inkAlpha` fades the player's own line inside the loupe: the point of
     magnifying is to see the guide their finger is covering, and at full
     strength their stroke simply hides it again. */
  Pad.prototype._scene = function (ctx, guide, inkc, inkAlpha) {
    if (this.showGuide) {
      ink.paintSignature(ctx, this.level, this._fit, guide);
    }
    if (inkAlpha != null) ctx.globalAlpha = inkAlpha;
    ink.paintStrokes(ctx, this.strokes, this.level.pen, this._fit, inkc);
    ctx.globalAlpha = 1;
  };

  Pad.prototype._loupe = function (ctx, guide, inkc, card, rule) {
    var w = this._css.w, h = this._css.h;
    var d = Math.min(LOUPE * Math.min(w, h), LOUPE_MAX);
    var rad = d / 2;
    var tip = this._tip;

    // Ride above the fingertip, and only drop below it when there is no room
    // left overhead - near the top of the card, where the alternative is a
    // glass half off the edge.
    var cy = tip.y - LOUPE_GAP - rad;
    if (cy - rad < LOUPE_PAD) cy = tip.y + LOUPE_GAP + rad;
    cy = Math.max(rad + LOUPE_PAD, Math.min(h - rad - LOUPE_PAD, cy));
    var cx = Math.max(rad + LOUPE_PAD, Math.min(w - rad - LOUPE_PAD, tip.x));

    ctx.save();
    ctx.beginPath();
    ctx.arc(cx, cy, rad, 0, Math.PI * 2);
    ctx.fillStyle = card;
    ctx.fill();

    ctx.save();
    ctx.clip();
    // Put the fingertip at the middle of the glass, magnified.
    ctx.translate(cx, cy);
    ctx.scale(LOUPE_ZOOM, LOUPE_ZOOM);
    ctx.translate(-tip.x, -tip.y);
    this._scene(ctx, guide, inkc, 0.55);
    ctx.restore();

    // Crosshair marking the exact point being scored.
    ctx.strokeStyle = rule;
    ctx.lineWidth = 1;
    var arm = 7;
    ctx.beginPath();
    ctx.moveTo(cx - arm, cy); ctx.lineTo(cx - 2, cy);
    ctx.moveTo(cx + 2, cy); ctx.lineTo(cx + arm, cy);
    ctx.moveTo(cx, cy - arm); ctx.lineTo(cx, cy - 2);
    ctx.moveTo(cx, cy + 2); ctx.lineTo(cx, cy + arm);
    ctx.stroke();

    // Rim. Re-traced rather than reusing the clip path: the crosshair above
    // replaced the current path, and restore() does not bring paths back.
    ctx.beginPath();
    ctx.arc(cx, cy, rad - 0.5, 0, Math.PI * 2);
    ctx.strokeStyle = rule;
    ctx.lineWidth = 1;
    ctx.stroke();
    ctx.restore();
  };

  SG.Pad = Pad;
  SG.Pad.MARGIN_FRAC = MARGIN_FRAC;
})(window.SG || (window.SG = {}));
