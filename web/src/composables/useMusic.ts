import { computed, ref } from 'vue'

import { clientLog, playerControl, playerEventsUrl, playerReport, rateTrack, type PlayerReport } from '../api'
import type {
  FromWhere,
  PlayerEvent,
  PlayerState,
  Rating,
  TrackMeta,
  TurnEvent,
  TurnsEvent,
  UpcomingTrack,
} from '../types'
import { ensurePlayback } from './useAudioSession'
import { SILENCE } from './usePlayer'

const REPORT_EVERY_MS = 10_000
const RECONNECT_MS = 3_000
// Столько <audio> может ждать данные, пока должно играть, — дальше просим
// ядро переключить трек (оно же запишет «завис» в журнал для метрики).
const STALL_MS = 10_000
// iOS иногда молча не начинает новый трек: play() не падает, но и 'waiting'
// нет — сторож выше не запускается, экран показывает трек в тишине. Столько
// ждём 'playing' после play(); потом одна перезагрузка, потом — как завис.
const START_MS = 6_000
// Переключение показано сразу, до ответа ядра. Ядро ответило, а новый трек
// за столько не пришёл — значит, не переключилось («дальше ничего нет»): откат.
const CONFIRM_MS = 2_000
// Следующего ещё нет (волна подбирает): столько ждём его, потом откат.
// Чуть больше, чем ждёт ядро (player.REFILL_WAIT), — запрос ответит раньше.
const PICK_WAIT_MS = 17_000
// Что на экране, пока следующий трек ещё подбирается.
const PICKING = 'Подбираю…'

type Neighbor = UpcomingTrack & { src: string | null }

// Переключение, которое экран уже показал, а ядро ещё не подтвердило.
interface Pending {
  // seq до нажатия и тот, которым ядро подтвердит последнее (каждое «дальше» — +1).
  base: number
  target: number
  // Сразу заиграли этот src (файл уже на ядре) — ядро пришлёт его же, не перезагружать.
  src: string | null
  // Звук уже не старого трека — при откате вернуть его с этой позиции.
  audioChanged: boolean
  position: number
  timer: number | null
  // Ядро ещё не знает, что играть следующим, — экран показывает «Подбираю…».
  unknown: boolean
  // Что ответило ядро: покажем, если переключение так и не случилось.
  answer: string | null
}

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
  // «Джарвис, покажи текст» — ядро просит экран открыть текст/очередь.
  const uiRequest = ref<{ open: 'lyrics' | 'queue'; at: number } | null>(null)
  // Следующий трек ещё подбирается: экран уже ушёл вперёд, трека пока нет.
  const searching = ref(false)
  // Короткое сообщение наверх (App показывает тостом).
  const notice = ref<{ text: string; at: number } | null>(null)

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
  // seq трека, который уже перезагружали из-за молчаливого старта.
  let reloadedSeq = -1
  // Что играет <audio> — адрес ядра (media/file/…), даже когда сам элемент
  // играет копию из памяти (blob:).
  let src: string | null = null
  let blobInUse: string | null = null
  // Последний снимок ядра — из него соседи трека и откат.
  let lastState: PlayerState | null = null
  let pending: Pending | null = null
  // Файл следующего, скачанный заранее: по «дальше» и в конце трека — без сети.
  let preload: { src: string; url: string | null; abort: AbortController } | null = null

  const hasTrack = computed(() => title.value !== null)

  function isVisible(): boolean {
    return document.visibilityState === 'visible'
  }

  function report(extra: Omit<PlayerReport, 'seq'> = {}): void {
    // Пока ядро не подтвердило переключение, seq ещё старый — позиция и конец
    // нового трека приписались бы старому.
    if (pending) return
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
      ensurePlayback()
      if (!isPlaying.value) watchStart()
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

  // --- звук: адрес ядра или заранее скачанная копия ------------------------

  function setSource(next: string | null): void {
    const oldBlob = blobInUse
    const url = next !== null && preload?.src === next ? preload.url : null
    src = next
    blobInUse = url
    if (next === null) {
      audio.removeAttribute('src')
      audio.load()
    } else {
      audio.src = url ?? next
    }
    if (oldBlob && oldBlob !== preload?.url) URL.revokeObjectURL(oldBlob)
  }

  function dropPreload(): void {
    if (!preload) return
    preload.abort.abort()
    if (preload.url && preload.url !== blobInUse) URL.revokeObjectURL(preload.url)
    preload = null
  }

  // Качаем файл следующего целиком в память: iOS атрибут preload у
  // неиграющего <audio> игнорирует, а копия из памяти стартует мгновенно.
  function preloadNext(state: PlayerState): void {
    const next = state.next
    const want = next && state.upcoming?.[0]?.ref === next.ref ? next.src : null
    if (preload?.src === want) return
    dropPreload()
    if (!want) return
    const entry = { src: want, url: null as string | null, abort: new AbortController() }
    preload = entry
    fetch(want, { signal: entry.abort.signal })
      .then((resp) => {
        if (!resp.ok) throw new Error(`Сервер ответил ${resp.status}`)
        return resp.blob()
      })
      .then((blob) => {
        if (preload === entry) entry.url = URL.createObjectURL(blob)
      })
      .catch(() => undefined)
  }

  // Файл следующего уже на ядре — по «дальше» можно играть, не дожидаясь ответа.
  function readySrc(ref: string): string | null {
    const next = lastState?.next
    return next && next.ref === ref ? next.src : null
  }

  function onState(state: PlayerState): void {
    lastState = state
    if (pending) {
      // Снимки старого трека и промежуточных «дальше» — экран уже впереди.
      if (state.seq < pending.target) {
        // Волна досыпала треки: в «Подбираю…» уже можно показать, что дальше.
        if (pending.unknown && meta.value) {
          meta.value = { ...meta.value, upcoming: state.upcoming ?? [] }
        }
        preloadNext(state)
        return
      }
      const adopt = pending.src !== null && state.src === pending.src
      settle()
      if (adopt) {
        // Уже играет то, что выбрало ядро, — не перезагружать.
        seq = state.seq
        if (state.start > 1) audio.currentTime = state.start
      }
    }
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
      }
      setSource(state.src)
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
    preloadNext(state)
  }

  // --- переключение сразу, ядро подтвердит ----------------------------------

  // Показать соседний трек и, если его файл уже на ядре, сразу играть.
  function jump(to: Neighbor, upcoming: UpcomingTrack[]): void {
    const was = meta.value
    if (!pending) {
      pending = {
        base: seq, target: seq, src: null, audioChanged: false,
        position: audio.currentTime || 0, timer: null, unknown: false, answer: null,
      }
    }
    pending.target += 1
    pending.unknown = false
    searching.value = false
    title.value = to.song
    trackRef.value = trackRef.value !== null ? to.ref : null
    rating.value = null
    from.value = null
    position.value = 0
    duration.value = 0
    meta.value = {
      song: to.song,
      artist: to.artist,
      cover: to.cover,
      coverCrop: to.coverCrop ?? true,
      service: was?.service ?? null,
      quality: null,
      note: null,
      upcoming,
    }
    isLoading.value = true
    wantPlaying.value = true
    pauseSelf()
    // Только на первом шаге: через два «дальше» файл заранее не готов.
    const ready = pending.target === seq + 1 ? to.src : null
    pending.src = ready
    if (ready) {
      pending.audioChanged = true
      pendingStart = 0
      setSource(ready)
      apply()
    }
    updateSession()
  }

  // Следующего ещё нет (волна подбирает) — всё равно реагируем сразу: карточка
  // улетает, на её месте «Подбираю…». Трек придёт снимком ядра; не придёт —
  // откат и сообщение.
  function askNext(): void {
    if (!pending) {
      pending = {
        base: seq, target: seq, src: null, audioChanged: false,
        position: audio.currentTime || 0, timer: null, unknown: true, answer: null,
      }
    }
    pending.target += 1
    pending.unknown = true
    searching.value = true
    title.value = PICKING
    trackRef.value = null
    rating.value = null
    from.value = null
    position.value = 0
    duration.value = 0
    meta.value = {
      song: PICKING,
      artist: null,
      cover: null,
      coverCrop: true,
      service: null,
      quality: null,
      note: null,
      upcoming: [],
    }
    isLoading.value = true
    wantPlaying.value = true
    pauseSelf()
    updateSession()
  }

  function settle(): void {
    if (pending?.timer) clearTimeout(pending.timer)
    pending = null
    searching.value = false
  }

  // Ядро не переключило (или запрос упал) — вернуть то, что оно считает текущим.
  // failed=false — экран откатываем сами («назад» во время подбора): не пугать.
  function revert(failed = true): void {
    const was = pending
    if (!was || !lastState) return settle()
    if (failed && was.unknown) {
      notice.value = { text: was.answer ?? 'Не получилось подобрать следующий трек', at: Date.now() }
    }
    settle()
    if (was.audioChanged) {
      seq = -1 // заставить onState перезагрузить звук
      onState(lastState.seq === was.base ? { ...lastState, start: was.position } : lastState)
      return
    }
    if (lastState.seq === seq) {
      // Звук старого трека на месте — вернуть только экран.
      title.value = lastState.title
      position.value = audio.currentTime || 0
      duration.value = Number.isFinite(audio.duration) ? audio.duration : 0
      isLoading.value = false
    }
    onState(lastState)
  }

  // Ждём снимок ядра: запрос упал — откат сразу, ответ пришёл — ещё CONFIRM_MS.
  // guard — страховка на время подбора: экран уже ушёл вперёд, и без ответа
  // он бы висел в «Подбираю…» сколько угодно.
  function confirm(request: Promise<string | null>, guard = 0): void {
    if (guard > 0 && pending) {
      if (pending.timer) clearTimeout(pending.timer)
      pending.timer = window.setTimeout(revert, guard)
    }
    request.then(
      (answer) => {
        if (!pending) return
        pending.answer = answer
        if (pending.timer) clearTimeout(pending.timer)
        pending.timer = window.setTimeout(revert, CONFIRM_MS)
      },
      () => revert(),
    )
  }

  function current(): UpcomingTrack | null {
    const m = meta.value
    if (!m || !title.value) return null
    return { ref: trackRef.value ?? title.value, song: m.song, artist: m.artist, cover: m.cover, coverCrop: m.coverCrop }
  }

  function onEvent(event: PlayerEvent): void {
    switch (event.type) {
      case 'state':
        onState(event)
        break
      case 'seek':
        if (event.seq === seq && !pending) audio.currentTime = event.position
        break
      case 'volume':
        if (volumeSupported) audio.volume = event.level / 100
        break
      case 'ui':
        uiRequest.value = { open: event.open, at: Date.now() }
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

  // Сторож старта: после play() должно прийти 'playing'. Не пришло — один
  // раз перезагружаем с той же позиции (то же, что «следующий → назад»),
  // не помогло — просим ядро переключить трек.
  function watchStart(): void {
    clearStall()
    const startSeq = seq
    stallTimer = window.setTimeout(() => {
      stallTimer = null
      if (startSeq !== seq || !hasTrack.value || !wantPlaying.value || held > 0) return
      if (isPlaying.value || blocked.value || !audio.getAttribute('src')) return
      if (reloadedSeq !== seq) {
        reloadedSeq = seq
        clientLog(`не стартовал, перезагружаю: ${title.value}`)
        if (!live.value) pendingStart = audio.currentTime || position.value
        audio.load()
        apply()
      } else if (!live.value) {
        clientLog(`завис на старте: ${title.value}`)
        report({ stalled: true })
      }
    }, START_MS)
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
    if (blobInUse && src) {
      // Копия из памяти не заиграла — берём тот же файл у ядра.
      clientLog(`копия не играет, беру у ядра: ${title.value}`)
      const at = audio.currentTime || 0
      dropPreload()
      pendingStart = at
      setSource(src)
      apply()
      return
    }
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
    const upcoming = meta.value?.upcoming ?? []
    if (upcoming[0]) {
      jump({ ...upcoming[0], src: readySrc(upcoming[0].ref) }, upcoming.slice(1))
      confirm(playerControl('next'))
      return
    }
    if (!hasTrack.value) {
      confirm(playerControl('next'))
      return
    }
    // Следующего ещё нет (волна подбирает) — не молчим: «Подбираю…» сразу.
    askNext()
    confirm(playerControl('next'), PICK_WAIT_MS)
  }

  function previous(): void {
    // «Подбираю…» отменяем: на экран возвращается то, что действительно играет.
    if (pending?.unknown) revert(false)
    const prev = lastState?.prev
    const now = current()
    if (pending) pending.target += 1 // после «дальше» назад — просто ждём ядро
    else if (prev && now) jump(prev, [now, ...(meta.value?.upcoming ?? [])])
    confirm(playerControl('previous'))
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

  // Дизлайк переключает трек на ядре — показываем следующий сразу.
  function dislike(): void {
    const ref = trackRef.value
    if (!ref) return
    rating.value = -1
    const upcoming = meta.value?.upcoming ?? []
    if (!pending && upcoming[0]) {
      jump({ ...upcoming[0], src: readySrc(upcoming[0].ref) }, upcoming.slice(1))
      confirm(rateTrack('dislike', ref).then((answer) => answer.text))
    } else {
      void rateTrack('dislike', ref).catch(() => (rating.value = null))
    }
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
    uiRequest,
    searching,
    notice,
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
