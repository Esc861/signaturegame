/* The drawing surface: a canvas the player traces on with a finger.
 *
 * Strokes are recorded in signature space, never in screen pixels, so a score
 * means the same thing on a phone and on a desktop.
 *
 * The player's line is drawn at the width the grader inks it at, apart from a
 * short taper as the nib lands and leaves. A velocity-tapered nib along the
 * whole stroke is still off the table: the line being scored would stop being
 * the line on screen and near-misses would look unfair. The end taper is
 * bounded, and thinner than the scored line rather than wider, so the ink can
 * never claim credit the score did not give.
 */
(function (SG) {
  'use strict';

  var geom = SG.geom, ink = SG.ink;

  // Breathing room around the signature, as a fraction of the card's *width*.
  // Kept tight on purpose: every pixel the signature gains is finer effective
  // touch resolution, since a finger's error is fixed in screen terms while the
  // score is measured in signature units. Not zero, because strokes that start
  // hard against the edge are awkward to begin.
  //
  // Width, not the short edge, because it has to be the same number of pixels
  // for every level. app.js sizes the card so that each signature is limited by
  // its width and so lands at one identical scale; a margin measured off the
  // card's height would vary with the signature's shape and undo that.
  var MARGIN_FRAC = 0.012;
  var MIN_STEP = 1.2;       // px between recorded points, to drop jitter

  // The loupe: a magnified window on whatever is under the fingertip, shown
  // while drawing. A finger covers the very thing it is trying to trace, which
  // is the whole difficulty of the game on a phone rather than a desk.
  //
  // It rides just above the fingertip rather than sitting in a corner. A corner
  // loupe has to switch sides to stay out from under the hand, and that jump is
  // far more distracting than the thing it was avoiding.
  //
  // It lives in its own element floating over the page, not inside the pad's
  // canvas. Drawn into the canvas it could never leave the card, so anywhere
  // near the top of a short card there was no room above the finger and it had
  // to flip underneath - which is the same distracting jump by another route,
  // and it fired across most of the card's height. Over the page it has the
  // header's worth of space to move into and effectively never flips.
  var LOUPE = 0.34;         // diameter, as a fraction of the pad's short edge
  var LOUPE_MAX = 124;      // ...but never bigger than this, in CSS px
  var LOUPE_ZOOM = 2.6;
  var LOUPE_GAP = 22;       // clear air between fingertip and glass
  var LOUPE_EDGE = 8;       // keep it this far inside the viewport

  // It only earns its place during detail work. Appearing on every stroke makes
  // it scenery, and a long confident sweep does not need magnifying - so it
  // waits for evidence that the hand has settled into careful work: this much
  // time spent moving slowly, not merely this much time with a finger down.
  var LOUPE_DELAY = 850;    // ms of careful drawing before it appears
  // Two thresholds rather than one, so a hand hovering around a single cutoff
  // cannot flicker it on and off. Between them, whatever it was doing persists.
  var LOUPE_SLOW = 150;     // px/s and below counts as careful
  var LOUPE_FAST = 340;     // px/s and above cancels it
  var SPEED_SMOOTH = 0.25;  // EMA weight on the newest speed sample

  function Pad(canvas, loupe) {
    this.canvas = canvas;
    this.ctx = canvas.getContext('2d');
    this.loupe = loupe || null;
    this.level = null;
    this.strokes = [];
    this.showGuide = true;
    this.locked = false;
    this.onChange = null;
    this._pointer = null;
    this._fit = { scale: 1, ox: 0, oy: 0 };
    this._css = { w: 0, h: 0 };
    this._frame = 0;
    this._tip = null;
    this._speed = 0;        // px/s, smoothed
    this._careful = 0;      // ms spent drawing slowly in this stroke
    this._showLoupe = false;
    this._mark = null;      // last position/time used for a speed sample
    this._ticker = 0;
    this._pen = null;       // nib dressing, read off CSS at paint time

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
      self._speed = 0;
      self._careful = 0;
      self._showLoupe = false;
      self._mark = { x: e.clientX, y: e.clientY, t: now() };
      self._add(e);
      self._startTicker();
      self.draw();
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
      // Speed is sampled once per delivered event, not per coalesced point:
      // the coalesced batch arrives with one timestamp, so timing it per point
      // would divide a real distance by no time at all.
      self._sampleSpeed(e.clientX, e.clientY);
      self.draw();
    });

    function end(e) {
      if (self._pointer !== e.pointerId) return;
      self._pointer = null;
      self._tip = null;
      self._showLoupe = false;
      self._stopTicker();
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

  function now() {
    return (window.performance && performance.now) ? performance.now() : Date.now();
  }

  Pad.prototype._sampleSpeed = function (clientX, clientY) {
    var t = now(), m = this._mark;
    if (!m) { this._mark = { x: clientX, y: clientY, t: t }; return; }
    var dt = t - m.t;
    if (dt < 8) return;                      // too short to time reliably
    var v = Math.hypot(clientX - m.x, clientY - m.y) / (dt / 1000);
    this._speed += (v - this._speed) * SPEED_SMOOTH;
    this._mark = { x: clientX, y: clientY, t: t };
  };

  var TICK = 50;            // ms between judgements of "is this detail work?"

  /* While a stroke is down, keep judging whether it has become detail work.
   *
   * This runs on its own clock rather than off pointer events because a hand
   * that has stopped to line something up emits no events at all, and holding
   * still is the most careful thing a hand can do - it should bring the glass
   * up, not stall it forever one sample short.
   *
   * On a timer rather than requestAnimationFrame, deliberately: rAF is a paint
   * clock, and a device that is throttling frames would then never decide the
   * hand had settled. Timing and drawing are different questions. Painting is
   * still left to draw(), which coalesces onto rAF.
   */
  Pad.prototype._startTicker = function () {
    var self = this;
    if (this._ticker) return;
    var last = now();
    this._ticker = setInterval(function () {
      if (self._pointer === null) { self._stopTicker(); return; }
      var t = now(), dt = t - last;
      last = t;

      // No movement since the last sample means the pen is resting: decay the
      // measured speed toward zero so a pause reads as careful, not as
      // whatever it was doing before it stopped.
      if (t - self._mark.t > 60) {
        self._speed += (0 - self._speed) * SPEED_SMOOTH;
      }

      if (self._speed <= LOUPE_SLOW) self._careful += dt;
      else if (self._speed >= LOUPE_FAST) self._careful = 0;

      var want = self._careful >= LOUPE_DELAY;
      if (want !== self._showLoupe) {
        self._showLoupe = want;
        self.draw();
      } else if (want) {
        self.draw();                          // keep the glass under the finger
      }
    }, TICK);
  };

  Pad.prototype._stopTicker = function () {
    if (this._ticker) clearInterval(this._ticker);
    this._ticker = 0;
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
      this._fit = geom.fit(this.level.w, this.level.h, w, h, w * MARGIN_FRAC);
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
    this._pen = { taper: true,
                  core: style.getPropertyValue('--ink-core').trim() || '#150d06' };

    this._scene(ctx, guide, inkc);
    this._paintLoupe(guide, inkc, card, rule);
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
    ink.paintStrokes(ctx, this.strokes, this.level.pen, this._fit, inkc,
                     this._pen);
    ctx.globalAlpha = 1;
  };

  Pad.prototype._paintLoupe = function (guide, inkc, card, rule) {
    var el = this.loupe;
    if (!el) return;
    var tip = this._tip;
    if (!tip || !this._showLoupe) { el.classList.remove('on'); return; }

    var d = Math.round(Math.min(LOUPE * Math.min(this._css.w, this._css.h),
                                LOUPE_MAX));
    var rad = d / 2;
    var dpr = Math.min(window.devicePixelRatio || 1, 3);

    if (el.width !== Math.round(d * dpr)) {
      el.width = Math.round(d * dpr);
      el.height = Math.round(d * dpr);
      el.style.width = d + 'px';
      el.style.height = d + 'px';
    }

    var ctx = el.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, d, d);
    ctx.fillStyle = card;
    ctx.fillRect(0, 0, d, d);          // CSS rounds the element into a circle

    ctx.save();
    // Put the fingertip at the middle of the glass, magnified.
    ctx.translate(rad, rad);
    ctx.scale(LOUPE_ZOOM, LOUPE_ZOOM);
    ctx.translate(-tip.x, -tip.y);
    this._scene(ctx, guide, inkc, 0.55);
    ctx.restore();

    // Crosshair marking the exact point being scored.
    ctx.strokeStyle = rule;
    ctx.lineWidth = 1;
    var arm = 7;
    ctx.beginPath();
    ctx.moveTo(rad - arm, rad); ctx.lineTo(rad - 2, rad);
    ctx.moveTo(rad + 2, rad); ctx.lineTo(rad + arm, rad);
    ctx.moveTo(rad, rad - arm); ctx.lineTo(rad, rad - 2);
    ctx.moveTo(rad, rad + 2); ctx.lineTo(rad, rad + arm);
    ctx.stroke();

    // Always above the hand, never below: below it is under the finger again,
    // which is the whole thing this is here to avoid. If the fingertip is high
    // enough that there is no room left, the glass stops against the top of the
    // viewport rather than jumping to the other side.
    var r = this.canvas.getBoundingClientRect();
    var left = r.left + tip.x - rad;
    var top = r.top + tip.y - LOUPE_GAP - d;
    left = Math.max(LOUPE_EDGE,
                    Math.min(window.innerWidth - d - LOUPE_EDGE, left));
    top = Math.max(LOUPE_EDGE, top);

    el.style.transform = 'translate(' + Math.round(left) + 'px,'
                       + Math.round(top) + 'px)';
    el.hidden = false;
    el.classList.add('on');
  };

  SG.Pad = Pad;
  SG.Pad.MARGIN_FRAC = MARGIN_FRAC;
})(window.SG || (window.SG = {}));
