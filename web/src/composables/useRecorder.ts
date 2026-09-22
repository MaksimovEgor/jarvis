import { ref } from 'vue'

const PREFERRED_MIME = ['audio/webm;codecs=opus', 'audio/mp4', 'audio/ogg;codecs=opus']

export function useRecorder() {
  const isRecording = ref(false)
  let recorder: MediaRecorder | null = null
  let chunks: Blob[] = []

  async function start(): Promise<void> {
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
  }

  function stop(): Promise<Blob> {
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
