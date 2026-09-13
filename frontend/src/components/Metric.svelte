<script>
  import Icon from './Icon.svelte';
  import { counter, reveal } from '../lib/anim.js';
  let { title, value, note, icon, index = 0 } = $props();
</script>

<article class="metric" use:reveal={index}>
  <div class="metric-label"><span>{title}</span><span class="metric-icon"><Icon name={icon} /></span></div>
  <p class="metric-value" use:counter={value}></p>
  <p class="metric-note">{note}</p>
</article>
