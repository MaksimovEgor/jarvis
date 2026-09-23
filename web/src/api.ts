import type { HistoryEntry } from './types'

// Озвучка ответа: куски забираются по одному (app/services/speech.py).
export interface SpeechMore {
  id: string
  count: number
}

export async function fetchSpeechChunk(id: string, n: number): Promise<Blob> {
  const resp = await fetch(`tts/chunk/${id}/${n}`)
  if (!resp.ok) {
    throw new Error(`Сервер ответил ${resp.status}`)
  }
  return resp.blob()
}

// Один session_id с голосовым listener'ом → в Hermes это один и тот же
// разговор: начал голосом дома, продолжил с телефона.
const SESSION_ID = 'default'

const DEVICE_KEY = 'jarvis:device'

// Музыка и таймеры звучат там, откуда попросили: у каждого браузера свой id
// (телефон, Mac), ядро по нему шлёт плеер именно сюда.
export const DEVICE_ID = readDeviceId()

function readDeviceId(): string {
  try {
    const saved = localStorage.getItem(DEVICE_KEY)
    if (saved) return saved
    const id = `web:${crypto.randomUUID()}`
    localStorage.setItem(DEVICE_KEY, id)
    return id
  } catch {
    // приватный режим — id живёт до перезагрузки страницы
    return `web:${crypto.randomUUID()}`
  }
}

// Пути относительные — не завязаны на то, где смонтирован фронт.
// turnId — id реплики: их может выполняться несколько одновременно.
// detach: ядро сразу отвечает «принято», а расшифровка, прогресс и ответ
// приходят событиями по SSE (useMusic → onTurn). Долгий POST через туннель
// рвался, и готовый ответ до экрана не доходил.
export async function sendAudio(blob: Blob, turnId: string): Promise<void> {
  const form = new FormData()
  form.append('file', blob, `command.${extensionFor(blob.type)}`)
  const query = new URLSearchParams({ session_id: SESSION_ID, device: DEVICE_ID, turn_id: turnId, detach: 'true' })
  const resp = await fetch(`chat/audio?${query}`, { method: 'POST', body: form })
  await accepted(resp)
}

export async function sendText(text: string, speak: boolean, turnId: string): Promise<void> {
  const resp = await fetch('chat/text', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: SESSION_ID, text, speak, device: DEVICE_ID, turn_id: turnId, detach: true }),
  })
  await accepted(resp)
}

export async function fetchHistory(): Promise<HistoryEntry[]> {
  const resp = await fetch(`chat/history?${new URLSearchParams({ session_id: SESSION_ID })}`)
  return resp.ok ? ((await resp.json()) as HistoryEntry[]) : []
}

export async function cancelTurn(turnId: string): Promise<void> {
  await postJson('chat/cancel', { session_id: SESSION_ID, turn_id: turnId })
}

export type PlayerAction = 'pause' | 'resume' | 'next' | 'previous' | 'stop' | 'seek'

export interface PlayerReport {
  seq: number
  position?: number
  paused?: boolean
  ended?: boolean
  error?: string
  volume_supported?: boolean
  hls?: boolean
}

// Диагностика с телефона в журнал ядра: в Safari на iPhone нет консоли под рукой.
export function clientLog(message: string): void {
  void postJson('client/log', { device: DEVICE_ID, message }).catch(() => undefined)
}

// since — id последнего события: ядро дошлёт пропущенное, пока SSE лежал.
export function playerEventsUrl(since: string): string {
  const query = new URLSearchParams({ device: DEVICE_ID })
  if (since) query.set('since', since)
  return `player/events?${query}`
}

export async function playerControl(action: PlayerAction, position?: number): Promise<void> {
  await postJson('player/control', { device: DEVICE_ID, action, position })
}

// Отчёты не должны ронять интерфейс — сеть на телефоне моргает.
export async function playerReport(report: PlayerReport): Promise<void> {
  try {
    await postJson('player/report', { device: DEVICE_ID, ...report })
  } catch {
    // следующий отчёт догонит
  }
}

async function postJson(url: string, body: unknown): Promise<void> {
  const resp = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    // Отчёт при сворачивании страницы должен уйти, даже если её выгрузят.
    keepalive: true,
  })
  if (!resp.ok) {
    throw new Error(`Сервер ответил ${resp.status}`)
  }
}

async function accepted(resp: Response): Promise<void> {
  if (!resp.ok) {
    throw new Error(`Сервер ответил ${resp.status}`)
  }
}

// Расширение нужно ядру только чтобы ffmpeg/PyAV в faster-whisper угадал
// контейнер: Chrome пишет webm/opus, Safari — mp4/aac.
function extensionFor(mime: string): string {
  if (mime.includes('mp4')) return 'mp4'
  if (mime.includes('ogg')) return 'ogg'
  return 'webm'
}
