export type Status = 'idle' | 'recording' | 'thinking' | 'speaking'

export interface Message {
  id: number
  role: 'user' | 'assistant' | 'error'
  text: string
  tools?: string[]
}
