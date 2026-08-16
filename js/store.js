/* Progress in localStorage.
 *
 * All six tracks are open from the start - on a phone, parallel tracks beat one
 * long linear gate, because a signature you cannot manage shouldn't wall off
 * everything else. Within a track you unlock the next signature by clearing the
 * one before it.
 */
(function (SG) {
  'use strict';

  var KEY = 'signature-forger/v1';
  var state = null;

  function blank() {
    return { best: {}, plays: 0, seenIntro: false };
  }

  function load() {
    if (state) return state;
    try {
      var raw = window.localStorage.getItem(KEY);
      state = raw ? JSON.parse(raw) : blank();
      if (!state || typeof state !== 'object' || !state.best) state = blank();
    } catch (e) {
      // Private browsing, disabled storage, or corrupt JSON. The game is still
      // perfectly playable without persistence, so carry on in memory.
      state = blank();
    }
    return state;
  }

  function save() {
    try {
      window.localStorage.setItem(KEY, JSON.stringify(load()));
    } catch (e) { /* nothing useful to do */ }
  }

  function best(levelId) {
    return load().best[levelId] || 0;
  }

  /* Returns true when this beat the previous best. */
  function record(levelId, accuracy) {
    var s = load();
    s.plays++;
    var improved = accuracy > (s.best[levelId] || 0);
    if (improved) s.best[levelId] = accuracy;
    save();
    return improved;
  }

  function cleared(level) {
    return best(level.id) >= level['pass'];
  }

  function isUnlocked(theme, index) {
    if (index <= 0) return true;
    return cleared(theme.levels[index - 1]);
  }

  function progress(theme) {
    var done = 0;
    for (var i = 0; i < theme.levels.length; i++) {
      if (cleared(theme.levels[i])) done++;
    }
    return { done: done, total: theme.levels.length };
  }

  /* First level in a track the player hasn't cleared - where "Continue" goes. */
  function nextIndex(theme) {
    for (var i = 0; i < theme.levels.length; i++) {
      if (!cleared(theme.levels[i])) return i;
    }
    return theme.levels.length - 1;
  }

  function seenIntro(set) {
    var s = load();
    if (set === true && !s.seenIntro) { s.seenIntro = true; save(); }
    return s.seenIntro;
  }

  function reset() {
    state = blank();
    save();
  }

  SG.store = {
    best: best,
    record: record,
    cleared: cleared,
    isUnlocked: isUnlocked,
    progress: progress,
    nextIndex: nextIndex,
    seenIntro: seenIntro,
    reset: reset
  };
})(window.SG || (window.SG = {}));
