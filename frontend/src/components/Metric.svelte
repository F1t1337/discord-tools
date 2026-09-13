<script>
  import Icon from './Icon.svelte';
  import { counter, reveal } from '../lib/anim.js';
  let { title, value, note, icon, index = 0, spark = null, sparkTitle = '' } = $props();

  // Точки мини-графика тренда (нормированные в область 60×18).
  const sparkPoints = $derived.by(() => {
    if (!spark || spark.length < 2) return '';
    const nums = spark.map((n) => Number(n) || 0);
    const max = Math.max(1, ...nums), span = spark.length - 1;
    return nums.map((n, i) => (i * 60 / span).toFixed(1) + ',' + (16 - n / max * 14).toFixed(1)).join(' ');
  });
</script>

<article class="metric" use:reveal={index}>
  <div class="metric-label"><span>{title}</span><span class="metric-icon"><Icon name={icon} /></span></div>
  <p class="metric-value" use:counter={value}></p>
  <p class="metric-note">{note}</p>
  {#if sparkPoints}
    <svg class="spark" viewBox="0 0 60 18" preserveAspectRatio="none" role="img" aria-label={sparkTitle}>
      <title>{sparkTitle}</title>
      <polyline points={sparkPoints}></polyline>
    </svg>
  {/if}
</article>
