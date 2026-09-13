<script>
  // options: [{value, label}]; value через $bindable
  let { options = [], value = $bindable(''), ariaLabel = '' } = $props();
</script>

<div class="segmented" role="group" aria-label={ariaLabel}>
  {#each options as opt}
    <button type="button" aria-pressed={value === opt.value} onclick={() => (value = opt.value)}>
      {opt.label}
    </button>
  {/each}
</div>
