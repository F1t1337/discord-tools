// Живые обновления через Server-Sent Events (/api/stream) с откатом к поллингу.
// Перенос логики из vanilla app.js. Один активный источник на приложение.
let source = null;
let fallbackTimer = null;
let retry = 0;

function clearFallback() {
  if (fallbackTimer) { clearTimeout(fallbackTimer); fallbackTimer = null; }
}

export function stopLive() {
  if (source) { source.close(); source = null; }
  clearFallback();
  retry = 0;
}

// onUpdate() — перечитать текущую вкладку; onStatus(mode) — 'live' | 'polling' | 'offline'.
export function startLive(onUpdate, onStatus) {
  stopLive();
  if (!window.EventSource) {
    onStatus?.('polling');
    poll(onUpdate, onStatus);
    return;
  }
  const src = new EventSource('/api/stream');
  source = src;
  src.onopen = () => { retry = 0; onStatus?.('live'); };
  src.addEventListener('update', () => { retry = 0; onStatus?.('live'); onUpdate?.(); });
  src.onerror = () => {
    if (source !== src) return;
    onStatus?.('offline');
    if (++retry >= 4) { stopLive(); onStatus?.('polling'); poll(onUpdate, onStatus); }
  };
}

function poll(onUpdate, onStatus) {
  clearFallback();
  fallbackTimer = setTimeout(() => {
    if (!document.hidden) onUpdate?.();
    poll(onUpdate, onStatus);
  }, 5000);
}
