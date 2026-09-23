// Service worker Джарвиса — только уведомления (Web Push, app/services/webpush.py).
// Ядро шлёт пуш, когда вкладка свёрнута: фоновая задача готова, сработал таймер.
// Кэша и офлайна нет — страница всегда свежая с ядра.

self.addEventListener('install', () => self.skipWaiting())
self.addEventListener('activate', (event) => event.waitUntil(self.clients.claim()))

self.addEventListener('push', (event) => {
  let data = {}
  try {
    data = event.data ? event.data.json() : {}
  } catch {
    data = { body: event.data ? event.data.text() : '' }
  }
  // iOS требует показывать уведомление на каждый пуш, иначе отзывает подписку.
  event.waitUntil(
    self.registration.showNotification(data.title || 'Джарвис', {
      body: data.body || '',
      tag: data.tag || undefined,
      icon: 'icon-192.png',
      badge: 'icon-192.png',
    }),
  )
})

// Тап по уведомлению — открыть Джарвиса (или вернуть уже открытую вкладку).
self.addEventListener('notificationclick', (event) => {
  event.notification.close()
  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((windows) => {
      const open = windows.find((w) => 'focus' in w)
      return open ? open.focus() : self.clients.openWindow(self.registration.scope)
    }),
  )
})
