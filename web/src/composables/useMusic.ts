import { computed, ref } from 'vue'

import { clientLog, playerControl, playerEventsUrl, playerReport, rateTrack, type PlayerReport } from '../api'
import type { FromWhere, PlayerEvent, PlayerState, Rating, TrackMeta, TurnEvent, TurnsEvent } from '../types'
import { SILENCE } from './usePlayer'

const REPORT_EVERY_MS = 10_000
const RECONNECT_MS = 3_000
// Столько <audio> может ждать данные, пока должно играть, — дальше просим
// ядро переключить трек (оно же запишет «завис» в журнал для метрики).
const STALL_MS = 10_000

// Громкость <audio> на iPhone не меняется (только кнопками) — сообщаем ядру,
// чтобы «тише» отвечало честно.
function detectVolumeSupport(): boolean {
  const probe = new Audio()
  probe.volume = 0.5
  return probe.volume === 0.5
}

// Музыка на этом устройстве. Ядро присылает желаемое состояние (что играть,
// с какой секунды, пауза), браузер приводит к нему свой <audio> и
// отчитывается позицией и концом трека — очередь и память позиций на ядре.
// Тот же SSE-канал везёт ходы (расшифровка, прогресс, ответ) — их отдаём
// наружу через onTurn/onTurns.
export function useMusic(
  onAnnounce: (url: string) => Promise<void>,
  onTurn: (event: TurnEvent) => void,
  onTurns: (event: TurnsEvent) => void,
) {
  const title = ref<string | null>(null)
  const live = ref(false)
  const wantPlaying = ref(false)
  const isPlaying = ref(false)
  const isLoading = ref(false)
  const position = ref(0)
  const duration = ref(0)
  const trackRef = ref<string | null>(null)
  const rating = ref<Rating>(null)
  // iOS не дал включить звук без касания — ждём тап, сторож ядра не трогает трек.
  const blocked = ref(false)
  const origin = ref<string | null>(null)
  const from = ref<FromWhere | null>(null)
  const meta = ref<TrackMeta | null>(null)

  const audio = new Audio()
  audio.preload = 'auto'
  const volumeSupported = detectVolumeSupport()
  // Safari: длинное с YouTube играет только как HLS — ядро выберет формат.
  const hls = audio.canPlayType('application/vnd.apple.mpegurl') !== ''

  let seq = 0
  let held = 0
  let pendingStart = 0
  let lastReport = 0
  let unlocked = false
  let unlocking = false
  let selfPause = false
  let events: EventSource | null = null
  // id последнего события — при переподключении ядро дошлёт пропущенное.
  let lastEventId = ''
  let reconnectTimer: number | null = null
  let stallTimer: number | null = null

  const hasTrack = computed(() => title.value !== null)

  function isVisible(): boolean {
    return document.visibilityState === 'visible'
  }

  function report(extra: Omit<PlayerReport, 'seq'> = {}): void {
    lastReport = Date.now()
    void playerReport({ seq, position: audio.currentTime || 0, ...extra })
  }

  // Пауза, которую делаем мы сами (удержание, смена трека), — не путать с
  // паузой с экрана блокировки или отключением Bluetooth-колонки.
  function pauseSelf(): void {
    clearStall()
    if (audio.paused) return
    selfPause = true
    audio.pause()
  }

  function apply(): void {
    if (!hasTrack.value) return
    if (wantPlaying.value && held === 0) {
      audio
        .play()
        .then(() => setBlocked(false))
        .catch((e: unknown) => {
          isPlaying.value = false
          if (e instanceof DOMException && e.name === 'NotAllowedError') setBlocked(true)
        })
    } else {
      pauseSelf()
    }
  }

  function setBlocked(value: boolean): void {
    if (blocked.value === value) return
    blocked.value = value
    void playerReport({ seq, blocked: value })
  }

  function onState(state: PlayerState): void {
    if (state.seq !== seq) {
      seq = state.seq
      pauseSelf()
      title.value = state.title
      live.value = Boolean(state.live)
      position.value = state.start
      duration.value = 0
      if (state.src) {
        pendingStart = state.start
        isLoading.value = true
        audio.src = state.src
      } else {
        audio.removeAttribute('src')
        audio.load()
      }
    }
    trackRef.value = state.ref ?? null
    rating.value = state.rating ?? null
    origin.value = state.origin ?? null
    from.value = state.from ?? null
    meta.value = state.title
      ? {
          song: state.song ?? state.title,
          artist: state.artist ?? null,
          cover: state.cover ?? null,
          coverCrop: state.coverCrop ?? true,
          service: state.service ?? null,
          quality: state.quality ?? null,
          note: state.note ?? null,
          upcoming: state.upcoming ?? [],
        }
      : null
    wantPlaying.value = !state.paused && state.src !== null
    updateSession()
    apply()
  }

  function onEvent(event: PlayerEvent): void {
    switch (event.type) {
      case 'state':
        onState(event)
        break
      case 'seek':
        if (event.seq === seq) audio.currentTime = event.position
        break
      case 'volume':
        if (volumeSupported) audio.volume = event.level / 100
        break
      case 'announce':
        hold()
        void onAnnounce(event.url).finally(release)
        break
      case 'turn':
        onTurn(event)
        break
      case 'turns':
        onTurns(event)
        break
    }
  }

  function connect(): void {
    if (reconnectTimer !== null) {
      clearTimeout(reconnectTimer)
      reconnectTimer = null
    }
    events?.close()
    events = new EventSource(playerEventsUrl(lastEventId))
    events.onmessage = (e) => {
      if (e.lastEventId) lastEventId = e.lastEventId
      onEvent(JSON.parse(e.data) as PlayerEvent)
    }
    events.onopen = () => void playerReport({ seq, volume_supported: volumeSupported, hls, visible: isVisible() })
    // Браузер переподключается сам, но после ответа 5xx (ядро рестартует)
    // сдаётся и закрывает канал — тогда пробуем снова сами.
    events.onerror = () => {
      if (events?.readyState !== EventSource.CLOSED || reconnectTimer !== null) return
      reconnectTimer = window.setTimeout(() => {
        reconnectTimer = null
        if (document.visibilityState === 'visible') connect()
      }, RECONNECT_MS)
    }
  }

  // --- события <audio> ------------------------------------------------------

  audio.addEventListener('loadedmetadata', () => {
    duration.value = Number.isFinite(audio.duration) ? audio.duration : 0
    if (pendingStart > 0) {
      audio.currentTime = pendingStart
      pendingStart = 0
    }
  })
  function clearStall(): void {
    if (stallTimer !== null) {
      clearTimeout(stallTimer)
      stallTimer = null
    }
  }

  // Сеть моргнула и буфер кончился — не молчим бесконечно, как «зависшая колонка».
  function watchStall(): void {
    clearStall()
    const stalledSeq = seq
    stallTimer = window.setTimeout(() => {
      stallTimer = null
      if (stalledSeq === seq && hasTrack.value && !live.value && wantPlaying.value && held === 0) {
        clientLog(`завис: ${title.value}`)
        report({ stalled: true })
      }
    }, STALL_MS)
  }

  audio.addEventListener('playing', () => {
    isPlaying.value = true
    isLoading.value = false
    clearStall()
  })
  audio.addEventListener('waiting', () => {
    isLoading.value = true
    if (wantPlaying.value && held === 0) watchStall()
  })
  audio.addEventListener('stalled', () => {
    if (wantPlaying.value && held === 0 && !isPlaying.value) watchStall()
  })
  audio.addEventListener('pause', () => {
    isPlaying.value = false
    if (selfPause) {
      selfPause = false
      return
    }
    if (!hasTrack.value || audio.ended) return
    // Пауза снаружи: экран блокировки, колонка, звонок.
    wantPlaying.value = false
    report({ paused: true })
    updateSession()
  })
  audio.addEventListener('play', () => {
    if (!hasTrack.value || wantPlaying.value || unlocking) return
    wantPlaying.value = true
    report({ paused: false })
    updateSession()
  })
  audio.addEventListener('timeupdate', () => {
    position.value = audio.currentTime
    if (hasTrack.value && Date.now() - lastReport > REPORT_EVERY_MS) {
      report()
      updatePositionState()
    }
  })
  audio.addEventListener('ended', () => {
    if (hasTrack.value) report({ ended: true })
  })
  audio.addEventListener('error', () => {
    if (hasTrack.value && audio.getAttribute('src')) {
      isLoading.value = false
      report({ error: audio.error?.message || `код ${audio.error?.code ?? '?'}` })
    }
  })

  // --- Media Session: экран блокировки, наушники, Bluetooth-колонка --------

  function updateSession(): void {
    if (!('mediaSession' in navigator)) return
    const m = meta.value
    navigator.mediaSession.metadata = title.value
      ? new MediaMetadata({
          title: m?.song ?? title.value,
          artist: m?.artist ?? 'Джарвис',
          artwork: m?.cover ? [{ src: new URL(m.cover, location.href).href, sizes: '480x360', type: 'image/jpeg' }] : [],
        })
      : null
    navigator.mediaSession.playbackState = !hasTrack.value ? 'none' : wantPlaying.value ? 'playing' : 'paused'
  }

  function updatePositionState(): void {
    if (!('mediaSession' in navigator) || live.value || !duration.value) return
    try {
      navigator.mediaSession.setPositionState({
        duration: duration.value,
        position: Math.min(audio.currentTime, duration.value),
        playbackRate: 1,
      })
    } catch {
      // Safari бывает строг к значениям — не критично
    }
  }

  if ('mediaSession' in navigator) {
    const session = navigator.mediaSession
    session.setActionHandler('play', () => resume())
    session.setActionHandler('pause', () => pause())
    session.setActionHandler('nexttrack', () => next())
    session.setActionHandler('previoustrack', () => previous())
    session.setActionHandler('seekbackward', () => seekBy(-15))
    session.setActionHandler('seekforward', () => seekBy(30))
    session.setActionHandler('seekto', (d) => d.seekTime !== undefined && seek(d.seekTime))
  }

  // --- управление -----------------------------------------------------------

  // iOS: первый play() должен быть внутри жеста. Разблокируем элемент без
  // звука на первом тапе — дальше ядро может включать музыку само.
  function unlock(): void {
    if (unlocked) return
    const hadSrc = Boolean(audio.getAttribute('src'))
    if (!hadSrc) audio.src = SILENCE
    audio.muted = true
    unlocking = true
    audio
      .play()
      .then(() => {
        unlocked = true
        if (!hadSrc || !wantPlaying.value || held > 0) pauseSelf()
        else setBlocked(false)
      })
      .catch(() => undefined)
      .finally(() => {
        audio.muted = false
        unlocking = false
      })
  }

  // Пока пользователь говорит и Джарвис отвечает — музыка молчит. Счётчик:
  // объявление таймера может прийти посреди разговора.
  function hold(): void {
    held += 1
    if (held === 1 && hasTrack.value) void playerReport({ seq, held: true })
    pauseSelf()
  }

  function release(): void {
    const was = held
    held = Math.max(0, held - 1)
    if (was > 0 && held === 0 && hasTrack.value) void playerReport({ seq, held: false })
    apply()
  }

  function pause(): void {
    wantPlaying.value = false
    pauseSelf()
    updateSession()
    void playerControl('pause').catch(() => undefined)
  }

  function resume(): void {
    wantPlaying.value = true
    apply()
    updateSession()
    void playerControl('resume').catch(() => undefined)
  }

  // Повторить play() внутри жеста пользователя (после блокировки iOS).
  function retry(): void {
    apply()
  }

  function toggle(): void {
    if (wantPlaying.value) pause()
    else resume()
  }

  function next(): void {
    void playerControl('next').catch(() => undefined)
  }

  function previous(): void {
    void playerControl('previous').catch(() => undefined)
  }

  function stop(): void {
    void playerControl('stop').catch(() => undefined)
  }

  // Оценка сразу на экране, ядро подтвердит снимком состояния. Дизлайк
  // переключает трек на ядре — новый трек придёт тем же снимком.
  function like(): void {
    if (!trackRef.value) return
    const liked = rating.value === 1
    rating.value = liked ? null : 1
    void rateTrack(liked ? 'none' : 'like', trackRef.value).catch(() => (rating.value = liked ? 1 : null))
  }

  function dislike(): void {
    if (!trackRef.value) return
    rating.value = -1
    void rateTrack('dislike', trackRef.value).catch(() => (rating.value = null))
  }

  // «Отменить» после дизлайка: трек уже переключён, просто снимаем оценку.
  function unrate(ref: string): void {
    void rateTrack('none', ref).catch(() => undefined)
    if (trackRef.value === ref) rating.value = null
  }

  function seek(to: number): void {
    if (live.value) return
    audio.currentTime = Math.max(0, duration.value ? Math.min(to, duration.value - 1) : to)
    report()
  }

  function seekBy(delta: number): void {
    seek(audio.currentTime + delta)
  }

  // iOS рвёт SSE у свёрнутой вкладки; при возвращении — переподключаемся и
  // получаем свежий снимок. При сворачивании — сохранить позицию.
  // Видимость — ядру: свёрнутой вкладке готовое уходит пушем.
  document.addEventListener('visibilitychange', () => {
    if (isVisible()) {
      if (!events || events.readyState === EventSource.CLOSED) connect()
      else void playerReport({ seq, visible: true })
    } else {
      void playerReport({ seq, position: audio.currentTime || 0, visible: false })
    }
  })

  connect()

  return {
    title,
    live,
    wantPlaying,
    isPlaying,
    isLoading,
    position,
    duration,
    hasTrack,
    trackRef,
    rating,
    origin,
    from,
    meta,
    blocked,
    like,
    dislike,
    unrate,
    unlock,
    retry,
    hold,
    release,
    toggle,
    next,
    previous,
    stop,
    seek,
    seekBy,
  }
}
