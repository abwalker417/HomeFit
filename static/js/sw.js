/* BuiltHere service worker — offline-first for app shell */

const CACHE = 'builthere-v1';
const APP_SHELL = [
  '/static/css/style.css',
  '/static/js/app.js',
  '/static/js/weight-modes.js',
  '/static/js/workout-flow.js',
  '/static/manifest.json',
];

// A deliberately small private offline pack. These pages contain the selected
// profile's workout data, so we cache only the current device/session and never
// cache arbitrary routes, APIs, food, history, or Coach content.
const OFFLINE_PAGES = new Set(['/', '/offline-workout', '/today-workout']);
const STATIC_ORIGINS = [self.location.origin];
const STATIC_PATHS = ['/static/'];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE).then((cache) => cache.addAll(APP_SHELL))
  );
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
    )
  );
  self.clients.claim();
});

self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return;

  const url = new URL(req.url);

  if (req.mode === 'navigate') {
    if (!OFFLINE_PAGES.has(url.pathname)) return;
    event.respondWith((async () => {
      try {
        const response = await fetch(req);
        // Redirects are profile/session guards, not an offline workout page.
        if (response.ok && !response.redirected) {
          const cache = await caches.open(CACHE);
          await cache.put(req, response.clone());
        }
        return response;
      } catch (_) {
        const cached = await caches.match(req);
        if (cached) return cached;
        return new Response('<!doctype html><title>BuiltHere offline</title><p>Open BuiltHere once while connected to save today\'s workout for offline use.</p>', {
          headers: {'Content-Type': 'text/html; charset=utf-8'}, status: 503,
        });
      }
    })());
    return;
  }

  // Only cache /static/ assets — not API endpoints, not pages
  if (!STATIC_PATHS.some((p) => url.pathname.startsWith(p))) return;

  // Cache-first for static assets
  event.respondWith(
    caches.match(req).then((cached) =>
      cached ||
      fetch(req).then((res) => {
        if (res.ok) {
          const copy = res.clone();
          caches.open(CACHE).then((c) => c.put(req, copy));
        }
        return res;
      })
    )
  );
});

self.addEventListener('message', (event) => {
  if (!event.data || event.data.type !== 'CACHE_OFFLINE_WORKOUT') return;
  const urls = ['/','/offline-workout'];
  event.waitUntil((async () => {
    const cache = await caches.open(CACHE);
    await Promise.all(urls.map(async (url) => {
      try {
        const response = await fetch(url, {credentials: 'same-origin'});
        if (response.ok && !response.redirected) await cache.put(url, response);
      } catch (_) {
        // Keep the last known good offline pack during a transient outage.
      }
    }));
  })());
});

/* ---------- Web push ---------- */
self.addEventListener('push', (event) => {
  let data = {};
  try { data = event.data.json(); } catch { data = { title: 'BuiltHere', body: event.data && event.data.text() }; }
  event.waitUntil(
    self.registration.showNotification(data.title || 'BuiltHere', {
      body: data.body || '',
      icon: '/static/icons/icon-192.png',
      badge: '/static/icons/icon-192.png',
      data: { url: data.url || '/' },
    })
  );
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const url = (event.notification.data && event.notification.data.url) || '/';
  event.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then((wins) => {
      for (const w of wins) {
        if ('focus' in w) { w.navigate(url); return w.focus(); }
      }
      return clients.openWindow(url);
    })
  );
});
