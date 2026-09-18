/* SorinFlow service worker — the panel as an app.
 *
 * Two rules, and only two:
 *   1. Nothing under /api/ is ever cached. Data is always live; offline it
 *      fails the way it would in a browser, and the panel says so.
 *   2. The shell is served network-first: when online you get exactly what
 *      the server has (so a deploy is picked up on the next load, no version
 *      to bump here); when offline you get the last copy that loaded.
 *      Vendor files, fonts and icons never change under the same name, so
 *      they are cache-first.
 */
var CACHE = 'sorinflow-shell-v1';
var SCOPE = self.registration.scope;                 // https://sorinflow.com/dashboard/
var SHELL = ['', 'index.html', 'manifest.webmanifest', 'icons/icon-192.png'];

self.addEventListener('install', function (e) {
  e.waitUntil(
    caches.open(CACHE).then(function (c) {
      return Promise.all(SHELL.map(function (p) {
        return fetch(SCOPE + p, { cache: 'no-cache' })
          .then(function (r) { if (r.ok) return c.put(SCOPE + p, r); })
          .catch(function () {});
      }));
    }).then(function () { return self.skipWaiting(); })
  );
});

self.addEventListener('activate', function (e) {
  e.waitUntil(
    caches.keys().then(function (keys) {
      return Promise.all(keys.filter(function (k) { return k !== CACHE; }).map(function (k) { return caches.delete(k); }));
    }).then(function () { return self.clients.claim(); })
  );
});

function isApi(url) { return url.pathname.indexOf('/api/') === 0 || url.pathname === '/health' || url.pathname === '/ready'; }
function isImmutable(url) {
  return /\/dashboard\/(vendor|icons)\//.test(url.pathname) || /\.(woff2?|ttf|png|jpg|svg)$/.test(url.pathname);
}

self.addEventListener('fetch', function (e) {
  var req = e.request;
  if (req.method !== 'GET') return;
  var url = new URL(req.url);
  if (url.origin !== location.origin || isApi(url) || url.pathname.indexOf('/images/') === 0 || url.pathname.indexOf('/downloads/') === 0) return;

  if (isImmutable(url)) {
    e.respondWith(
      caches.match(req).then(function (hit) {
        return hit || fetch(req).then(function (r) {
          if (r.ok) { var copy = r.clone(); caches.open(CACHE).then(function (c) { c.put(req, copy); }); }
          return r;
        });
      })
    );
    return;
  }

  // the shell: network first, cache as the fallback
  e.respondWith(
    fetch(req).then(function (r) {
      if (r.ok && (req.mode === 'navigate' || /\.(css|js|webmanifest|html)(\?|$)/.test(url.pathname + url.search))) {
        var copy = r.clone();
        caches.open(CACHE).then(function (c) {
          c.put(req, copy);
          // one copy of a versioned file, not one per deploy: drop the
          // same path under other ?v= values
          if (url.search) {
            c.keys().then(function (keys) {
              keys.forEach(function (k) {
                var ku = new URL(k.url);
                if (ku.pathname === url.pathname && ku.search !== url.search) c.delete(k);
              });
            });
          }
        });
      }
      return r;
    }).catch(function () {
      return caches.match(req).then(function (hit) {
        if (hit) return hit;
        if (req.mode === 'navigate') return caches.match(SCOPE) || caches.match(SCOPE + 'index.html');
        return new Response('', { status: 504, statusText: 'offline' });
      });
    })
  );
});
