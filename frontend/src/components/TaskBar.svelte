<script>
  import { purchaseStatus } from '../lib/store.js';
  import { number } from '../lib/format.js';
  import Icon from './Icon.svelte';

  const s = $derived($purchaseStatus);
  const active = $derived(s && (s.status === 'running' || s.status === 'stopping'));
  const p = $derived(s?.pipeline || {});
  const pct = $derived(s?.requested ? Math.min(100, (s.bought || 0) / s.requested * 100) : 0);
</script>

{#if active}
  <a class="taskbar" href="#purchase" aria-label="Открыть задачу покупки">
    <span class="taskbar-dot"></span>
    <strong>Покупка</strong>
    <span class="taskbar-num">{number(s.bought || 0)} / {number(s.requested || 0)}</span>
    <span class="taskbar-sep">·</span>
    <span class="taskbar-muted">очистка {number(p.cleaning || 0)}</span>
    <span class="taskbar-sep">·</span>
    <span class="taskbar-muted">готово {number(p.ready || 0)}</span>
    <span class="taskbar-progress"><span style:width={pct + '%'}></span></span>
    {#if s.status === 'stopping'}<span class="taskbar-muted">останавливается…</span>{/if}
    <Icon name="arrow" size={15} />
  </a>
{/if}
