import { afterEach, expect, it, vi } from 'vitest';
import { mount, unmount } from 'svelte';
import App from '../src/App.svelte';
import Tokens from '../src/views/Tokens.svelte';
import { refreshTick, session } from '../src/lib/store.js';

let component;
afterEach(async () => {
  if (component) await unmount(component);
  component = null;
  document.body.innerHTML = '';
  session.set({ authenticated: false });
  vi.unstubAllGlobals();
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
  expect(button('Выгрузить готовые').disabled).toBe(true);
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
