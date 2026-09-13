<script>
  import { api, saveBlob } from '../lib/api.js';
  import { refreshTick, STATUS_LABELS, toast } from '../lib/store.js';
  import { number, money, date } from '../lib/format.js';
  import PageHeader from '../components/PageHeader.svelte';
  import StatusPill from '../components/StatusPill.svelte';
  import Segmented from '../components/Segmented.svelte';
  import Drawer from '../components/Drawer.svelte';
  import DetailList from '../components/DetailList.svelte';
  import Skeleton from '../components/Skeleton.svelte';
  import Icon from '../components/Icon.svelte';

  const pageSize = 25;
  let result = $state({ items: [], total: 0 });
  let loaded = $state(false);
  let error = $state('');
  let statusFilter = $state('');
  let searchInput = $state('');
  let search = $state('');
  let offset = $state(0);
  let token = 0;
  let selected = $state(null);
  let downloading = $state(false);

  const options = [{ value: '', label: 'Все' }, { value: 'pending_review', label: 'Незавершённые' },
    ...Object.entries(STATUS_LABELS).filter(([k]) => k !== 'pending_review').map(([value, label]) => ({ value, label }))];

  // Дебаунс поиска.
  $effect(() => {
    const value = searchInput;
    const timer = setTimeout(() => { search = value.trim(); offset = 0; }, 350);
    return () => clearTimeout(timer);
  });

  async function load() {
    const my = ++token;
    try {
      const r = await api('/accounts?' + new URLSearchParams({ limit: pageSize, offset, status: statusFilter, search }));
      if (my === token) { result = r; error = ''; loaded = true; }
    } catch (e) { if (my === token) error = e.message; }
  }
  $effect(() => { $refreshTick; statusFilter; search; offset; load(); });
  $effect(() => { statusFilter; offset = 0; });

  async function downloadCsv() {
    downloading = true;
    try {
      const blob = await api('/accounts/report.csv?' + new URLSearchParams({ status: statusFilter, search }), { blob: true });
      saveBlob(blob, 'accounts-report.csv'); toast('Отчёт подготовлен');
    } catch (e) { toast(e.message); } finally { downloading = false; }
  }

  const pageLabel = $derived(result.total
    ? number(offset + 1) + '–' + number(Math.min(offset + pageSize, result.total)) + ' из ' + number(result.total)
    : 'Нет записей');
</script>

<PageHeader title="Аккаунты" description="Поиск и просмотр записей без раскрытия токенов доступа.">
  {#snippet actions()}
    <button class="button secondary" onclick={downloadCsv} disabled={downloading}><Icon name="download" size={16} /> Отчёт CSV</button>
  {/snippet}
</PageHeader>

{#if error}<div class="alert error">{error}</div>{/if}

<div class="toolbar">
  <div class="search-box"><input type="search" placeholder="Имя, продавец или ID…" maxlength="100" bind:value={searchInput}></div>
</div>
<div class="toolbar"><Segmented {options} bind:value={statusFilter} ariaLabel="Фильтр по состоянию" /></div>

{#if !loaded && !error}
  <Skeleton kind="table" count={8} />
{:else}
<div class="panel table-panel">
  <div class="table-overflow">
    <table>
      <thead><tr><th>ID</th><th>Аккаунт</th><th>Состояние</th><th>Продавец</th><th>Цена</th><th>Добавлен</th><th><span class="sr-only">Подробнее</span></th></tr></thead>
      <tbody>
        {#if !result.items.length}
          <tr><td colspan="7"><div class="empty-state"><strong>Записи не найдены</strong><p>Попробуйте изменить поиск или фильтр.</p></div></td></tr>
        {:else}
          {#each result.items as account (account.id)}
            <tr>
              <td class="mono">#{account.id}</td>
              <td>{account.username || 'Без имени'}</td>
              <td><StatusPill status={account.status} /></td>
              <td>{account.seller_username || '—'}</td>
              <td class="nowrap">{money(account.price)}</td>
              <td class="mono nowrap">{date(account.created_at)}</td>
              <td><button class="icon-button" aria-label={'Подробнее об аккаунте ' + account.id} onclick={() => (selected = account)}><Icon name="arrow" /></button></td>
            </tr>
          {/each}
        {/if}
      </tbody>
    </table>
  </div>
  <div class="table-footer">
    <span>{pageLabel}</span>
    <div>
      <button class="button secondary small" disabled={offset === 0} onclick={() => (offset = Math.max(0, offset - pageSize))}>← Назад</button>
      <button class="button secondary small" disabled={offset + pageSize >= result.total} onclick={() => (offset += pageSize)}>Вперёд →</button>
    </div>
  </div>
</div>
{/if}
<p class="table-note">В панели и отчёте нет секретных токенов. CSV содержит до 10 000 записей с выбранными фильтрами.</p>

<Drawer open={!!selected} title="Карточка аккаунта" onClose={() => (selected = null)}>
  {#snippet children()}
    {#if selected}
      <DetailList pairs={[
        ['ID', selected.id], ['Аккаунт', selected.username], ['Состояние', STATUS_LABELS[selected.status] || 'Архивное состояние'],
        ['Продавец', selected.seller_username], ['Цена', money(selected.price)], ['Добавлен', date(selected.created_at)],
        ['Проверен', date(selected.validated_at)], ['Очищен', date(selected.cleaned_at)], ['Отправлен', date(selected.sent_at)],
        ['Последнее действие', selected.cleaning_progress || '—'],
      ]} />
    {/if}
  {/snippet}
</Drawer>
