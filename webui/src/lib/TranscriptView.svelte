<script>
  import { tick } from "svelte";
  import { fade } from "svelte/transition";
  import { workspace, recorder } from "./session.svelte.js";
  import { notice } from "./notice.svelte.js";
  import { clock } from "./format.js";
  import Icon from "./Icon.svelte";

  let { query = "", showTimes = true, onmatches } = $props();

  const transcript = $derived(workspace.current);
  // Live: this transcript is the one being recorded into.
  const live = $derived(recorder.active && recorder.target === transcript);

  // Editing one turn's lines: segment id -> draft text. Emptying a line deletes it.
  let editing = $state(null);
  let drafts = $state({});
  let saving = $state(false);

  function startEdit(turn) {
    editing = turn.id;
    drafts = Object.fromEntries(turn.segments.map((s) => [s.id, s.text]));
  }

  async function saveEdit(turn) {
    saving = true;
    try {
      for (const seg of turn.segments) {
        if (seg.id in drafts) await transcript.editSegment(seg, drafts[seg.id]);
      }
      editing = null;
    } catch (e) {
      notice.error(`Couldn't save the edit: ${e.message}`);
    } finally {
      saving = false;
    }
  }

  function editKeys(e, turn) {
    if (e.key === "Escape") editing = null;
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) saveEdit(turn);
  }

  function grow(node) {
    const fit = () => {
      node.style.height = "auto";
      node.style.height = `${node.scrollHeight}px`;
    };
    fit();
    node.addEventListener("input", fit);
    return { destroy: () => node.removeEventListener("input", fit) };
  }

  let scroller;
  let stick = $state(true);

  const needle = $derived(query.trim().toLowerCase());

  const visible = $derived(
    needle
      ? transcript.turns.filter((t) => t.segments.some((s) => s.text.toLowerCase().includes(needle)))
      : transcript.turns,
  );

  const matchCount = $derived.by(() => {
    if (!needle) return 0;
    let n = 0;
    for (const t of visible) for (const s of t.segments) n += s.text.toLowerCase().split(needle).length - 1;
    return n;
  });

  $effect(() => onmatches?.(matchCount));

  const recordingNo = $derived(new Map(transcript.recordings.map((r, i) => [r.id, i + 1])));
  const startedAt = $derived(new Map(transcript.recordings.map((r) => [r.id, r.startedAt])));

  /** Split text around matches so they can be wrapped in <mark>. */
  function parts(text) {
    if (!needle) return [{ text, hit: false }];
    const out = [];
    const lower = text.toLowerCase();
    let i = 0;
    for (let j = lower.indexOf(needle); j !== -1; j = lower.indexOf(needle, i)) {
      if (j > i) out.push({ text: text.slice(i, j), hit: false });
      out.push({ text: text.slice(j, j + needle.length), hit: true });
      i = j + needle.length;
    }
    if (i < text.length) out.push({ text: text.slice(i), hit: false });
    return out;
  }

  function initials(name) {
    const words = name.replace(/·.*$/, "").trim().split(/\s+/);
    if (/^speaker$/i.test(words[0]) && words[1]) return words[1].slice(0, 2);
    return (words[0][0] + (words[1]?.[0] ?? "")).toUpperCase();
  }

  function onscroll() {
    stick = scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < 80;
  }

  async function toBottom(smooth = true) {
    await tick();
    scroller?.scrollTo({ top: scroller.scrollHeight, behavior: smooth ? "smooth" : "instant" });
  }

  // Follow new text while the user is at the bottom.
  $effect(() => {
    transcript.segments.length;
    live;
    if (stick && !needle && !editing) toBottom();
  });

  $effect(() => {
    if (scroller) toBottom(false);
  });

  const showDividers = $derived(transcript.recordings.length > 1);
</script>

<div class="wrap">
  <div class="scroller" bind:this={scroller} {onscroll}>
    <div class="column">
      {#if transcript.status === "loading"}
        <div class="empty small"><p>Loading transcript…</p></div>
      {:else if transcript.status === "missing"}
        <div class="empty">
          <h2>Transcript not found</h2>
          <p>It may have been deleted. <a href="#/transcripts">Back to your transcripts</a></p>
        </div>
      {:else if transcript.status === "error"}
        <div class="empty">
          <h2>Couldn't load this transcript</h2>
          <p>{transcript.error} · <button class="link" onclick={() => transcript.load()}>Try again</button></p>
        </div>
      {:else if transcript.isEmpty && !live && transcript.id}
        <div class="empty">
          <h2>No lines yet</h2>
          <p>Press record or <kbd>R</kbd> to add a recording to this transcript.</p>
        </div>
      {:else if transcript.isEmpty && !live}
        <div class="empty">
          <div class="empty-icon"><Icon name="wave" size={28} /></div>
          <h2>Start a transcript</h2>
          <p>
            Press the record button or <kbd>R</kbd>. Speech is transcribed live, each speaker is labeled
            automatically, and the transcript is saved to your account as you go.
          </p>
        </div>
      {:else if needle && visible.length === 0}
        <div class="empty small">
          <p>No matches for “{query.trim()}”</p>
        </div>
      {/if}

      {#each visible as turn, i (turn.id)}
        {@const prev = visible[i - 1]}
        {#if showDividers && turn.recording !== prev?.recording}
          <div class="divider">
            <span>
              Recording {recordingNo.get(turn.recording)}
              {#if startedAt.get(turn.recording)}
                · {new Date(startedAt.get(turn.recording)).toLocaleString([], {
                  dateStyle: "medium",
                  timeStyle: "short",
                })}
              {/if}
            </span>
          </div>
        {/if}
        <article
          class="turn"
          class:unknown={turn.speaker.unknown}
          style="--c: var(--spk-{turn.speaker.unknown ? 'unknown' : turn.speaker.color})"
          in:fade={{ duration: 180 }}
        >
          <div class="avatar" aria-hidden="true">{turn.speaker.unknown ? "?" : initials(turn.speaker.name)}</div>
          <div class="body">
            <header>
              <span class="name">{turn.speaker.name}</span>
              {#if showTimes}<time>{clock(turn.start)}</time>{/if}
              {#if transcript.id && editing !== turn.id}
                <button class="edit" onclick={() => startEdit(turn)} title="Edit these lines" aria-label="Edit these lines">
                  <Icon name="pencil" size={13} />
                </button>
              {/if}
            </header>
            {#if editing === turn.id}
              <div class="editor">
                {#each turn.segments as seg (seg.id)}
                  {#if seg.id in drafts}
                    <textarea
                      bind:value={drafts[seg.id]}
                      use:grow
                      rows="1"
                      onkeydown={(e) => editKeys(e, turn)}
                      aria-label="Line at {clock(seg.start)}"
                      placeholder="Empty — this line will be deleted"
                    ></textarea>
                  {/if}
                {/each}
                <div class="editor-actions">
                  <span class="hint">Clear a line to delete it · Ctrl+Enter saves</span>
                  <button class="btn" onclick={() => (editing = null)} disabled={saving}>Cancel</button>
                  <button class="btn primary" onclick={() => saveEdit(turn)} disabled={saving}>
                    {saving ? "Saving…" : "Save"}
                  </button>
                </div>
              </div>
            {:else}
              <p>
                {#each turn.segments as seg (seg.id)}
                  <span class="seg" title="{clock(seg.start)} – {clock(seg.end)}" in:fade={{ duration: 250 }}
                    >{#each parts(seg.text) as p}{#if p.hit}<mark>{p.text}</mark>{:else}{p.text}{/if}{/each}</span
                  >{" "}
                {/each}
              </p>
            {/if}
          </div>
        </article>
      {/each}

      {#if live && !needle}
        <div class="live" aria-live="polite">
          <span class="dots"><i></i><i></i><i></i></span>
          {#if recorder.phase === "loading" || recorder.phase === "connecting"}
            Preparing models — speech will appear once they're ready
          {:else if recorder.phase === "paused"}
            Paused
          {:else if recorder.phase === "finishing"}
            Wrapping up the last sentence
          {:else}
            Listening — lines appear at the end of each sentence or pause
          {/if}
        </div>
      {/if}
    </div>
  </div>

  {#if !stick && !transcript.isEmpty}
    <button class="jump" onclick={() => ((stick = true), toBottom())} transition:fade={{ duration: 120 }}>
      <Icon name="down" size={14} /> Latest
    </button>
  {/if}
</div>

<style>
  .wrap {
    position: relative;
    flex: 1;
    min-height: 0;
    display: flex;
  }

  .scroller {
    flex: 1;
    overflow-y: auto;
    scrollbar-gutter: stable;
  }

  .column {
    max-width: 820px;
    margin: 0 auto;
    padding: 28px 24px 40px;
  }

  /* ---- empty state ---- */
  .empty {
    margin: 12vh auto 0;
    max-width: 420px;
    text-align: center;
    color: var(--text-2);
  }

  .empty.small {
    margin-top: 40px;
  }

  .empty-icon {
    display: inline-grid;
    place-items: center;
    width: 56px;
    height: 56px;
    border-radius: 16px;
    background: color-mix(in oklab, var(--accent) 14%, transparent);
    color: var(--accent);
    margin-bottom: 16px;
  }

  .empty h2 {
    margin: 0 0 8px;
    font-size: 20px;
    font-weight: 650;
    color: var(--text);
    letter-spacing: -0.01em;
  }

  .empty p {
    margin: 0;
    font-size: 14px;
  }

  kbd {
    font-family: var(--mono);
    font-size: 12px;
    padding: 1px 6px;
    border: 1px solid var(--border-strong);
    border-bottom-width: 2px;
    border-radius: 5px;
    background: var(--surface-2);
  }

  /* ---- turns ---- */
  .divider {
    display: flex;
    align-items: center;
    gap: 12px;
    margin: 28px 0 18px;
    color: var(--text-3);
    font-size: 12px;
    font-weight: 500;
    text-transform: uppercase;
    letter-spacing: 0.05em;
  }

  .divider::before,
  .divider::after {
    content: "";
    flex: 1;
    height: 1px;
    background: var(--border);
  }

  .divider:first-child {
    margin-top: 0;
  }

  .turn {
    display: grid;
    grid-template-columns: 36px 1fr;
    gap: 14px;
    padding: 10px 0;
  }

  .avatar {
    width: 36px;
    height: 36px;
    border-radius: 50%;
    display: grid;
    place-items: center;
    font-size: 13px;
    font-weight: 650;
    color: var(--c);
    background: color-mix(in oklab, var(--c) 16%, transparent);
    border: 1px solid color-mix(in oklab, var(--c) 35%, transparent);
    user-select: none;
  }

  .body {
    min-width: 0;
  }

  header {
    display: flex;
    align-items: baseline;
    gap: 10px;
    margin-bottom: 2px;
    user-select: none;
  }

  .name {
    font-weight: 600;
    font-size: 14px;
    color: var(--c);
  }

  time {
    font-family: var(--mono);
    font-size: 12px;
    color: var(--text-3);
    font-variant-numeric: tabular-nums;
  }

  p {
    margin: 0;
    font-size: 15.5px;
    line-height: 1.65;
    color: var(--text);
    overflow-wrap: anywhere;
  }

  .unknown p {
    color: var(--text-2);
  }

  .seg {
    border-radius: 3px;
    transition: background 0.15s;
  }

  .seg:hover {
    background: var(--surface-2);
  }

  .edit {
    display: grid;
    place-items: center;
    margin-left: auto;
    padding: 3px;
    border: none;
    border-radius: 5px;
    background: none;
    color: var(--text-3);
    opacity: 0;
    transition: opacity 0.15s;
  }

  .turn:hover .edit,
  .edit:focus-visible {
    opacity: 1;
  }

  .edit:hover {
    color: var(--text);
    background: var(--surface-2);
  }

  @media (hover: none) {
    .edit {
      opacity: 1;
    }
  }

  .editor {
    display: flex;
    flex-direction: column;
    gap: 6px;
    margin-top: 4px;
  }

  .editor textarea {
    width: 100%;
    resize: none;
    overflow: hidden;
    padding: 6px 9px;
    border: 1px solid var(--border-strong);
    border-radius: 7px;
    background: var(--surface);
    color: var(--text);
    font: inherit;
    font-size: 15px;
    line-height: 1.55;
  }

  .editor textarea:focus {
    outline: none;
    border-color: var(--accent);
  }

  .editor-actions {
    display: flex;
    align-items: center;
    gap: 6px;
    justify-content: flex-end;
  }

  .editor-actions .hint {
    margin-right: auto;
    font-size: 12px;
    color: var(--text-3);
  }

  .link {
    padding: 0;
    border: none;
    background: none;
    color: var(--accent);
  }

  mark {
    background: var(--mark);
    color: inherit;
    border-radius: 3px;
    padding: 0 1px;
  }

  /* ---- live indicator ---- */
  .live {
    display: flex;
    align-items: center;
    gap: 12px;
    margin-top: 14px;
    padding-left: 50px;
    color: var(--text-3);
    font-size: 13px;
  }

  .dots {
    display: inline-flex;
    gap: 4px;
  }

  .dots i {
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: currentColor;
    animation: bounce 1.2s ease-in-out infinite;
  }

  .dots i:nth-child(2) {
    animation-delay: 0.15s;
  }

  .dots i:nth-child(3) {
    animation-delay: 0.3s;
  }

  @keyframes bounce {
    0%,
    60%,
    100% {
      opacity: 0.25;
      transform: translateY(0);
    }
    30% {
      opacity: 1;
      transform: translateY(-3px);
    }
  }

  .jump {
    position: absolute;
    left: 50%;
    bottom: 16px;
    transform: translateX(-50%);
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 7px 14px;
    border-radius: 999px;
    border: 1px solid var(--border-strong);
    background: var(--surface);
    color: var(--text);
    font-size: 13px;
    font-weight: 500;
    box-shadow: var(--shadow);
  }

  @media (max-width: 560px) {
    .column {
      padding: 20px 16px 32px;
    }

    .turn {
      grid-template-columns: 30px 1fr;
      gap: 10px;
    }

    .avatar {
      width: 30px;
      height: 30px;
      font-size: 12px;
    }

    .live {
      padding-left: 40px;
    }
  }
</style>
