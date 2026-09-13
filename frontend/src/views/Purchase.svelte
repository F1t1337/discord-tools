<script>
  import { api } from '../lib/api.js';
  import { refreshTick, toast, tick, confirmDialog } from '../lib/store.js';
  import { number, money } from '../lib/format.js';
  import PageHeader from '../components/PageHeader.svelte';
  import Metric from '../components/Metric.svelte';
  import Icon from '../components/Icon.svelte';
  import { reveal } from '../lib/anim.js';

  let status = $state(null);
  let error = $state('');
  let token = 0;

  let pmax = $state(80);
  let chatMin = $state(60);
  let estimate = $state(null);
  let estimating = $state(false);
  let count = $state(null);
  let workers = $state(20);

  async function load() {
    const my = ++token;
    try { const s = await api('/purchase/status'); if (my === token) { status = s; error = ''; } }
    catch (e) { if (my === token) error = e.message; }
  }
  $effect(() => { $refreshTick; load(); });

  const running = $derived(status && (status.status === 'running' || status.status === 'stopping'));
  const workerReady = $derived(!!status?.worker_running);
  const pipeline = $derived(status?.pipeline || {});
  const progress = $derived(status?.requested ? Math.min(100, (status.bought || 0) / status.requested * 100) : 0);
  const statusText = $derived(({ idle: 'Задача не запущена', running: 'Задача выполняется',
    stopping: 'Останавливается', done: 'Задача завершена', error: 'Задача прервана' })[status?.status] || '—');

  async function runEstimate() {
    if (!(pmax > 0) || !(chatMin >= 0)) return toast('Введите цену и минимум чатов');
    estimating = true;
    try {
      const r = await api('/purchase/estimate?' + new URLSearchParams({ pmax, chat_min: chatMin }));
      estimate = r;
      if (count == null || count > r.max_affordable) count = r.max_affordable || null;
      toast('Найдено ' + number(r.count) + ' аккаунтов');
    } catch (e) { toast(e.message); }
    finally { estimating = false; }
  }

  async function start() {
    if (!(count >= 1)) return toast('Укажите количество аккаунтов');
    if (!(workers >= 1 && workers <= 200)) return toast('Потоки очистки: 1–200');
    if (estimate && count > estimate.max_affordable) return toast('Не больше ' + number(estimate.max_affordable) + ' на баланс');
    const ok = await confirmDialog('Запустить покупку?',
      'Будет куплено до ' + number(count) + ' аккаунтов, максимум ' + money(pmax * count)
      + ' (по ' + money(pmax) + ' за шт.). Тратятся реальные деньги.');
    if (!ok) return;
    try {
      await api('/purchase/start', { method: 'POST',
        body: JSON.stringify({ pmax: Number(pmax), chat_min: Number(chatMin), count: Number(count), cleaner_workers: Number(workers) }) });
      toast('Задача запущена'); tick();
    } catch (e) { toast(e.message); }
  }

  async function stop() {
    if (!(await confirmDialog('Остановить задачу?',
      'Новые покупки прекратятся. Уже купленные аккаунты продолжат обработку.'))) return;
    try { await api('/purchase/stop', { method: 'POST', body: '{}' }); toast('Остановка задачи'); tick(); }
    catch (e) { toast(e.message); }
  }
</script>

<PageHeader title="Задача покупки"
  description="Покупка аккаунтов с LZT по фильтрам и обработка в реальном времени.">
  {#snippet actions()}
    {#if running}
      <button class="button danger" onclick={stop}><Icon name="stop" size={16} /> Остановить задачу</button>
    {/if}
  {/snippet}
</PageHeader>

{#if !workerReady}
  <div class="alert warning">Обработка не запущена. Задача покупки недоступна: запустите сервис с обработчиком.</div>
{/if}
{#if error}<div class="alert error">{error}</div>{/if}

<div class="metrics">
  <Metric title="Куплено" value={status?.bought || 0} note="Успешных покупок" icon="purchase" index={0} />
  <Metric title="Пропущено мёртвых" value={status?.skipped_dead || 0} note="Отбраковано при покупке" icon="ban" index={1} />
  <Metric title="В очистке" value={pipeline.cleaning || 0} note="Сейчас очищаются" icon="refresh" index={2} />
  <Metric title="Готово" value={pipeline.ready || 0} note="Состояние ready" icon="check" index={3} />
</div>

<div class="steps">
  <section class="step" use:reveal={0}>
    <div class="step-head"><span class="step-num">1</span><h2>Фильтр покупки</h2></div>
    <p class="muted">Укажите максимальную цену и минимум чатов на аккаунте. Остальные фильтры
      (язык, страна, состояние, срок регистрации) заданы заранее. Запросы к LZT не проксируются.</p>
    <div class="inline-fields">
      <div class="field"><label for="pmax">Макс. цена, ₽</label>
        <input id="pmax" type="number" min="1" max="100000" step="1" bind:value={pmax}></div>
      <div class="field"><label for="chatmin">Мин. чатов</label>
        <input id="chatmin" type="number" min="0" max="1000000" step="1" bind:value={chatMin}></div>
      <button class="button primary" onclick={runEstimate} disabled={estimating || !workerReady}>Рассчитать</button>
    </div>
  </section>

  {#if estimate}
    <section class="step" use:reveal={1}>
      <div class="step-head"><span class="step-num">2</span><h2>Результат расчёта</h2></div>
      <div class="statgrid">
        <div class="stat"><div class="k">Подходящих аккаунтов</div><div class="v">{number(estimate.count)}{#if estimate.truncated}<span class="muted"> *</span>{/if}</div></div>
        <div class="stat"><div class="k">Суммарная стоимость</div><div class="v">{money(estimate.total_cost)}</div></div>
        <div class="stat"><div class="k">Баланс LZT</div><div class="v">{estimate.balance == null ? '—' : money(estimate.balance)}</div></div>
        <div class="stat hero"><div class="k">Можно купить на баланс</div><div class="v">{number(estimate.max_affordable)}</div></div>
      </div>
      {#if estimate.truncated}<p class="muted">* стоимость посчитана по первым {number(estimate.collected)} аккаунтам.</p>{/if}
    </section>

    <section class="step" use:reveal={2}>
      <div class="step-head"><span class="step-num">3</span><h2>Параметры и запуск</h2></div>
      <p class="muted">Сколько аккаунтов купить (не больше доступного на баланс) и во сколько потоков
        очищать. Мёртвые аккаунты отбраковываются — итог может быть меньше запроса.</p>
      <div class="inline-fields">
        <div class="field"><label for="count">Кол-во аккаунтов</label>
          <input id="count" type="number" min="1" max={estimate.max_affordable || 1} step="1" bind:value={count}></div>
        <div class="field"><label for="workers">Потоки очистки</label>
          <input id="workers" type="number" min="1" max="200" step="1" bind:value={workers}></div>
        <button class="button primary" onclick={start} disabled={running || !workerReady || !(estimate.max_affordable > 0)}>
          <Icon name="play" size={16} /> Запустить задачу
        </button>
      </div>
    </section>
  {/if}

  <section class="step" use:reveal={3}>
    <div class="step-head"><span class="step-num">4</span><h2>Живой прогресс</h2></div>
    <div class="progress-label"><span>{statusText}{status?.message ? ' · ' + status.message : ''}</span>
      <span><b>{number(status?.bought || 0)}</b> / {number(status?.requested || 0)}</span></div>
    <div class="progress"><span style:width={progress + '%'}></span></div>

    <div class="funnel">
      <div class="funnel-step"><div class="v">{number((pipeline.new || 0) + (pipeline.validated || 0))}</div><div class="k">В валидации</div></div>
      <Icon name="arrow" cls="funnel-arrow" size={18} />
      <div class="funnel-step"><div class="v">{number(pipeline.cleaning || 0)}</div><div class="k">В очистке</div></div>
      <Icon name="arrow" cls="funnel-arrow" size={18} />
      <div class="funnel-step"><div class="v">{number(pipeline.ready || 0)}</div><div class="k">Готово</div></div>
    </div>

    <dl class="detail-list">
      <dt>Проверено при покупке</dt><dd>{number(status?.checked || 0)}</dd>
      <dt>Пропущено (мёртвые/продан)</dt><dd>{number(status?.skipped_dead || 0)}</dd>
      <dt>Ошибок</dt><dd>{number(status?.errors || 0)}</dd>
      <dt>Потрачено</dt><dd>{money(status?.spent || 0)}</dd>
      <dt>Баланс LZT</dt><dd>{status?.balance == null ? '—' : money(status.balance)}</dd>
      <dt>Очищено (ждут финальной)</dt><dd>{number(pipeline.cleaned || 0)}</dd>
    </dl>
  </section>
</div>
