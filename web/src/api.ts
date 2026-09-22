export interface ChatReply {
  transcript?: string
  reply: string
  tool_calls: string[]
  audio_base64: string | null
}

// Один session_id с голосовым listener'ом → в Hermes это один и тот же
// разговор: начал голосом дома, продолжил с телефона.
const SESSION_ID = 'default'

// Пути относительные — не завязаны на то, где смонтирован фронт.
export async function sendAudio(blob: Blob): Promise<ChatReply> {
  const form = new FormData()
  form.append('file', blob, `command.${extensionFor(blob.type)}`)
  const resp = await fetch(`chat/audio?session_id=${SESSION_ID}`, { method: 'POST', body: form })
  return parse(resp)
}

export async function sendText(text: string, speak: boolean): Promise<ChatReply> {
  const resp = await fetch('chat/text', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: SESSION_ID, text, speak }),
  })
  return parse(resp)
}

async function parse(resp: Response): Promise<ChatReply> {
  if (!resp.ok) {
    throw new Error(`Сервер ответил ${resp.status}`)
  }
  return (await resp.json()) as ChatReply
}

// Расширение нужно ядру только чтобы ffmpeg/PyAV в faster-whisper угадал
// контейнер: Chrome пишет webm/opus, Safari — mp4/aac.
function extensionFor(mime: string): string {
  if (mime.includes('mp4')) return 'mp4'
  if (mime.includes('ogg')) return 'ogg'
  return 'webm'
}
