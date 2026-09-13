<script>
  import { confirmState } from '../lib/store.js';
  let dialog = $state();

  $effect(() => {
    if ($confirmState && dialog && !dialog.open) dialog.showModal();
  });
  function done(value) {
    const state = $confirmState;
    confirmState.set(null);
    dialog?.close();
    state?.resolve(value);
  }
</script>

<dialog bind:this={dialog} onclose={() => done(false)}>
  {#if $confirmState}
    <h2>{$confirmState.title}</h2>
    <p class="muted">{$confirmState.text}</p>
    <div class="dialog-actions">
      <button class="button secondary" onclick={() => done(false)}>Отмена</button>
      <button class="button danger" onclick={() => done(true)}>Подтвердить</button>
    </div>
  {/if}
</dialog>
