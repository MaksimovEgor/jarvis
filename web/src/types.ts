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
  | TurnsEvent

// Ход этого устройства (app/turns.py): ответ приходит сюда, а не в POST.
// Одно событие — одно из полей: расшифровка, инструмент или итог хода.
export interface TurnEvent {
  type: 'turn'
  id: string
  transcript?: string
  tool?: string
  // Итог: ответ (и озвучка по id), отмена или ошибка.
  reply?: string
  tools?: string[]
  speech?: { id: string; count: number } | null
  cancelled?: boolean
  error?: string
  // Событие дослано после переподключения SSE — столько секунд назад.
  age?: number
}

// После (пере)подключения SSE — какие ходы устройства ещё идут на ядре.
export interface TurnsEvent {
  type: 'turns'
  active: { id: string; text: string; tool: string | null }[]
}
