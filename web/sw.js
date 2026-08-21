// herdr-outpost Service Worker — Hardened Cache & Offline Support
const CACHE_NAME = 'herdr-outpost-v4';
const ASSETS_TO_CACHE = [
  './',
  './index.html',
  './logo.svg',
  './manifest.json',
  './vendor/qrcode.js'
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      return cache.addAll(ASSETS_TO_CACHE).catch((err) => {
        console.warn('[SW] Caching assets on install warning:', err);
      });
    }).then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) => {
      return Promise.all(
        keys.map((key) => {
          if (key !== CACHE_NAME) {
            return caches.delete(key);
          }
        })
      );
    }).then(() => self.clients.claim())
  );
});

// Network-first with cache fallback strategy for dynamic agility
self.addEventListener('fetch', (event) => {
  if (event.request.method !== 'GET') return;
  const url = new URL(event.request.url);
  if (!url.protocol.startsWith('http')) return;

  event.respondWith(
    fetch(event.request)
      .then((response) => {
        if (response && response.status === 200 && response.type === 'basic') {
          const responseToCache = response.clone();
          caches.open(CACHE_NAME).then((cache) => {
            cache.put(event.request, responseToCache).catch(() => {});
          });
        }
        return response;
      })
      .catch(async () => {
        const cached = await caches.match(event.request);
        if (cached) return cached;
        if (event.request.mode === 'navigate') {
          return caches.match('./index.html') || caches.match('./');
        }
        return new Response('Offline', { status: 503, statusText: 'Service Unavailable' });
      })
  );
});

// Web Push Notification Handler
self.addEventListener('push', (event) => {
  let data = {
    title: 'herdr-outpost Notification',
    body: 'An agent status update was received.',
    icon: 'logo.svg',
    badge: 'logo.svg',
    tag: 'agent-status',
    data: {}
  };

  if (event.data) {
    try {
      const json = event.data.json();
      data = { ...data, ...json };
    } catch (e) {
      data.body = event.data.text() || data.body;
    }
  }

  const options = {
    body: data.body,
    icon: data.icon || 'logo.svg',
    badge: data.badge || 'logo.svg',
    tag: data.tag || 'herdr-agent',
    data: data.data || {},
    vibrate: [200, 100, 200],
    requireInteraction: data.status === 'blocked',
    actions: data.actions || (data.status === 'blocked' ? [
      { action: 'approve', title: '✓ Approve' },
      { action: 'reject', title: '✕ Reject' }
    ] : [
      { action: 'open', title: 'Open Dashboard' }
    ])
  };

  event.waitUntil(
    self.registration.showNotification(data.title, options)
  );
});

// Web Push Notification Click Handler
self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const notificationData = event.notification.data || {};
  const agentId = notificationData.paneId || notificationData.pane_id || notificationData.agent_id || notificationData.id || '';
  const action = event.action;

  let targetUrl = './';
  if (agentId) {
    targetUrl = './session/' + encodeURIComponent(agentId);
    if (action && action !== 'open') {
      const params = new URLSearchParams();
      params.set('action', action);
      targetUrl += '?' + params.toString();
    }
  }

  event.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then((clientList) => {
      for (const client of clientList) {
        if (client.url && 'focus' in client) {
          if (agentId) {
            client.postMessage({
              type: 'NOTIFICATION_ACTION',
              agentId: agentId,
              action: action
            });
          }
          return client.focus();
        }
      }
      if (clients.openWindow) {
        return clients.openWindow(targetUrl);
      }
    })
  );
});
