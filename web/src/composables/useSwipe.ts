import { ref } from 'vue'

// Ось жеста фиксируется по первым пикселям: дальше горизонтальный свайп
// листает треки, вертикальный вниз — закрывает экран, и они не мешают друг другу.
const AXIS_LOCK_PX = 10
const SWIPE_X_PX = 70
const SWIPE_DOWN_PX = 110

export interface SwipeHandlers {
  left?: () => void // палец влево — следующий
  right?: () => void // вправо — предыдущий
  down?: () => void
  // Можно ли сейчас листать вбок (в очереди и тексте песни — нельзя).
  allowX?: () => boolean
}

export function useSwipe(handlers: SwipeHandlers) {
  // Сдвиг пальца — для анимации «тянется за пальцем».
  const dx = ref(0)
  const dy = ref(0)
  let start: { x: number; y: number } | null = null
  let axis: 'x' | 'y' | null = null
  // Был свайп — click после touchend не должен открыть/нажать что-то.
  let swiped = false

  function onTouchStart(e: TouchEvent): void {
    start = { x: e.touches[0].clientX, y: e.touches[0].clientY }
    axis = null
    swiped = false
  }

  function onTouchMove(e: TouchEvent): void {
    if (!start) return
    const mx = e.touches[0].clientX - start.x
    const my = e.touches[0].clientY - start.y
    if (!axis) {
      if (Math.abs(mx) > AXIS_LOCK_PX && Math.abs(mx) > Math.abs(my)) axis = 'x'
      else if (Math.abs(my) > AXIS_LOCK_PX) axis = 'y'
      if (axis) swiped = true
    }
    const canX = (handlers.left || handlers.right) && (handlers.allowX?.() ?? true)
    if (axis === 'x' && canX) dx.value = mx
    if (axis === 'y' && handlers.down) dy.value = Math.max(0, my)
  }

  function onTouchEnd(): void {
    if (dx.value <= -SWIPE_X_PX) handlers.left?.()
    else if (dx.value >= SWIPE_X_PX) handlers.right?.()
    else if (dy.value > SWIPE_DOWN_PX) handlers.down?.()
    dx.value = 0
    dy.value = 0
    start = null
    axis = null
  }

  function wasSwipe(): boolean {
    return swiped
  }

  return { dx, dy, onTouchStart, onTouchMove, onTouchEnd, wasSwipe }
}
