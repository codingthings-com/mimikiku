<script>
  import { push, link } from "svelte-spa-router";
  import { workspace, recorder } from "../lib/session.svelte.js";
  import { notice } from "../lib/notice.svelte.js";
  import TranscriptView from "../lib/TranscriptView.svelte";
  import SpeakersPanel from "../lib/SpeakersPanel.svelte";
  import RecordDock from "../lib/RecordDock.svelte";
  import ExportMenu from "../lib/ExportMenu.svelte";
  import Icon from "../lib/Icon.svelte";

  // From the router: /t/:id opens a saved transcript, /new starts a new one.
  let { params = {} } = $props();

  $effect(() => {
    if (params.id) workspace.show(params.id);
    else workspace.showNew();
  });

  const transcript = $derived(workspace.current);
  const live = $derived(recorder.active && recorder.target === transcript);
  const elsewhere = $derived(recorder.active && recorder.target !== transcript);

  let query = $state("");
  let matches = $state(0);
  let showTimes = $state(true);
  let panelOpen = $state(false); // narrow screens only
  let confirmDelete = $state(false);
  let search;

  async function remove() {
    if (!confirmDelete) {
      confirmDelete = true;
      setTimeout(() => (confirmDelete = false), 3000);
      return;
    }
    confirmDelete = false;
    try {
      await transcript.remove();
      notice.show("Transcript deleted");
      query = "";
      push("/transcripts");
    } catch (e) {
      notice.error(`Delete failed: ${e.message}`);
    }
  }

  // Title: click to rename.
  let editingTitle = $state(false);
  let titleDraft = $state("");

  function editTitle() {
    if (!transcript.id) return;
    titleDraft = transcript.title;
    editingTitle = true;
  }

  function commitTitle() {
    if (!editingTitle) return;
    editingTitle = false;
    if (titleDraft.trim() !== transcript.title) {
      transcript.setTitle(titleDraft).catch((e) => notice.error(`Rename failed: ${e.message}`));
    }
  }

  function titleKeys(e) {
    if (e.key === "Enter") commitTitle();
    if (e.key === "Escape") editingTitle = false;
  }

  function focus(node) {
    node.focus();
    node.select();
  }

  function onkeydown(e) {
    if (e.metaKey || e.ctrlKey || e.altKey) return;
    const el = e.target;
    if (el instanceof HTMLElement && (el.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName))) {
      if (e.key === "Escape" && el === search) {
        query = "";
        search.blur();
      }
      return;
    }
    if (workspace.confirmingNew) return;
    const key = e.key.toLowerCase();
    if (key === "r") {
      e.preventDefault();
      if (recorder.capturing) recorder.stop();
      else if (transcript.status === "ready") recorder.start();
    } else if (key === "s" && recorder.capturing) {
      e.preventDefault();
      recorder.stop();
    } else if (key === "n" && !recorder.active) {
      e.preventDefault();
      workspace.startNew();
    } else if (e.key === " " && recorder.capturing) {
      e.preventDefault();
      recorder.togglePause();
    } else if (e.key === "/") {
      e.preventDefault();
      search?.focus();
    }
  }
</script>

<svelte:window {onkeydown} />

<div class="page">
  <div class="main">
    {#if elsewhere}
      <div class="banner">
        Recording into another transcript.
        {#if recorder.target?.id}<a href="/t/{recorder.target.id}" use:link>Go to it</a>{:else}<a href="/new" use:link>Go to it</a>{/if}
      </div>
    {/if}
    <div class="toolbar">
      <div class="title">
        {#if editingTitle}
          <input
            class="title-input"
            bind:value={titleDraft}
            onblur={commitTitle}
            onkeydown={titleKeys}
            use:focus
            maxlength="200"
            placeholder="Untitled"
            aria-label="Transcript title"
          />
        {:else}
          <button
            class="title-btn"
            class:untitled={!transcript.title}
            onclick={editTitle}
            disabled={!transcript.id}
            title={transcript.id ? "Rename" : "Saved once the first words arrive"}
          >
            <span>{transcript.displayTitle}</span>
            {#if transcript.id}<Icon name="pencil" size={12} />{/if}
          </button>
        {/if}
      </div>

      <label class="search">
        <Icon name="search" size={15} />
        <span class="visually-hidden">Search transcript</span>
        <input bind:this={search} bind:value={query} placeholder="Search transcript" disabled={transcript.isEmpty} />
        {#if query.trim()}
          <span class="matches">{matches}</span>
          <button class="clear-q" onclick={() => (query = "")} aria-label="Clear search"><Icon name="x" size={13} /></button>
        {:else}
          <kbd>/</kbd>
        {/if}
      </label>

      <div class="actions">
        <button
          class="btn icon"
          class:active={showTimes}
          onclick={() => (showTimes = !showTimes)}
          title={showTimes ? "Hide timestamps" : "Show timestamps"}
          aria-pressed={showTimes}
        >
          <Icon name="clock" />
        </button>
        <button
          class="btn icon panel-toggle"
          class:active={panelOpen}
          onclick={() => (panelOpen = !panelOpen)}
          title="Speakers"
          aria-pressed={panelOpen}
        >
          <Icon name="users" />
        </button>
        <ExportMenu {showTimes} />
        {#if transcript.id && !live}
          <button class="btn" onclick={() => workspace.startNew()} disabled={recorder.active} title="New transcript (N)">
            <Icon name="plus" />
            <span>New</span>
          </button>
        {/if}
        <button
          class="btn"
          class:danger={confirmDelete}
          onclick={remove}
          disabled={!transcript.id || live}
          title={live ? "Stop recording to delete" : "Delete transcript"}
        >
          <Icon name="trash" />
          {#if confirmDelete}<span>Delete transcript?</span>{/if}
        </button>
      </div>
    </div>

    <TranscriptView {query} {showTimes} onmatches={(n) => (matches = n)} />
    <RecordDock />
  </div>

  <div class="side" class:open={panelOpen}>
    <SpeakersPanel />
  </div>
</div>

<style>
  .page {
    flex: 1;
    min-height: 0;
    display: grid;
    grid-template-columns: 1fr 300px;
  }

  .main {
    min-width: 0;
    min-height: 0;
    display: flex;
    flex-direction: column;
  }

  .side {
    min-height: 0;
    display: flex;
    flex-direction: column;
  }

  .side > :global(*) {
    flex: 1;
  }

  .toolbar {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 10px 16px;
    border-bottom: 1px solid var(--border);
  }

  .banner {
    padding: 8px 16px;
    font-size: 13px;
    color: var(--warn);
    background: color-mix(in oklab, var(--warn) 12%, transparent);
    border-bottom: 1px solid var(--border);
  }

  .banner a {
    margin-left: 6px;
    font-weight: 600;
  }

  .title {
    flex: 1 1 200px;
    min-width: 0;
  }

  .title-btn {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    max-width: 100%;
    padding: 4px 8px;
    margin-left: -8px;
    border: none;
    border-radius: 7px;
    background: none;
    color: var(--text);
    font-size: 15px;
    font-weight: 600;
    letter-spacing: -0.01em;
  }

  .title-btn span {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .title-btn :global(svg) {
    flex: none;
    color: var(--text-3);
  }

  .title-btn:hover:not(:disabled) {
    background: var(--surface-2);
  }

  .title-btn:disabled {
    cursor: default;
    color: var(--text-2);
  }

  .title-input {
    width: 100%;
    height: 32px;
    padding: 0 8px;
    margin-left: -8px;
    border: 1px solid var(--accent);
    border-radius: 7px;
    background: var(--surface);
    color: var(--text);
    font-size: 15px;
    font-weight: 600;
    outline: none;
  }

  .search {
    flex: 0 1 300px;
    min-width: 0;
    display: flex;
    align-items: center;
    gap: 8px;
    height: 34px;
    padding: 0 10px;
    border: 1px solid var(--border);
    border-radius: 8px;
    background: var(--surface);
    color: var(--text-3);
  }

  .search:focus-within {
    border-color: var(--accent);
  }

  .search input {
    flex: 1;
    min-width: 0;
    border: none;
    background: none;
    outline: none;
    font-size: 13.5px;
    color: var(--text);
  }

  .search input::placeholder {
    color: var(--text-3);
  }

  .search kbd {
    font-family: var(--mono);
    font-size: 11px;
    padding: 0 5px;
    border: 1px solid var(--border-strong);
    border-radius: 4px;
  }

  .matches {
    font-size: 12px;
    font-variant-numeric: tabular-nums;
  }

  .clear-q {
    display: grid;
    place-items: center;
    padding: 2px;
    border: none;
    border-radius: 4px;
    background: none;
    color: var(--text-3);
  }

  .clear-q:hover {
    color: var(--text);
  }

  .actions {
    flex: none;
    margin-left: auto;
    display: flex;
    gap: 6px;
  }

  .panel-toggle {
    display: none;
  }

  @media (max-width: 960px) {
    .page {
      grid-template-columns: 1fr;
      position: relative;
    }

    .panel-toggle {
      display: inline-flex;
    }

    .side {
      display: none;
      position: absolute;
      top: 55px;
      right: 12px;
      z-index: 10;
      width: min(320px, calc(100% - 24px));
      max-height: calc(100% - 140px);
      border-radius: var(--radius);
      overflow: hidden;
      border: 1px solid var(--border-strong);
      box-shadow: var(--shadow);
    }

    .side.open {
      display: flex;
    }

    .side :global(.panel) {
      border-left: none;
    }
  }

  @media (max-width: 560px) {
    .toolbar {
      flex-wrap: wrap;
      padding: 8px 12px;
      gap: 8px;
    }

    .title {
      flex-basis: 100%;
    }

    .search kbd {
      display: none;
    }
  }
</style>
