import { ref } from 'vue'

import { clientLog } from '../api'

const ENABLED_KEY = 'jarvis:wake'

// Как распознаватель пишет «Джарвис» — вариантов несколько.
const WAKE = /(джарвис|джервис|жарвис|джарвиз|джарвес|jarvis)[\s,.!?:;—-]*/i
// iOS заканчивает сессию распознавания сам (тишина, ~минута) — перезапуск.
const RESTART_MS = 300
// Слово услышали, а фраза так и не закончилась — отпускаем через столько.
const ALERT_TIMEOUT_MS = 8000

export interface WakeHandlers {
  onAlert: () => void // услышал «Джарвис» — сигнал, приглушить музыку
  onWake: (command: string) => void // фраза закончилась: команда или '' (только имя)
  onCancel: () => void // тревога ложная — распознаватель передумал
}

// «Джарвис» при открытой вкладке — через встроенное распознавание речи
// браузера (на iPhone это распознавание Apple). Слушает, только пока
// Джарвис свободен: App зовёт resume()/pause() по статусу.
export function useWakeWord(handlers: WakeHandlers) {
  const Recognition = window.SpeechRecognition ?? window.webkitSpeechRecognition
  const supported = Boolean(Recognition)
  const enabled = ref(supported && readPref())
  const listening = ref(false)
  // Браузер не дал начать без жеста/разрешения — нужен тап.
  const blocked = ref(false)

  let rec: SpeechRecognitionLike | null = null
  let wanted = false
  let alerted = false
  let alertTimer: number | null = null

  function clearAlert(): void {
    alerted = false
    if (alertTimer !== null) {
      clearTimeout(alertTimer)
      alertTimer = null
    }
  }

  function begin(): void {
    if (!Recognition || !enabled.value || !wanted || rec) return
    const r = new Recognition()
    rec = r
    r.lang = 'ru-RU'
    r.continuous = true
    r.interimResults = true

    r.onstart = () => {
      clientLog('wake: onstart')
      listening.value = true
      blocked.value = false
    }
    r.onresult = (e) => {
      for (let i = e.resultIndex; i < e.results.length; i++) {
        const result = e.results[i]
        const text = result[0].transcript
        const match = WAKE.exec(text)
        clientLog(`wake: result ${result.isFinal ? 'final' : 'interim'} "${text.slice(0, 80)}" match=${Boolean(match)}`)
        if (match && !alerted) {
          alerted = true
          handlers.onAlert()
          alertTimer = window.setTimeout(() => {
            clearAlert()
            handlers.onCancel()
          }, ALERT_TIMEOUT_MS)
        }
        if (!result.isFinal || !alerted) continue
        clearAlert()
        if (match) {
          pause()
          handlers.onWake(text.slice(match.index + match[0].length).trim())
        } else {
          handlers.onCancel()
        }
        return
      }
    }
    r.onerror = (e) => {
      clientLog(`wake: onerror ${e.error}`)
      if (e.error === 'not-allowed' || e.error === 'service-not-allowed') {
        blocked.value = true
      }
    }
    r.onend = () => {
      clientLog(`wake: onend wanted=${wanted} blocked=${blocked.value}`)
      listening.value = false
      if (rec === r) rec = null
      if (wanted && !blocked.value) window.setTimeout(begin, RESTART_MS)
    }

    try {
      clientLog('wake: start()')
      r.start()
    } catch (e) {
      clientLog(`wake: start() threw ${e instanceof Error ? e.message : String(e)}`)
      rec = null
      blocked.value = true
    }
  }

  function resume(): void {
    wanted = true
    begin()
  }

  function pause(): void {
    wanted = false
    if (alerted) {
      clearAlert()
      handlers.onCancel()
    }
    rec?.abort()
  }

  // Включение — всегда по тапу: заодно это жест, который iOS требует для старта.
  function setEnabled(value: boolean): void {
    clientLog(`wake: setEnabled ${value} supported=${supported}`)
    enabled.value = value
    blocked.value = false
    try {
      localStorage.setItem(ENABLED_KEY, value ? '1' : '0')
    } catch {
      // приватный режим — настройка просто не запомнится
    }
    if (value) {
      resume()
    } else {
      rec?.abort()
    }
  }

  function readPref(): boolean {
    try {
      return localStorage.getItem(ENABLED_KEY) === '1'
    } catch {
      return false
    }
  }

  return { supported, enabled, listening, blocked, resume, pause, setEnabled }
}
