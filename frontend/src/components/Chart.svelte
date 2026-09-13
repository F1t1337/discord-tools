<script>
  import { number } from '../lib/format.js';
  let { series = [] } = $props();
  const reduce = typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches;
  const gridY = [0, 1, 2, 3, 4];

  let hover = $state(-1);
  let tipX = $state(0);
  let wrapWidth = $state(1);

  const ceiling = $derived.by(() => {
    const values = series.flatMap((r) => [Number(r.tokens_cleaned) || 0, Number(r.tokens_sent) || 0]);
    return Math.ceil(Math.max(4, ...values) / 4) * 4;
  });
  const span = $derived(Math.max(1, series.length - 1));
  const x = (i) => 40 + i * 545 / span;
  const y = (v) => 204 - (Number(v) || 0) / ceiling * 184;
  const points = (key) => series.map((r, i) => x(i) + ',' + y(r[key])).join(' ');
  const labelIdx = $derived([...new Set([0, Math.floor(span / 2), series.length - 1])].filter((i) => series[i]));

  function draw(node) {
    if (reduce) return;
    try { node.style.setProperty('--len', node.getTotalLength()); node.classList.add('chart-draw'); } catch (e) {}
  }
  function onMove(event) {
    const rect = event.currentTarget.getBoundingClientRect();
    wrapWidth = rect.width;
    const vx = (event.clientX - rect.left) / rect.width * 600; // в координатах viewBox
    const idx = Math.round((vx - 40) / (545 / span));
    hover = Math.max(0, Math.min(series.length - 1, idx));
    tipX = (x(hover) / 600) * rect.width;
  }
  const tipRow = $derived(hover >= 0 ? series[hover] : null);
</script>

<div class="chart-wrap">
  <svg viewBox="0 0 600 245" role="img" aria-label="Очищенные и отправленные аккаунты по дням"
       onpointermove={onMove} onpointerleave={() => (hover = -1)}>
    {#each gridY as i}
      <line x1="40" y1={20 + i * 46} x2="585" y2={20 + i * 46} class="chart-grid" />
      <text x="30" y={20 + i * 46 + 4} text-anchor="end" class="chart-label">{number(ceiling * (4 - i) / 4)}</text>
    {/each}
    {#if hover >= 0}<line x1={x(hover)} y1="16" x2={x(hover)} y2="208" class="chart-guide" />{/if}
    <polyline points={points('tokens_cleaned')} class="chart-cleaned" use:draw />
    <polyline points={points('tokens_sent')} class="chart-sent" use:draw />
    {#each series as r, i}
      <circle cx={x(i)} cy={y(r.tokens_cleaned)} r={hover === i ? 4 : 2.4} class="chart-dot cleaned" />
      <circle cx={x(i)} cy={y(r.tokens_sent)} r={hover === i ? 4 : 2.4} class="chart-dot sent" />
    {/each}
    {#each labelIdx as i}
      <text x={x(i)} y="235" text-anchor={i === 0 ? 'start' : i === series.length - 1 ? 'end' : 'middle'} class="chart-label">
        {series[i].date.slice(5).split('-').reverse().join('.')}
      </text>
    {/each}
  </svg>
  {#if tipRow}
    <div class="chart-tip" style:left={tipX + 'px'} style:transform={tipX > wrapWidth / 2 ? 'translateX(-100%)' : 'none'}>
      <strong>{tipRow.date.slice(5).split('-').reverse().join('.')}</strong>
      <span><i class="legend-cleaned"></i>Очищено: {number(tipRow.tokens_cleaned)}</span>
      <span><i class="legend-sent"></i>Отправлено: {number(tipRow.tokens_sent)}</span>
    </div>
  {/if}
</div>
