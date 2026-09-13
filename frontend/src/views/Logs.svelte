<script>
  import { api } from '../lib/api.js';
  import { refreshTick } from '../lib/store.js';
  import PageHeader from '../components/PageHeader.svelte';
  import Segmented from '../components/Segmented.svelte';

  let data = $state({ items: [], available: true });
  let error = $state('');
  let level = $state('');
  let token = 0;

  const options = [
    { value: '', label: 'Все' }, { value: 'INFO', label: 'Информация' }, { value: 'WARNING', label: 'Предупреждения' },
    { value: 'ERROR', label: 'Ошибки' }, { value: 'CRITICAL', label: 'Критические' }, { value: 'DEBUG', label: 'Отладка' },
  ];
  const tone = (lvl) => ['ERROR', 'CRITICAL'].includes(lvl) ? 'bad' : lvl === 'WARNING' ? 'warn' : 'accent';

  async function load() {
    const my = ++token;
    try { const r = await api('/logs?limit=100&level=' + level); if (my === token) { data = r; error = ''; } }
    catch (e) { if (my === token) error = e.message; }
  }
  $effect(() => { $refreshTick; level; load(); });
</script>

<PageHeader title="Журнал событий" description="События приложения и диагностика сервера." />

{#if error}<div class="alert error">{error}</div>{/if}

<div class="toolbar"><Segmented {options} bind:value={level} ariaLabel="Уровень журнала" /></div>

<div class="panel log-panel">
  {#if !data.items.length}
    <div class="empty-state">
      <strong>{data.available ? 'Событий не найдено' : 'Журнал ещё не создан'}</strong>
      <p>{data.available ? 'Выберите другой уровень или дождитесь новых событий.' : 'Записи появятся после первого события приложения.'}</p>
    </div>
  {:else}
    {#each data.items as item}
      <article class="log-row">
        <div class="log-meta">
          <time>{item.time}</time>
          <span class="status-pill {tone(item.level)}">{item.level}</span>
          <span>{item.module}</span>
        </div>
        <p>{item.message}</p>
      </article>
    {/each}
  {/if}
</div>
