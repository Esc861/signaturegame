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
      + ' collections. Each one you master unlocks a harder hand.';

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

  /* Signatures are not all letterbox-shaped: Pasteur's is nearly square, van
     Gogh's is a long ribbon. A fixed-height card would shrink the square ones
     to a stamp, so the card takes its height from the signature. */
  function cardHeight(level, width, minH, maxH) {
    var ideal = width * (level.h / level.w);
    return Math.round(Math.max(minH, Math.min(ideal, maxH)));
  }

  function sizePad(level) {
    var c = $('pad');
    var w = c.getBoundingClientRect().width || c.parentNode.clientWidth;
    var room = Math.round(window.innerHeight * 0.56);
    c.style.height = cardHeight(level, w, 190, Math.max(210, room)) + 'px';
  }

  function renderPlay(theme, index) {
    var lv = theme.levels[index];
    current.theme = theme;
    current.index = index;
    current.result = null;

    $('play-name').textContent = lv.name;
    $('play-years').textContent = lv.years || '—';
    $('play-target').textContent = lv['pass'] + '%';
    var pips = $('play-pips');
    pips.textContent = '';
    pips.appendChild(difficultyPips(lv.difficulty));

    $('result').hidden = true;
    $('play-hint').textContent = store.seenIntro()
      ? 'Keep the line steady and flowing.'
      : 'Trace the grey signature. A steady, flowing line counts for more than'
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
    if (res.economy < 70) note = ' Too much ink — you drew far further than the signature.';
    else if (res.fluency < 70) note = ' Your line wavered. Slow down and keep it flowing.';
    else if (res.coverage < res.precision - 12) note = ' You missed parts of it.';
    else if (res.precision < res.coverage - 12) note = ' You drifted off the line.';

    $('res-need').textContent = (passed
      ? 'You needed ' + lv['pass'] + '%.'
      : 'You need ' + lv['pass'] + '% to unlock the next hand.')
      + note + ' Best ' + Math.max(prevBest, res.accuracy) + '%.';
    $('res-flu').textContent = res.fluency + '%';
    $('res-prec').textContent = res.precision + '%';
    $('res-cov').textContent = res.coverage + '%';

    var wiki = $('res-wiki');
    if (lv.wiki) {
      wiki.href = lv.wiki;
      wiki.textContent = 'Read about ' + lv.name + ' →';
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

  function route() {
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

    window.addEventListener('hashchange', route);
    window.addEventListener('resize', function () {
      if (pad && pad.level) { sizePad(pad.level); pad.resize(); }
      if (current.result && !$('result').hidden) {
        drawResultMap(pad.level, pad.strokes, current.result);
      }
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
