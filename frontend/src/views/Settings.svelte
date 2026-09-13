<script>
  import { api } from '../lib/api.js';
  import { refreshTick, toast, confirmDialog, session } from '../lib/store.js';
  import { money } from '../lib/format.js';
  import { stopLive } from '../lib/sse.js';
  import PageHeader from '../components/PageHeader.svelte';
  import DetailList from '../components/DetailList.svelte';
  import Skeleton from '../components/Skeleton.svelte';
  import { reveal } from '../lib/anim.js';

  let settings = $state(null);
  let balance = $state(null);
  let error = $state('');
  let token = 0;
  let workers = $state(20);
  let applying = $state(false);
  let closeChannels = $state(false);

  async function load() {
    const my = ++token;
    try {
      const [s, b] = await Promise.all([api('/settings'), api('/lzt/balance')]);
      if (my !== token) return;
      settings = s; balance = b; error = '';
      if (document.activeElement?.id !== 'cleaner-workers') workers = s.cleaner_workers;
      closeChannels = !!s.close_channels;
    } catch (e) { if (my === token) error = e.message; }
  }
  $effect(() => { $refreshTick; load(); });

  async function applyWorkers() {
    if (!(workers >= 1 && workers <= 200)) return toast('Введите число от 1 до 200');
    applying = true;
    try { const r = await api('/settings/cleaner-workers', { method: 'POST', body: JSON.stringify({ workers: Number(workers) }) });
      toast(r.live ? 'Потоков очистки: ' + r.workers : 'Сохранено в конфиг: ' + r.workers); }
    catch (e) { toast(e.message); } finally { applying = false; }
  }
  async function toggleClose(event) {
    const value = event.target.checked;
    try { const r = await api('/settings/toggle', { method: 'POST', body: JSON.stringify({ key: 'close_channels', value }) });
      closeChannels = r.value; toast((r.live ? 'Применено' : 'Сохранено') + ': ' + (r.value ? 'вкл' : 'выкл')); }
    catch (e) { closeChannels = !value; toast(e.message); }
  }
  async function revoke() {
    if (!(await confirmDialog('Завершить все сессии?', 'Вход будет сброшен на всех устройствах, включая это.'))) return;
    try { await api('/auth/revoke', { method: 'POST', body: '{}' }); stopLive(); session.update((s) => ({ ...s, authenticated: false })); }
    catch (e) { toast(e.message); }
  }

  const bal = $derived(!balance ? '—' : balance.balance == null ? (balance.available ? '—' : 'Нет данных процесса') : money(balance.balance));
</script>

<PageHeader title="Настройки" description="Параметры доступа и текущая конфигурация сервера." />

{#if error}<div class="alert error">{error}</div>{/if}

{#if !settings && !error}<Skeleton kind="panels" count={4} />{/if}

{#if settings}
  <div class="settings-grid">
    <article class="panel" use:reveal={0}>
      <div class="panel-heading"><div><p class="section-label">Безопасность</p><h2>Доступ к панели</h2></div></div>
      <DetailList pairs={[
        ['Администратор', settings.username], ['Адрес панели', settings.public_url],
        ['HTTPS', settings.https ? 'Включён' : 'Локальный HTTP'], ['Срок сессии', settings.session_hours + ' ч'],
        ['Telegram-владельцы', settings.telegram_owners.join(', ') || 'Не настроены'],
      ]} />
      <div class="panel-action">
        <button class="button danger" onclick={revoke}>Завершить все сессии</button>
        <p class="muted">Включая текущую. После этого потребуется войти снова.</p>
      </div>
    </article>

    <article class="panel" use:reveal={1}>
      <div class="panel-heading"><div><p class="section-label">Конфигурация</p><h2>Настройки сервера</h2></div></div>
      <DetailList pairs={[
        ['Режим', settings.monitoring_only ? 'Мониторинг БД' : 'Подключён к приложению'],
        ['База данных', settings.database_file], ['Версия схемы', settings.schema_version],
        ['Потоки проверки', settings.validator_workers], ['Потоки очистки', settings.cleaner_workers],
      ]} />
      <p class="settings-hint">Пароль и список владельцев задаются на сервере. После изменения перезапустите сервис.</p>
    </article>

    <article class="panel" use:reveal={2}>
      <div class="panel-heading"><div><p class="section-label">Обработка</p><h2>Потоки очистки</h2></div></div>
      <p class="muted">Сколько токенов очищается одновременно. Каждому потоку выдаётся отдельный прокси из пула.</p>
      <div class="worker-control">
        <label for="cleaner-workers">Число потоков</label>
        <input id="cleaner-workers" type="number" min="1" max="200" step="1" bind:value={workers}>
        <button class="button primary" onclick={applyWorkers} disabled={applying}>Применить</button>
      </div>
      <p class="muted">{settings.monitoring_only
        ? 'Рабочий процесс не подключён — изменение сохранится в конфиге и применится при следующем запуске.'
        : 'Применяется на лету: потоки добавляются или завершаются после текущего токена.'}</p>
    </article>

    <article class="panel" use:reveal={3}>
      <div class="panel-heading"><div><p class="section-label">Управление</p><h2>Переключатели и баланс</h2></div></div>
      <div class="toggle-row">
        <div><strong>Закрытие чатов при очистке</strong><small>Закрывать чаты со спам-ссылками</small></div>
        <label class="switch"><input type="checkbox" checked={closeChannels} onchange={toggleClose}><span class="slider" aria-hidden="true"></span></label>
      </div>
      <DetailList pairs={[['Баланс LZT', bal], ['Порог оповещения', money(balance?.min_balance || 0)]]} />
    </article>
  </div>
{/if}
