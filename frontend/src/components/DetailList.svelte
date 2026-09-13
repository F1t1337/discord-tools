<script>
  import Hint from './Hint.svelte';
  let { pairs = [] } = $props();
</script>

<dl class="detail-list">
  {#each pairs as [key, value, hint]}
    <dt>{key}{#if hint}<Hint text={hint} />{/if}</dt><dd>{value ?? '—'}</dd>
  {/each}
</dl>
