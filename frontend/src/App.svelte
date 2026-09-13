<script>
  import { onMount } from 'svelte';
  import { api, authenticate, setCsrf, setUnauthorizedHandler } from './lib/api.js';
  import { startLive, stopLive } from './lib/sse.js';
  import { session, route, liveState, autoLive, currentHash, TITLES, tick, refreshTick, purchaseStatus, toast } from './lib/store.js';
  import Sidebar from './components/Sidebar.svelte';
  import Toast from './components/Toast.svelte';
  import TaskBar from './components/TaskBar.svelte';
  import ConfirmDialog from './components/ConfirmDialog.svelte';
  import Login from './views/Login.svelte';
  import Overview from './views/Overview.svelte';
  import Purchase from './views/Purchase.svelte';
  import Accounts from './views/Accounts.svelte';
  import Tokens from './views/Tokens.svelte';
  import Network from './views/Network.svelte';
  import Proxies from './views/Proxies.svelte';
  import Logs from './views/Logs.svelte';
  import Settings from './views/Settings.svelte';

  const VIEWMAP = { overview: Overview, purchase: Purchase, accounts: Accounts, tokens: Tokens,
    network: Network, proxies: Proxies, logs: Logs, settings: Settings };
  const CurrentView = $derived(VIEWMAP[$route] || Overview);

  let booted = $state(false);
  let bootError = $state('');

  setUnauthorizedHandler(() => {
    session.update((s) => ({ ...s, authenticated: false }));
    stopLive();
  });

  async function boot() {
    booted = false; bootError = '';
    try {
      const r = await api('/auth/session');
      setCsrf(r.csrf);
      session.set({ authenticated: r.authenticated, username: r.username, booted: true, error: '' });
    } catch (e) {
      bootError = e.message;
    } finally {
      booted = true;
    }
  }

  async function login(username, password) {
    const r = await authenticate(username, password);
    session.set({ authenticated: true, username: r.username, booted: true, error: '' });
  }

  async function logout() {
    try { await api('/auth/logout', { method: 'POST', body: '{}' }); }
    catch (e) { toast(e.message); return; }
    session.update((s) => ({ ...s, authenticated: false }));
    stopLive();
  }

  onMount(() => {
    const onHash = () => route.set(currentHash());
    const onVis = () => { if (!document.hidden && $session.authenticated) tick(); };
    window.addEventListener('hashchange', onHash);
    document.addEventListener('visibilitychange', onVis);
    boot();
    return () => {
      window.removeEventListener('hashchange', onHash);
      document.removeEventListener('visibilitychange', onVis);
      stopLive();
    };
  });

  // Живое подключение: активно при авторизации и включённом live.
  $effect(() => {
    if ($session.authenticated && $autoLive) {
      liveState.set({ mode: 'connecting', at: null });
      startLive(() => tick(), (mode) => liveState.set({ mode, at: Date.now() }));
      return () => stopLive();
    }
    stopLive();
    if (!$autoLive) liveState.set({ mode: 'offline', at: null });
  });

  // Заголовок вкладки браузера.
  $effect(() => {
    const [title] = TITLES[$route] || ['Панель'];
    document.title = title + ' · Discord Tools';
  });

  // Общий опрос задачи покупки — для глобального индикатора (виден на любой вкладке).
  $effect(() => {
    $refreshTick;
    if (!$session.authenticated) { purchaseStatus.set(null); return; }
    api('/purchase/status').then((s) => purchaseStatus.set(s)).catch(() => {});
  });
</script>

{#if !booted || bootError}
  <section class="boot-screen" role="status">
    <span class="brand-mark">D</span>
    <p>{bootError || 'Подключение к панели…'}</p>
    {#if bootError}<button class="button secondary" onclick={boot}>Повторить</button>{/if}
  </section>
{:else if !$session.authenticated}
  <Login {login} />
{:else}
  <div class="workspace">
    <Sidebar onLogout={logout} />
    <main class="main" id="main" tabindex="-1">
      <TaskBar />
      <CurrentView />
      <footer class="page-footer"><span>DISCORD TOOLS</span><span>Данные с вашего сервера</span></footer>
    </main>
  </div>
{/if}
<Toast />
<ConfirmDialog />
