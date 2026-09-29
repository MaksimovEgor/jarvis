import { ref } from 'vue'

import { clientLog } from '../api'
import { releaseMic, takeMic } from './useAudioSession'

const ENABLED_KEY = 'jarvis:wake'

// Как распознаватель пишет «Джарвис»: джарвис, джервис, жарвис, джарвиз,
// джарвес, джавис, джарви, джэрвис, «джарвиса», дарвис, «джар вис», jarvis.
// Только целым словом — иначе «обжарив» или «жаворонок» будили бы Джарвиса.
const WAKE =
  /(?:(?<![а-яё])(?:д?ж[аеэя]р?в[иеэы][сз]?[а-яё]{0,2}|дарвис|джа рвис|джар вис)(?![а-яё])|jarvis|jervis)[\s,.!?:;—-]*/i
// iOS заканчивает сессию распознавания сам (тишина, ~минута) — перезапуск.
const RESTART_MS = 300
// После «Джарвис» команда собирается, пока человек говорит: закончилась,
// когда распознаватель молчит столько. Раньше брали первый «final» — а iOS
// ставит его на первой же паузе, и уходил обрывок («включи…»).
const COMMAND_PAUSE_MS = 1800
// Страховка: столько после «Джарвис» — команда точно закончилась.
const MAX_COMMAND_MS = 30000

export interface WakeHandlers {
  onAlert: () => void // услышал «Джарвис» — сигнал, приглушить музыку
  // фраза закончилась: команда или '' (только имя); heardAt — когда распознано
  // последнее слово (Date.now()), отсчёт задержки ответа — от него, а не от паузы
  onWake: (command: string, heardAt: number) => void
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
  // Сбор команды после «Джарвис»: текст после слова и был ли он в последнем
  // варианте распознавания (распознаватель может «передумать»).
  let alerted = false
  let command = ''
  let heardWake = false
  let lastResultAt = 0
  let pauseTimer: number | null = null
  let maxTimer: number | null = null

  function clearAlert(): void {
    alerted = false
    command = ''
    heardWake = false
    for (const t of [pauseTimer, maxTimer]) if (t !== null) clearTimeout(t)
    pauseTimer = maxTimer = null
  }

  // Пауза после «Джарвис …» — команда закончилась (или это была ложная тревога).
  function finishCommand(): void {
    if (!alerted) return
    const text = command
    const real = heardWake
    const heardAt = lastResultAt
    clearAlert()
    if (!real) {
      handlers.onCancel()
      return
    }
    clientLog(`wake: command "${text.slice(0, 80)}"`)
    pause()
    handlers.onWake(text, heardAt)
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
      // Весь текст сессии: финальные куски + текущий промежуточный — команда
      // после «Джарвис» может растянуться на несколько «final».
      let full = ''
      for (let i = 0; i < e.results.length; i++) full += ` ${e.results[i][0].transcript}`
      // Последнее «Джарвис» в тексте — команда после него.
      const matches = [...full.matchAll(new RegExp(WAKE.source, 'gi'))]
      const last = matches[matches.length - 1]
      if (!last && !alerted) return
      if (last && !alerted) {
        alerted = true
        handlers.onAlert()
        maxTimer = window.setTimeout(finishCommand, MAX_COMMAND_MS)
      }
      heardWake = Boolean(last)
      lastResultAt = Date.now()
      if (last) command = full.slice((last.index ?? 0) + last[0].length).trim()
      if (pauseTimer !== null) clearTimeout(pauseTimer)
      pauseTimer = window.setTimeout(finishCommand, COMMAND_PAUSE_MS)
    }
    r.onerror = (e) => {
      clientLog(`wake: onerror ${e.error}`)
      if (e.error === 'not-allowed' || e.error === 'service-not-allowed') {
        blocked.value = true
      }
    }
    r.onend = () => {
      clientLog(`wake: onend wanted=${wanted} blocked=${blocked.value}`)
      releaseMic()
      // iOS закрыл сессию посреди команды — дальше текст не придёт, берём что есть.
      if (alerted) finishCommand()
      listening.value = false
      if (rec === r) rec = null
      if (wanted && !blocked.value) window.setTimeout(begin, RESTART_MS)
    }

    try {
      clientLog('wake: start()')
      takeMic()
      r.start()
    } catch (e) {
      clientLog(`wake: start() threw ${e instanceof Error ? e.message : String(e)}`)
      releaseMic()
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
