/* Offline cache.
 *
 * The whole game is a handful of static files plus one baked data file, so it
 * can simply be precached wholesale. Bump CACHE when any of them change -
 * the old cache is dropped on activate.
 */
var CACHE = 'forgers-archive-v2';

var ASSETS = [
  './',
  './index.html',
  './css/app.css',
  './js/geom.js',
  './js/ink.js',
  './js/grade.js',
  './js/pad.js',
  './js/store.js',
  './js/app.js',
  './data/signatures.js',
  './manifest.webmanifest',
  './icon.svg'
];

self.addEventListener('install', function (e) {
  e.waitUntil(
    caches.open(CACHE)
      .then(function (c) { return c.addAll(ASSETS); })
      .then(function () { return self.skipWaiting(); })
  );
});

self.addEventListener('activate', function (e) {
  e.waitUntil(
    caches.keys().then(function (keys) {
      return Promise.all(keys.map(function (k) {
        return k === CACHE ? null : caches.delete(k);
      }));
    }).then(function () { return self.clients.claim(); })
  );
});

self.addEventListener('fetch', function (e) {
  if (e.request.method !== 'GET') return;
  e.respondWith(
    caches.match(e.request).then(function (hit) {
      if (hit) return hit;
      return fetch(e.request).then(function (res) {
        // Cache same-origin successes so a first visit that misses the
        // precache list still works offline afterwards.
        if (res && res.ok && res.type === 'basic') {
          var copy = res.clone();
          caches.open(CACHE).then(function (c) { c.put(e.request, copy); });
        }
        return res;
      }).catch(function () {
        return caches.match('./index.html');
      });
    })
  );
});
