<script>
  import { toastMessage } from '../lib/store.js';
</script>

{#if $toastMessage}
  <div class="toast" role="status">{$toastMessage}</div>
{/if}
