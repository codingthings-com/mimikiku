<svelte:options runes={true} />

<script>
  import { onMount } from "svelte";
  import Router, { link, location, replace } from "svelte-spa-router";
  import { wrap } from "svelte-spa-router/wrap";
  import Home from "./routes/Home.svelte";
  import Transcription from "./routes/Transcription.svelte";
  import Transcripts from "./routes/Transcripts.svelte";
  import Account from "./routes/Account.svelte";
  import Users from "./routes/Users.svelte";
  import Service from "./routes/Service.svelte";
  import Monitoring from "./routes/Monitoring.svelte";
  import Login from "./routes/Login.svelte";
  import SetPassword from "./routes/SetPassword.svelte";
  import { auth } from "./lib/auth.svelte.js";
  import { recorder, workspace } from "./lib/session.svelte.js";
  import { clock } from "./lib/format.js";
  import Icon from "./lib/Icon.svelte";
  import Notice from "./lib/Notice.svelte";
  import NewTranscriptDialog from "./lib/NewTranscriptDialog.svelte";

  const adminOnly = (component) => wrap({ component, conditions: [() => auth.isAdmin] });

  const routes = {
    "/": Home,
    "/new": Transcription,
    "/t/:id": Transcription,
    "/transcripts": Transcripts,
    "/account": Account,
    "/admin/users": adminOnly(Users),
    "/admin/service": adminOnly(Service),
    "/monitoring": adminOnly(Monitoring),
    "*": Home,
  };

  onMount(() => auth.load());

  // Set-password links work signed in or out.
  const linkSecret = $derived($location.match(/^\/set-password\/(.+)$/)?.[1]);

  const recordingHref = $derived(recorder.target?.id ? `/t/${recorder.target.id}` : "/new");
  const onRecording = $derived($location === recordingHref || ($location === "/new" && !recorder.target?.id));

  let menuOpen = $state(false);

  async function signOut() {
    menuOpen = false;
    if (recorder.active) await recorder.stop();
    await auth.logout();
    replace("/");
  }

  function onwindowclick(e) {
    if (menuOpen && !e.target.closest?.(".user")) menuOpen = false;
  }
</script>

<svelte:window onclick={onwindowclick} onkeydown={(e) => e.key === "Escape" && (menuOpen = false)} />

<div class="shell">
  {#if !auth.ready}
    <div class="boot"></div>
  {:else if linkSecret}
    <SetPassword params={{ secret: linkSecret }} />
  {:else if !auth.user}
    <Login />
  {:else}
    <header class="topbar">
      <a href="/" use:link class="brand" aria-label="Mimikiku home">
        <span class="logo"><Icon name="wave" size={18} /></span>
        <span class="wordmark">Mimikiku</span>
      </a>

      <nav>
        <a
          href="#/new"
          onclick={(e) => {
            e.preventDefault();
            workspace.startNew();
          }}
          class:active={$location === "/new" || $location.startsWith("/t/")}>Transcribe</a
        >
        <a href="/transcripts" use:link class:active={$location === "/transcripts"}>Transcripts</a>
        {#if auth.isAdmin}
          <a href="/admin/users" use:link class:active={$location === "/admin/users"}>Users</a>
          <a href="/admin/service" use:link class:active={$location === "/admin/service"}>Service</a>
          <a href="/monitoring" use:link class:active={$location === "/monitoring"}>Monitoring</a>
        {/if}
      </nav>

      <div class="right">
        {#if recorder.active && !onRecording}
          <a href={recordingHref} use:link class="live-pill" class:paused={recorder.phase === "paused"}>
            <span class="dot"></span>
            {recorder.phase === "paused" ? "Paused" : "Recording"} · {clock(recorder.elapsed)}
          </a>
        {/if}

        <div class="user">
          <button class="user-btn" onclick={() => (menuOpen = !menuOpen)} aria-haspopup="menu" aria-expanded={menuOpen}>
            <Icon name="user" size={15} />
            <span class="email">{auth.user.email}</span>
          </button>
          {#if menuOpen}
            <div class="menu" role="menu">
              <div class="who">
                <span class="who-email">{auth.user.email}</span>
                {#if auth.isAdmin}<span class="chip accent">admin</span>{/if}
              </div>
              <a href="/account" use:link role="menuitem" onclick={() => (menuOpen = false)}>
                <Icon name="user" size={15} /> Account & API tokens
              </a>
              <button role="menuitem" onclick={signOut}><Icon name="logout" size={15} /> Sign out</button>
            </div>
          {/if}
        </div>
      </div>
    </header>

    <main class:scroll={$location !== "/new" && !$location.startsWith("/t/")}>
      <Router {routes} on:conditionsFailed={() => replace("/")} />
    </main>
    <NewTranscriptDialog />
  {/if}
  {#if auth.ready}
    <footer class="footer">© {new Date().getFullYear()}
      <a href="https://www.codingthings.com/" target="_blank" rel="noopener">codingthings.com</a></footer
    >
  {/if}
  <Notice />
</div>

<style>
  .shell {
    height: 100%;
    display: flex;
    flex-direction: column;
  }

  .boot {
    flex: 1;
  }

  .topbar {
    flex: none;
    display: flex;
    align-items: center;
    gap: 24px;
    height: 52px;
    padding: 0 16px;
    border-bottom: 1px solid var(--border);
    background: var(--surface);
  }

  .brand {
    display: flex;
    align-items: center;
    gap: 9px;
    color: var(--text);
    font-weight: 650;
    letter-spacing: -0.01em;
  }

  .logo {
    display: grid;
    place-items: center;
    width: 28px;
    height: 28px;
    border-radius: 8px;
    background: var(--accent);
    color: var(--accent-contrast);
  }

  nav {
    display: flex;
    gap: 2px;
    min-width: 0;
    overflow-x: auto;
    scrollbar-width: none;
  }

  nav a {
    padding: 6px 12px;
    border-radius: 7px;
    color: var(--text-2);
    font-size: 13.5px;
    font-weight: 500;
    white-space: nowrap;
  }

  nav a:hover {
    color: var(--text);
    background: var(--surface-2);
  }

  nav a.active {
    color: var(--text);
    background: var(--surface-3);
  }

  .right {
    margin-left: auto;
    display: flex;
    align-items: center;
    gap: 12px;
  }

  .live-pill {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    padding: 4px 12px;
    border-radius: 999px;
    font-size: 12.5px;
    font-weight: 600;
    font-variant-numeric: tabular-nums;
    white-space: nowrap;
    color: var(--rec);
    background: color-mix(in oklab, var(--rec) 14%, transparent);
  }

  .live-pill .dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: currentColor;
    animation: blink 1.4s ease-in-out infinite;
  }

  .live-pill.paused {
    color: var(--warn);
    background: color-mix(in oklab, var(--warn) 14%, transparent);
  }

  .live-pill.paused .dot {
    animation: none;
  }

  @keyframes blink {
    50% {
      opacity: 0.3;
    }
  }

  .user {
    position: relative;
  }

  .user-btn {
    display: inline-flex;
    align-items: center;
    gap: 7px;
    max-width: 220px;
    height: 32px;
    padding: 0 10px;
    border: 1px solid var(--border);
    border-radius: 8px;
    background: none;
    color: var(--text-2);
    font-size: 13px;
  }

  .user-btn:hover {
    color: var(--text);
    border-color: var(--border-strong);
  }

  .email {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .menu {
    position: absolute;
    right: 0;
    top: calc(100% + 6px);
    z-index: 30;
    min-width: 230px;
    width: max-content;
    max-width: min(380px, calc(100vw - 24px));
    padding: 5px;
    border: 1px solid var(--border-strong);
    border-radius: var(--radius);
    background: var(--surface);
    box-shadow: var(--shadow);
  }

  .who {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 8px 10px 10px;
    margin-bottom: 4px;
    border-bottom: 1px solid var(--border);
    font-size: 13px;
    color: var(--text-2);
  }

  .who-email {
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .menu a,
  .menu button {
    display: flex;
    align-items: center;
    gap: 9px;
    width: 100%;
    padding: 8px 10px;
    border: none;
    border-radius: 6px;
    background: none;
    color: var(--text);
    font-size: 13.5px;
    text-align: left;
  }

  .menu a:hover,
  .menu button:hover {
    background: var(--surface-2);
  }

  main {
    flex: 1;
    min-height: 0;
    display: flex;
    flex-direction: column;
  }

  main.scroll {
    overflow-y: auto;
  }

  .footer {
    flex: none;
    padding: 6px 16px;
    border-top: 1px solid var(--border);
    background: var(--surface);
    color: var(--text-3);
    font-size: 11.5px;
    text-align: center;
  }

  .footer a {
    color: inherit;
  }

  .footer a:hover {
    color: var(--text);
    text-decoration: underline;
  }

  @media (max-width: 720px) {
    .topbar {
      gap: 12px;
      padding: 0 12px;
    }

    .wordmark,
    .email {
      display: none;
    }

    nav a {
      padding: 6px 8px;
    }
  }
</style>
