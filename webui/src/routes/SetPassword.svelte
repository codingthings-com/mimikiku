<script>
  // Opened from an invite or reset link: /#/set-password/<secret>
  import { onMount } from "svelte";
  import { replace } from "svelte-spa-router";
  import { auth } from "../lib/auth.svelte.js";
  import { get } from "../lib/api.js";
  import Icon from "../lib/Icon.svelte";

  let { params = {} } = $props();

  let link = $state(null); // {email, kind}
  let loadError = $state("");
  let password = $state("");
  let confirm = $state("");
  let error = $state("");
  let busy = $state(false);

  onMount(async () => {
    try {
      link = await get(`/api/auth/links/${encodeURIComponent(params.secret)}`, { quiet401: true });
    } catch (e) {
      loadError = e.message;
    }
  });

  async function submit(e) {
    e.preventDefault();
    error = "";
    if (password !== confirm) {
      error = "The passwords don't match";
      return;
    }
    busy = true;
    try {
      await auth.redeem(params.secret, password);
      replace("/");
    } catch (err) {
      error = err.message;
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

    {#if loadError}
      <h1>Link not valid</h1>
      <p class="muted">{loadError}. Ask an admin for a new one.</p>
      <a class="btn" href="#/">Go to sign in</a>
    {:else if !link}
      <p class="muted">Checking your link…</p>
    {:else}
      <h1>{link.kind === "invite" ? "Welcome to Mimikiku" : "Reset your password"}</h1>
      <p class="muted">Choose a password for <strong>{link.email}</strong>. You'll be signed in right away.</p>
      <input type="email" autocomplete="username" value={link.email} hidden readonly />
      <label class="field">
        New password
        <input class="input" type="password" autocomplete="new-password" minlength="8" bind:value={password} required />
      </label>
      <label class="field">
        Repeat it
        <input class="input" type="password" autocomplete="new-password" minlength="8" bind:value={confirm} required />
      </label>
      {#if error}<p class="form-error">{error}</p>{/if}
      <button class="btn primary submit" disabled={busy}>{busy ? "Saving…" : "Set password and sign in"}</button>
    {/if}
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
    width: min(400px, 100%);
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
    margin: 6px 0 0;
    font-size: 20px;
    font-weight: 650;
    letter-spacing: -0.01em;
  }

  p {
    margin: 0;
    font-size: 14px;
  }

  strong {
    color: var(--text);
  }

  .submit {
    justify-content: center;
    height: 38px;
    margin-top: 4px;
    font-size: 14px;
  }
</style>
