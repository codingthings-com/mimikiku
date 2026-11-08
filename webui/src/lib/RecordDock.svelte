<script>
  import { onMount } from "svelte";
  import { recorder, workspace } from "./session.svelte.js";
  import { clock } from "./format.js";
  import Icon from "./Icon.svelte";

  onMount(() => recorder.refreshDevices());

  const status = $derived(
    {
      idle: {
        label: "Ready",
        hint: workspace.current.id ? "R to keep recording here · N for a new transcript" : "Press R or the button to start",
      },
      connecting: { label: "Connecting…", hint: "Opening microphone and server connection" },
      loading: { label: "Loading models…", hint: "First start after idle can take a little while" },
      listening: { label: "Listening", hint: "S to stop · Space to pause" },
      paused: { label: "Paused", hint: "Audio is not being sent · Space to resume" },
      finishing: { label: "Finishing…", hint: "Waiting for the last words" },
      error: { label: "Something went wrong", hint: recorder.error },
    }[recorder.phase],
  );

  const BARS = 14;
</script>

<div class="dock" data-phase={recorder.phase}>
  <div class="status">
    <span class="indicator"></span>
    <div class="status-text">
      <div class="label">
        {status.label}
        {#if recorder.phase === "listening" || recorder.phase === "paused"}
          <span class="timer">{clock(recorder.elapsed)}</span>
        {/if}
      </div>
      {#if status.hint}<div class="hint" title={status.hint}>{status.hint}</div>{/if}
    </div>
    {#if recorder.phase === "error"}
      <button class="btn icon" onclick={() => recorder.dismissError()} title="Dismiss" aria-label="Dismiss error">
        <Icon name="x" />
      </button>
    {/if}
  </div>

  <div class="controls">
    {#if recorder.capturing}
      <button
        class="round secondary"
        onclick={() => recorder.togglePause()}
        disabled={recorder.phase !== "listening" && recorder.phase !== "paused"}
        title={recorder.phase === "paused" ? "Resume (Space)" : "Pause (Space)"}
        aria-label={recorder.phase === "paused" ? "Resume" : "Pause"}
      >
        <Icon name={recorder.phase === "paused" ? "play" : "pause"} size={18} />
      </button>
    {/if}

    <button
      class="round primary"
      class:live={recorder.capturing}
      onclick={() => (recorder.capturing ? recorder.stop() : recorder.start())}
      disabled={recorder.phase === "finishing"}
      title={recorder.capturing ? "Stop (S)" : "Start recording (R)"}
      aria-label={recorder.capturing ? "Stop recording" : "Start recording"}
    >
      {#if recorder.phase === "finishing" || recorder.phase === "connecting"}
        <span class="spinner"></span>
      {:else}
        <Icon name={recorder.capturing ? "stop" : "mic"} size={22} />
      {/if}
    </button>
  </div>

  <div class="input">
    <div class="meter" aria-hidden="true">
      {#each { length: BARS } as _, i}
        <span class:on={recorder.level * BARS > i} class:hot={i >= BARS - 2}></span>
      {/each}
    </div>
    <label class="device">
      <span class="visually-hidden">Microphone</span>
      <Icon name="mic" size={14} />
      <select
        value={recorder.deviceId}
        onchange={(e) => recorder.setDevice(e.currentTarget.value)}
        disabled={recorder.active}
        title={recorder.active ? "Stop recording to change microphone" : "Microphone"}
      >
        <option value="">Default microphone</option>
        {#each recorder.devices.filter((d) => d.deviceId !== "default") as d (d.deviceId)}
          <option value={d.deviceId}>{d.label || "Microphone"}</option>
        {/each}
      </select>
      <Icon name="chevron" size={14} />
    </label>
  </div>
</div>

<style>
  .dock {
    display: grid;
    grid-template-columns: 1fr auto 1fr;
    align-items: center;
    gap: 16px;
    padding: 14px 20px;
    border-top: 1px solid var(--border);
    background: var(--surface);
  }

  /* ---- status ---- */
  .status {
    display: flex;
    align-items: center;
    gap: 12px;
    min-width: 0;
  }

  .indicator {
    flex: none;
    width: 10px;
    height: 10px;
    border-radius: 50%;
    background: var(--text-3);
  }

  [data-phase="listening"] .indicator {
    background: var(--rec);
    box-shadow: 0 0 0 0 color-mix(in oklab, var(--rec) 60%, transparent);
    animation: ping 1.6s ease-out infinite;
  }

  [data-phase="paused"] .indicator {
    background: var(--warn);
  }

  [data-phase="connecting"] .indicator,
  [data-phase="loading"] .indicator,
  [data-phase="finishing"] .indicator {
    background: var(--accent);
    animation: blink 1s ease-in-out infinite;
  }

  [data-phase="error"] .indicator {
    background: var(--danger);
  }

  @keyframes ping {
    70% {
      box-shadow: 0 0 0 8px transparent;
    }
    100% {
      box-shadow: 0 0 0 0 transparent;
    }
  }

  @keyframes blink {
    50% {
      opacity: 0.3;
    }
  }

  .status-text {
    min-width: 0;
  }

  .label {
    font-weight: 600;
    font-size: 14px;
    display: flex;
    gap: 10px;
    align-items: baseline;
  }

  [data-phase="error"] .label {
    color: var(--danger);
  }

  .timer {
    font-family: var(--mono);
    font-variant-numeric: tabular-nums;
    font-weight: 500;
    color: var(--text-2);
  }

  .hint {
    font-size: 12.5px;
    color: var(--text-3);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  [data-phase="error"] .hint {
    white-space: normal;
    color: var(--text-2);
  }

  /* ---- buttons ---- */
  .controls {
    display: flex;
    align-items: center;
    gap: 14px;
  }

  .round {
    display: grid;
    place-items: center;
    border-radius: 50%;
    border: 1px solid var(--border);
    transition: transform 0.12s, background 0.15s, box-shadow 0.2s;
  }

  .round:active:not(:disabled) {
    transform: scale(0.95);
  }

  .round:disabled {
    opacity: 0.5;
    cursor: default;
  }

  .secondary {
    width: 42px;
    height: 42px;
    background: var(--surface-2);
    color: var(--text);
  }

  .secondary:hover:not(:disabled) {
    background: var(--surface-3);
  }

  .primary {
    width: 58px;
    height: 58px;
    border: none;
    background: var(--rec);
    color: #fff;
    box-shadow: 0 4px 16px color-mix(in oklab, var(--rec) 35%, transparent);
  }

  .primary:hover:not(:disabled) {
    background: color-mix(in oklab, var(--rec) 88%, #fff);
  }

  .primary.live {
    background: var(--surface-3);
    color: var(--rec);
    box-shadow: 0 0 0 3px color-mix(in oklab, var(--rec) 45%, transparent);
  }

  .spinner {
    width: 20px;
    height: 20px;
    border-radius: 50%;
    border: 2.5px solid color-mix(in oklab, currentColor 25%, transparent);
    border-top-color: currentColor;
    animation: spin 0.8s linear infinite;
  }

  @keyframes spin {
    to {
      transform: rotate(360deg);
    }
  }

  /* ---- input ---- */
  .input {
    display: flex;
    align-items: center;
    justify-content: flex-end;
    gap: 14px;
    min-width: 0;
  }

  .meter {
    display: flex;
    align-items: flex-end;
    gap: 2px;
    height: 18px;
  }

  .meter span {
    width: 3px;
    height: 100%;
    border-radius: 2px;
    background: var(--surface-3);
    transition: background 0.06s;
  }

  .meter span.on {
    background: var(--ok);
  }

  .meter span.on.hot {
    background: var(--warn);
  }

  .device {
    position: relative;
    display: flex;
    align-items: center;
    gap: 6px;
    height: 32px;
    padding: 0 8px;
    border: 1px solid var(--border);
    border-radius: 8px;
    color: var(--text-2);
    min-width: 0;
    max-width: 220px;
  }

  .device select {
    appearance: none;
    border: none;
    background: transparent;
    font-size: 12.5px;
    min-width: 0;
    flex: 1;
    text-overflow: ellipsis;
    cursor: pointer;
    outline: none;
  }

  .device select:disabled {
    cursor: default;
  }

  .device select option {
    background: var(--surface);
  }

  .device:focus-within {
    border-color: var(--accent);
  }

  @media (max-width: 720px) {
    .dock {
      grid-template-columns: 1fr auto;
      padding: 12px 16px;
    }

    .input {
      display: none;
    }
  }
</style>
