/* Screens, routing and the play loop. */
(function (SG) {
  'use strict';

  var geom = SG.geom, ink = SG.ink, store = SG.store;
  var DATA = window.SIGNATURES;

  var pad = null;
  var current = { theme: null, index: 0, result: null };

  function $(id) { return document.getElementById(id); }
  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }
  function themeById(id) {
    for (var i = 0; i < DATA.themes.length; i++) {
      if (DATA.themes[i].id === id) return DATA.themes[i];
    }
    return null;
  }

  /* -------------------------------------------------------------- home */

  function renderHome() {
    // Counted from the corpus rather than written into the copy, which went
    // stale the moment the tracks grew.
    var total = DATA.themes.reduce(function (n, t) { return n + t.levels.length; }, 0);
    $('lede').textContent = total + ' signatures across ' + DATA.themes.length
      + ' collections. Clear one to unlock the next, each harder'
      + ' than the last.';

    var wrap = $('tracks');
    wrap.textContent = '';
    DATA.themes.forEach(function (theme) {
      var p = store.progress(theme);
      var card = el('button', 'track');
      card.type = 'button';

      var top = el('div', 'track-top');
      top.appendChild(el('h3', null, theme.name));
      top.appendChild(el('span', 'count', p.done + ' / ' + p.total));
      card.appendChild(top);
      card.appendChild(el('p', null, theme.blurb));

      var meter = el('div', 'meter');
      var bar = el('i');
      bar.style.width = (p.total ? (100 * p.done / p.total) : 0) + '%';
      meter.appendChild(bar);
      card.appendChild(meter);

      card.addEventListener('click', function () { go('#/t/' + theme.id); });
      wrap.appendChild(card);
    });
  }

  /* ------------------------------------------------------------- track */

  function difficultyPips(n) {
    var wrap = el('div', 'pips');
    var lit = Math.max(1, Math.min(5, Math.ceil(n / 20)));
    for (var i = 0; i < 5; i++) {
      wrap.appendChild(el('i', i < lit ? 'lit' : null));
    }
    return wrap;
  }

  function renderTrack(theme) {
    $('track-name').textContent = theme.name;
    $('track-blurb').textContent = theme.blurb;

    var list = $('levels');
    list.textContent = '';
    theme.levels.forEach(function (lv, i) {
      var unlocked = store.isUnlocked(theme, i);
      var best = store.best(lv.id);
      var done = store.cleared(lv);

      var li = document.createElement('li');
      var btn = el('button', 'level');
      btn.type = 'button';
      if (!unlocked) btn.disabled = true;

      btn.appendChild(el('span', 'idx', String(i + 1)));

      // Locked levels still show who they are. The row below would give the
      // name away regardless, and seeing Beethoven waiting three rows down is
      // a better reason to keep going than a row of anonymous padlocks.
      var who = el('div', 'who');
      who.appendChild(el('b', null, lv.name));
      who.appendChild(el('span', null, unlocked ? (lv.years || '')
        : 'clear ' + theme.levels[i - 1].name + ' first'));
      btn.appendChild(who);

      btn.appendChild(difficultyPips(lv.difficulty));

      var score = el('span', 'score' + (done ? ' done' : ''),
                     unlocked ? (best ? best + '%' : '—') : '•');
      if (!unlocked) score.classList.add('locked');
      btn.appendChild(score);

      if (unlocked) {
        btn.addEventListener('click', function () {
          go('#/p/' + theme.id + '/' + i);
        });
      }
      li.appendChild(btn);
      list.appendChild(li);
    });
  }

  /* -------------------------------------------------------------- play */

  /* The card's shape, and why every signature ends up the same size on screen.
   *
   * Signatures are normalized to a long side of 1000, so a level rendered at
   * card width W always lands at scale W/1000 - as long as the card is short
   * enough that the fit is limited by its width rather than its height. That
   * holds for every signature at least CARD_ASPECT wide, and the corpus is
   * built to contain nothing squarer: tools/build_corpus.py rejects anything
   * under the same number. The two constants have to agree.
   *
   * Getting this wrong is not cosmetic. A signature that ends up height-limited
   * renders smaller, and since a finger's error is fixed in screen pixels while
   * the score is measured in signature units, it silently becomes harder to
   * trace for reasons that have nothing to do with the handwriting. The card is
   * therefore one constant width for every level, and only its height varies -
   * hugging the signature, so a long ribbon gets a ribbon-shaped card. */
  var CARD_ASPECT = 3.0;

  function sizePad(level) {
    var c = $('pad');
    var wrap = c.parentNode;
    var m = SG.Pad.MARGIN_FRAC;                  // per side, of the card's width
    var availW = wrap.clientWidth || window.innerWidth;
    var availH = wrap.clientHeight || Math.round(window.innerHeight * 0.6);

    // The tallest card any level can ask for, per unit of width. Sizing off
    // this rather than off *this* level is what keeps the width constant.
    var tallest = 2 * m + (1 - 2 * m) / CARD_ASPECT;
    var w = Math.min(availW, availH / tallest);
    // The min() only bites if a level squarer than CARD_ASPECT ever slips into
    // the corpus, in which case a card that overflows its row is the visible
    // symptom of the two constants having drifted apart.
    var h = Math.min(availH, w * (2 * m + (1 - 2 * m) * (level.h / level.w)));

    c.style.width = Math.round(w) + 'px';
    c.style.height = Math.round(h) + 'px';
  }

  function renderPlay(theme, index) {
    var lv = theme.levels[index];
    current.theme = theme;
    current.index = index;
    current.result = null;

    // The name links out from the header, before the level is cleared. Wanting
    // to know whose hand this is happens while you are looking at it.
    var who = $('play-name');
    $('play-name-text').textContent = lv.name;
    if (lv.wiki) {
      who.href = lv.wiki;
      who.title = 'Read about ' + lv.name + ' on Wikipedia';
    } else {
      who.removeAttribute('href');
      who.removeAttribute('title');
    }
    $('play-years').textContent = lv.years || '—';
    $('play-target').textContent = lv['pass'] + '%';
    var pips = $('play-pips');
    pips.textContent = '';
    pips.appendChild(difficultyPips(lv.difficulty));

    $('result').hidden = true;
    $('play-hint').textContent = store.seenIntro()
      ? 'Keep the line steady and flowing.'
      : 'Trace the gray signature. A steady, flowing line counts for more than'
        + ' landing it exactly.';
    store.seenIntro(true);

    sizePad(lv);
    if (!pad) {
      pad = new SG.Pad($('pad'), $('loupe'));
      pad.onChange = syncTools;
    }
    pad.setLevel(lv);
    syncTools();
  }

  function syncTools() {
    var empty = !pad || pad.isEmpty();
    $('btn-undo').disabled = empty;
    $('btn-clear').disabled = empty;
    $('btn-done').disabled = empty;
  }

  function submit() {
    if (!pad || pad.isEmpty()) return;
    var lv = pad.level;
    var res = SG.grade.score(lv, pad.strokes);
    current.result = res;

    var passed = res.accuracy >= lv['pass'];
    var prevBest = store.best(lv.id);
    store.record(lv.id, res.accuracy);

    if (passed && navigator.vibrate) {
      try { navigator.vibrate([14, 40, 22]); } catch (e) { /* ignore */ }
    }

    $('res-verdict').textContent = passed ? 'Cleared' : 'Not yet';
    $('res-verdict').className = 'verdict ' + (passed ? 'win' : 'lose');
    $('res-score').textContent = res.accuracy;

    // Name the weakest part, so a low score says what went wrong rather than
    // just how badly. Steadiness first: it is what an examiner looks at first.
    var note = '';
    if (res.economy < 70) note = ' Too much ink — you drew a lot further than the signature.';
    else if (res.fluency < 70) note = ' Your line wavered. Slow down and keep it flowing.';
    else if (res.coverage < res.precision - 12) note = ' You missed parts of it.';
    else if (res.precision < res.coverage - 12) note = ' You drifted off the line.';

    $('res-need').textContent = (passed
      ? 'You needed ' + lv['pass'] + '%.'
      : 'You need ' + lv['pass'] + '% to unlock the next signature.')
      + note + ' Best ' + Math.max(prevBest, res.accuracy) + '%.';
    $('res-flu').textContent = res.fluency + '%';
    $('res-prec').textContent = res.precision + '%';
    $('res-cov').textContent = res.coverage + '%';

    var wiki = $('res-wiki');
    if (lv.wiki) {
      wiki.href = lv.wiki;
      $('res-wiki-text').textContent = 'Read about ' + lv.name;
      wiki.parentNode.hidden = false;
    } else {
      wiki.parentNode.hidden = true;
    }

    var last = current.index >= current.theme.levels.length - 1;
    var next = $('btn-next');
    next.textContent = passed ? (last ? 'Back to collection' : 'Next signature')
                              : 'Back to collection';
    $('result').hidden = false;
    drawResultMap(lv, pad.strokes, res);
  }

  /* The player's own line, recoloured by local error over a ghost of the
     target: far more use than the number alone when trying to improve. */
  function drawResultMap(level, strokes, res) {
    var c = $('res-map');
    // Fit the *box* to the signature rather than letterboxing a square hand
    // like Pasteur's inside a wide strip. Measure the canvas at its CSS width
    // rather than the parent's clientWidth, which includes the sheet's padding
    // and so overflowed the panel sideways.
    c.style.width = '100%';
    var boxW = c.getBoundingClientRect().width;
    var maxH = Math.max(140, Math.round(window.innerHeight * 0.34));
    var h = Math.max(120, Math.min(boxW * (level.h / level.w), maxH));
    c.style.height = h + 'px';
    c.style.width = Math.min(boxW, Math.round(h * (level.w / level.h))) + 'px';

    var rect = c.getBoundingClientRect();
    var dpr = Math.min(window.devicePixelRatio || 1, 3);
    c.width = Math.round(rect.width * dpr);
    c.height = Math.round(rect.height * dpr);
    var ctx = c.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, rect.width, rect.height);

    var t = geom.fit(level.w, level.h, rect.width, rect.height,
                     Math.min(rect.width, rect.height) * 0.08);
    var style = getComputedStyle(c);
    ink.paintSignature(ctx, level, t,
                       style.getPropertyValue('--guide').trim() || '#d5cab2');
    ink.paintErrors(ctx, strokes, res.errors, level.pen, t);
  }

  /* ------------------------------------------------------------- about */

  function renderAbout() {
    var list = $('credits');
    if (list.childNodes.length) return;      // built once
    DATA.themes.forEach(function (theme) {
      theme.levels.forEach(function (lv) {
        var li = document.createElement('li');
        if (lv.wiki) {
          var w = el('a', 'who', lv.name);
          w.href = lv.wiki;
          w.target = '_blank';
          w.rel = 'noopener noreferrer';
          li.appendChild(w);
        } else {
          li.appendChild(el('b', null, lv.name));
        }
        li.appendChild(document.createTextNode(' — ' + lv.credit.license
          + ', ' + lv.credit.author + '. '));
        if (lv.credit.url) {
          var a = el('a', null, 'Commons');
          a.href = lv.credit.url;
          a.target = '_blank';
          a.rel = 'noopener noreferrer';
          li.appendChild(a);
        }
        list.appendChild(li);
      });
    });
  }

  /* -------------------------------------------------------------- gate */

  /* The game asks for a landscape touchscreen, and both halves of that are
     load-bearing rather than preference.

     Landscape, because the whole corpus is wide: turned upright, a signature
     has to shrink to a third of the size to fit the screen's short edge, and
     tracing it stops being a test of a steady hand and becomes a test of
     eyesight. Touch, because the difficulty is calibrated against a fingertip
     covering the very line it is trying to follow - which is what the loupe
     exists for, and what a mouse pointer simply does not do.

     Turning the phone fixes the first, so it is a hard stop. Nothing fixes the
     second on a laptop, and a dead end is a worse answer than a warning, so
     that one can be waved through - per load, not remembered.

     Neither applies until there is a signature on screen. The collection lists
     read perfectly well upright on any device, and demanding a posture from
     somebody before they have seen a single thing the game does is the worst
     possible first impression. The rule is about tracing, so it starts when
     the tracing does. */
  var waved = false;

  function isTouch() {
    return (navigator.maxTouchPoints || 0) > 0 || 'ontouchstart' in window
      || (window.matchMedia && matchMedia('(any-pointer: coarse)').matches);
  }

  function playing() {
    return /^#\/p\//.test(location.hash || '');
  }

  function checkGate() {
    var portrait = window.innerHeight >= window.innerWidth;
    var why = !playing() ? null
            : (!isTouch() && !waved) ? 'touch'
            : portrait ? 'turn' : null;
    var g = $('gate');

    document.body.classList.toggle('gated', !!why);
    g.hidden = !why;
    $('gate-phone').hidden = why !== 'turn';
    $('gate-anyway').hidden = why !== 'touch';
    if (why === 'turn') {
      $('gate-title').textContent = 'Turn it sideways';
      $('gate-body').textContent = isTouch()
        ? 'These signatures are wide ones. Held upright there is nowhere near '
          + 'enough room for one, so the game is played sideways.'
        : 'These signatures are wide ones. Make the window wider than it is '
          + 'tall and there will be room for one.';
    } else if (why === 'touch') {
      $('gate-title').textContent = 'Made for a fingertip';
      $('gate-body').textContent = 'Historic Ink is meant for a touchscreen. '
        + 'Half of what makes it hard is that your finger covers the line you '
        + 'are trying to follow, which a mouse never does. Open it on a phone '
        + 'or tablet if you can.';
    }
    // The card's size is measured off the layout, and the layout has usually
    // just changed underneath it.
    if (!why && pad && pad.level) { sizePad(pad.level); pad.resize(); }
  }

  /* ------------------------------------------------------------ router */

  var SCREENS = ['home', 'track', 'play', 'about'];

  function show(name) {
    SCREENS.forEach(function (s) {
      $('screen-' + s).classList.toggle('on', s === name);
    });
  }

  function go(hash) {
    if (location.hash === hash) route();
    else location.hash = hash;
  }

  // The gate depends on which screen is showing, not only on the shape of the
  // window, so every route change has to reconsider it - and afterwards, once
  // the screen it decides about is actually up.
  function route() {
    dispatch();
    checkGate();
  }

  function dispatch() {
    var parts = (location.hash || '#/').replace(/^#\/?/, '').split('/');

    if (parts[0] === 't' && parts[1]) {
      var theme = themeById(parts[1]);
      if (theme) { renderTrack(theme); show('track'); window.scrollTo(0, 0); return; }
    }

    if (parts[0] === 'p' && parts[1]) {
      var th = themeById(parts[1]);
      var i = parseInt(parts[2], 10) || 0;
      if (th && th.levels[i] && store.isUnlocked(th, i)) {
        show('play');
        renderPlay(th, i);
        window.scrollTo(0, 0);
        return;
      }
      if (th) { go('#/t/' + th.id); return; }
    }

    if (parts[0] === 'about') { renderAbout(); show('about'); window.scrollTo(0, 0); return; }

    renderHome();
    show('home');
  }

  /* -------------------------------------------------------------- wire */

  function onResize() {
    checkGate();
    if (pad && pad.level) { sizePad(pad.level); pad.resize(); }
    if (current.result && !$('result').hidden) {
      drawResultMap(pad.level, pad.strokes, current.result);
    }
  }

  function init() {
    document.querySelectorAll('[data-go]').forEach(function (b) {
      b.addEventListener('click', function () { go(b.getAttribute('data-go')); });
    });

    $('play-back').addEventListener('click', function () {
      go('#/t/' + current.theme.id);
    });
    $('btn-undo').addEventListener('click', function () { pad.undo(); });
    $('btn-clear').addEventListener('click', function () { pad.clear(); });
    $('btn-done').addEventListener('click', submit);

    $('btn-again').addEventListener('click', function () {
      $('result').hidden = true;
      pad.clear();
    });

    $('btn-next').addEventListener('click', function () {
      var theme = current.theme, i = current.index;
      var passed = current.result && current.result.accuracy >= theme.levels[i]['pass'];
      $('result').hidden = true;
      if (passed && i < theme.levels.length - 1) go('#/p/' + theme.id + '/' + (i + 1));
      else go('#/t/' + theme.id);
    });

    $('btn-reset').addEventListener('click', function () {
      store.reset();
      go('#/');
    });

    // Offline + installable. Skipped on file://, where registration throws, and
    // on localhost, where a cache-first worker serves yesterday's code back to
    // you and quietly wastes an afternoon.
    var local = /^(localhost|127\.0\.0\.1|\[::1\])$/.test(location.hostname);
    if ('serviceWorker' in navigator && /^https?:/.test(location.protocol) && !local) {
      navigator.serviceWorker.register('sw.js').catch(function () { /* fine */ });
    }

    $('gate-anyway').addEventListener('click', function () {
      waved = true;
      checkGate();
    });

    $('gate-back').addEventListener('click', function () {
      go(current.theme ? '#/t/' + current.theme.id : '#/');
    });

    window.addEventListener('hashchange', route);
    window.addEventListener('resize', onResize);
    // Safari fires orientationchange before the viewport has settled, so the
    // resize that follows is the one worth measuring; this is only here for the
    // browsers that do not send one.
    window.addEventListener('orientationchange', function () {
      setTimeout(onResize, 120);
    });

    route();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

  // `pad` is exposed for debugging from the console; the app never reads it.
  SG.app = { go: go, pad: function () { return pad; } };
})(window.SG || (window.SG = {}));
