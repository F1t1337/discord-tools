<script>
  // kind: metrics | panels | list | table
  let { kind = 'list', count = 4, cols = 6 } = $props();
</script>

{#if kind === 'metrics'}
  <div class="metrics">
    {#each Array(4) as _}<div class="skeleton" style:height="104px"></div>{/each}
  </div>
{:else if kind === 'panels'}
  <div class="settings-grid">
    {#each Array(count) as _}<div class="skeleton" style:height="220px"></div>{/each}
  </div>
{:else if kind === 'table'}
  <div class="panel table-panel">
    <div class="skeleton" style:height="46px" style:border-radius="0"></div>
    {#each Array(count) as _}<div class="skeleton" style:height="53px" style:border-radius="0" style:opacity="0.6"></div>{/each}
  </div>
{:else}
  <div class="stack">
    {#each Array(count) as _}<div class="skeleton" style:height="56px"></div>{/each}
  </div>
{/if}
