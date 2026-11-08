<script>
  import { onMount } from "svelte";
  import { auth } from "../lib/auth.svelte.js";
  import { get, post, del } from "../lib/api.js";
  import { notice } from "../lib/notice.svelte.js";
  import Icon from "../lib/Icon.svelte";

  // ---- password ----
  let current = $state("");
  let next = $state("");
  let repeat = $state("");
  let pwError = $state("");
  let pwBusy = $state(false);

  async function changePassword(e) {
    e.preventDefault();
    pwError = "";
    if (next !== repeat) {
      pwError = "The new passwords don't match";
      return;
    }
    pwBusy = true;
    try {
      await post("/api/auth/password", { current, new: next });
      current = next = repeat = "";
      notice.show("Password changed. Other devices were signed out.");
    } catch (err) {
      pwError = err.message;
    } finally {
      pwBusy = false;
    }
  }

  // ---- API tokens ----
  let tokens = $state([]);
  let tokenName = $state("");
  let created = $state(null); // {name, token} shown once
  let tokenError = $state("");
  let copied = $state(false);
  let confirming = $state(null);

  onMount(loadTokens);

  async function loadTokens() {
    try {
      tokens = await get("/api/tokens");
    } catch (e) {
      tokenError = e.message;
    }
  }

  async function createToken(e) {
    e.preventDefault();
    tokenError = "";
    try {
      created = await post("/api/tokens", { name: tokenName.trim() });
      tokenName = "";
      copied = false;
      await loadTokens();
    } catch (err) {
      tokenError = err.message;
    }
  }

  async function revoke(t) {
    if (confirming !== t.id) {
      confirming = t.id;
      setTimeout(() => confirming === t.id && (confirming = null), 3000);
      return;
    }
    confirming = null;
    try {
      await del(`/api/tokens/${encodeURIComponent(t.id)}`);
      tokens = tokens.filter((x) => x.id !== t.id);
      if (created?.id === t.id) created = null;
    } catch (e) {
      notice.error(`Revoke failed: ${e.message}`);
    }
  }

  async function copy(text) {
    try {
      await navigator.clipboard.writeText(text);
      copied = true;
      setTimeout(() => (copied = false), 1600);
    } catch {
      notice.error("Copy failed — select the token and copy it manually");
    }
  }

  const wsUrl = `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/listen`;
  const date = (iso) => (iso ? new Date(iso).toLocaleDateString([], { dateStyle: "medium" }) : "never");
</script>

<div class="page-narrow">
  <h1 class="page-title">Account</h1>
  <p class="page-sub">Signed in as <strong>{auth.user?.email}</strong>{auth.isAdmin ? " · admin" : ""}</p>

  <section class="card">
    <h2>Password</h2>
    <p class="sub">Changing it signs you out everywhere else.</p>
    <form class="grid" onsubmit={changePassword}>
      <input type="email" autocomplete="username" value={auth.user?.email} hidden readonly />
      <label class="field">
        Current password
        <input class="input" type="password" autocomplete="current-password" bind:value={current} required />
      </label>
      <label class="field">
        New password
        <input class="input" type="password" autocomplete="new-password" minlength="8" bind:value={next} required />
      </label>
      <label class="field">
        Repeat new password
        <input class="input" type="password" autocomplete="new-password" minlength="8" bind:value={repeat} required />
      </label>
      <div class="actions">
        {#if pwError}<p class="form-error">{pwError}</p>{/if}
        <button class="btn primary" disabled={pwBusy}>{pwBusy ? "Saving…" : "Change password"}</button>
      </div>
    </form>
  </section>

  <section class="card">
    <h2>API tokens</h2>
    <p class="sub">
      For apps that can't sign in with a browser, such as the <code>mimikiku-capture</code> terminal client. A token can
      record and manage your transcripts, but can't change your account.
    </p>

    {#if created}
      <div class="created">
        <p><strong>{created.name}</strong> — copy this token now. It won't be shown again.</p>
        <div class="secret">
          <code>{created.token}</code>
          <button class="btn icon" onclick={() => copy(created.token)} title="Copy token">
            <Icon name={copied ? "check" : "copy"} />
          </button>
        </div>
        <p class="muted usage">
          Use it with: <code>mimikiku-capture --ws-url {wsUrl} --token &lt;token&gt;</code> (or set <code>MIMIKIKU_TOKEN</code>)
        </p>
      </div>
    {/if}

    <form class="new-token" onsubmit={createToken}>
      <input class="input" bind:value={tokenName} placeholder="Name, e.g. work laptop" maxlength="60" required />
      <button class="btn primary">Create token</button>
    </form>
    {#if tokenError}<p class="form-error">{tokenError}</p>{/if}

    {#if tokens.length}
      <table>
        <thead><tr><th>Name</th><th>Token</th><th>Created</th><th>Last used</th><th></th></tr></thead>
        <tbody>
          {#each tokens as t (t.id)}
            <tr>
              <td>{t.name}</td>
              <td class="mono">mk_…{t.hint}</td>
              <td>{date(t.createdAt)}</td>
              <td>{date(t.lastUsedAt)}</td>
              <td class="right">
                <button class="btn" class:danger={confirming === t.id} onclick={() => revoke(t)}>
                  {confirming === t.id ? "Revoke?" : "Revoke"}
                </button>
              </td>
            </tr>
          {/each}
        </tbody>
      </table>
    {/if}
  </section>
</div>

<style>
  strong {
    color: var(--text);
  }

  code {
    font-family: var(--mono);
    font-size: 12.5px;
  }

  .grid {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 14px;
  }

  .actions {
    grid-column: 1 / -1;
    display: flex;
    align-items: center;
    justify-content: flex-end;
    gap: 12px;
  }

  .actions .form-error {
    margin-right: auto;
  }

  .created {
    display: flex;
    flex-direction: column;
    gap: 8px;
    margin-bottom: 16px;
    font-size: 13.5px;
  }

  .created p {
    margin: 0;
  }

  .usage {
    font-size: 12.5px;
    overflow-wrap: anywhere;
  }

  .new-token {
    display: flex;
    gap: 8px;
  }

  .new-token .input {
    flex: 0 1 320px;
  }

  table {
    width: 100%;
    margin-top: 16px;
    border-collapse: collapse;
    font-size: 13.5px;
  }

  th {
    padding: 6px 8px;
    text-align: left;
    font-size: 11.5px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: var(--text-3);
    border-bottom: 1px solid var(--border);
  }

  td {
    padding: 8px;
    border-bottom: 1px solid var(--border);
  }

  .mono {
    font-family: var(--mono);
    font-size: 12.5px;
    color: var(--text-2);
  }

  .right {
    text-align: right;
  }

  @media (max-width: 720px) {
    .grid {
      grid-template-columns: 1fr;
    }

    th:nth-child(3),
    td:nth-child(3),
    th:nth-child(4),
    td:nth-child(4) {
      display: none;
    }
  }
</style>
