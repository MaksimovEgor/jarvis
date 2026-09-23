/// <reference types="vite/client" />

declare module '*.vue' {
  import type { DefineComponent } from 'vue'
  const component: DefineComponent
  export default component
}

// Web Speech API: в lib.dom есть результаты распознавания, но нет самого
// распознавателя. Safari (iOS 14.5+) и Chrome дают его как webkitSpeechRecognition.
interface SpeechRecognitionEventLike extends Event {
  readonly resultIndex: number
  readonly results: SpeechRecognitionResultList
}

interface SpeechRecognitionErrorLike extends Event {
  readonly error: string
}

interface SpeechRecognitionLike extends EventTarget {
  lang: string
  continuous: boolean
  interimResults: boolean
  start(): void
  stop(): void
  abort(): void
  onstart: (() => void) | null
  onresult: ((e: SpeechRecognitionEventLike) => void) | null
  onerror: ((e: SpeechRecognitionErrorLike) => void) | null
  onend: (() => void) | null
}

interface Window {
  SpeechRecognition?: new () => SpeechRecognitionLike
  webkitSpeechRecognition?: new () => SpeechRecognitionLike
}
