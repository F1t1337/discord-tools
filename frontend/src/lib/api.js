// Обёртка над fetch с CSRF и обработкой 401 (перенос логики из vanilla app.js).
let csrf = '';
let onUnauthorized = null;

export function setCsrf(value) { csrf = value || ''; }
export function getCsrf() { return csrf; }
export function setUnauthorizedHandler(fn) { onUnauthorized = fn; }

export async function authenticate(username, password) {
  // Logout/revoke clears the server cookie; bootstrap a fresh CSRF even when
  // the login form is shown without a full page reload.
  const bootstrap = await api('/auth/session');
  setCsrf(bootstrap.csrf);
  const result = await api('/auth/login', {
    method: 'POST', body: JSON.stringify({ username, password }),
  });
  setCsrf(result.csrf);
  return result;
}

export async function api(path, options = {}) {
  const headers = new Headers(options.headers || {});
  if (options.method && options.method !== 'GET') {
    headers.set('X-CSRF-Token', csrf);
    headers.set('Content-Type', 'application/json');
  }
  const response = await fetch('/api' + path, {
    ...options, headers, credentials: 'same-origin', cache: 'no-store',
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    if (response.status === 401 && !path.startsWith('/auth/login')) {
      if (onUnauthorized) onUnauthorized();
      const bootstrap = await fetch('/api/auth/session', { credentials: 'same-origin', cache: 'no-store' });
      if (bootstrap.ok) csrf = (await bootstrap.json()).csrf;
    }
    throw new Error(body.error || 'Сервер недоступен. Повторите запрос.');
  }
  return options.blob ? response.blob() : response.json();
}

// Скачивание blob как файла (CSP разрешает blob:-ссылки; клиентская загрузка).
export function saveBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url; link.download = filename;
  document.body.append(link); link.click(); link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
