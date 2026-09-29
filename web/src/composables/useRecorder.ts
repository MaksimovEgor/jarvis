import { ref } from 'vue'

import { releaseMic, takeMic } from './useAudioSession'
import { audioContext } from './useEarcon'

const PREFERRED_MIME = ['audio/webm;codecs=opus', 'audio/mp4', 'audio/ogg;codecs=opus']

// Автостоп по тишине (как у Алисы): речь громче шума в SPEECH_RATIO раз;
// после речи SILENCE_MS тишины — команда закончилась. Паузы щедрые: при 1,2 с
// Джарвис обрывал на полуслове, стоило задуматься посреди фразы.
const TICK_MS = 100
const CALIBRATE_MS = 300
const SPEECH_RATIO = 2.5
const MIN_SPEECH_LEVEL = 0.015
// Шум калибруется по первым 300 мс — если человек уже говорит, «шум»
// получается громким и тихая речь дальше не распознаётся. Потолок.
const MAX_NOISE_LEVEL = 0.01
const SILENCE_MS = 1800
// Сказал совсем немного («Джарвис, включи…») — наверняка подбирает слова.
const SHORT_SPEECH_MS = 1500
const SHORT_SILENCE_MS = 2500
const NO_SPEECH_MS = 6000
// Было 15 с — длинные вопросы обрезались посередине.
const MAX_MS = 45000

export interface AutoStop {
  onEnd: () => void // договорил или вышло время
  onNoSpeech: () => void // так ничего и не сказал
}

export function useRecorder() {
  const isRecording = ref(false)
  let recorder: MediaRecorder | null = null
  let chunks: Blob[] = []
  let vadTimer: number | null = null
  let source: MediaStreamAudioSourceNode | null = null
  let vadContext: AudioContext | null = null

  // Анализ тишины — в своём AudioContext, который закрывается после записи:
  // общий (сигналы) с подключённым микрофоном держал iOS в режиме «звонка» —
  // музыка потом тихая. Свой контекст вне жеста может не запуститься —
  // тогда общий, он разблокирован на тапах.
  async function vadAudioContext(): Promise<AudioContext> {
    const own = new AudioContext()
    await Promise.race([own.resume(), new Promise((r) => setTimeout(r, 300))])
    if (own.state === 'running') {
      vadContext = own
      return own
    }
    void own.close()
    return audioContext()
  }

  async function watchSilence(stream: MediaStream, auto: AutoStop): Promise<void> {
    const ctx = await vadAudioContext()
    // Пока контекст запускался, запись могли остановить.
    if (recorder?.stream !== stream) {
      stopWatching()
      return
    }
    const analyser = ctx.createAnalyser()
    analyser.fftSize = 1024
    source = ctx.createMediaStreamSource(stream)
    source.connect(analyser)
    const buf = new Float32Array(analyser.fftSize)
    const startedAt = Date.now()
    let noise = 0
    let noiseTicks = 0
    let heardAt = 0
    let lastVoiceAt = 0
    let voicedMs = 0

    vadTimer = window.setInterval(() => {
      analyser.getFloatTimeDomainData(buf)
      const level = Math.sqrt(buf.reduce((sum, v) => sum + v * v, 0) / buf.length)
      const now = Date.now()
      const elapsed = now - startedAt
      if (elapsed < CALIBRATE_MS) {
        noise = Math.min(MAX_NOISE_LEVEL, (noise * noiseTicks + level) / ++noiseTicks)
        return
      }
      if (level > Math.max(MIN_SPEECH_LEVEL, noise * SPEECH_RATIO)) {
        heardAt ||= now
        lastVoiceAt = now
        voicedMs += TICK_MS
      }
      const silenceLimit = voicedMs < SHORT_SPEECH_MS ? SHORT_SILENCE_MS : SILENCE_MS
      if (heardAt && now - lastVoiceAt > silenceLimit) {
        stopWatching()
        auto.onEnd()
      } else if (!heardAt && elapsed > NO_SPEECH_MS) {
        stopWatching()
        auto.onNoSpeech()
      } else if (elapsed > MAX_MS) {
        stopWatching()
        auto.onEnd()
      }
    }, TICK_MS)
  }

  function stopWatching(): void {
    if (vadTimer !== null) {
      clearInterval(vadTimer)
      vadTimer = null
    }
    // Микрофон, подключённый к общему AudioContext, держит iOS в режиме
    // записи — Bluetooth не возвращается из «гарнитуры» в стерео.
    source?.disconnect()
    source = null
    void vadContext?.close()
    vadContext = null
  }

  async function start(auto?: AutoStop): Promise<void> {
    takeMic()
    let stream: MediaStream
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      })
    } catch (e) {
      releaseMic()
      throw e
    }
    try {
      const mimeType = PREFERRED_MIME.find((m) => MediaRecorder.isTypeSupported(m))
      recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined)
      chunks = []
      recorder.ondataavailable = (e) => {
        if (e.data.size > 0) chunks.push(e.data)
      }
      recorder.start()
    } catch (e) {
      // Иначе микрофон остаётся открытым и iOS не выходит из режима «звонка».
      stream.getTracks().forEach((t) => t.stop())
      recorder = null
      releaseMic()
      throw e
    }
    isRecording.value = true
    if (auto) await watchSilence(stream, auto)
  }

  function stop(): Promise<Blob> {
    stopWatching()
    return new Promise((resolve, reject) => {
      const rec = recorder
      if (!rec) {
        reject(new Error('Запись не идёт'))
        return
      }
      rec.onstop = () => {
        // Отпускаем микрофон, иначе на телефоне висит индикатор записи.
        rec.stream.getTracks().forEach((t) => t.stop())
        releaseMic()
        isRecording.value = false
        recorder = null
        resolve(new Blob(chunks, { type: rec.mimeType }))
      }
      rec.stop()
    })
  }

  return { isRecording, start, stop }
}
