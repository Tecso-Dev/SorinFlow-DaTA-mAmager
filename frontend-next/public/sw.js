/**
 * The panel's and the portal's service worker — one script, registered
 * twice with a different `scope` (see src/components/pwa/register.tsx), once
 * for /panel/ and once for /portal/. It never touches /dashboard/: the old
 * panel there has its own worker (frontend/sw.js) and this one must not
 * shadow it.
 *
 * Strategy:
 *   - navigations (HTML pages): network-first, falling back to the cached
 *     offline page for whichever scope the request fell under. Always
 *     network-first, never cache-first, so a signed-out redirect or a fresh
 *     deploy is never served stale from the cache.
 *   - /_next/static/, /icons/, /fonts/: cache-first in a versioned cache —
 *     these are content-hashed or rarely-changing, so a cache hit is safe
 *     and a miss falls through to the network and is cached for next time.
 *   - /api/, /images/, /downloads/, /dashboard/: never touched. Passed
 *     straight to the network with no cache read or write — live data, user
 *     uploads and the old panel's own territory.
 *   - everything else: network-first with no offline fallback (there is
 *     nothing meaningful to show offline for it).
 */

const CACHE_VERSION = "sf-pwa-v1";
const STATIC_CACHE = `${CACHE_VERSION}-static`;
const OFFLINE_CACHE = `${CACHE_VERSION}-offline`;

const OFFLINE_BY_SCOPE = {
  "/panel/": "/panel/offline",
  "/portal/": "/portal/offline",
};

const PRECACHE_URLS = [
  ...new Set(Object.values(OFFLINE_BY_SCOPE)),
  "/icons/icon-192.png",
  "/icons/icon-512.png",
  "/icons/maskable-512.png",
  "/icons/apple-touch-icon.png",
  "/icons/favicon-32.png",
];

const NEVER_CACHE_PREFIXES = ["/api/", "/images/", "/downloads/", "/dashboard/"];
const CACHE_FIRST_PREFIXES = ["/_next/static/", "/icons/", "/fonts/"];

function scopeFor(pathname) {
  return Object.keys(OFFLINE_BY_SCOPE).find((scope) => pathname.startsWith(scope));
}

function isNeverCached(pathname) {
  return NEVER_CACHE_PREFIXES.some((p) => pathname.startsWith(p));
}

function isCacheFirst(pathname) {
  return CACHE_FIRST_PREFIXES.some((p) => pathname.startsWith(p));
}

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(OFFLINE_CACHE).then((cache) =>
      // Each URL on its own: one missing route (e.g. /portal/offline before
      // the portal exists) must not fail every precache entry.
      Promise.all(PRECACHE_URLS.map((url) => cache.add(url).catch(() => undefined))),
    ),
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    (async () => {
      const names = await caches.keys();
      await Promise.all(
        names
          .filter((name) => name.startsWith("sf-pwa-") && name !== STATIC_CACHE && name !== OFFLINE_CACHE)
          .map((name) => caches.delete(name)),
      );
      await self.clients.claim();
    })(),
  );
});

async function networkFirstNavigation(request) {
  const url = new URL(request.url);
  try {
    const fresh = await fetch(request);
    return fresh;
  } catch {
    const scope = scopeFor(url.pathname);
    if (scope) {
      const cache = await caches.open(OFFLINE_CACHE);
      const cached = await cache.match(OFFLINE_BY_SCOPE[scope]);
      if (cached) return cached;
    }
    throw new Error("offline and nothing cached for this scope");
  }
}

async function cacheFirst(request) {
  const cache = await caches.open(STATIC_CACHE);
  const cached = await cache.match(request);
  if (cached) return cached;
  const fresh = await fetch(request);
  if (fresh.ok) cache.put(request, fresh.clone());
  return fresh;
}

self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (request.method !== "GET") return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  if (isNeverCached(url.pathname)) return;

  if (request.mode === "navigate") {
    event.respondWith(networkFirstNavigation(request));
    return;
  }

  if (isCacheFirst(url.pathname)) {
    event.respondWith(cacheFirst(request));
  }
});
