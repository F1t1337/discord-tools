<script>
  import { api } from '../lib/api.js';
  import { refreshTick } from '../lib/store.js';
  import { number, date } from '../lib/format.js';
  import PageHeader from '../components/PageHeader.svelte';
  import DetailList from '../components/DetailList.svelte';
  import { reveal } from '../lib/anim.js';

  let data = $state(null);
  let error = $state('');
  let token = 0;

  async function load() {
    const my = ++token;
    try { const r = await api('/network'); if (my === token) { data = r; error = ''; } }
    catch (e) { if (my === token) error = e.message; }
  }
  $effect(() => { $refreshTick; load(); });

  const m = $derived(data?.last_minute || {});
  const f = $derived(data?.last_five_minutes || {});
  const c = $derived(data?.cleaner_last_five_minutes || {});
  const bindings = $derived(data?.bindings || []);
  const errors = $derived([...(data?.recent_errors || [])].reverse());
  const active = $derived(data?.active || []);
  const cooldowns = $derived(data?.cooldowns || {});

  const advice = $derived.by(() => {
    if (!data) return '';
    if (!data.available) return 'Обработчик не подключён. Доступны только сохранённые назначения прокси.';
    if (data.window_truncated) return 'Буфер метрик переполнен: показаны нижние оценки нагрузки.';
    if (!data.proxy_enabled || m.accounts_waiting_proxy) return 'Есть ожидание прокси. Добавление потоков не поможет — сначала проверьте пул и закреплённые прокси.';
    if (m.rate_limits) return 'За последнюю минуту есть 429. Не увеличивайте потоки; при повторяющихся ограничениях уменьшите их и сравните следующие 5 минут.';
    if (f.network_errors) return 'Есть сетевые ошибки. Проверьте доступность прокси перед изменением числа потоков.';
    if (data.uptime_seconds < 300 || f.requests < 100) return 'Пока мало наблюдений. Сравните метрики после нескольких минут стабильной нагрузки.';
    return 'За последнюю минуту 429 не было. Меняйте число потоков небольшими шагами и наблюдайте 5 минут.';
  });
</script>

<PageHeader title="Сеть и лимиты" description="Закрепление прокси и ответы Discord за последние 1 и 5 минут." />

{#if error}<div class="alert error">{error}</div>{/if}

{#if data}
  <div class="alert" role="status">{advice}</div>

  <div class="settings-grid">
    <article class="panel" use:reveal={0}>
      <div class="panel-heading"><div><p class="section-label">Лимиты</p><h2>Ответы Discord</h2></div></div>
      <DetailList pairs={[
        ['Запросов за 1 / 5 минут', number(m.requests) + ' / ' + number(f.requests)],
        ['429 за 1 / 5 минут', number(m.rate_limits) + ' / ' + number(f.rate_limits)],
        ['Доля 429 за 5 минут', number(f.rate_limit_percent) + '%'],
        ['Заданные ожидания за 5 минут', number(f.retry_after_seconds) + ' с'],
        ['Аккаунтов с 429', number(f.accounts_limited)],
        ['Сетевых ошибок за 5 минут', number(f.network_errors)],
        ['Ответов 401/403 за 5 минут', number(f.forbidden)],
        ['Очистка: запросов / 429 за 5 минут', number(c.requests) + ' / ' + number(c.rate_limits)],
      ]} />
    </article>
    <article class="panel" use:reveal={1}>
      <div class="panel-heading"><div><p class="section-label">Прокси</p><h2>Пул и закрепления</h2></div></div>
      <DetailList pairs={[
        ['Прокси обязательны', data.proxy_enabled ? 'Да, прямой выход запрещён' : 'Выключены — запросы заблокированы'],
        ['Рабочих / свободных', number(data.alive_proxies) + ' / ' + number(data.free_proxies)],
        ['Закреплено / недоступно', number(data.assigned_proxies) + ' / ' + number(data.unavailable_bindings)],
        ['Потоки очистки', number(data.cleaner_workers)],
        ['Ожидание прокси за минуту', number(m.accounts_waiting_proxy)],
        ['Время сбора метрик', number(data.uptime_seconds) + ' с'],
      ]} />
    </article>
  </div>

  <div class="panel table-panel">
    <div class="panel-heading" style:padding="20px 24px 0"><div><p class="section-label">Закрепления</p><h2>Аккаунты (последние 250)</h2></div></div>
    <div class="table-overflow">
      <table>
        <thead><tr><th>Аккаунт</th><th>Прокси</th><th>Доступность</th><th>Этап</th><th>Cooldown</th></tr></thead>
        <tbody>
          {#if !bindings.length}
            <tr><td colspan="5"><div class="empty-state"><strong>Нет закреплений</strong><p>Появятся при обработке аккаунтов.</p></div></td></tr>
          {:else}
            {#each bindings as b}
              <tr>
                <td class="mono">{(b.account_id ? '#' + b.account_id + ' · ' : '') + b.account}</td>
                <td class="mono">{b.proxy} · {b.proxy_id}</td>
                <td>{b.available ? 'Доступен' : 'Ожидание'}</td>
                <td>{active.find((a) => a.account === b.account)?.stage || '—'}</td>
                <td>{cooldowns[b.account] ? cooldowns[b.account] + ' с' : '—'}</td>
              </tr>
            {/each}
          {/if}
        </tbody>
      </table>
    </div>
  </div>

  <div class="panel table-panel">
    <div class="panel-heading" style:padding="20px 24px 0"><div><p class="section-label">Ошибки</p><h2>Последние 50 ограничений и сетевых ошибок</h2></div></div>
    <div class="table-overflow">
      <table>
        <thead><tr><th>Время</th><th>Аккаунт</th><th>Этап</th><th>Ошибка</th><th>Область</th><th>Ожидание</th><th>Запрос</th></tr></thead>
        <tbody>
          {#if !errors.length}
            <tr><td colspan="7"><div class="empty-state"><strong>Ошибок нет</strong><p>За последние 5 минут ограничений и сбоев не зафиксировано.</p></div></td></tr>
          {:else}
            {#each errors as e}
              <tr>
                <td>{date(e.at)}</td><td class="mono">{e.account}</td><td>{e.stage}</td>
                <td>{e.status || e.error}</td><td>{e.scope || '—'}</td>
                <td>{number(e.retry_after)} с</td><td class="mono">{e.method} {e.route}</td>
              </tr>
            {/each}
          {/if}
        </tbody>
      </table>
    </div>
  </div>
{/if}
