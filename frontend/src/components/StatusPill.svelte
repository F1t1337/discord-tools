<script>
  import { STATUS_LABELS } from '../lib/store.js';
  let { status, text = '', tone = '' } = $props();
  const good = ['ready', 'sent'], bad = ['invalid', 'locked'], warn = ['cleaning'];
  const cls = $derived(
    tone || (good.includes(status) ? 'good' : bad.includes(status) ? 'bad'
      : warn.includes(status) ? 'warn' : 'accent')
  );
  const label = $derived(text || STATUS_LABELS[status] || 'Архивное состояние');
</script>

<span class="status-pill {cls}">{label}</span>
