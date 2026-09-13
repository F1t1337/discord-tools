<script>
  let { text } = $props();
</script>

<span class="hint" tabindex="0" role="note" aria-label={text}>
  <span class="hint-mark" aria-hidden="true">?</span>
  <span class="hint-tip">{text}</span>
</span>
