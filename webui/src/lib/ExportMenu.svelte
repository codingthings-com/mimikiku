<script>
  import { workspace } from "./session.svelte.js";
  import { toText, toMarkdown, toSrt, toVtt, toJson, download } from "./format.js";
  import Icon from "./Icon.svelte";

  let { showTimes = true } = $props();

  const transcript = $derived(workspace.current);
  const audioFiles = $derived(transcript.recordings.filter((r) => r.audio));

  let open = $state(false);
  let copied = $state(false);
  let root;

  function basename() {
    const at = new Date(transcript.recordings[0]?.startedAt ?? Date.now());
    const pad = (n) => String(n).padStart(2, "0");
    const slug = transcript.title.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 40);
    return `${slug || "transcript"}-${at.getFullYear()}-${pad(at.getMonth() + 1)}-${pad(at.getDate())}-${pad(at.getHours())}${pad(at.getMinutes())}`;
  }

  const formats = [
    { ext: "txt", label: "Plain text", type: "text/plain", make: () => toText(transcript.turns, { timestamps: showTimes }) },
    { ext: "md", label: "Markdown", type: "text/markdown", make: () => toMarkdown(transcript.turns, transcript.recordings, transcript.displayTitle) },
    { ext: "srt", label: "Subtitles (SRT)", type: "application/x-subrip", make: () => toSrt(transcript.segments, transcript.speakerOf) },
    { ext: "vtt", label: "WebVTT", type: "text/vtt", make: () => toVtt(transcript.segments, transcript.speakerOf) },
    {
      ext: "json",
      label: "JSON",
      type: "application/json",
      make: () => toJson(transcript.segments, transcript.speakerOf, transcript.recordings, transcript.title),
    },
  ];

  async function copy() {
    try {
      await navigator.clipboard.writeText(toText(transcript.turns, { timestamps: showTimes }));
      copied = true;
      setTimeout(() => (copied = false), 1600);
    } catch (e) {
      console.error("Copy failed:", e);
    }
  }

  function save(f) {
    download(`${basename()}.${f.ext}`, f.make(), f.type);
    open = false;
  }

  function onwindowclick(e) {
    if (open && !root.contains(e.target)) open = false;
  }
</script>

<svelte:window onclick={onwindowclick} onkeydown={(e) => e.key === "Escape" && (open = false)} />

<div class="group" bind:this={root}>
  <button class="btn" onclick={copy} disabled={transcript.isEmpty} title="Copy transcript">
    <Icon name={copied ? "check" : "copy"} />
    <span class="label">{copied ? "Copied" : "Copy"}</span>
  </button>

  <div class="menu-wrap">
    <button
      class="btn"
      class:active={open}
      onclick={() => (open = !open)}
      disabled={transcript.isEmpty}
      aria-haspopup="menu"
      aria-expanded={open}
      title="Download transcript"
    >
      <Icon name="download" />
      <span class="label">Export</span>
    </button>
    {#if open}
      <div class="menu" role="menu">
        {#each formats as f}
          <button role="menuitem" onclick={() => save(f)}>
            <span>{f.label}</span>
            <span class="ext">.{f.ext}</span>
          </button>
        {/each}
        {#each audioFiles as rec, i (rec.id)}
          <a
            role="menuitem"
            href="/api/transcripts/{transcript.id}/recordings/{rec.id}/audio"
            download
            onclick={() => (open = false)}
          >
            <span>Audio{audioFiles.length > 1 ? ` · recording ${i + 1}` : ""}</span>
            <span class="ext">.wav</span>
          </a>
        {/each}
      </div>
    {/if}
  </div>
</div>

<style>
  .group {
    display: flex;
    gap: 6px;
  }

  .menu-wrap {
    position: relative;
  }

  .menu {
    position: absolute;
    right: 0;
    top: calc(100% + 6px);
    z-index: 20;
    min-width: 200px;
    padding: 5px;
    border-radius: var(--radius);
    border: 1px solid var(--border-strong);
    background: var(--surface);
    box-shadow: var(--shadow);
  }

  .menu button,
  .menu a {
    display: flex;
    justify-content: space-between;
    gap: 16px;
    width: 100%;
    padding: 8px 10px;
    border: none;
    border-radius: 6px;
    background: none;
    font-size: 13.5px;
    text-align: left;
  }

  .menu a {
    color: var(--text);
  }

  .menu button:hover,
  .menu a:hover {
    background: var(--surface-2);
  }

  .ext {
    font-family: var(--mono);
    font-size: 12px;
    color: var(--text-3);
  }

  @media (max-width: 720px) {
    .label {
      display: none;
    }
  }
</style>
