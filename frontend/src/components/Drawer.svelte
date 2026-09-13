<script>
  import Icon from './Icon.svelte';
  let { open = false, title = '', onClose, children } = $props();

  $effect(() => {
    if (!open) return;
    const onKey = (e) => { if (e.key === 'Escape') onClose?.(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  });
</script>

{#if open}
  <!-- svelte-ignore a11y_no_static_element_interactions, a11y_click_events_have_key_events -->
  <div class="drawer-scrim" onclick={onClose} role="presentation"></div>
  <div class="drawer" role="dialog" aria-modal="true" aria-label={title}>
    <div class="drawer-head">
      <h2>{title}</h2>
      <button class="icon-button" onclick={onClose} aria-label="Закрыть"><Icon name="close" /></button>
    </div>
    {@render children?.()}
  </div>
{/if}
