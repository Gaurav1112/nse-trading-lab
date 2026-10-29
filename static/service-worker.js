// nse-trading-lab service worker — handles push notifications
self.addEventListener('push', (event) => {
  if (!event.data) return;
  let payload;
  try { payload = event.data.json(); } catch { payload = { title: 'NSE Lab', body: event.data.text() }; }
  const opts = {
    body: payload.body || '',
    icon: 'https://em-content.zobj.net/thumbs/240/apple/354/direct-hit_1f3af.png',
    badge: 'https://em-content.zobj.net/thumbs/240/apple/354/direct-hit_1f3af.png',
    data: payload.data || {},
    tag: payload.tag || 'nse-lab-signal',
  };
  event.waitUntil(self.registration.showNotification(payload.title || 'NSE Lab', opts));
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const url = event.notification.data.url || '/';
  event.waitUntil(clients.openWindow(url));
});
