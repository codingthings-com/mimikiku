<script>
  import { link } from "svelte-spa-router";
  import { get, del } from "../lib/api.js";
  import { recorder } from "../lib/session.svelte.js";
  import { notice } from "../lib/notice.svelte.js";
  import Icon from "../lib/Icon.svelte";

  const PAGE = 50;

  let items = $state([]);
  let query = $state("");
  let loading = $state(true);
  let error = $state("");
  let more = $state(false);
  let confirming = $state(null); // id awaiting a second click to delete

  let seq = 0;
  async function load(append = false) {
    const mine = ++seq;
    loading = true;
    error = "";
    try {
      const params = new URLSearchParams({ limit: String(PAGE), offset: String(append ? items.length : 0) });
      if (query.trim()) params.set("q", query.trim());
      const page = await get(`/api/transcripts?${params}`);
      if (mine !== seq) return;
      items = append ? [...items, ...page] : page;
      more = page.length === PAGE;
    } catch (e) {
      if (mine === seq) error = e.message;
    } finally {
      if (mine === seq) loading = false;
    }
  }

  // Debounced search; also the first load.
  $effect(() => {
    query;
    const t = setTimeout(() => load(), query ? 250 : 0);
    return () => clearTimeout(t);
  });

  async function remove(item) {
    if (confirming !== item.id) {
      confirming = item.id;
      setTimeout(() => confirming === item.id && (confirming = null), 3000);
      return;
    }
    confirming = null;
    try {
      await del(`/api/transcripts/${encodeURIComponent(item.id)}`);
      items = items.filter((t) => t.id !== item.id);
    } catch (e) {
      notice.error(`Delete failed: ${e.message}`);
    }
  }

  function when(iso) {
    const d = new Date(iso);
    const today = new Date();
    const sameDay = d.toDateString() === today.toDateString();
    return sameDay
      ? d.toLocaleTimeString([], { timeStyle: "short" })
      : d.toLocaleDateString([], { dateStyle: "medium" });
  }

  const title = (t) => t.title || new Date(t.createdAt).toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
</script>

<div class="page-narrow">
  <div class="head">
    <div>
      <h1 class="page-title">Transcripts</h1>
      <p class="page-sub">Everything you've recorded.</p>
    </div>
    <a class="btn primary" href="/new" use:link><Icon name="mic" /> New recording</a>
  </div>

  <label class="search">
    <Icon name="search" size={15} />
    <span class="visually-hidden">Search transcripts</span>
    <input bind:value={query} placeholder="Search titles and text" />
    {#if query}
      <button onclick={() => (query = "")} aria-label="Clear search"><Icon name="x" size={13} /></button>
    {/if}
  </label>

  {#if error}
    <p class="form-error">{error}</p>
  {:else if !loading && items.length === 0}
    <div class="empty">
      {#if query.trim()}
        No transcripts match “{query.trim()}”.
      {:else}
        No transcripts yet. Start a recording and it will show up here.
      {/if}
    </div>
  {/if}

  <ul>
    {#each items as t (t.id)}
      {@const isLive = recorder.active && recorder.target?.id === t.id}
      <li>
        <a class="item" href="/t/{t.id}" use:link>
          <div class="line1">
            <span class="title" class:untitled={!t.title}>{title(t)}</span>
            {#if isLive}<span class="chip bad">Recording</span>{/if}
            <time>{when(t.updatedAt)}</time>
          </div>
          <div class="preview">{t.preview || "No lines yet"}</div>
          <div class="meta">
            {t.segmentCount} {t.segmentCount === 1 ? "line" : "lines"} · {t.recordingCount}
            {t.recordingCount === 1 ? "recording" : "recordings"}
          </div>
        </a>
        <button
          class="btn icon"
          class:danger={confirming === t.id}
          onclick={() => remove(t)}
          disabled={isLive}
          title={confirming === t.id ? "Click again to delete" : "Delete"}
          aria-label="Delete transcript"
        >
          <Icon name={confirming === t.id ? "alert" : "trash"} />
        </button>
      </li>
    {/each}
  </ul>

  {#if more}
    <div class="more">
      <button class="btn" onclick={() => load(true)} disabled={loading}>{loading ? "Loading…" : "Show more"}</button>
    </div>
  {/if}
</div>

<style>
  .head {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 16px;
  }

  .search {
    display: flex;
    align-items: center;
    gap: 8px;
    height: 38px;
    padding: 0 12px;
    margin-bottom: 16px;
    border: 1px solid var(--border);
    border-radius: 9px;
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
    font-size: 14px;
    color: var(--text);
  }

  .search button {
    display: grid;
    place-items: center;
    padding: 2px;
    border: none;
    background: none;
    color: var(--text-3);
  }

  ul {
    margin: 0;
    padding: 0;
    list-style: none;
    display: flex;
    flex-direction: column;
    gap: 8px;
  }

  li {
    display: flex;
    align-items: center;
    gap: 8px;
  }

  .item {
    flex: 1;
    min-width: 0;
    display: block;
    padding: 12px 14px;
    border: 1px solid var(--border);
    border-radius: var(--radius);
    background: var(--surface);
    color: var(--text);
    transition: border-color 0.15s, background 0.15s;
  }

  .item:hover {
    border-color: var(--border-strong);
    background: var(--surface-2);
  }

  .line1 {
    display: flex;
    align-items: center;
    gap: 10px;
  }

  .title {
    flex: 1;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-weight: 600;
  }

  .title.untitled {
    color: var(--text-2);
  }

  time {
    flex: none;
    font-size: 12.5px;
    color: var(--text-3);
  }

  .preview {
    margin-top: 3px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-size: 13.5px;
    color: var(--text-2);
  }

  .meta {
    margin-top: 4px;
    font-size: 12px;
    color: var(--text-3);
  }

  .empty {
    padding: 40px 0;
    text-align: center;
    color: var(--text-2);
    font-size: 14px;
  }

  .more {
    display: flex;
    justify-content: center;
    margin-top: 16px;
  }

  @media (max-width: 560px) {
    .page-narrow {
      padding: 20px 16px 40px;
    }

    .head {
      flex-direction: column;
    }
  }
</style>
