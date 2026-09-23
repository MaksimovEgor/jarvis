import { ref } from 'vue'

import { fetchPushKey, savePushSubscription } from '../api'

// Уведомления о готовых фоновых задачах и таймерах, когда вкладка свёрнута
// (app/services/webpush.py). На iPhone PushManager есть только у PWA с экрана
// «Домой» (iOS 16.4+), а разрешение спрашивается только из тапа.
export function usePush() {
  const supported = 'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window
  const enabled = ref(supported && Notification.permission === 'granted')
  const error = ref<string | null>(null)

  const registration: Promise<ServiceWorkerRegistration> | null = supported
    ? navigator.serviceWorker.register('sw.js')
    : null

  async function subscribe(): Promise<void> {
    if (!registration) return
    const reg = await registration
    const key = await fetchPushKey()
    let sub = await reg.pushManager.getSubscription()
    // Ключ ядра сменился (data/vapid.pem пересоздан) — старая подписка мертва.
    if (sub && !sameKey(sub.options.applicationServerKey, key)) {
      await sub.unsubscribe()
      sub = null
    }
    sub ??= await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: decodeKey(key) })
    await savePushSubscription(sub.toJSON())
  }

  // Из тапа по кнопке: спросить разрешение и подписаться.
  async function enable(): Promise<void> {
    if (!supported) return
    error.value = null
    try {
      const permission = await Notification.requestPermission()
      if (permission !== 'granted') {
        error.value = 'Уведомления запрещены в настройках'
        return
      }
      await subscribe()
      enabled.value = true
    } catch (e) {
      error.value = e instanceof Error ? e.message : String(e)
    }
  }

  // Разрешение уже есть — напомнить ядру о подписке (оно могло её удалить).
  if (enabled.value) void subscribe().catch(() => undefined)

  return { supported, enabled, error, enable }
}

function decodeKey(base64url: string): Uint8Array<ArrayBuffer> {
  const base64 = (base64url + '='.repeat((4 - (base64url.length % 4)) % 4)).replace(/-/g, '+').replace(/_/g, '/')
  const raw = atob(base64)
  const bytes = new Uint8Array(new ArrayBuffer(raw.length))
  for (let i = 0; i < raw.length; i++) bytes[i] = raw.charCodeAt(i)
  return bytes
}

function sameKey(current: ArrayBuffer | null, key: string): boolean {
  if (!current) return false
  const a = new Uint8Array(current)
  const b = decodeKey(key)
  return a.length === b.length && a.every((v, i) => v === b[i])
}
