import { computed, ref } from 'vue'

import { playerControl, playerEventsUrl, playerReport, type PlayerReport } from '../api'
import type { PlayerEvent, PlayerState, TurnEvent, TurnsEvent } from '../types'
import { SILENCE } from './usePlayer'

const REPORT_EVERY_MS = 10_000
const RECONNECT_MS = 3_000

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

  const hasTrack = computed(() => title.value !== null)

  function report(extra: Omit<PlayerReport, 'seq'> = {}): void {
    lastReport = Date.now()
    void playerReport({ seq, position: audio.currentTime || 0, ...extra })
  }

  // Пауза, которую делаем мы сами (удержание, смена трека), — не путать с
  // паузой с экрана блокировки или отключением Bluetooth-колонки.
  function pauseSelf(): void {
    if (audio.paused) return
    selfPause = true
    audio.pause()
  }

  function apply(): void {
    if (!hasTrack.value) return
    if (wantPlaying.value && held === 0) {
      audio.play().catch(() => (isPlaying.value = false))
    } else {
      pauseSelf()
    }
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
    events.onopen = () => void playerReport({ seq, volume_supported: volumeSupported, hls })
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
  audio.addEventListener('playing', () => {
    isPlaying.value = true
    isLoading.value = false
  })
  audio.addEventListener('waiting', () => (isLoading.value = true))
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
    navigator.mediaSession.metadata = title.value
      ? new MediaMetadata({ title: title.value, artist: 'Джарвис' })
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
    pauseSelf()
  }

  function release(): void {
    held = Math.max(0, held - 1)
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
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') {
      if (!events || events.readyState === EventSource.CLOSED) connect()
    } else if (hasTrack.value) {
      report()
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
    unlock,
    hold,
    release,
    toggle,
    next,
    previous,
    stop,
    seek,
  }
}
