<script>
  import { api } from '../lib/api.js';
  import { refreshTick } from '../lib/store.js';
  import { money, number, date } from '../lib/format.js';
  import PageHeader from '../components/PageHeader.svelte';
  import Segmented from '../components/Segmented.svelte';
  import Skeleton from '../components/Skeleton.svelte';

  let period = $state('day');
  let selectedDate = $state('');
  let offset = $state(0);
  let data = $state(null);
  let error = $state('');
  let sequence = 0;
  const options = [{ value: 'day', label: 'День' }, { value: 'week', label: 'Неделя' }, { value: 'month', label: 'Месяц' }];
  const cash = (minor) => minor == null ? '—' : money(minor / 100);
  const submissionLabel = (row) => row.earned_minor != null ? 'Сумма получена'
    : row.task_id ? 'Ожидаем price_result' : row.export_id ? 'Выгружено' : 'Закупка / обработка';
  async function load() {
    const current = ++sequence;
    try {
      const result = await api('/statistics/finance?' + new URLSearchParams({ period, date: selectedDate, offset }));
      if (current === sequence) { data = result; error = ''; }
    } catch (e) { if (current === sequence) error = e.message; }
  }
  $effect(() => { $refreshTick; period; selectedDate; offset; load(); });
  $effect(() => { period; selectedDate; offset = 0; });
</script>

<PageHeader title="Статистика" description="Расходы на аккаунты, заработок Tskupka и прибыль." />
<div class="toolbar">
  <Segmented {options} bind:value={period} ariaLabel="Период статистики" />
  <label>Дата в периоде <input type="date" aria-label="Дата в периоде" value={selectedDate || data?.date || ''} onchange={(e) => selectedDate = e.target.value}></label>
</div>
{#if error}<div class="alert error">{error}</div>{/if}
{#if !data && !error}<Skeleton kind="metrics" />{/if}
{#if data}
  <p class="muted">{data.start} — {data.end} · Часовой пояс: {data.timezone}. Неделя — с понедельника, месяц — календарный.</p>
  <div class="metrics finance-metrics">
    <article class="metric"><p class="metric-label">Потрачено на аккаунты</p><p class="metric-value">{cash(data.spent_minor)}</p><p class="metric-note">По дате успешной покупки</p></article>
    <article class="metric"><p class="metric-label">Заработано в Tskupka</p><p class="metric-value">{cash(data.earned_minor)}</p><p class="metric-note">По дате первого получения price_result</p></article>
    <article class="metric"><p class="metric-label">Прибыль за период</p><p class="metric-value">{cash(data.profit_minor)}</p><p class="metric-note">Заработано − потрачено</p></article>
  </div>
  {#if data.awaiting_price}<div class="alert warning">Ожидают price_result: {number(data.awaiting_price)} задач. Они опрашиваются раз в 10 секунд, в том числе после перезапуска сервера. Их доход ещё не учтён.</div>{/if}
  {#if data.legacy_spent_minor}<p class="muted">В расход включено {cash(data.legacy_spent_minor)} из прежней дневной статистики. Для этих покупок связь со сдачами неизвестна.</p>{/if}
  <article class="panel table-panel">
    <div class="panel-heading"><h2>По дням</h2></div>
    <div class="table-overflow"><table>
      <thead><tr><th>День</th><th>Потрачено</th><th>Заработано</th><th>Прибыль</th></tr></thead>
      <tbody>{#each data.daily as row}<tr><td>{row.date}</td><td>{cash(row.spent_minor)}</td><td>{cash(row.earned_minor)}</td><td>{cash(row.profit_minor)}</td></tr>{/each}</tbody>
    </table></div>
  </article>
  <article class="panel table-panel">
    <div class="panel-heading"><h2>Закупка → сдача</h2></div>
    <p class="table-note">В каждой строке — полная стоимость закупки и её единственной сдачи, даже если они пришлись на разные дни. Общие итоги выше учитывают даты отдельных расходов и доходов.</p>
    <div class="table-overflow"><table>
      <thead><tr><th>Закупка</th><th>Аккаунты</th><th>Потрачено</th><th>Сдача</th><th>Заработано</th><th>Прибыль</th><th>Состояние</th></tr></thead>
      <tbody>
        {#each data.items as row (row.id)}
          <tr><td>#{row.id}<div class="meta">{date(row.started_at)}</div></td><td>{number(row.bought)} / {number(row.requested)}</td>
            <td>{cash(row.spent_minor)}</td><td>{row.task_id ? 'Tskupka #' + row.task_id : row.export_id ? 'Файл #' + row.export_id : '—'}<div class="meta">{date(row.export_created_at)}</div></td>
            <td>{cash(row.earned_minor)}<div class="meta">{date(row.price_at)}</div></td><td>{cash(row.profit_minor)}</td>
            <td>{submissionLabel(row)}{#if row.error}<div class="form-error">{row.error}</div>{/if}</td></tr>
        {:else}<tr><td colspan="7">За этот период закупок и сдач нет.</td></tr>{/each}
      </tbody>
    </table></div>
    <div class="table-footer"><span>Связок: {number(data.total)}</span><div>
      <button class="button secondary small" disabled={offset === 0} onclick={() => offset = Math.max(0, offset - 50)}>← Назад</button>
      <button class="button secondary small" disabled={offset + 50 >= data.total} onclick={() => offset += 50}>Вперёд →</button>
    </div></div>
  </article>
  {#if data.unlinked.length}
    <article class="panel table-panel"><div class="panel-heading"><h2>Сдачи без связи с закупкой</h2></div>
      <p class="table-note">Архивные и ручные выгрузки: показываются последние 100 за период. Доход включён в общие итоги по дате получения; прибыль отдельной сдачи неизвестна.</p>
      <div class="table-overflow"><table><thead><tr><th>Файл</th><th>Задача</th><th>price_result</th><th>Получен</th></tr></thead>
        <tbody>{#each data.unlinked as row}<tr><td>#{row.export_id}</td><td>#{row.task_id}</td><td>{cash(row.earned_minor)}</td><td>{date(row.price_at)}</td></tr>{/each}</tbody>
      </table></div>
    </article>
  {/if}
{/if}
