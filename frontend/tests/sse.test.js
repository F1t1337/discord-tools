import { beforeEach, afterEach, expect, it, vi } from 'vitest';
import { startLive, stopLive } from '../src/lib/sse.js';

let sources;
beforeEach(() => {
  sources = [];
  vi.useFakeTimers();
  vi.stubGlobal('EventSource', class {
    listeners = {};
    close = vi.fn();
    constructor() { sources.push(this); }
    addEventListener(event, callback) { this.listeners[event] = callback; }
  });
});
afterEach(() => { stopLive(); vi.useRealTimers(); vi.unstubAllGlobals(); });

it('refreshes on updates and stops after authentication expires', () => {
  const update = vi.fn(), status = vi.fn();
  startLive(update, status);
  sources[0].onopen();
  sources[0].listeners.update();
  expect(update).toHaveBeenCalledTimes(1);
  sources[0].listeners['auth-expired']();
  expect(sources[0].close).toHaveBeenCalledOnce();
  expect(status).toHaveBeenLastCalledWith('offline');
  expect(update).toHaveBeenCalledTimes(2);
  vi.advanceTimersByTime(20000);
  expect(update).toHaveBeenCalledTimes(2);
});

it('falls back after four connection failures and cancels polling on stop', () => {
  const update = vi.fn(), status = vi.fn();
  startLive(update, status);
  for (let i = 0; i < 4; i++) sources[0].onerror();
  expect(status).toHaveBeenLastCalledWith('polling');
  vi.advanceTimersByTime(5000);
  expect(update).toHaveBeenCalledOnce();
  stopLive();
  vi.advanceTimersByTime(20000);
  expect(update).toHaveBeenCalledOnce();
});

it('polls when EventSource is unavailable', () => {
  vi.stubGlobal('EventSource', undefined);
  const update = vi.fn();
  startLive(update);
  vi.advanceTimersByTime(10000);
  expect(update).toHaveBeenCalledTimes(2);
});
