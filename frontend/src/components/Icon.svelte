<script>
  import { ICONS } from '../lib/icons.js';
  let { name, size = 18, cls = '' } = $props();
</script>

<svg class={cls} viewBox="0 0 24 24" width={size} height={size} fill="none" stroke="currentColor"
     stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
  {@html ICONS[name] || ''}
</svg>
