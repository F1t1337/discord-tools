<script>
  import { api, saveBlob } from '../lib/api.js';
  import { refreshTick, toast, tick, confirmDialog } from '../lib/store.js';
  import { number, date } from '../lib/format.js';
  import PageHeader from '../components/PageHeader.svelte';
  import Icon from '../components/Icon.svelte';
  import Skeleton from '../components/Skeleton.svelte';
  import { reveal } from '../lib/anim.js';

  let job = $state(null);
  let error = $state('');
  let tokenInput = $state('');
  let uploadBusy = $state(false);
  let token = 0;

  async function load() {
    const my = ++token;
    try { const r = await api('/tokens/export/status'); if (my === token) { job = r; error = ''; } }
    catch (e) { if (my === token) error = e.message; }
  }
  $effect(() => { $refreshTick; load(); });

  const workerRunning = $derived(!!job?.worker_running);
  const busy = $derived(!!job?.running);
  const history = $derived(job?.history || []);

  async function upload() {
    if (!tokenInput.trim()) return toast('Вставьте токены');
    uploadBusy = true;
    try {
      const r = await api('/tokens/upload', { method: 'POST', body: JSON.stringify({ tokens: tokenInput }) });
      toast('Принято: ' + r.added + '; дубликатов: ' + r.duplicates + '; ошибок: ' + r.errors);
      if (!r.errors) tokenInput = '';
    } catch (e) { toast(e.message); } finally { uploadBusy = false; tick(); }
  }

  async function exportTokens() {
    if (busy) return;
    if (!(await confirmDialog('Выгрузить готовые токены?',
      'Проверенные токены сохранятся в истории и будут помечены «отправлены». Затем начнётся доставка файла в Telegram.'))) return;
    try { await api('/tokens/export', { method: 'POST', body: '{}' }); toast('Выгрузка запущена'); tick(); }
    catch (e) { toast(e.message); }
  }

  async function download(id) {
    try {
      const blob = await api('/tokens/export/download?id=' + encodeURIComponent(id), { blob: true });
      saveBlob(blob, 'tokens.txt'); toast('Файл готов');
    } catch (e) { toast(e.message); }
  }

  function deliveryLabel(d) {
    return d === 'sent' ? 'Telegram: доставлен' : d === 'failed' ? 'Telegram: ошибка' : 'Telegram: не подтверждён';
  }
</script>

<PageHeader title="Токены" description="Загрузка токенов в обработку и выгрузка готовых.">
  {#snippet actions()}
    <button class="button primary" onclick={exportTokens} disabled={busy || !workerRunning}>
      <Icon name="upload" size={16} /> Выгрузить готовые
    </button>
  {/snippet}
</PageHeader>

{#if error}<div class="alert error">{error}</div>{/if}
{#if !job && !error}<Skeleton kind="panels" count={2} />{/if}
{#if job && !workerRunning}
  <div class="alert warning">Обработка не запущена. Загрузка и новая выгрузка недоступны; сохранённые файлы можно скачать.</div>
{/if}

{#if job?.running}
  <div class="alert">Выгрузка: проверено {number(job.done)} из {number(job.total)}</div>
{:else if job?.error}
  <div class="alert error">Выгрузка прервана. Сохранённые файлы доступны в истории; повторите попытку.</div>
{:else if job?.finished_at}
  <div class="alert warning">Сохранено: {number(job.valid)}, невалидных: {number(job.invalid)}, отложено: {number(job.deferred)}.
    {job.delivery === 'sent' ? 'Файл отправлен в Telegram.' : job.delivery === 'failed' ? 'Telegram не доставил — скачайте ниже.' : ''}</div>
{/if}

{#if job}
<div class="settings-grid">
  <article class="panel" use:reveal={0}>
    <div class="panel-heading"><div><p class="section-label">Загрузка</p><h2>Добавить токены</h2></div></div>
    <p class="muted">Вставьте токены — по одному в строке. Поддерживается формат <code>login:pass:token</code>
      (берётся последний сегмент). Токены уходят в очередь обработки.</p>
    <textarea class="proxy-input" rows="8" placeholder="MTk...token&#10;login:pass:MTk...token" maxlength="1000000" bind:value={tokenInput}></textarea>
    <div class="panel-action">
      <button class="button primary" onclick={upload} disabled={uploadBusy || busy || !workerRunning}>Загрузить в обработку</button>
      <p class="muted">Дубликаты пропускаются. Принятые записи сохраняются в базе перед постановкой в очередь.</p>
    </div>
  </article>

  <article class="panel" use:reveal={1}>
    <div class="panel-heading"><div><p class="section-label">Выгрузка</p><h2>Сохранённые файлы</h2></div></div>
    <p class="muted">Проверенные токены сохраняются в истории и помечаются «отправлены». Файл остаётся доступным после перезапуска и ошибок Telegram.</p>
    {#if !history.length}
      <div class="empty-state"><strong>Пока нет выгрузок</strong><p>Нажмите «Выгрузить готовые», когда накопятся токены.</p></div>
    {:else}
      <div class="list">
        {#each history as item (item.id)}
          <div class="list-item">
            <div><strong>#{item.id} · {number(item.count)} шт.</strong><div class="meta">{date(item.created_at)} · {deliveryLabel(item.delivery)}</div></div>
            <button class="button secondary small" onclick={() => download(item.id)}><Icon name="download" size={15} /> .txt</button>
          </div>
        {/each}
      </div>
    {/if}
  </article>
</div>
{/if}
