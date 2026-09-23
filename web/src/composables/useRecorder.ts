import { ref } from 'vue'

import { audioContext } from './useEarcon'

const PREFERRED_MIME = ['audio/webm;codecs=opus', 'audio/mp4', 'audio/ogg;codecs=opus']

// Автостоп по тишине (как у Алисы): речь громче шума в SPEECH_RATIO раз;
// после речи SILENCE_MS тишины — команда закончилась.
const TICK_MS = 100
const CALIBRATE_MS = 300
const SPEECH_RATIO = 2.5
const MIN_SPEECH_LEVEL = 0.015
const SILENCE_MS = 1200
const NO_SPEECH_MS = 6000
const MAX_MS = 15000

export interface AutoStop {
  onEnd: () => void // договорил или вышло время
  onNoSpeech: () => void // так ничего и не сказал
}

export function useRecorder() {
  const isRecording = ref(false)
  let recorder: MediaRecorder | null = null
  let chunks: Blob[] = []
  let vadTimer: number | null = null

  function watchSilence(stream: MediaStream, auto: AutoStop): void {
    const ctx = audioContext()
    const analyser = ctx.createAnalyser()
    analyser.fftSize = 1024
    ctx.createMediaStreamSource(stream).connect(analyser)
    const buf = new Float32Array(analyser.fftSize)
    const startedAt = Date.now()
    let noise = 0
    let noiseTicks = 0
    let heardAt = 0
    let lastVoiceAt = 0

    vadTimer = window.setInterval(() => {
      analyser.getFloatTimeDomainData(buf)
      const level = Math.sqrt(buf.reduce((sum, v) => sum + v * v, 0) / buf.length)
      const now = Date.now()
      const elapsed = now - startedAt
      if (elapsed < CALIBRATE_MS) {
        noise = (noise * noiseTicks + level) / ++noiseTicks
        return
      }
      if (level > Math.max(MIN_SPEECH_LEVEL, noise * SPEECH_RATIO)) {
        heardAt ||= now
        lastVoiceAt = now
      }
      if (heardAt && now - lastVoiceAt > SILENCE_MS) {
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
  }

  async function start(auto?: AutoStop): Promise<void> {
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    })
    const mimeType = PREFERRED_MIME.find((m) => MediaRecorder.isTypeSupported(m))
    recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined)
    chunks = []
    recorder.ondataavailable = (e) => {
      if (e.data.size > 0) chunks.push(e.data)
    }
    recorder.start()
    isRecording.value = true
    if (auto) watchSilence(stream, auto)
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
        isRecording.value = false
        recorder = null
        resolve(new Blob(chunks, { type: rec.mimeType }))
      }
      rec.stop()
    })
  }

  return { isRecording, start, stop }
}
