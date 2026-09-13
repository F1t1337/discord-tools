<script>
  import { api } from '../lib/api.js';
  import { refreshTick, toast, tick, confirmDialog } from '../lib/store.js';
  import { number, date } from '../lib/format.js';
  import PageHeader from '../components/PageHeader.svelte';
  import Metric from '../components/Metric.svelte';
  import StatusPill from '../components/StatusPill.svelte';
  import Skeleton from '../components/Skeleton.svelte';
  import { reveal } from '../lib/anim.js';

  let data = $state(null);
  let error = $state('');
  let proxyInput = $state('');
  let importing = $state(false);
  let token = 0;

  async function load() {
    const my = ++token;
    try { const r = await api('/proxies'); if (my === token) { data = r; error = ''; } }
    catch (e) { if (my === token) error = e.message; }
  }
  $effect(() => { $refreshTick; load(); });

  const counts = $derived(data?.counts || {});
  const job = $derived(data?.job || {});
  const busy = $derived(!!job.running);
  const items = $derived(data?.items || []);

  async function importProxies() {
    if (busy) return;
    const raw = proxyInput.trim();
    if (!raw) return toast('Вставьте список прокси');
    importing = true;
    try { const r = await api('/proxies/import', { method: 'POST', body: JSON.stringify({ proxies: raw }) });
      toast('Проверка запущена: ' + r.queued + ' прокси'); proxyInput = ''; tick(); }
    catch (e) { toast(e.message); } finally { importing = false; }
  }
  async function recheck() {
    if (busy) return;
    try { const r = await api('/proxies/recheck', { method: 'POST', body: '{}' }); toast('Перепроверка запущена: ' + r.queued); tick(); }
    catch (e) { toast(e.message); }
  }
  async function clear(scope) {
    if (busy) return;
    const ok = await confirmDialog(scope === 'all' ? 'Очистить весь пул?' : 'Удалить мёртвые прокси?',
      scope === 'all' ? 'Все прокси будут удалены без возможности восстановления.' : 'Будут удалены все прокси со статусом «мёртвый».');
    if (!ok) return;
    try { const r = await api('/proxies/clear', { method: 'POST', body: JSON.stringify({ scope }) }); toast('Удалено: ' + r.removed); tick(); }
    catch (e) { toast(e.message); }
  }

  const pill = (s) => s === 'alive' ? ['good', 'Живой'] : s === 'dead' ? ['bad', 'Мёртвый'] : ['accent', 'Не проверен'];
</script>

<PageHeader title="Прокси" description="Загрузка, проверка на живость и пул рабочих прокси." />

{#if error}<div class="alert error">{error}</div>{/if}

{#if !data && !error}
  <Skeleton kind="metrics" />
  <Skeleton kind="panels" count={2} />
{/if}
{#if data}

{#if job.running}
  <div class="alert">{job.kind === 'recheck' ? 'Перепроверка пула' : 'Проверка новых прокси'}:
    {number(job.done)} из {number(job.total)} · живых {number(job.alive)}, мёртвых {number(job.dead)}</div>
{:else if job.error}
  <div class="alert error">Проверка прервана из-за ошибки. Попробуйте ещё раз.</div>
{:else if job.finished_at}
  <div class="alert warning">Проверка завершена: обработано {number(job.total)}, живых {number(job.alive)}, мёртвых {number(job.dead)}.</div>
{/if}

<div class="metrics">
  <Metric title="Рабочих" value={counts.alive || 0} note="Готовы к использованию" icon="check" index={0} />
  <Metric title="Мёртвых" value={counts.dead || 0} note="Не прошли проверку" icon="ban" index={1} />
  <Metric title="Не проверено" value={counts.unchecked || 0} note="Ожидают проверки" icon="clock" index={2} />
  <Metric title="Всего в пуле" value={counts.total || 0} note="Записей всего" icon="proxies" index={3} />
</div>

<div class="settings-grid">
  <article class="panel" use:reveal={0}>
    <div class="panel-heading"><div><p class="section-label">Добавить</p><h2>Загрузка прокси</h2></div></div>
    <p class="muted">Вставьте список — по одному в строке. Форматы: <code>host:port</code>,
      <code>host:port:login:password</code>, <code>login:password@host:port</code>.</p>
    <textarea class="proxy-input" rows="8" placeholder="1.2.3.4:8080&#10;login:password@5.6.7.8:3128" maxlength="1000000" bind:value={proxyInput}></textarea>
    <div class="panel-action">
      <button class="button primary" onclick={importProxies} disabled={busy || importing}>Проверить и сохранить</button>
      <p class="muted">Проверяются на живость; в пул попадают только рабочие прокси.</p>
    </div>
  </article>
  <article class="panel" use:reveal={1}>
    <div class="panel-heading"><div><p class="section-label">Пул</p><h2>Управление пулом</h2></div></div>
    <p class="muted">За аккаунтом постоянно закрепляется один прокси для проверки, очистки и выгрузки.
      При нехватке или сбое аккаунт ждёт; прямого выхода и автоматической замены нет.</p>
    <div class="panel-action proxy-actions">
      <button class="button secondary" onclick={recheck} disabled={busy}>Перепроверить пул</button>
      <button class="button secondary" onclick={() => clear('dead')} disabled={busy}>Удалить мёртвые</button>
      <button class="button danger" onclick={() => clear('all')} disabled={busy}>Очистить всё</button>
    </div>
  </article>
</div>

<div class="panel table-panel">
  <div class="table-overflow">
    <table>
      <thead><tr><th>Прокси</th><th>Авторизация</th><th>Состояние</th><th>Пинг</th><th>Проверен</th></tr></thead>
      <tbody>
        {#if !items.length}
          <tr><td colspan="5"><div class="empty-state"><strong>Пул пуст</strong><p>Добавьте прокси через форму выше.</p></div></td></tr>
        {:else}
          {#each items as item (item.id)}
            {@const [tone, label] = pill(item.status)}
            <tr>
              <td class="mono">{item.endpoint}</td>
              <td>{item.auth ? 'логин/пароль' : 'нет'}</td>
              <td><StatusPill {tone} text={label} /></td>
              <td class="nowrap">{item.ping != null ? item.ping + ' мс' : '—'}</td>
              <td class="mono nowrap">{item.last_checked_at ? date(item.last_checked_at) : '—'}</td>
            </tr>
          {/each}
        {/if}
      </tbody>
    </table>
  </div>
</div>
{/if}
