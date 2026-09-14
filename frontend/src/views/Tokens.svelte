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
  let tskupkaBusy = $state(null);
  let exportStarting = $state(false);
  let purchaseSelection = $state('');
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
  const purchases = $derived(job?.purchases || []);
  const selectedPurchase = $derived(purchases.find((p) => String(p.id) === purchaseSelection));
  const canExport = $derived(!purchases.length || purchaseSelection === 'legacy' ||
    (selectedPurchase && !selectedPurchase.export_id && !selectedPurchase.pending && selectedPurchase.ready > 0 &&
     !['running', 'stopping'].includes(selectedPurchase.status)));

  async function upload() {
    if (!tokenInput.trim()) return toast('Вставьте токены');
    uploadBusy = true;
    try {
      const r = await api('/tokens/upload', { method: 'POST', body: JSON.stringify({ tokens: tokenInput }) });
      toast('Принято: ' + r.added + '; дубликатов: ' + r.duplicates + '; ошибок: ' + r.errors);
      if (!r.errors) tokenInput = '';
    } catch (e) { toast(e.message); } finally { uploadBusy = false; tick(); }
  }

  async function exportTokens(destination = 'telegram') {
    if (busy || exportStarting || !canExport) return;
    exportStarting = true;
    if (destination === 'telegram' && !(await confirmDialog('Выгрузить готовые токены?',
      'Проверенные токены сохранятся в истории и будут помечены «отправлены». Затем начнётся доставка файла в Telegram.'))) {
      exportStarting = false;
      return;
    }
    try {
      await api('/tokens/export' + (destination === 'tskupka' ? '/tskupka' : ''), {
        method: 'POST', body: JSON.stringify({ purchase_id: selectedPurchase?.id ?? null }),
      });
      toast(destination === 'tskupka' ? 'Выгрузка в Tskupka запущена' : 'Обычная выгрузка запущена');
    } catch (e) { toast(e.message); }
    finally { await load(); exportStarting = false; }
  }

  async function download(id) {
    try {
      const blob = await api('/tokens/export/download?id=' + encodeURIComponent(id), { blob: true });
      saveBlob(blob, 'tokens.txt'); toast('Файл готов');
    } catch (e) { toast(e.message); }
  }

  function deliveryLabel(d) {
    if (d === 'not_requested') return 'Telegram: не отправлялось';
    return d === 'sent' ? 'Telegram: доставлен' : d === 'failed' ? 'Telegram: ошибка' : 'Telegram: не подтверждён';
  }

  async function sendTskupka(id, refresh = false) {
    if (tskupkaBusy !== null) return;
    tskupkaBusy = id;
    try {
      await api(`/tokens/export/${id}/tskupka${refresh ? '/refresh' : ''}`, { method: 'POST', body: '{}' });
      toast(refresh ? 'Статус Tskupka обновлён' : 'Задача Tskupka создана — без ожидания подтверждения');
    } catch (e) { toast(e.message); }
    finally { await load(); tskupkaBusy = null; }
  }

  function tskupkaLabel(task) {
    if (!task) return 'Tskupka: не отправлено';
    if (task.state === 'sending') return 'Tskupka: отправка начата; повторная отправка заблокирована';
    if (task.state === 'unknown') return 'Tskupka: результат неизвестен — проверьте задачу в сервисе, повторная отправка заблокирована';
    if (task.state === 'rejected') return 'Tskupka: запрос отклонён';
    const labels = { completed: 'завершена', cancelled: 'отменена', awaiting_confirmation: 'сервис ожидает подтверждения' };
    return `Tskupka #${task.task_id}: ${labels[task.remote_status] || task.remote_status || 'задача создана'}`;
  }
</script>

<PageHeader title="Токены" description="Загрузка токенов в обработку и выгрузка готовых.">
  {#snippet actions()}
    <button class="button secondary" onclick={() => exportTokens()} disabled={busy || exportStarting || !workerRunning || !canExport}>
      <Icon name="upload" size={16} /> Обычная выгрузка
    </button>
    <button class="button primary" onclick={() => exportTokens('tskupka')} disabled={busy || exportStarting || !workerRunning || !job?.tskupka_configured || !canExport}>
      Выгрузить в Tskupka
    </button>
  {/snippet}
</PageHeader>

{#if purchases.length}
  <div class="toolbar"><label>Закупка для сдачи
    <select bind:value={purchaseSelection} aria-label="Закупка для сдачи">
      <option value="">Выберите закупку</option>
      {#each purchases as p}
        <option value={String(p.id)}>#{p.id} · куплено {p.bought}, готово {p.ready}, в обработке {p.pending}{p.export_id ? ' · уже выгружена' : ''}</option>
      {/each}
      <option value="legacy">Ручные / архивные записи без закупки</option>
    </select>
  </label></div>
  <p class="muted">Одна закупка — одна сдача. Новая выгрузка доступна после окончания закупки и обработки всех её аккаунтов. Готовые аккаунты разных закупок не смешиваются.</p>
{/if}

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
    <p class="muted">«Обычная выгрузка» сохраняет файл и отправляет его в Telegram. «Выгрузить в Tskupka» проверяет готовые токены выбранной закупки и передаёт их без отдельного подтверждения. Предварительная сумма (initial_total) подтягивается автоматически, после чего опрос прекращается. Итоговую сумму (final_total, после ручной проверки Tskupka) подтягивает кнопка «Обновить Tskupka» — пока она не готова, значение не меняется.</p>
    {#if !job.tskupka_configured}<p class="muted">Отправка в Tskupka не настроена. Задайте TSKUPKA_API_KEY в .env сервера и перезапустите приложение.</p>{/if}
    {#if !history.length}
      <div class="empty-state"><strong>Пока нет выгрузок</strong><p>Выберите обычную выгрузку или Tskupka, когда накопятся готовые токены.</p></div>
    {:else}
      <div class="list">
        {#each history as item (item.id)}
          <div class="list-item export-history-item">
            <div><strong>#{item.id} · {number(item.count)} шт.{item.purchase_id ? ' · Закупка #' + item.purchase_id : ''}</strong><div class="meta">{date(item.created_at)} · {deliveryLabel(item.delivery)}</div>
              <p class="meta">{tskupkaLabel(item.tskupka)}</p>
              {#if item.tskupka?.error}<p class="form-error">{item.tskupka.error}</p>{/if}
              {#if item.tskupka?.price_minor != null}<p class="meta">price_result: {number(item.tskupka.price_minor / 100)} ₽</p>
              {:else if item.tskupka?.task_id}<p class="meta">Ожидаем предварительную сумму…</p>{/if}
            </div>
            <div class="header-actions">
              <button class="button secondary small" onclick={() => download(item.id)}><Icon name="download" size={15} /> .txt</button>
              <button class="button primary small" onclick={() => sendTskupka(item.id)}
                disabled={!job.tskupka_configured || tskupkaBusy !== null || (item.tskupka && item.tskupka.state !== 'rejected')}>
                {tskupkaBusy === item.id ? 'Tskupka…' : 'В Tskupka'}
              </button>
              {#if item.tskupka?.task_id}
                <button class="button secondary small" onclick={() => sendTskupka(item.id, true)}
                  disabled={!job.tskupka_configured || tskupkaBusy !== null}>Обновить Tskupka</button>
              {/if}
            </div>
          </div>
        {/each}
      </div>
    {/if}
  </article>
</div>
{/if}
