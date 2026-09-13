<script>
  import { number } from '../lib/format.js';
  let { series = [] } = $props();
  const reduce = typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches;
  const gridY = [0, 1, 2, 3, 4];

  const ceiling = $derived.by(() => {
    const values = series.flatMap((r) => [Number(r.tokens_cleaned) || 0, Number(r.tokens_sent) || 0]);
    return Math.ceil(Math.max(4, ...values) / 4) * 4;
  });
  const span = $derived(Math.max(1, series.length - 1));
  const points = (key) => series
    .map((r, i) => (40 + i * 545 / span) + ',' + (204 - (Number(r[key]) || 0) / ceiling * 184))
    .join(' ');
  const labelIdx = $derived([...new Set([0, Math.floor(span / 2), series.length - 1])].filter((i) => series[i]));

  function draw(node) {
    if (reduce) return;
    try { node.style.setProperty('--len', node.getTotalLength()); node.classList.add('chart-draw'); } catch (e) {}
  }
</script>

<svg viewBox="0 0 600 245" role="img" aria-label="Очищенные и отправленные аккаунты по дням">
  {#each gridY as i}
    <line x1="40" y1={20 + i * 46} x2="585" y2={20 + i * 46} class="chart-grid" />
    <text x="30" y={20 + i * 46 + 4} text-anchor="end" class="chart-label">{number(ceiling * (4 - i) / 4)}</text>
  {/each}
  <polyline points={points('tokens_cleaned')} class="chart-cleaned" use:draw />
  <polyline points={points('tokens_sent')} class="chart-sent" use:draw />
  {#each labelIdx as i}
    <text x={40 + i * 545 / span} y="235"
          text-anchor={i === 0 ? 'start' : i === series.length - 1 ? 'end' : 'middle'} class="chart-label">
      {series[i].date.slice(5).split('-').reverse().join('.')}
    </text>
  {/each}
</svg>
