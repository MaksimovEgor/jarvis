export type Status = 'idle' | 'recording' | 'thinking' | 'speaking'

export interface Message {
  id: number
  role: 'user' | 'assistant' | 'error'
  text: string
  tools?: string[]
  // Реплику отменила более новая («стоп», «нет, включи другое») или ✕.
  cancelled?: boolean
  // Просьба ещё выполняется: id хода (для ✕), с какого момента, что делает.
  pending?: { turnId: string; since: number; tool?: string }
}

// Запись истории чата с ядра (GET /chat/history).
export interface HistoryEntry {
  ts: number
  user: string
  reply: string
  tools: string[]
  cancelled: boolean
}

// Снимок плеера этого устройства от ядра (SSE /player/events).
export interface PlayerState {
  type: 'state'
  seq: number
  src: string | null
  title: string | null
  start: number
  paused: boolean
  live?: boolean
}

export type PlayerEvent =
  | PlayerState
  | { type: 'seek'; seq: number; position: number }
  | { type: 'announce'; url: string }
  | { type: 'volume'; level: number }
  | TurnEvent

// Прогресс/отмена просьбы этого устройства (app/turns.py).
export interface TurnEvent {
  type: 'turn'
  id: string
  tool?: string
  cancelled?: boolean
}
