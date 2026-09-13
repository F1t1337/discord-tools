import { afterEach, expect, it, vi } from 'vitest';
import { mount, unmount } from 'svelte';
import App from '../src/App.svelte';
import Tokens from '../src/views/Tokens.svelte';
import { refreshTick, session, confirmState } from '../src/lib/store.js';
import { get } from 'svelte/store';

let component;
afterEach(async () => {
  if (component) await unmount(component);
  component = null;
  document.body.innerHTML = '';
  session.set({ authenticated: false });
  confirmState.set(null);
  vi.unstubAllGlobals();
});

it('offers two export buttons and sends to Tskupka without a confirmation dialog', async () => {
  const fetch = vi.fn().mockImplementation((url) => Promise.resolve(Response.json(
    url === '/api/tokens/export/status'
      ? { worker_running: true, running: false, tskupka_configured: true, history: [] }
      : { success: true }, { status: url === '/api/tokens/export/tskupka' ? 202 : 200 })));
  vi.stubGlobal('fetch', fetch);
  component = mount(Tokens, { target: document.body });
  const find = (text) => [...document.querySelectorAll('button')].find((b) => b.textContent.includes(text));
  await vi.waitFor(() => expect(find('Выгрузить в Tskupka').disabled).toBe(false));
  expect(find('Обычная выгрузка').disabled).toBe(false);
  find('Выгрузить в Tskupka').click();
  await vi.waitFor(() => expect(fetch.mock.calls.some(([url]) => url === '/api/tokens/export/tskupka')).toBe(true));
  expect(get(confirmState)).toBeNull();
  expect(fetch.mock.calls.some(([url]) => url === '/api/tokens/export')).toBe(false);
});

it('blocks Tskupka without a key while ordinary export remains available', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json({
    worker_running: true, running: false, tskupka_configured: false, history: [],
  })));
  component = mount(Tokens, { target: document.body });
  const find = (text) => [...document.querySelectorAll('button')].find((b) => b.textContent.includes(text));
  await vi.waitFor(() => expect(find('Обычная выгрузка').disabled).toBe(false));
  expect(find('Выгрузить в Tskupka').disabled).toBe(true);
});

it('keeps startup failure visible and allows retry without reloading', async () => {
  const fetch = vi.fn()
    .mockResolvedValueOnce(Response.json({ error: 'Сервер недоступен' }, { status: 503 }))
    .mockResolvedValueOnce(Response.json({ authenticated: false, csrf: 'bootstrap' }));
  vi.stubGlobal('fetch', fetch);
  component = mount(App, { target: document.body });
  await vi.waitFor(() => expect(document.body.textContent).toContain('Сервер недоступен'));
  expect(document.querySelector('form')).toBeNull();
  document.querySelector('.boot-screen button').click();
  await vi.waitFor(() => expect(document.querySelector('.login-form')).not.toBeNull());
  expect(fetch).toHaveBeenCalledTimes(2);
});

it('disables intake for a stopped worker but keeps failed deliveries downloadable', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json({
    worker_running: false, running: false,
    history: [{ id: 17, count: 2, delivery: 'failed', created_at: '2026-09-13' }],
  })));
  component = mount(Tokens, { target: document.body });
  await vi.waitFor(() => expect(document.body.textContent).toContain('Telegram: ошибка'));
  const button = (text) => [...document.querySelectorAll('button')].find((b) => b.textContent.includes(text));
  expect(button('Обычная выгрузка').disabled).toBe(true);
  expect(button('Выгрузить в Tskupka').disabled).toBe(true);
  expect(button('Загрузить в обработку').disabled).toBe(true);
  expect(button('.txt').disabled).toBe(false);
});

it('retains historical downloads while a new export is running', async () => {
  const history = [{ id: 17, count: 2, delivery: 'sent', created_at: '2026-09-13' }];
  vi.stubGlobal('fetch', vi.fn()
    .mockResolvedValueOnce(Response.json({ worker_running: true, running: false, history }))
    .mockResolvedValueOnce(Response.json({ worker_running: true, running: true, done: 1, total: 4, history })));
  component = mount(Tokens, { target: document.body });
  await vi.waitFor(() => expect(document.body.textContent).toContain('#17'));
  refreshTick.update((n) => n + 1);
  await vi.waitFor(() => expect(document.body.textContent).toContain('Выгрузка: проверено'));
  expect(document.body.textContent).toContain('#17');
  expect([...document.querySelectorAll('button')].find((b) => b.textContent.includes('.txt')).disabled).toBe(false);
});
