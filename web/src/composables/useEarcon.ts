// Короткие сигналы «слушаю» / «не расслышал» — синтез в Web Audio, без
// файлов. AudioContext на iOS запускается только в жесте, поэтому unlock()
// зовётся на тапах.
let context: AudioContext | null = null

export function audioContext(): AudioContext {
  context ??= new AudioContext()
  return context
}

function tone(freq: number, at: number, length: number, volume = 0.18): void {
  const ctx = audioContext()
  const osc = ctx.createOscillator()
  const gain = ctx.createGain()
  osc.type = 'sine'
  osc.frequency.value = freq
  const start = ctx.currentTime + at
  // Мягкая атака и затухание — без щелчков.
  gain.gain.setValueAtTime(0, start)
  gain.gain.linearRampToValueAtTime(volume, start + 0.015)
  gain.gain.exponentialRampToValueAtTime(0.001, start + length)
  osc.connect(gain).connect(ctx.destination)
  osc.start(start)
  osc.stop(start + length + 0.02)
}

export function useEarcon() {
  function unlock(): void {
    void audioContext().resume()
  }

  // Восходящее «дин-дин» — Джарвис слушает.
  function listen(): void {
    tone(784, 0, 0.12)
    tone(1175, 0.09, 0.18)
  }

  // Нисходящее — команду не услышал, снова жду «Джарвис».
  function cancel(): void {
    tone(660, 0, 0.1, 0.12)
    tone(440, 0.08, 0.16, 0.12)
  }

  return { unlock, listen, cancel }
}
