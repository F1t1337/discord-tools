<script>
  let { text } = $props();
</script>

<button class="hint" type="button" aria-label={text}>
  <span class="hint-mark" aria-hidden="true">?</span>
  <span class="hint-tip">{text}</span>
</button>
