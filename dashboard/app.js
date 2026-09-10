'use strict';
const $ = id => document.getElementById(id);
const titles = {
  overview: ['Обзор', 'Текущее состояние системы и последние изменения.'],
  accounts: ['Аккаунты', 'Поиск и просмотр записей без раскрытия токенов доступа.'],
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
    } else if (view === 'logs') result = await api('/logs?limit=100&level=' + $('log-level').value, opts);
    else result = await api('/settings', opts);
    if (generation !== requestGeneration || !loggedIn) return;
    if (view === 'overview') renderOverview(...result);
    else if (view === 'accounts') renderAccounts(result);
    else if (view === 'logs') renderLogs(result);
    else renderSettings(result);
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
function renderLogs(result) {
  if (!result.items.length) return empty('log-entries',result.available?'Событий не найдено':'Журнал ещё не создан',result.available?'Выберите другой уровень или дождитесь новых событий.':'Записи появятся после первого события приложения.');
  $('log-entries').replaceChildren(...result.items.map(item=>{
    const row=element('article',null,'log-row'), meta=element('div',null,'log-meta');
    meta.append(element('time',item.time),element('span',item.level,'status-pill '+(['ERROR','CRITICAL'].includes(item.level)?'bad':item.level==='WARNING'?'warn':'accent')),element('span',item.module)); row.append(meta,element('p',item.message)); return row;
  }));
}
function renderSettings(result) {
  details('security-settings',[['Администратор',result.username],['Адрес панели',result.public_url],['HTTPS',result.https?'Включён':'Локальный HTTP'],['Срок сессии',result.session_hours+' ч'],['Telegram-владельцы',result.telegram_owners.join(', ')||'Не настроены']]);
  details('server-settings',[['Режим',result.monitoring_only?'Мониторинг БД':'Подключён к приложению'],['База данных',result.database_file],['Версия схемы',result.schema_version],['Получение с LZT',result.lzt_enabled?'Включено в конфиге':'Выключено'],['Закрытие чатов',result.close_channels?'Включено в конфиге':'Выключено'],['Потоки проверки',result.validator_workers],['Потоки очистки',result.cleaner_workers]]);
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
