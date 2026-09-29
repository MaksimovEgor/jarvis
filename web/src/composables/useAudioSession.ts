import { clientLog } from '../api'

// Аудиосессия iOS (Safari 16.4+: navigator.audioSession). Открытый микрофон
// переводит её в «звонок» (play-and-record): вывод приглушён, Bluetooth —
// гарнитура. Сама iOS обратно в «воспроизведение» возвращается не всегда —
// и музыка на колонке остаётся тихой. Поэтому режим задаём явно:
// микрофон открыт — play-and-record, закрыт — playback.
type SessionType = 'auto' | 'playback' | 'transient' | 'transient-solo' | 'ambient' | 'play-and-record'

interface AudioSessionLike {
  type: SessionType
}

// Кто держит микрофон: запись по кнопке и слушание «Джарвис».
let micUsers = 0

function session(): AudioSessionLike | null {
  const nav = navigator as Navigator & { audioSession?: AudioSessionLike }
  return nav.audioSession ?? null
}

function setType(type: SessionType): void {
  const s = session()
  if (!s || s.type === type) return
  try {
    s.type = type
    clientLog(`audioSession: ${type}`)
  } catch (e) {
    clientLog(`audioSession: ${type} не встал: ${e instanceof Error ? e.message : String(e)}`)
  }
}

export function takeMic(): void {
  micUsers += 1
  setType('play-and-record')
}

export function releaseMic(): void {
  micUsers = Math.max(0, micUsers - 1)
  if (micUsers === 0) setType('playback')
}

// Перед play() музыки и голоса: если микрофон свободен — только воспроизведение.
export function ensurePlayback(): void {
  if (micUsers === 0) setType('playback')
}
