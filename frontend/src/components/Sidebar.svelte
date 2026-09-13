<script>
  import Icon from './Icon.svelte';
  import { NAV, route, liveState, session } from '../lib/store.js';
  let { onLogout } = $props();

  const LIVE = {
    connecting: 'Подключение…', live: 'Live-подключение', polling: 'Обновление опросом', offline: 'Нет связи',
  };
  const liveLabel = $derived(LIVE[$liveState.mode] || 'Подключение…');
  const liveTime = $derived($liveState.at
    ? 'Обновлено ' + new Date($liveState.at).toLocaleTimeString('ru-RU') : 'Ожидаем данные');
  const initial = $derived(($session.username || 'A').slice(0, 1).toUpperCase());
</script>

<aside class="sidebar">
  <a class="brand" href="#overview"><span class="brand-mark">D</span> discord tools</a>
  {#each NAV as group}
    <div class="nav-section">
      <p class="section-label">{group.section}</p>
      <nav aria-label={group.section}>
        {#each group.items as item}
          <a href={'#' + item.id} aria-current={$route === item.id ? 'page' : undefined}>
            <Icon name={item.icon} cls="nav-icon" size={19} />{item.label}
          </a>
        {/each}
      </nav>
    </div>
  {/each}
  <div class="sidebar-bottom">
    <div class="server-card">
      <span class="live-dot {$liveState.mode}"></span>
      <div><strong>{liveLabel}</strong><small>{liveTime}</small></div>
    </div>
    <div class="profile">
      <span class="avatar">{initial}</span>
      <div><strong>{$session.username}</strong><small>Личный кабинет</small></div>
      <button class="icon-button" onclick={onLogout} title="Выйти" aria-label="Выйти из панели"><Icon name="logout" /></button>
    </div>
  </div>
</aside>
