<script>
  import Icon from '../components/Icon.svelte';
  let { login } = $props();
  let username = $state('admin');
  let password = $state('');
  let error = $state('');
  let busy = $state(false);

  async function submit(event) {
    event.preventDefault();
    busy = true; error = '';
    try {
      await login(username.trim(), password);
    } catch (err) {
      error = err.message;
    } finally {
      busy = false; password = '';
    }
  }
</script>

<section class="login-layout">
  <div class="login-brand">
    <a class="brand" href="/"><span class="brand-mark">D</span> discord tools<span class="brand-tag">ADMIN</span></a>
    <div>
      <p class="eyebrow">Ваша рабочая панель</p>
      <h1>Всё состояние.<br>В одном месте.</h1>
      <p class="login-description">Аккаунты, статистика обработки и журнал событий. Доступ только для владельца.</p>
    </div>
    <p class="muted">Защищённая сессия · данные с вашего сервера</p>
  </div>
  <div class="login-form-wrap">
    <form class="login-form" onsubmit={submit}>
      <span class="section-label">Вход в панель</span>
      <h2>С возвращением</h2>
      <p class="muted">Введите данные администратора.</p>
      <label for="username">Логин</label>
      <input id="username" autocomplete="username" required maxlength="100" bind:value={username}>
      <label for="password">Пароль</label>
      <input id="password" type="password" autocomplete="current-password" required maxlength="1024" bind:value={password}>
      {#if error}<p class="form-error" role="alert">{error}</p>{/if}
      <button type="submit" class="button primary full" disabled={busy}>
        Войти в панель <Icon name="arrow" size={17} />
      </button>
      <p class="login-hint">Если доступа ещё нет, настройте логин и пароль на сервере по инструкции в README.</p>
    </form>
  </div>
</section>
