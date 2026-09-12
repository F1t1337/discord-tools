'use strict';
const $ = id => document.getElementById(id);
const titles = {
  overview: ['Обзор', 'Текущее состояние системы и последние изменения.'],
  purchase: ['Задача покупки', 'Покупка аккаунтов с LZT по фильтрам и обработка в реальном времени.'],
  accounts: ['Аккаунты', 'Поиск и просмотр записей без раскрытия токенов доступа.'],
  tokens: ['Токены', 'Загрузка токенов в обработку и выгрузка готовых.'],
  network: ['Сеть и лимиты', 'Закрепление прокси и ответы Discord за последние 1 и 5 минут.'],
  proxies: ['Прокси', 'Загрузка, проверка на живость и пул рабочих прокси.'],
  logs: ['Журнал событий', 'События приложения и диагностика сервера.'],
  settings: ['Настройки', 'Параметры доступа и текущая конфигурация сервера.'],
};
const labels = {
  pending_review: 'Незавершённые',
  new: 'Новые', validated: 'Проверены', cleaning: 'В очистке', cleaned: 'Очищены',
  ready: 'Готовы', sent: 'Отправлены',
  invalid: 'Невалидные', locked: 'Заблокированы',
};
let csrf = '', loggedIn = false, view = 'overview', requestGeneration = 0, viewController;
let accountOffset = 0, accountTotal = 0;
let toastTimer, searchTimer;
let evtSource = null, liveDebounce = null, fallbackTimer = null, streamRetry = 0;
let animatedView = null, enterAnim = false;
const pageSize = 25;
const SVGNS = 'http://www.w3.org/2000/svg';
const prefersReduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
function icon(name, className) {
  const svg = document.createElementNS(SVGNS, 'svg');
  if (className) svg.setAttribute('class', className);
  svg.setAttribute('aria-hidden', 'true');
  const use = document.createElementNS(SVGNS, 'use');
  use.setAttribute('href', '#i-' + name);
  svg.append(use);
  return svg;
}
function countUp(el, to) {
  to = Number(to) || 0;
  if (prefersReduced || to === 0) { el.textContent = number(to); return; }
  const dur = 650, start = performance.now();
  (function frame(now) {
    const p = Math.min(1, (now - start) / dur), eased = 1 - Math.pow(1 - p, 3);
    el.textContent = number(Math.round(to * eased));
    if (p < 1) requestAnimationFrame(frame);
  })(start);
}
function runEnter(host) {
  if (!host || prefersReduced) return;
  host.querySelectorAll('.metric, .panel, .system-strip, .table-panel, .log-row').forEach((el, i) => {
    el.style.setProperty('--d', Math.min(i * 0.05, 0.4) + 's');
    el.classList.remove('rise'); void el.offsetWidth; el.classList.add('rise');
  });
  host.querySelectorAll('.metric-value[data-to]').forEach(el => countUp(el, el.dataset.to));
}
function metricCard([title, value, note, iconName]) {
  const card = element('article', null, 'metric'), label = element('div', null, 'metric-label');
  label.append(element('span', title), icon(iconName, 'metric-icon'));
  const valueEl = element('p', number(value), 'metric-value');
  valueEl.dataset.to = Number(value) || 0;
  card.append(label, valueEl, element('p', note, 'metric-note'));
  return card;
}
const number = value => new Intl.NumberFormat('ru-RU').format(Number(value) || 0);
const money = value => new Intl.NumberFormat('ru-RU', {style: 'currency', currency: 'RUB', maximumFractionDigits: 2}).format(Number(value) || 0);
function date(value) {
  if (!value) return '—';
  const parsed = new Date(typeof value === 'number' ? value * 1000 : value);
  return Number.isNaN(parsed.getTime()) ? '—' : parsed.toLocaleString('ru-RU', {day:'2-digit', month:'2-digit', year:'numeric', hour:'2-digit', minute:'2-digit'});
}
function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined && text !== null) node.textContent = String(text);
  if (className) node.className = className;
  return node;
}
function pill(status) {
  const good = ['ready', 'sent'];
  const bad = ['invalid', 'locked'];
  const warn = ['cleaning'];
  return element('span', labels[status] || 'Архивное состояние', 'status-pill ' + (good.includes(status) ? 'good' : bad.includes(status) ? 'bad' : warn.includes(status) ? 'warn' : 'accent'));
}
function details(target, pairs) {
  const fragment = document.createDocumentFragment();
  for (const [key, value] of pairs) fragment.append(element('dt', key), element('dd', value ?? '—'));
  $(target).replaceChildren(fragment);
}
function empty(target, title, description, columns) {
  const box = element('div', null, 'empty-state');
  box.append(element('strong', title), element('p', description));
  if (columns) {
    const tr = element('tr'), td = element('td'); td.colSpan = columns; td.append(box); tr.append(td);
    $(target).replaceChildren(tr);
  } else $(target).replaceChildren(box);
}
function toast(message) {
  $('toast').textContent = message; $('toast').hidden = false;
  clearTimeout(toastTimer); toastTimer = setTimeout(() => $('toast').hidden = true, 4500);
}
async function api(path, options = {}) {
  const headers = new Headers(options.headers || {});
  if (options.method && options.method !== 'GET') {
    headers.set('X-CSRF-Token', csrf);
    headers.set('Content-Type', 'application/json');
  }
  const response = await fetch('/api' + path, {...options, headers, credentials: 'same-origin', cache: 'no-store'});
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    if (response.status === 401 && !path.startsWith('/auth/login')) {
      showLogin('Сессия завершена. Войдите снова.');
      const bootstrap = await fetch('/api/auth/session', {credentials:'same-origin', cache:'no-store'});
      if (bootstrap.ok) csrf = (await bootstrap.json()).csrf;
    }
    throw new Error(body.error || 'Сервер недоступен. Повторите запрос.');
  }
  return options.blob ? response.blob() : response.json();
}
function showLogin(message = '') {
  loggedIn = false; stopLive(); viewController?.abort(); animatedView = null;
  $('workspace').hidden = true; $('boot-screen').hidden = true; $('login-screen').hidden = false;
  $('login-error').textContent = message; $('password').value = ''; $('token-input').value = '';
  document.querySelectorAll('dialog[open]').forEach(dialog => dialog.close());
}
function showWorkspace(username) {
  loggedIn = true; $('login-screen').hidden = true; $('boot-screen').hidden = true; $('workspace').hidden = false;
  $('profile-name').textContent = username; animatedView = null; navigate(); $('main').focus(); startLive();
}
async function boot() {
  $('workspace').hidden = true; $('login-screen').hidden = true; $('boot-screen').hidden = false;
  $('boot-retry').hidden = true; $('boot-message').textContent = 'Подключение к панели…';
  try {
    const result = await api('/auth/session'); csrf = result.csrf;
    result.authenticated ? showWorkspace(result.username) : showLogin();
  } catch (error) {
    $('boot-message').textContent = error.message; $('boot-retry').hidden = false;
  }
}
function navigate() {
  const hash = location.hash.slice(1);
  view = Object.hasOwn(titles, hash) ? hash : 'overview';
  const [title, description] = titles[view];
  $('page-title').textContent = title; $('page-description').textContent = description;
  $('breadcrumb').textContent = 'РАБОЧЕЕ ПРОСТРАНСТВО / ' + title.toUpperCase();
  document.title = title + ' · Discord Tools';
  document.querySelectorAll('.view').forEach(node => node.hidden = node.id !== 'view-' + view);
  document.querySelectorAll('[data-view]').forEach(node => {
    if (node.dataset.view === view) node.setAttribute('aria-current', 'page');
    else node.removeAttribute('aria-current');
  });
  if (loggedIn) loadView();
}
function connection(ok, live) {
  $('connection-light').className = 'server-light ' + (ok ? 'online' : 'offline');
  $('connection-label').textContent = ok ? (live ? 'Live-подключение' : 'Сервер доступен') : 'Нет связи с сервером';
  $('connection-time').textContent = ok ? 'Обновлено ' + new Date().toLocaleTimeString('ru-RU') : 'Проверьте подключение';
}
// ---------- Живые обновления (SSE) с откатом к поллингу ----------
function stopLive() {
  if (evtSource) { evtSource.close(); evtSource = null; }
  clearTimeout(fallbackTimer); clearTimeout(liveDebounce); fallbackTimer = null;
}
function pollFallback() {
  clearTimeout(fallbackTimer);
  if (!loggedIn || !$('auto-refresh').checked) return;
  fallbackTimer = setTimeout(async () => {
    if (!document.hidden) await loadView();
    pollFallback();
  }, 5000);
}
function nudgeLive() {
  clearTimeout(liveDebounce);
  liveDebounce = setTimeout(() => { if (!document.hidden && loggedIn) loadView(); }, 180);
}
function startLive() {
  stopLive();
  if (!loggedIn || !$('auto-refresh').checked) return;
  if (!window.EventSource) { connection(true); pollFallback(); return; }
  const source = new EventSource('/api/stream');
  evtSource = source;
  source.onopen = () => { streamRetry = 0; connection(true, true); };
  source.addEventListener('update', () => { streamRetry = 0; connection(true, true); nudgeLive(); });
  source.onerror = () => {
    if (evtSource !== source) return;
    connection(false);
    if (++streamRetry >= 4) { stopLive(); pollFallback(); }  // SSE недоступен — откат к поллингу
  };
}
async function loadView() {
  if (!loggedIn) return;
  viewController?.abort(); viewController = new AbortController();
  const generation = ++requestGeneration, opts = {signal: viewController.signal};
  $('page-loading').hidden = false; $('page-error').hidden = true;
  try {
    let result;
    if (view === 'overview') {
      result = await Promise.all([api('/status', opts), api('/statistics/today', opts), api('/statistics/range?days=' + $('chart-days').value, opts)]);
    } else if (view === 'accounts') {
      result = await api('/accounts?' + new URLSearchParams({limit:pageSize, offset:accountOffset, status:$('account-status').value, search:$('account-search').value.trim()}), opts);
    } else if (view === 'purchase') result = await api('/purchase/status', opts);
    else if (view === 'proxies') result = await api('/proxies', opts);
    else if (view === 'network') result = await api('/network', opts);
    else if (view === 'tokens') result = await api('/tokens/export/status', opts);
    else if (view === 'logs') result = await api('/logs?limit=100&level=' + $('log-level').value, opts);
    else result = await Promise.all([api('/settings', opts), api('/lzt/balance', opts)]);
    if (generation !== requestGeneration || !loggedIn) return;
    enterAnim = view !== animatedView;
    if (view === 'overview') renderOverview(...result);
    else if (view === 'purchase') renderPurchase(result);
    else if (view === 'accounts') renderAccounts(result);
    else if (view === 'proxies') renderProxies(result);
    else if (view === 'network') renderNetwork(result);
    else if (view === 'tokens') renderTokens(result);
    else if (view === 'logs') renderLogs(result);
    else renderSettings(...result);
    connection(true, !!evtSource);
    $('last-updated').textContent = 'Последнее обновление: ' + new Date().toLocaleTimeString('ru-RU');
    if (enterAnim) { animatedView = view; runEnter($('view-' + view)); }
  } catch (error) {
    if (error.name !== 'AbortError' && generation === requestGeneration && loggedIn) {
      connection(false); $('page-error').textContent = error.message + ' Показаны последние полученные данные.'; $('page-error').hidden = false;
    }
  } finally {
    if (generation === requestGeneration) $('page-loading').hidden = true;
  }
}
function renderOverview(state, today, series) {
  const counts = state.counts, total = Object.values(counts).reduce((sum, n) => sum + n, 0);
  const metrics = [
    ['Всего аккаунтов', total, 'Записей в базе', 'accounts'],
    ['Готовы', counts.ready || 0, 'Состояние ready', 'check'],
    ['В обработке', state.pending_count, 'Промежуточные состояния', 'refresh'],
    ['Невалидные', counts.invalid || 0, 'По последней проверке', 'ban'],
  ];
  $('metrics').replaceChildren(...metrics.map(metricCard));
  $('system-state').textContent = state.mode === 'monitor' ? 'Мониторинг БД' : state.stopping ? 'Остановка' : state.running ? 'Обработка запущена' : 'Обработка остановлена';
  $('system-state').className = 'status-pill ' + (state.running ? 'good' : 'accent');
  $('system-detail').textContent = state.mode === 'monitor' ? 'Панель читает сохранённые данные; обработчики в этом процессе не запущены.' : 'Панель подключена к текущему процессу приложения.';
  $('stop-pipeline').hidden = !state.running && !state.stopping;
  $('stop-pipeline').disabled = state.stopping;
  $('pending-notice').hidden = state.pending_count === 0;
  $('pending-text').textContent = number(state.pending_count) + ' записей в промежуточных состояниях. Статус в БД сам по себе не подтверждает активность обработки. После перезапуска эти записи требуют ручного разбора; автоматического возобновления нет.';
  const distribution = Object.entries(counts).sort((a,b) => b[1]-a[1]);
  if (!distribution.length) empty('status-distribution', 'Пока нет записей', 'Распределение появится, когда база будет содержать данные.');
  else $('status-distribution').replaceChildren(...distribution.map(([status, count]) => {
    const row = element('div', null, 'distribution-row'), head = element('div', labels[status] || 'Архивное состояние');
    head.append(element('strong', number(count))); const meter = element('meter'); meter.min=0; meter.max=Math.max(total,1); meter.value=count; meter.setAttribute('aria-label', (labels[status] || 'Архивное состояние') + ': ' + number(count)); row.append(head,meter); return row;
  }));
  details('today-summary', [['Куплено',number(today.tokens_bought)],['Очищено',number(today.tokens_cleaned)],['Отправлено',number(today.tokens_sent)],['Потрачено',money(today.money_spent)]]);
  const alive = Object.values(state.threads).filter(Boolean).length;
  const uptime = Math.floor(state.uptime_seconds / 3600) + ' ч ' + Math.floor(state.uptime_seconds % 3600 / 60) + ' мин';
  details('server-summary', [['Панель работает',uptime],['Схема базы','Версия ' + state.schema_version],['Потоки обработки',state.mode === 'monitor' ? 'Не подключены' : alive + ' / ' + Object.keys(state.threads).length],['Очередь новых',state.queues ? number(state.queues.new_tokens) : 'Нет данных процесса'],['Очередь очистки',state.queues ? number(state.queues.validated) : 'Нет данных процесса']]);
  renderChart(series);
}
function svgElement(tag, attrs, text) {
  const node = document.createElementNS('http://www.w3.org/2000/svg', tag);
  for (const [key,value] of Object.entries(attrs)) node.setAttribute(key, String(value));
  if (text !== undefined) node.textContent = text; return node;
}
function renderChart(series) {
  const svg = svgElement('svg', {viewBox:'0 0 600 245', role:'img', 'aria-label':'Количество очищенных и отправленных аккаунтов по дням'});
  const values = series.flatMap(row => [Number(row.tokens_cleaned)||0,Number(row.tokens_sent)||0]);
  const top = Math.max(4, ...values), ceiling = Math.ceil(top/4)*4;
  for (let i=0;i<5;i++) {
    const y=20+i*46;
    svg.append(svgElement('line',{x1:40,y1:y,x2:585,y2:y,class:'chart-grid'}),svgElement('text',{x:30,y:y+4,'text-anchor':'end',class:'chart-label'},number(ceiling*(4-i)/4)));
  }
  for (const [key,cls] of [['tokens_cleaned','chart-cleaned'],['tokens_sent','chart-sent']]) {
    const points = series.map((row,i) => (40+i*545/Math.max(1,series.length-1))+','+(204-(Number(row[key])||0)/ceiling*184));
    const line = svgElement('polyline',{points:points.join(' '),class:cls + (enterAnim && !prefersReduced ? ' chart-draw' : '')});
    svg.append(line);
    if (enterAnim && !prefersReduced) { try { line.style.setProperty('--len', line.getTotalLength()); } catch (e) {} }
  }
  const positions = [...new Set([0,Math.floor((series.length-1)/2),series.length-1])];
  for (const i of positions) if (series[i]) svg.append(svgElement('text',{x:40+i*545/Math.max(1,series.length-1),y:235,'text-anchor':i===0?'start':i===series.length-1?'end':'middle',class:'chart-label'},series[i].date.slice(5).split('-').reverse().join('.')));
  const text = series.map(row => row.date + ': очищено ' + number(row.tokens_cleaned) + ', отправлено ' + number(row.tokens_sent)).join('; ');
  svg.append(svgElement('desc',{},text)); $('activity-chart').replaceChildren(svg);
}
function showDetails(account) {
  details('account-detail', [['ID',account.id],['Аккаунт',account.username],['Состояние',labels[account.status]||'Архивное состояние'],['Продавец',account.seller_username],['Цена',money(account.price)],['Добавлен',date(account.created_at)],['Проверен',date(account.validated_at)],['Очищен',date(account.cleaned_at)],['Отправлен',date(account.sent_at)],['Последнее действие',account.cleaning_progress||'—']]);
  $('detail-dialog').showModal();
}
function pageLabel(total, offset) { return total ? number(offset+1)+'–'+number(Math.min(offset+pageSize,total))+' из '+number(total) : 'Нет записей'; }
function renderAccounts(result) {
  accountTotal=result.total; $('accounts-page').textContent=pageLabel(accountTotal,accountOffset);
  $('accounts-prev').disabled=accountOffset===0; $('accounts-next').disabled=accountOffset+pageSize>=accountTotal;
  if (!result.items.length) return empty('accounts-body','Записи не найдены','Попробуйте изменить поиск или фильтр.',7);
  $('accounts-body').replaceChildren(...result.items.map(account => {
    const row=element('tr');
    row.append(element('td','#'+account.id,'mono'),element('td',account.username||'Без имени'));
    const status=element('td'); status.append(pill(account.status)); row.append(status,element('td',account.seller_username||'—'),element('td',money(account.price),'nowrap'),element('td',date(account.created_at),'mono nowrap'));
    const action=element('td'), button=element('button',null,'icon-button'); button.append(icon('arrow')); button.setAttribute('aria-label','Подробнее об аккаунте '+account.id); button.addEventListener('click',()=>showDetails(account)); action.append(button); row.append(action); return row;
  }));
}
function proxyStatusPill(status) {
  const map = {alive: ['good', 'Живой'], dead: ['bad', 'Мёртвый'], unchecked: ['accent', 'Не проверен']};
  const [cls, text] = map[status] || ['accent', status];
  return element('span', text, 'status-pill ' + cls);
}
let proxyBusy = false;
function renderProxies(result) {
  const counts = result.counts || {};
  const metrics = [
    ['Рабочих', counts.alive || 0, 'Готовы к использованию', 'check'],
    ['Мёртвых', counts.dead || 0, 'Не прошли проверку', 'ban'],
    ['Не проверено', counts.unchecked || 0, 'Ожидают проверки', 'clock'],
    ['Всего в пуле', counts.total || 0, 'Записей всего', 'proxies'],
  ];
  $('proxy-metrics').replaceChildren(...metrics.map(metricCard));
  const job = result.job || {};
  proxyBusy = !!job.running;
  const banner = $('proxy-job');
  if (job.running) {
    banner.hidden = false; banner.className = 'alert';
    const kind = job.kind === 'recheck' ? 'Перепроверка пула' : 'Проверка новых прокси';
    banner.textContent = kind + ': ' + number(job.done) + ' из ' + number(job.total) +
      ' · живых ' + number(job.alive) + ', мёртвых ' + number(job.dead);
  } else if (job.error) {
    banner.hidden = false; banner.className = 'alert error';
    banner.textContent = 'Проверка прервана из-за ошибки. Попробуйте ещё раз.';
  } else if (job.finished_at) {
    banner.hidden = false; banner.className = 'alert warning';
    banner.textContent = 'Проверка завершена: обработано ' + number(job.total) +
      ', живых ' + number(job.alive) + ', мёртвых ' + number(job.dead) + '.';
  } else banner.hidden = true;
  details('proxy-summary', [['Рабочих', number(counts.alive || 0)], ['Мёртвых', number(counts.dead || 0)],
    ['Не проверено', number(counts.unchecked || 0)], ['Всего', number(counts.total || 0)],
    ['Discord через прокси', result.proxy_enabled ? 'Обязательно' : 'Работа заблокирована']]);
  for (const id of ['proxy-import', 'proxy-recheck', 'proxy-clear-dead', 'proxy-clear-all']) $(id).disabled = proxyBusy;
  const items = result.items || [];
  if (!items.length) return empty('proxies-body', 'Пул пуст', 'Добавьте прокси через форму слева.', 5);
  $('proxies-body').replaceChildren(...items.map(item => {
    const row = element('tr');
    row.append(element('td', item.endpoint, 'mono'), element('td', item.auth ? 'логин/пароль' : 'нет'));
    const status = element('td'); status.append(proxyStatusPill(item.status));
    row.append(status, element('td', item.ping != null ? item.ping + ' мс' : '—', 'nowrap'),
      element('td', item.last_checked_at ? date(item.last_checked_at) : '—', 'mono nowrap'));
    return row;
  }));
}
function renderNetwork(result) {
  const minute = result.last_minute || {}, five = result.last_five_minutes || {}, cleaning = result.cleaner_last_five_minutes || {};
  details('network-summary', [
    ['Прокси обязательны', result.proxy_enabled ? 'Да, прямой выход запрещён' : 'Прокси выключены — Discord-запросы заблокированы'],
    ['Рабочих / свободных прокси', number(result.alive_proxies) + ' / ' + number(result.free_proxies)],
    ['Закреплено / недоступно', number(result.assigned_proxies) + ' / ' + number(result.unavailable_bindings)],
    ['Потоки очистки', number(result.cleaner_workers)],
    ['Запросов за 1 / 5 минут', number(minute.requests) + ' / ' + number(five.requests)],
    ['429 за 1 / 5 минут', number(minute.rate_limits) + ' / ' + number(five.rate_limits)],
    ['Очистка: запросов / 429 за 5 минут', number(cleaning.requests) + ' / ' + number(cleaning.rate_limits)],
    ['Доля 429 за 5 минут', number(five.rate_limit_percent) + '%'],
    ['Заданные Discord ожидания за 5 минут', number(five.retry_after_seconds) + ' с'],
    ['Аккаунтов с 429', number(five.accounts_limited)],
    ['Аккаунтов без доступного прокси за минуту', number(minute.accounts_waiting_proxy)],
    ['Сетевых ошибок за 5 минут', number(five.network_errors)],
    ['Ответов 401/403 за 5 минут', number(five.forbidden)],
    ['Время сбора метрик', number(result.uptime_seconds) + ' с'],
  ]);
  let advice;
  if (!result.available) advice = 'Обработчик не подключён. Доступны только сохранённые назначения прокси.';
  else if (result.window_truncated) advice = 'Буфер метрик переполнен: показаны нижние оценки нагрузки. Не увеличивайте потоки по этим данным.';
  else if (!result.proxy_enabled || minute.accounts_waiting_proxy) advice = 'Есть ожидание прокси. Добавление потоков не поможет: сначала проверьте пул и закреплённые прокси.';
  else if (minute.rate_limits) advice = 'За последнюю минуту есть 429. Не увеличивайте потоки; при повторяющихся ограничениях уменьшите их и сравните следующие 5 минут. Паузы Discord соблюдаются автоматически.';
  else if (five.network_errors) advice = 'Есть сетевые ошибки. Проверьте доступность прокси перед изменением числа потоков.';
  else if (result.uptime_seconds < 300 || five.requests < 100) advice = 'Пока мало наблюдений. Сравните метрики после нескольких минут стабильной нагрузки.';
  else advice = 'За последнюю минуту 429 не было. Это не гарантирует запас лимита: меняйте число потоков небольшими шагами и наблюдайте 5 минут.';
  $('network-advice').textContent = advice;
  const bindings = result.bindings || [];
  $('network-bindings').replaceChildren(...bindings.map(item => {
    const row = element('tr');
    row.append(element('td', (item.account_id ? '#' + item.account_id + ' · ' : '') + item.account, 'mono'),
      element('td', item.proxy + ' · ' + item.proxy_id, 'mono'),
      element('td', item.available ? 'Доступен' : 'Ожидание'),
      element('td', (result.active || []).find(active => active.account === item.account)?.stage || '—'),
      element('td', result.cooldowns?.[item.account] ? result.cooldowns[item.account] + ' с' : '—'));
    return row;
  }));
  const errors = [...(result.recent_errors || [])].reverse();
  $('network-errors').replaceChildren(...errors.map(item => {
    const row = element('tr');
    row.append(element('td', date(item.at)), element('td', item.account, 'mono'),
      element('td', item.stage), element('td', item.status || item.error),
      element('td', item.scope || '—'), element('td', number(item.retry_after) + ' с'),
      element('td', item.method + ' ' + item.route, 'mono'));
    return row;
  }));
}
async function proxyAction(action) {
  if (proxyBusy) return;
  try { await action(); await loadView(); }
  catch (error) { toast(error.message); }
}
let exportBusy = false, uploadBusy = false;
function renderTokens(job) {
  job = job || {};
  exportBusy = !!job.running;
  const banner = $('export-job');
  if (job.running) {
    banner.hidden = false; banner.className = 'alert';
    banner.textContent = 'Выгрузка: проверено ' + number(job.done) + ' из ' + number(job.total);
  } else if (job.error) {
    banner.hidden = false; banner.className = 'alert error';
    banner.textContent = 'Выгрузка прервана. Сохранённые файлы доступны в истории; повторите попытку.';
  } else if (job.finished_at) {
    banner.hidden = false; banner.className = 'alert warning';
    const delivery = job.delivery === 'sent' ? ' Файл отправлен в Telegram.'
      : job.delivery === 'failed' ? ' Telegram не доставил файл — скачайте его ниже.'
      : job.delivery === 'unknown' ? ' Статус доставки неизвестен — файл сохранён ниже.' : '';
    banner.textContent = 'Сохранено: ' + number(job.valid) + ', невалидных: ' + number(job.invalid) +
      ', отложено из-за недоступности проверки: ' + number(job.deferred) + '.' + delivery;
  } else banner.hidden = true;
  $('token-worker-note').hidden = !!job.worker_running;
  $('token-export').disabled = exportBusy || !job.worker_running;
  $('token-upload').disabled = exportBusy || uploadBusy || !job.worker_running;
  const history = job.history || [], select = $('token-export-history'), previous = select.value;
  select.replaceChildren(...history.map(item => {
    const state = item.delivery === 'sent' ? 'Telegram: доставлен' : item.delivery === 'failed' ? 'Telegram: ошибка' : 'Telegram: не подтверждён';
    const option = element('option', '#' + item.id + ' · ' + date(item.created_at) + ' · ' + number(item.count) + ' шт. · ' + state);
    option.value = String(item.id); return option;
  }));
  if (history.some(item => String(item.id) === previous)) select.value = previous;
  select.disabled = !history.length;
  $('token-download').disabled = !history.length;
}
let purchaseMax = null;
function renderPurchase(s) {
  s = s || {};
  const running = s.status === 'running' || s.status === 'stopping';
  $('purchase-worker-note').hidden = !!s.worker_running;
  const p = s.pipeline || {};
  const metrics = [
    ['Куплено', s.bought || 0, 'Успешных покупок', 'purchase'],
    ['Пропущено мёртвых', s.skipped_dead || 0, 'Отбраковано при покупке', 'ban'],
    ['В очистке', p.cleaning || 0, 'Сейчас очищаются', 'refresh'],
    ['Готово', p.ready || 0, 'Состояние ready', 'check'],
  ];
  $('purchase-metrics').replaceChildren(...metrics.map(metricCard));
  const statusText = {idle: 'Задача не запущена', running: 'Задача выполняется', stopping: 'Останавливается',
    done: 'Задача завершена', error: 'Задача прервана'}[s.status] || '—';
  const banner = $('purchase-banner');
  if (s.status && s.status !== 'idle') {
    banner.hidden = false;
    banner.className = 'alert ' + (s.status === 'error' ? 'error' : running ? '' : 'warning');
    banner.textContent = statusText + (s.message ? ' · ' + s.message : '');
  } else banner.hidden = true;
  details('purchase-stats', [
    ['Статус', statusText],
    ['Запрошено', number(s.requested || 0)],
    ['Куплено', number(s.bought || 0)],
    ['Проверено при покупке', number(s.checked || 0)],
    ['Пропущено (мёртвые/продан)', number(s.skipped_dead || 0)],
    ['Ошибок', number(s.errors || 0)],
    ['Потрачено', money(s.spent || 0)],
    ['Баланс LZT', s.balance == null ? '—' : money(s.balance)],
    ['В валидации', number((p.new || 0) + (p.validated || 0))],
    ['В очистке', number(p.cleaning || 0)],
    ['Очищено (ждут финальной)', number(p.cleaned || 0)],
    ['Готово к выгрузке', number(p.ready || 0)],
    ['Невалидные', number(p.invalid || 0)],
  ]);
  $('purchase-stop').disabled = !running;
  $('purchase-start').disabled = running || !s.worker_running || !(purchaseMax > 0);
}
function renderLogs(result) {
  if (!result.items.length) return empty('log-entries',result.available?'Событий не найдено':'Журнал ещё не создан',result.available?'Выберите другой уровень или дождитесь новых событий.':'Записи появятся после первого события приложения.');
  $('log-entries').replaceChildren(...result.items.map(item=>{
    const row=element('article',null,'log-row'), meta=element('div',null,'log-meta');
    meta.append(element('time',item.time),element('span',item.level,'status-pill '+(['ERROR','CRITICAL'].includes(item.level)?'bad':item.level==='WARNING'?'warn':'accent')),element('span',item.module)); row.append(meta,element('p',item.message)); return row;
  }));
}
function renderSettings(result, balance) {
  details('security-settings',[['Администратор',result.username],['Адрес панели',result.public_url],['HTTPS',result.https?'Включён':'Локальный HTTP'],['Срок сессии',result.session_hours+' ч'],['Telegram-владельцы',result.telegram_owners.join(', ')||'Не настроены']]);
  details('server-settings',[['Режим',result.monitoring_only?'Мониторинг БД':'Подключён к приложению'],['База данных',result.database_file],['Версия схемы',result.schema_version],['Потоки проверки',result.validator_workers],['Потоки очистки',result.cleaner_workers]]);
  const input = $('cleaner-workers');
  if (document.activeElement !== input) input.value = result.cleaner_workers;
  $('cleaner-workers-note').textContent = result.monitoring_only
    ? 'Рабочий процесс не подключён — изменение сохранится в конфиге и применится при следующем запуске.'
    : 'Применяется на лету: потоки добавляются или завершаются после текущего токена.';
  const close = $('toggle-close');
  if (document.activeElement !== close) close.checked = !!result.close_channels;
  close.disabled = false;
  balance = balance || {};
  const bal = balance.balance == null ? (balance.available ? '—' : 'Нет данных процесса') : money(balance.balance);
  details('balance-summary',[['Баланс LZT',bal],['Порог оповещения',money(balance.min_balance||0)]]);
}
function confirmAction(title,text) {
  $('confirm-title').textContent=title; $('confirm-text').textContent=text;
  const dialog=$('confirm-dialog'); dialog.returnValue='cancel'; dialog.showModal();
  return new Promise(resolve=>dialog.addEventListener('close',()=>resolve(dialog.returnValue==='confirm'),{once:true}));
}
for (const [value,label] of Object.entries(labels)) if (value===value.toLowerCase()) {
  const option=element('option',label); option.value=value; $('account-status').append(option);
}
$('login-form').addEventListener('submit',async event=>{
  event.preventDefault(); $('login-submit').disabled=true; $('login-error').textContent='';
  try { const result=await api('/auth/login',{method:'POST',body:JSON.stringify({username:$('username').value.trim(),password:$('password').value})}); csrf=result.csrf; $('password').value=''; showWorkspace(result.username); }
  catch(error){$('login-error').textContent=error.message;}
  finally{$('login-submit').disabled=false;}
});
$('logout').addEventListener('click',async()=>{
  try {await api('/auth/logout',{method:'POST',body:'{}'}); showLogin(); await boot();}catch(error){toast(error.message);}
});
$('revoke-sessions').addEventListener('click',async()=>{
  if (!await confirmAction('Завершить все сессии?','Вход будет сброшен на всех устройствах, включая это.')) return;
  try {await api('/auth/revoke',{method:'POST',body:'{}'}); showLogin(); await boot();}catch(error){toast(error.message);}
});
$('stop-pipeline').addEventListener('click',async()=>{
  if (!await confirmAction('Остановить обработку?','Приложение завершит обработку по мере выхода текущих операций. Панель останется доступной.')) return;
  try {await api('/control/stop',{method:'POST',body:'{}'}); toast('Запрос остановки принят'); await loadView();}catch(error){toast(error.message);}
});
$('download-report').addEventListener('click',async()=>{
  const button=$('download-report'); button.disabled=true;
  try {
    const params=new URLSearchParams({status:$('account-status').value,search:$('account-search').value.trim()});
    const blob=await api('/accounts/report.csv?'+params,{blob:true});
    const url=URL.createObjectURL(blob), link=element('a'); link.href=url; link.download='accounts-report.csv'; document.body.append(link); link.click(); link.remove(); setTimeout(()=>URL.revokeObjectURL(url),1000); toast('Отчёт подготовлен');
  } catch(error){toast(error.message);} finally{button.disabled=false;}
});
$('proxy-import').addEventListener('click',async()=>{
  if (proxyBusy) return;
  const raw=$('proxy-input').value.trim();
  if (!raw) return toast('Вставьте список прокси');
  $('proxy-import').disabled=true;
  try { const result=await api('/proxies/import',{method:'POST',body:JSON.stringify({proxies:raw})}); toast('Проверка запущена: '+result.queued+' прокси'); $('proxy-input').value=''; await loadView(); }
  catch(error){toast(error.message);$('proxy-import').disabled=false;}
});
$('proxy-recheck').addEventListener('click',async()=>{
  if (proxyBusy) return;
  try { const result=await api('/proxies/recheck',{method:'POST',body:'{}'}); toast('Перепроверка запущена: '+result.queued); await loadView(); }
  catch(error){toast(error.message);}
});
$('proxy-clear-dead').addEventListener('click',async()=>{
  if (proxyBusy) return;
  if (!await confirmAction('Удалить мёртвые прокси?','Из пула будут удалены все прокси со статусом «мёртвый».')) return;
  try { const result=await api('/proxies/clear',{method:'POST',body:JSON.stringify({scope:'dead'})}); toast('Удалено: '+result.removed); await loadView(); }
  catch(error){toast(error.message);}
});
$('proxy-clear-all').addEventListener('click',async()=>{
  if (proxyBusy) return;
  if (!await confirmAction('Очистить весь пул?','Все прокси будут удалены без возможности восстановления.')) return;
  try { const result=await api('/proxies/clear',{method:'POST',body:JSON.stringify({scope:'all'})}); toast('Удалено: '+result.removed); await loadView(); }
  catch(error){toast(error.message);}
});
$('cleaner-workers-apply').addEventListener('click',async()=>{
  const value=parseInt($('cleaner-workers').value,10);
  if (!Number.isInteger(value)||value<1||value>200) return toast('Введите число от 1 до 200');
  $('cleaner-workers-apply').disabled=true;
  try { const result=await api('/settings/cleaner-workers',{method:'POST',body:JSON.stringify({workers:value})}); toast(result.live?('Потоков очистки: '+result.workers):('Сохранено в конфиг: '+result.workers)); await loadView(); }
  catch(error){toast(error.message);}
  finally{$('cleaner-workers-apply').disabled=false;}
});
async function toggleSetting(key, checkbox){
  const value=checkbox.checked;
  checkbox.disabled=true;
  try { const result=await api('/settings/toggle',{method:'POST',body:JSON.stringify({key,value})}); checkbox.checked=result.value; toast((result.live?'Применено':'Сохранено в конфиг')+': '+(result.value?'вкл':'выкл')); }
  catch(error){checkbox.checked=!value; toast(error.message);}
  finally{checkbox.disabled=false;}
}
$('toggle-close').addEventListener('change',()=>toggleSetting('close_channels',$('toggle-close')));
$('purchase-estimate-btn').addEventListener('click',async()=>{
  const pmax=parseFloat($('purchase-pmax').value), chatmin=parseInt($('purchase-chatmin').value,10);
  if (!(pmax>0)||!Number.isInteger(chatmin)||chatmin<0) return toast('Введите цену и минимум чатов');
  const btn=$('purchase-estimate-btn'); btn.disabled=true;
  try {
    const r=await api('/purchase/estimate?'+new URLSearchParams({pmax,chat_min:chatmin}));
    purchaseMax=r.max_affordable||0;
    details('purchase-estimate',[
      ['Подходящих аккаунтов',number(r.count)+(r.truncated?' (сумма по '+number(r.collected)+')':'')],
      ['Суммарная стоимость',money(r.total_cost)],
      ['Баланс LZT',r.balance==null?'—':money(r.balance)],
      ['Можно купить на баланс',number(r.max_affordable)],
    ]);
    const cnt=$('purchase-count'); cnt.max=Math.max(1,purchaseMax);
    if (!cnt.value||parseInt(cnt.value,10)>purchaseMax) cnt.value=purchaseMax||'';
    $('purchase-hint').textContent=purchaseMax>0?('Доступно к покупке: '+number(purchaseMax)+' аккаунтов.'):'Нет доступных к покупке аккаунтов (баланс или фильтр).';
    $('purchase-start').disabled=!(purchaseMax>0);
    toast('Найдено '+number(r.count)+' аккаунтов');
  } catch(error){toast(error.message);}
  finally{btn.disabled=false;}
});
$('purchase-start').addEventListener('click',async()=>{
  const pmax=parseFloat($('purchase-pmax').value), chatmin=parseInt($('purchase-chatmin').value,10);
  const count=parseInt($('purchase-count').value,10), workers=parseInt($('purchase-workers').value,10);
  if (!(count>=1)) return toast('Укажите количество аккаунтов');
  if (!(workers>=1&&workers<=200)) return toast('Потоки очистки: 1–200');
  if (purchaseMax!=null&&count>purchaseMax) return toast('Не больше '+number(purchaseMax)+' на баланс');
  if (!await confirmAction('Запустить покупку?','Будет куплено до '+number(count)+' аккаунтов, максимум '+money(pmax*count)+' (по '+money(pmax)+' за шт.). Тратятся реальные деньги.')) return;
  const btn=$('purchase-start'); btn.disabled=true;
  try { await api('/purchase/start',{method:'POST',body:JSON.stringify({pmax,chat_min:chatmin,count,cleaner_workers:workers})}); toast('Задача запущена'); await loadView(); }
  catch(error){toast(error.message); btn.disabled=false;}
});
$('purchase-stop').addEventListener('click',async()=>{
  if (!await confirmAction('Остановить задачу?','Новые покупки прекратятся. Уже купленные аккаунты продолжат обработку.')) return;
  try { await api('/purchase/stop',{method:'POST',body:'{}'}); toast('Остановка задачи'); await loadView(); }
  catch(error){toast(error.message);}
});
$('token-upload').addEventListener('click',async()=>{
  const raw=$('token-input').value.trim();
  if (!raw) return toast('Вставьте токены');
  uploadBusy=true; $('token-upload').disabled=true;
  try { const result=await api('/tokens/upload',{method:'POST',body:JSON.stringify({tokens:raw})}); toast('Принято: '+result.added+'; дубликатов: '+result.duplicates+'; ошибок записи: '+result.errors); if (!result.errors) $('token-input').value=''; }
  catch(error){toast(error.message);}
  finally{uploadBusy=false; await loadView();}
});
$('token-export').addEventListener('click',async()=>{
  if (exportBusy) return;
  if (!await confirmAction('Выгрузить готовые токены?','Проверенные токены сохранятся в истории выгрузок и будут помечены «отправлены». Затем начнётся доставка файла в Telegram.')) return;
  try { await api('/tokens/export',{method:'POST',body:'{}'}); toast('Выгрузка запущена'); await loadView(); }
  catch(error){toast(error.message);}
});
$('token-download').addEventListener('click',async()=>{
  const button=$('token-download'); button.disabled=true;
  try {
    const blob=await api('/tokens/export/download?id='+encodeURIComponent($('token-export-history').value),{blob:true});
    const url=URL.createObjectURL(blob), link=element('a'); link.href=url; link.download='tokens.txt'; document.body.append(link); link.click(); link.remove(); setTimeout(()=>URL.revokeObjectURL(url),1000); toast('Файл готов');
  } catch(error){toast(error.message);} finally{button.disabled=false;}
});
$('account-search').addEventListener('input',()=>{clearTimeout(searchTimer);searchTimer=setTimeout(()=>{accountOffset=0;loadView();},350);});
$('account-status').addEventListener('change',()=>{accountOffset=0;loadView();});
$('accounts-prev').addEventListener('click',()=>{accountOffset=Math.max(0,accountOffset-pageSize);loadView();});
$('accounts-next').addEventListener('click',()=>{if(accountOffset+pageSize<accountTotal){accountOffset+=pageSize;loadView();}});
$('show-pending').addEventListener('click',()=>{$('account-status').value='pending_review';$('account-search').value='';accountOffset=0;location.hash='accounts';});
$('chart-days').addEventListener('change',loadView);
$('log-level').addEventListener('change',loadView);
$('auto-refresh').addEventListener('change',()=>{$('live-label').textContent=$('auto-refresh').checked?'Live-обновление':'Обновление вручную';startLive();if($('auto-refresh').checked)loadView();});
$('refresh').addEventListener('click',loadView);
$('close-detail').addEventListener('click',()=>$('detail-dialog').close());
$('boot-retry').addEventListener('click',boot);
window.addEventListener('hashchange',navigate);
document.addEventListener('visibilitychange',()=>{if(!document.hidden&&loggedIn&&$('auto-refresh').checked)loadView();});
// Рябь по нажатию на кнопки (микровзаимодействие)
if(!prefersReduced) document.addEventListener('pointerdown',event=>{
  const button=event.target.closest('.button');
  if(!button||button.disabled)return;
  const rect=button.getBoundingClientRect(), size=Math.max(rect.width,rect.height);
  const ripple=document.createElement('span'); ripple.className='ripple';
  ripple.style.width=ripple.style.height=size+'px';
  ripple.style.left=(event.clientX-rect.left-size/2)+'px';
  ripple.style.top=(event.clientY-rect.top-size/2)+'px';
  button.append(ripple); ripple.addEventListener('animationend',()=>ripple.remove());
});
boot();
