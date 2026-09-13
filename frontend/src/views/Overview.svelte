<script>
  import { api } from '../lib/api.js';
  import { refreshTick, STATUS_LABELS, toast, tick, confirmDialog, route } from '../lib/store.js';
  import { number, money } from '../lib/format.js';
  import PageHeader from '../components/PageHeader.svelte';
  import Metric from '../components/Metric.svelte';
  import Chart from '../components/Chart.svelte';
  import DetailList from '../components/DetailList.svelte';
  import StatusPill from '../components/StatusPill.svelte';
  import { reveal } from '../lib/anim.js';

  let status = $state(null);
  let today = $state(null);
  let series = $state([]);
  let error = $state('');
  let days = $state('14');
  let token = 0;

  async function load() {
    const my = ++token;
    try {
      const [s, t, r] = await Promise.all([
        api('/status'), api('/statistics/today'), api('/statistics/range?days=' + days),
      ]);
      if (my !== token) return;
      status = s; today = t; series = r; error = '';
    } catch (e) {
      if (my === token) error = e.message;
    }
  }
  $effect(() => { $refreshTick; days; load(); });

  async function stopPipeline() {
    if (!(await confirmDialog('Остановить обработку?',
      'Приложение завершит обработку по мере выхода текущих операций. Панель останется доступной.'))) return;
    try { await api('/control/stop', { method: 'POST', body: '{}' }); toast('Запрос остановки принят'); tick(); }
    catch (e) { toast(e.message); }
  }

  const counts = $derived(status?.counts || {});
  const total = $derived(Object.values(counts).reduce((a, n) => a + n, 0));
  const distribution = $derived(Object.entries(counts).sort((a, b) => b[1] - a[1]));
  const threadsAlive = $derived(status ? Object.values(status.threads || {}).filter(Boolean).length : 0);
  const threadsTotal = $derived(status ? Object.keys(status.threads || {}).length : 0);
  const uptime = $derived(status
    ? Math.floor(status.uptime_seconds / 3600) + ' ч ' + Math.floor(status.uptime_seconds % 3600 / 60) + ' мин' : '—');
</script>

<PageHeader title="Дашборд" description="Текущее состояние системы и последние изменения.">
  {#snippet actions()}
    {#if status && (status.running || status.stopping)}
      <button class="button danger" onclick={stopPipeline} disabled={status.stopping}>Остановить обработку</button>
    {/if}
  {/snippet}
</PageHeader>

{#if error}<div class="alert error">{error} Показаны последние полученные данные.</div>{/if}

{#if status}
  <div class="system-strip">
    <div>
      <StatusPill status={status.running ? 'ready' : 'new'}
        text={status.mode === 'monitor' ? 'Мониторинг БД' : status.stopping ? 'Остановка' : status.running ? 'Обработка запущена' : 'Обработка остановлена'}
        tone={status.running ? 'good' : 'accent'} />
      <span class="muted">{status.mode === 'monitor'
        ? 'Панель читает сохранённые данные; обработчики в этом процессе не запущены.'
        : 'Панель подключена к текущему процессу приложения.'}</span>
    </div>
  </div>

  <div class="metrics">
    <Metric title="Всего аккаунтов" value={total} note="Записей в базе" icon="accounts" index={0} />
    <Metric title="Готовы" value={counts.ready || 0} note="Состояние ready" icon="check" index={1} />
    <Metric title="В обработке" value={status.pending_count} note="Промежуточные состояния" icon="refresh" index={2} />
    <Metric title="Невалидные" value={counts.invalid || 0} note="По последней проверке" icon="ban" index={3} />
  </div>

  <div class="overview-grid">
    <article class="panel" use:reveal={0}>
      <div class="panel-heading">
        <div><p class="section-label">Динамика</p><h2>Обработка по дням</h2></div>
        <select bind:value={days} aria-label="Период графика">
          <option value="7">7 дней</option><option value="14">14 дней</option><option value="30">30 дней</option>
        </select>
      </div>
      <div class="activity-chart"><Chart {series} /></div>
      <div class="chart-legend"><span><i class="legend-cleaned"></i>Очищено</span><span><i class="legend-sent"></i>Отправлено</span></div>
    </article>

    <article class="panel" use:reveal={1}>
      <div class="panel-heading"><div><p class="section-label">Состояния</p><h2>Распределение записей</h2></div></div>
      {#if !distribution.length}
        <div class="empty-state"><strong>Пока нет записей</strong><p>Распределение появится, когда база будет содержать данные.</p></div>
      {:else}
        <div class="distribution">
          {#each distribution as [st, count]}
            <div class="distribution-row">
              <div><span>{STATUS_LABELS[st] || 'Архивное состояние'}</span><strong>{number(count)}</strong></div>
              <meter min="0" max={Math.max(total, 1)} value={count}
                aria-label={(STATUS_LABELS[st] || 'Архивное состояние') + ': ' + number(count)}></meter>
            </div>
          {/each}
        </div>
      {/if}
    </article>

    <article class="panel" use:reveal={2}>
      <div class="panel-heading"><div><p class="section-label">Сегодня</p><h2>Показатели за день</h2></div></div>
      <DetailList pairs={[
        ['Куплено', number(today?.tokens_bought)], ['Очищено', number(today?.tokens_cleaned)],
        ['Отправлено', number(today?.tokens_sent)], ['Потрачено', money(today?.money_spent)],
      ]} />
    </article>

    <article class="panel" use:reveal={3}>
      <div class="panel-heading"><div><p class="section-label">Сервер</p><h2>Состояние приложения</h2></div></div>
      <DetailList pairs={[
        ['Панель работает', uptime], ['Схема базы', 'Версия ' + status.schema_version],
        ['Потоки обработки', status.mode === 'monitor' ? 'Не подключены' : threadsAlive + ' / ' + threadsTotal],
        ['Очередь новых', status.queues ? number(status.queues.new_tokens) : 'Нет данных процесса'],
        ['Очередь очистки', status.queues ? number(status.queues.validated) : 'Нет данных процесса'],
      ]} />
    </article>
  </div>
{/if}
