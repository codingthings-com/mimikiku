<script>
  import { auth } from "../lib/auth.svelte.js";
  import Icon from "../lib/Icon.svelte";

  let email = $state("");
  let password = $state("");
  let error = $state("");
  let busy = $state(false);

  async function submit(e) {
    e.preventDefault();
    error = "";
    busy = true;
    try {
      await auth.login(email, password);
    } catch (err) {
      error = err.message;
      password = "";
    } finally {
      busy = false;
    }
  }
</script>

<div class="gate">
  <form class="card" onsubmit={submit}>
    <div class="brand">
      <span class="logo"><Icon name="wave" size={20} /></span>
      <span>Mimikiku</span>
    </div>
    <h1>Sign in</h1>

    <label class="field">
      Email
      <input class="input" type="email" autocomplete="username" bind:value={email} required />
    </label>
    <label class="field">
      Password
      <input class="input" type="password" autocomplete="current-password" bind:value={password} required />
    </label>

    {#if error}<p class="form-error">{error}</p>{/if}

    <button class="btn primary submit" disabled={busy}>{busy ? "Signing in…" : "Sign in"}</button>
    <p class="muted small">Accounts are created by an admin. Forgot your password? Ask an admin for a reset link.</p>
  </form>
</div>

<style>
  .gate {
    flex: 1;
    display: grid;
    place-items: center;
    padding: 24px 16px;
  }

  form {
    width: min(380px, 100%);
    display: flex;
    flex-direction: column;
    gap: 14px;
    padding: 28px;
  }

  .brand {
    display: flex;
    align-items: center;
    gap: 10px;
    font-weight: 650;
  }

  .logo {
    display: grid;
    place-items: center;
    width: 32px;
    height: 32px;
    border-radius: 9px;
    background: var(--accent);
    color: var(--accent-contrast);
  }

  h1 {
    margin: 6px 0 2px;
    font-size: 20px;
    font-weight: 650;
    letter-spacing: -0.01em;
  }

  .submit {
    justify-content: center;
    height: 38px;
    margin-top: 4px;
    font-size: 14px;
  }

  .small {
    margin: 0;
    font-size: 12.5px;
  }
</style>
