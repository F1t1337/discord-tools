import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest';
import { api, authenticate, getCsrf, setCsrf } from '../src/lib/api.js';

describe('login after session reset', () => {
  beforeEach(() => setCsrf('old-session-token'));
  afterEach(() => vi.unstubAllGlobals());

  it.each(['logout', 'revoke'])('bootstraps CSRF after %s before sending credentials', async (action) => {
    const fetch = vi.fn()
      .mockResolvedValueOnce(Response.json({ success: true }))
      .mockResolvedValueOnce(Response.json({ csrf: 'fresh-session-token' }))
      .mockResolvedValueOnce(Response.json({ authenticated: true, csrf: 'logged-in-token' }));
    vi.stubGlobal('fetch', fetch);
    await api('/auth/' + action, { method: 'POST', body: '{}' });
    expect((await authenticate('admin', 'synthetic-password')).authenticated).toBe(true);
    expect(fetch.mock.calls.map(([url]) => url)).toEqual([
      '/api/auth/' + action, '/api/auth/session', '/api/auth/login',
    ]);
    expect(fetch.mock.calls[2][1].headers.get('X-CSRF-Token')).toBe('fresh-session-token');
    expect(getCsrf()).toBe('logged-in-token');
  });

  it('does not submit credentials when session bootstrap fails', async () => {
    const fetch = vi.fn().mockResolvedValue(Response.json({ error: 'Unavailable' }, { status: 503 }));
    vi.stubGlobal('fetch', fetch);
    await expect(authenticate('admin', 'synthetic-password')).rejects.toThrow('Unavailable');
    expect(fetch).toHaveBeenCalledTimes(1);
  });
});
