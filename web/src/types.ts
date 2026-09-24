export type Status = 'idle' | 'recording' | 'thinking' | 'speaking'

export interface Message {
  id: number
  role: 'user' | 'assistant' | 'error'
  text: string
  tools?: string[]
  // Реплику отменила более новая («стоп», «нет, включи другое») или ✕.
  cancelled?: boolean
  // Просьба ещё выполняется: id хода (для ✕), с какого момента, что делает.
  // background — ушла в фон: не держит «думаю», видна в полоске фоновых задач.
  pending?: { turnId: string; since: number; tool?: string; background?: boolean }
}

// Запись истории чата с ядра (GET /chat/history).
export interface HistoryEntry {
  ts: number
  user: string
  reply: string
  tools: string[]
  cancelled: boolean
  // id хода — по нему находится ответ, событие которого потерялось.
  turn_id?: string | null
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
  // id трека YouTube — только его можно лайкнуть; у радио и файлов null.
  ref?: string | null
  rating?: Rating
  // «волна · вечер», «мои лайки» — откуда трек в очереди.
  origin?: string | null
  from?: FromWhere | null
}

export type Rating = 1 | -1 | null
export type FromWhere = 'liked' | 'cache' | 'net' | 'stream'
export type Mood = 'auto' | 'energetic' | 'calm' | 'focus' | 'sleep' | 'discover'

export interface LibraryTrack {
  ref: string
  title: string
  artist: string | null
  duration: number | null
  addedAt: number
  rating: Rating
}

export interface LibraryStorage {
  likedMb: number
  cacheMb: number
  limitMb: number
}

export interface HiddenArtist {
  artist: string
  dislikes: number
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
  // Ход ушёл в фон: ack — подтверждение («скажу, когда будет готово»), speech — его озвучка.
  background?: boolean
  ack?: string
  // Событие дослано после переподключения SSE — столько секунд назад.
  age?: number
}

// После (пере)подключения SSE — какие ходы устройства ещё идут на ядре.
export interface TurnsEvent {
  type: 'turns'
  active: { id: string; text: string; tool: string | null; background: boolean }[]
}
