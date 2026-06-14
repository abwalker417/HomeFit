/* HomeFit service worker — offline-first for app shell */

const CACHE = 'homefit-v58';
const APP_SHELL = [
  '/static/css/style.css',
  '/static/js/app.js',
  '/static/manifest.json',
];

// Only cache true static assets — never navigation pages
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

  // Never cache navigation — always hit the server so routes stay fresh
  if (req.mode === 'navigate') return;

  const url = new URL(req.url);

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

/* ---------- Web push ---------- */
self.addEventListener('push', (event) => {
  let data = {};
  try { data = event.data.json(); } catch { data = { title: 'HomeFit', body: event.data && event.data.text() }; }
  event.waitUntil(
    self.registration.showNotification(data.title || 'HomeFit', {
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
