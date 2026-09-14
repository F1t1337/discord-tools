<script>
  import { api } from '../lib/api.js';
  import { refreshTick } from '../lib/store.js';
  import { number, money, date } from '../lib/format.js';
  import PageHeader from '../components/PageHeader.svelte';
  import Skeleton from '../components/Skeleton.svelte';

  let items = $state(null);
  let error = $state('');
  let token = 0;

  async function load() {
    const my = ++token;
    try { const r = await api('/sellers'); if (my === token) { items = r.items || []; error = ''; } }
    catch (e) { if (my === token) error = e.message; }
  }
  $effect(() => { $refreshTick; load(); });

  // Цвет качества по проценту валида.
  const tone = (pct) => pct >= 80 ? 'good' : pct >= 50 ? 'warn' : 'bad';
  const cost = (v) => v == null ? '—' : money(v);
</script>

<PageHeader title="Продавцы" description="Рейтинг продавцов: у кого ниже цена за валидный аккаунт — тот лучше." />

{#if error}<div class="alert error">{error}</div>{/if}
{#if !items && !error}<Skeleton kind="table" count={8} />{/if}

{#if items}
  <p class="muted">Считается по всем купленным аккаунтам. «Цена за валид» = потрачено ÷ валидные (не невалидные);
    это реальная стоимость рабочего аккаунта у продавца. Сортировка — по ней, лучшие сверху.</p>
  <div class="panel table-panel">
    <div class="table-overflow">
      <table>
        <thead><tr>
          <th>#</th><th>Продавец</th><th>Куплено</th><th>Отправлено</th><th>Невалидные</th>
          <th>% валида</th><th>Потрачено</th><th>Ср. цена</th><th>Цена за валид</th>
        </tr></thead>
        <tbody>
          {#if !items.length}
            <tr><td colspan="9"><div class="empty-state"><strong>Пока нет данных</strong><p>Появятся после первых покупок с указанием продавца.</p></div></td></tr>
          {:else}
            {#each items as s, i (s.seller)}
              <tr class:top-seller={i === 0 && s.cost_per_valid != null}>
                <td class="mono">{i + 1}</td>
                <td>{s.seller}</td>
                <td class="nowrap">{number(s.bought)}</td>
                <td class="nowrap">{number(s.sent)}</td>
                <td class="nowrap">{number(s.invalid)}</td>
                <td class="nowrap"><b class="valid-pct {tone(s.valid_percent)}">{number(s.valid_percent)}%</b></td>
                <td class="nowrap">{money(s.spent)}</td>
                <td class="nowrap">{money(s.avg_price)}</td>
                <td class="nowrap"><b>{cost(s.cost_per_valid)}</b></td>
              </tr>
            {/each}
          {/if}
        </tbody>
      </table>
    </div>
  </div>
{/if}

<style>
  .valid-pct.good { color: var(--mint); }
  .valid-pct.warn { color: var(--amber); }
  .valid-pct.bad { color: var(--red); }
  tr.top-seller td { background: var(--accent-soft); }
  tr.top-seller td:first-child { color: var(--accent); font-weight: 700; }
</style>
