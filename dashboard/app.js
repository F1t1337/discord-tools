'use strict';
const $ = id => document.getElementById(id);
const titles = {
  overview: ['Обзор', 'Текущее состояние системы и последние изменения.'],
  accounts: ['Аккаунты', 'Поиск и просмотр записей без раскрытия токенов доступа.'],
  tokens: ['Токены', 'Загрузка токенов в обработку и выгрузка готовых.'],
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
let pollTimer, toastTimer, searchTimer;
const pageSize = 25;
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
  loggedIn = false; clearTimeout(pollTimer); viewController?.abort();
  $('workspace').hidden = true; $('boot-screen').hidden = true; $('login-screen').hidden = false;
  $('login-error').textContent = message; $('password').value = '';
  document.querySelectorAll('dialog[open]').forEach(dialog => dialog.close());
}
function showWorkspace(username) {
  loggedIn = true; $('login-screen').hidden = true; $('boot-screen').hidden = true; $('workspace').hidden = false;
  $('profile-name').textContent = username; navigate(); $('main').focus();
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
function connection(ok) {
  $('connection-light').className = 'server-light ' + (ok ? 'online' : 'offline');
  $('connection-label').textContent = ok ? 'Сервер доступен' : 'Нет связи с сервером';
  $('connection-time').textContent = ok ? 'Обновлено ' + new Date().toLocaleTimeString('ru-RU') : 'Проверьте подключение';
}
function schedule() {
  clearTimeout(pollTimer);
  if (loggedIn && $('auto-refresh').checked) pollTimer = setTimeout(() => {
    if (document.hidden) schedule(); else loadView();
  }, 5000);
}
async function loadView() {
  if (!loggedIn) return;
  clearTimeout(pollTimer); viewController?.abort(); viewController = new AbortController();
  const generation = ++requestGeneration, opts = {signal: viewController.signal};
  $('page-loading').hidden = false; $('page-error').hidden = true;
  try {
    let result;
    if (view === 'overview') {
      result = await Promise.all([api('/status', opts), api('/statistics/today', opts), api('/statistics/range?days=' + $('chart-days').value, opts)]);
    } else if (view === 'accounts') {
      result = await api('/accounts?' + new URLSearchParams({limit:pageSize, offset:accountOffset, status:$('account-status').value, search:$('account-search').value.trim()}), opts);
    } else if (view === 'proxies') result = await api('/proxies', opts);
    else if (view === 'tokens') result = await api('/tokens/export/status', opts);
    else if (view === 'logs') result = await api('/logs?limit=100&level=' + $('log-level').value, opts);
    else result = await Promise.all([api('/settings', opts), api('/lzt/balance', opts)]);
    if (generation !== requestGeneration || !loggedIn) return;
    if (view === 'overview') renderOverview(...result);
    else if (view === 'accounts') renderAccounts(result);
    else if (view === 'proxies') renderProxies(result);
    else if (view === 'tokens') renderTokens(result);
    else if (view === 'logs') renderLogs(result);
    else renderSettings(...result);
    connection(true);
    $('last-updated').textContent = 'Последнее обновление: ' + new Date().toLocaleTimeString('ru-RU');
  } catch (error) {
    if (error.name !== 'AbortError' && generation === requestGeneration && loggedIn) {
      connection(false); $('page-error').textContent = error.message + ' Показаны последние полученные данные.'; $('page-error').hidden = false;
    }
  } finally {
    if (generation === requestGeneration) { $('page-loading').hidden = true; schedule(); }
  }
}
function renderOverview(state, today, series) {
  const counts = state.counts, total = Object.values(counts).reduce((sum, n) => sum + n, 0);
  const metrics = [
    ['Всего аккаунтов', total, 'Записей в базе', '▤'],
    ['Готовы', counts.ready || 0, 'Состояние ready', '✓'],
    ['В обработке', state.pending_count, 'Промежуточные состояния', '↻'],
    ['Невалидные', counts.invalid || 0, 'По последней проверке', '⊘'],
  ];
  $('metrics').replaceChildren(...metrics.map(([title, value, note, icon]) => {
    const card = element('article', null, 'metric'), label = element('div', title, 'metric-label');
    label.append(element('span', icon, 'metric-icon')); card.append(label, element('p', number(value), 'metric-value'), element('p', note, 'metric-note')); return card;
  }));
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
    svg.append(svgElement('polyline',{points:points.join(' '),class:cls}));
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
    const action=element('td'), button=element('button','↗','icon-button'); button.setAttribute('aria-label','Подробнее об аккаунте '+account.id); button.addEventListener('click',()=>showDetails(account)); action.append(button); row.append(action); return row;
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
    ['Рабочих', counts.alive || 0, 'Готовы к использованию', '✓'],
    ['Мёртвых', counts.dead || 0, 'Не прошли проверку', '⊘'],
    ['Не проверено', counts.unchecked || 0, 'Ожидают проверки', '…'],
    ['Всего в пуле', counts.total || 0, 'Записей всего', '⇄'],
  ];
  $('proxy-metrics').replaceChildren(...metrics.map(([title, value, note, icon]) => {
    const card = element('article', null, 'metric'), label = element('div', title, 'metric-label');
    label.append(element('span', icon, 'metric-icon'));
    card.append(label, element('p', number(value), 'metric-value'), element('p', note, 'metric-note'));
    return card;
  }));
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
    ['Прокси в очистке', result.proxy_enabled ? 'Включены в конфиге' : 'Выключены']]);
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
async function proxyAction(action) {
  if (proxyBusy) return;
  try { await action(); await loadView(); }
  catch (error) { toast(error.message); }
}
let exportBusy = false;
function renderTokens(job) {
  job = job || {};
  exportBusy = !!job.running;
  const banner = $('export-job');
  if (job.running) {
    banner.hidden = false; banner.className = 'alert';
    banner.textContent = 'Выгрузка: проверено ' + number(job.done) + ' из ' + number(job.total) +
      ' · валидных ' + number(job.valid) + ', невалидных ' + number(job.invalid);
  } else if (job.error) {
    banner.hidden = false; banner.className = 'alert error';
    banner.textContent = 'Выгрузка прервана из-за ошибки. Попробуйте ещё раз.';
  } else if (job.finished_at) {
    banner.hidden = false; banner.className = 'alert warning';
    banner.textContent = 'Готово: валидных ' + number(job.valid) + ', невалидных ' + number(job.invalid) +
      (job.valid ? '. Файл отправлен в Telegram, можно скачать .txt.' : '. Валидных токенов нет.');
  } else banner.hidden = true;
  $('token-export').disabled = exportBusy;
  $('token-upload').disabled = exportBusy;
  $('token-download').disabled = exportBusy || !job.available;
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
  const lzt = $('toggle-lzt'), close = $('toggle-close');
  if (document.activeElement !== lzt) lzt.checked = !!result.lzt_enabled;
  if (document.activeElement !== close) close.checked = !!result.close_channels;
  lzt.disabled = close.disabled = result.monitoring_only;
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
$('toggle-lzt').addEventListener('change',()=>toggleSetting('lzt_enabled',$('toggle-lzt')));
$('toggle-close').addEventListener('change',()=>toggleSetting('close_channels',$('toggle-close')));
$('token-upload').addEventListener('click',async()=>{
  const raw=$('token-input').value.trim();
  if (!raw) return toast('Вставьте токены');
  $('token-upload').disabled=true;
  try { const result=await api('/tokens/upload',{method:'POST',body:JSON.stringify({tokens:raw})}); toast('Загружено в обработку: '+result.added+' из '+result.total); $('token-input').value=''; }
  catch(error){toast(error.message);}
  finally{$('token-upload').disabled=false;}
});
$('token-export').addEventListener('click',async()=>{
  if (exportBusy) return;
  if (!await confirmAction('Выгрузить готовые токены?','Готовые токены пройдут проверку, валидные будут помечены «отправлены» и уйдут файлом в Telegram.')) return;
  try { await api('/tokens/export',{method:'POST',body:'{}'}); toast('Выгрузка запущена'); await loadView(); }
  catch(error){toast(error.message);}
});
$('token-download').addEventListener('click',async()=>{
  const button=$('token-download'); button.disabled=true;
  try {
    const blob=await api('/tokens/export/download',{blob:true});
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
$('auto-refresh').addEventListener('change',schedule);
$('refresh').addEventListener('click',loadView);
$('close-detail').addEventListener('click',()=>$('detail-dialog').close());
$('boot-retry').addEventListener('click',boot);
window.addEventListener('hashchange',navigate);
document.addEventListener('visibilitychange',()=>{if(!document.hidden&&loggedIn&&$('auto-refresh').checked)loadView();});
boot();
