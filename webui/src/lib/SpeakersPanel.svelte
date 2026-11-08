<script>
  import { workspace } from "./session.svelte.js";
  import { notice } from "./notice.svelte.js";

  const transcript = $derived(workspace.current);
  import { clock } from "./format.js";
  import Icon from "./Icon.svelte";

  const speakers = $derived(transcript.speakerIndex.list.toSorted((a, b) => b.talk - a.talk));
  const totalTalk = $derived(speakers.reduce((n, s) => n + s.talk, 0));
  const duration = $derived.by(() => {
    const ends = new Map();
    for (const s of transcript.segments) ends.set(s.recording, Math.max(ends.get(s.recording) ?? 0, s.end));
    return [...ends.values()].reduce((a, b) => a + b, 0);
  });

  let editing = $state(null); // speaker id being renamed
  let draft = $state("");

  function edit(sp) {
    if (sp.unknown) return;
    editing = sp.id;
    draft = sp.name;
  }

  function commit(sp) {
    if (editing !== sp.id) return;
    const name = draft.trim();
    if (name && name !== sp.name) transcript.rename(sp, name).catch((e) => notice.error(`Rename failed: ${e.message}`));
    editing = null;
  }

  function onkeydown(e, sp) {
    if (e.key === "Enter") commit(sp);
    if (e.key === "Escape") editing = null;
  }

  function focus(node) {
    node.focus();
    node.select();
  }
</script>

<aside class="panel">
  <section>
    <h3>Speakers <span class="count">{speakers.filter((s) => !s.unknown).length || ""}</span></h3>

    {#if speakers.length === 0}
      <p class="muted">Speakers appear here as they're detected.</p>
    {:else}
      <ul>
        {#each speakers as sp (sp.id)}
          {@const share = totalTalk ? sp.talk / totalTalk : 0}
          <li style="--c: var(--spk-{sp.unknown ? 'unknown' : sp.color})">
            <span class="swatch"></span>
            <div class="info">
              {#if editing === sp.id}
                <input
                  class="name-input"
                  bind:value={draft}
                  onblur={() => commit(sp)}
                  onkeydown={(e) => onkeydown(e, sp)}
                  use:focus
                  maxlength="40"
                  aria-label="Speaker name"
                />
              {:else}
                <button
                  class="name"
                  class:static={sp.unknown}
                  onclick={() => edit(sp)}
                  title={sp.unknown ? "Speech the diarizer couldn't attribute" : "Rename — give two speakers the same name to merge them"}
                >
                  <span>{sp.name}</span>
                  {#if !sp.unknown}<Icon name="pencil" size={12} />{/if}
                </button>
              {/if}
              <div class="bar"><span style="width: {share * 100}%"></span></div>
              <div class="stats">
                <span>{clock(sp.talk)}</span>
                <span>{Math.round(share * 100)}%</span>
                <span>{sp.words.toLocaleString()} words</span>
              </div>
            </div>
          </li>
        {/each}
      </ul>
    {/if}
  </section>

  <section>
    <h3>Transcript</h3>
    <dl>
      <div><dt>Duration</dt><dd>{clock(duration)}</dd></div>
      <div><dt>Words</dt><dd>{transcript.wordCount.toLocaleString()}</dd></div>
      <div><dt>Lines</dt><dd>{transcript.segments.length.toLocaleString()}</dd></div>
      <div><dt>Recordings</dt><dd>{transcript.recordings.length}</dd></div>
    </dl>
    <p class="muted small">Saved to your account. Speaker numbering restarts with each recording.</p>
  </section>
</aside>

<style>
  .panel {
    display: flex;
    flex-direction: column;
    gap: 28px;
    padding: 22px 18px;
    overflow-y: auto;
    background: var(--surface);
    border-left: 1px solid var(--border);
  }

  h3 {
    display: flex;
    align-items: center;
    gap: 8px;
    margin: 0 0 12px;
    font-size: 11.5px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.07em;
    color: var(--text-3);
  }

  .count {
    color: var(--text-2);
  }

  .muted {
    margin: 0;
    font-size: 13px;
    color: var(--text-3);
  }

  .muted.small {
    margin-top: 12px;
    font-size: 12px;
  }

  ul {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: 14px;
  }

  li {
    display: grid;
    grid-template-columns: 10px 1fr;
    gap: 10px;
  }

  .swatch {
    width: 10px;
    height: 10px;
    margin-top: 7px;
    border-radius: 3px;
    background: var(--c);
  }

  .info {
    min-width: 0;
  }

  .name,
  .name-input {
    display: flex;
    align-items: center;
    gap: 6px;
    width: 100%;
    height: 26px;
    padding: 0 6px;
    margin-left: -6px;
    border-radius: 6px;
    font-size: 14px;
    font-weight: 600;
    text-align: left;
  }

  .name {
    border: 1px solid transparent;
    background: none;
    color: var(--text);
  }

  .name span {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .name :global(svg) {
    flex: none;
    opacity: 0;
    color: var(--text-3);
    transition: opacity 0.15s;
  }

  .name:hover:not(.static) {
    background: var(--surface-2);
  }

  .name:hover :global(svg),
  .name:focus-visible :global(svg) {
    opacity: 1;
  }

  .name.static {
    cursor: default;
    color: var(--text-2);
  }

  .name-input {
    border: 1px solid var(--accent);
    background: var(--surface-2);
    outline: none;
  }

  .bar {
    height: 4px;
    margin: 6px 0 5px;
    border-radius: 2px;
    background: var(--surface-3);
    overflow: hidden;
  }

  .bar span {
    display: block;
    height: 100%;
    border-radius: 2px;
    background: var(--c);
    transition: width 0.4s ease;
  }

  .stats {
    display: flex;
    gap: 10px;
    font-size: 12px;
    color: var(--text-3);
    font-variant-numeric: tabular-nums;
  }

  .stats span:first-child {
    color: var(--text-2);
  }

  dl {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 8px;
    margin: 0;
  }

  dl div {
    padding: 10px 12px;
    border-radius: 8px;
    background: var(--surface-2);
  }

  dt {
    font-size: 11.5px;
    color: var(--text-3);
  }

  dd {
    margin: 2px 0 0;
    font-size: 16px;
    font-weight: 600;
    font-variant-numeric: tabular-nums;
  }
</style>
