<script>
  import { onMount } from "svelte";
  import { auth } from "../lib/auth.svelte.js";
  import { get, post, patch, del } from "../lib/api.js";
  import { notice } from "../lib/notice.svelte.js";
  import Icon from "../lib/Icon.svelte";

  let users = $state([]);
  let loadError = $state("");

  // New user form
  let email = $state("");
  let role = $state("user");
  let password = $state("");
  let formError = $state("");
  let busy = $state(false);

  // A set-password link to hand over: {email, url}
  let handoff = $state(null);
  let copied = $state(false);

  // Inline "set password" for one user
  let pwFor = $state(null);
  let pwDraft = $state("");
  let confirming = $state(null); // `${action}:${id}`

  onMount(load);

  async function load() {
    try {
      users = await get("/api/admin/users");
      loadError = "";
    } catch (e) {
      loadError = e.message;
    }
  }

  function linkUrl(path) {
    return `${location.origin}${location.pathname}${path.replace(/^\//, "")}`;
  }

  async function create(e) {
    e.preventDefault();
    formError = "";
    busy = true;
    try {
      const res = await post("/api/admin/users", { email, role, password: password || null });
      handoff = res.link ? { email: res.user.email, url: linkUrl(res.link), kind: "invite" } : null;
      if (!res.link) notice.show(`Created ${res.user.email}`);
      email = password = "";
      role = "user";
      await load();
    } catch (err) {
      formError = err.message;
    } finally {
      busy = false;
    }
  }

  async function update(user, body, done) {
    try {
      const updated = await patch(`/api/admin/users/${user.id}`, body);
      users = users.map((u) => (u.id === user.id ? updated : u));
      if (done) notice.show(done);
    } catch (e) {
      notice.error(e.message);
    }
  }

  async function resetLink(user) {
    try {
      const res = await post(`/api/admin/users/${user.id}/reset-link`);
      handoff = { email: user.email, url: linkUrl(res.link), kind: user.hasPassword ? "reset" : "invite" };
      copied = false;
    } catch (e) {
      notice.error(e.message);
    }
  }

  async function setPassword(user, e) {
    e.preventDefault();
    await update(user, { password: pwDraft }, `Password changed for ${user.email}`);
    pwFor = null;
    pwDraft = "";
  }

  function confirmed(action, user) {
    const key = `${action}:${user.id}`;
    if (confirming === key) {
      confirming = null;
      return true;
    }
    confirming = key;
    setTimeout(() => confirming === key && (confirming = null), 3000);
    return false;
  }

  async function remove(user) {
    if (!confirmed("delete", user)) return;
    try {
      await del(`/api/admin/users/${user.id}`);
      users = users.filter((u) => u.id !== user.id);
      notice.show(`Deleted ${user.email} and their transcripts`);
    } catch (e) {
      notice.error(e.message);
    }
  }

  async function copy() {
    try {
      await navigator.clipboard.writeText(handoff.url);
      copied = true;
      setTimeout(() => (copied = false), 1600);
    } catch {
      notice.error("Copy failed — select the link and copy it manually");
    }
  }

  const date = (iso) => (iso ? new Date(iso).toLocaleDateString([], { dateStyle: "medium" }) : "—");
</script>

<div class="page-narrow">
  <h1 class="page-title">Users</h1>
  <p class="page-sub">
    Create accounts and hand people a link to set their password.
  </p>

  {#if handoff}
    <section class="card handoff">
      <div class="handoff-head">
        <h2>{handoff.kind === "invite" ? "Invite link" : "Password reset link"} for {handoff.email}</h2>
        <button class="btn icon" onclick={() => (handoff = null)} aria-label="Dismiss"><Icon name="x" /></button>
      </div>
      <p class="sub">
        Send this to them. It works once and expires in {handoff.kind === "invite" ? "7" : "3"} days. Creating another link
        cancels this one.
      </p>
      <div class="secret">
        <code>{handoff.url}</code>
        <button class="btn icon" onclick={copy} title="Copy link"><Icon name={copied ? "check" : "copy"} /></button>
      </div>
    </section>
  {/if}

  <section class="card">
    <h2>Add a user</h2>
    <p class="sub">Leave the password empty to get an invite link instead.</p>
    <form class="new" onsubmit={create}>
      <label class="field grow">
        Email
        <input class="input" type="email" bind:value={email} required autocomplete="off" />
      </label>
      <label class="field">
        Role
        <select class="input" bind:value={role}>
          <option value="user">User</option>
          <option value="admin">Admin</option>
        </select>
      </label>
      <label class="field">
        Password (optional)
        <input class="input" type="password" bind:value={password} minlength="8" autocomplete="new-password" />
      </label>
      <button class="btn primary" disabled={busy}>{busy ? "Adding…" : "Add user"}</button>
    </form>
    {#if formError}<p class="form-error">{formError}</p>{/if}
  </section>

  <section class="card">
    <h2>All users <span class="muted">{users.length || ""}</span></h2>
    {#if loadError}<p class="form-error">{loadError}</p>{/if}
    <ul class="rows">
      {#each users as u (u.id)}
        {@const me = u.id === auth.user?.id}
        <li class="urow" class:disabled={u.disabled}>
          <div class="who">
            <div class="email">{u.email}{#if me}<span class="chip">you</span>{/if}</div>
            <div class="meta">
              {#if u.disabled}<span class="chip bad">Disabled</span>{/if}
              {#if !u.hasPassword}<span class="chip">Invite pending</span>{/if}
              <span>Last sign-in {date(u.lastLoginAt)} · added {date(u.createdAt)}</span>
            </div>
          </div>
          <select
            class="input small role"
            value={u.role}
            disabled={me}
            title={me ? "You can't change your own role" : "Role"}
            aria-label="Role for {u.email}"
            onchange={(e) => update(u, { role: e.currentTarget.value }, `${u.email} is now ${e.currentTarget.value === "admin" ? "an admin" : "a user"}`)}
          >
            <option value="user">User</option>
            <option value="admin">Admin</option>
          </select>
          <div class="row-actions">
            {#if pwFor === u.id}
              <form class="pw" onsubmit={(e) => setPassword(u, e)}>
                <input class="input small" type="password" bind:value={pwDraft} minlength="8" placeholder="New password" required autocomplete="new-password" />
                <button class="btn primary">Save</button>
                <button type="button" class="btn" onclick={() => (pwFor = null)}>Cancel</button>
              </form>
            {:else}
              <button class="btn" onclick={() => ((pwFor = u.id), (pwDraft = ""))}>Set password</button>
              <button class="btn" onclick={() => resetLink(u)}>{u.hasPassword ? "Reset link" : "Invite link"}</button>
              {#if !me}
                <button
                  class="btn"
                  onclick={() => update(u, { disabled: !u.disabled }, `${u.email} ${u.disabled ? "enabled" : "disabled"}`)}
                >
                  {u.disabled ? "Enable" : "Disable"}
                </button>
                <button class="btn" class:danger={confirming === `delete:${u.id}`} onclick={() => remove(u)}>
                  {confirming === `delete:${u.id}` ? "Delete user and transcripts?" : "Delete"}
                </button>
              {/if}
            {/if}
          </div>
        </li>
      {/each}
    </ul>
  </section>
</div>

<style>
  .handoff {
    border-color: color-mix(in oklab, var(--accent) 45%, var(--border));
  }

  .handoff-head {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 12px;
  }

  .new {
    display: flex;
    flex-wrap: wrap;
    align-items: flex-end;
    gap: 12px;
  }

  .grow {
    flex: 1 1 220px;
  }

  .rows {
    margin: 4px 0 0;
    padding: 0;
    list-style: none;
  }

  .urow {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 10px 16px;
    padding: 12px 0;
    border-top: 1px solid var(--border);
  }

  .who {
    flex: 1 1 240px;
    min-width: 0;
  }

  .email {
    display: flex;
    align-items: center;
    gap: 8px;
    font-weight: 500;
    font-size: 14px;
    overflow-wrap: anywhere;
  }

  .urow.disabled .email {
    color: var(--text-3);
  }

  .meta {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 6px;
    margin-top: 3px;
    font-size: 12.5px;
    color: var(--text-3);
  }

  .input.small {
    height: 30px;
    font-size: 13px;
  }

  .role {
    flex: none;
  }

  .row-actions {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    margin-left: auto;
  }

  .row-actions .btn {
    height: 30px;
    padding: 0 10px;
    font-size: 12.5px;
  }

  .pw {
    display: inline-flex;
    flex-wrap: wrap;
    gap: 6px;
  }

  .pw .input {
    width: 170px;
  }

  @media (max-width: 560px) {
    .page-narrow {
      padding: 20px 16px 40px;
    }

    .row-actions {
      margin-left: 0;
    }
  }
</style>
