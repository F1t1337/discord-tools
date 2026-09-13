<script>
  let { pairs = [] } = $props();
</script>

<dl class="detail-list">
  {#each pairs as [key, value]}
    <dt>{key}</dt><dd>{value ?? '—'}</dd>
  {/each}
</dl>
