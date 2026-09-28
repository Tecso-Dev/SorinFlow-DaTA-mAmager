// Web push was removed from the site, but a browser that registered this
// worker while it was there keeps it, and keeps asking for it, until a newer
// script replaces it. So the file stays at its address and now does one thing:
// remove its own registration. It has no fetch handler, imports nothing and
// connects to nothing, so it is never in the path of a request of any page.
//
// skipWaiting() matters: without it the new version would wait behind the old
// one until every tab it controls is closed, and the old one would carry on.
self.addEventListener("install", () => {
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(self.registration.unregister());
});
