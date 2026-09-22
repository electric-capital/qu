import { useSyncExternalStore } from 'react'

/**
 * Viewport-keyed mobile detection for the adaptive shell split (MobileShell vs
 * the desktop AppContent layout). Keyed on viewport width via matchMedia — NOT
 * user-agent sniffing — so narrow desktop windows, tablets, and devtools device
 * emulation all behave consistently, and resizing across the breakpoint
 * switches shells live without a reload.
 */
const MOBILE_BREAKPOINT_PX = 768

const query = `(max-width: ${MOBILE_BREAKPOINT_PX}px)`

function subscribe(callback: () => void): () => void {
  const mql = window.matchMedia(query)
  mql.addEventListener('change', callback)
  return () => mql.removeEventListener('change', callback)
}

function getSnapshot(): boolean {
  return window.matchMedia(query).matches
}

export function useIsMobile(): boolean {
  return useSyncExternalStore(subscribe, getSnapshot)
}
