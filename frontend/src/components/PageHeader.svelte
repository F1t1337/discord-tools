<script>
  import Icon from './Icon.svelte';
  import { liveState, autoLive, tick } from '../lib/store.js';
  let { title, description, actions } = $props();

  const LIVE = { connecting: 'Подключение', live: 'Live', polling: 'Опрос', offline: 'Оффлайн' };
  const badge = $derived($autoLive ? (LIVE[$liveState.mode] || 'Live') : 'Выключено');
</script>

<header class="page-header">
  <div>
    <p class="eyebrow">Рабочее пространство / {title}</p>
    <h1>{title}</h1>
    <p class="page-description">{description}</p>
  </div>
  <div class="header-actions">
    {@render actions?.()}
    <button class="live-badge" onclick={() => autoLive.update((v) => !v)}
            title="Переключить живое обновление">
      <span class="live-dot {$autoLive ? $liveState.mode : ''}"></span>{badge}
    </button>
    <button class="button secondary" onclick={() => tick()}><Icon name="refresh" size={16} /> Обновить</button>
  </div>
</header>
