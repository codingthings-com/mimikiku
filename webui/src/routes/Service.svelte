<script>
  // Admin: transcription and diarization tuning (server/service_settings.py).
  import { onMount } from "svelte";
  import { get, api } from "../lib/api.js";
  import { notice } from "../lib/notice.svelte.js";
  import Icon from "../lib/Icon.svelte";

  let state = $state(null); // {fields, values, defaults, version, instances}
  let draft = $state({});
  let loadError = $state("");
  let saving = $state(false);

  onMount(load);

  async function load() {
    try {
      state = await get("/api/admin/settings");
      draft = { ...state.values };
      loadError = "";
    } catch (e) {
      loadError = e.message;
    }
  }

  const groups = $derived.by(() => {
    const out = new Map();
    for (const f of state?.fields ?? []) {
      if (!out.has(f.group)) out.set(f.group, []);
      out.get(f.group).push(f);
    }
    return [...out];
  });

  const dirty = $derived(state && Object.keys(draft).some((k) => draft[k] !== state.values[k]));
  const pendingRestart = $derived((state?.instances ?? []).filter((i) => i.pendingRestart.length));

  async function save() {
    saving = true;
    try {
      const res = await api("/api/admin/settings", { method: "PUT", body: { version: state.version, values: draft } });
      state = res;
      draft = { ...res.values };
      const parts = [];
      if (res.changed.next_recording.length) parts.push("new recordings use the line and speaker changes right away");
      if (res.changed.restart.length) parts.push(`restart the server to apply: ${res.changed.restart.join(", ")}`);
      notice.show(parts.length ? `Saved — ${parts.join("; ")}.` : "Saved.", "info", 8000);
    } catch (e) {
      notice.error(e.message);
      if (e.status === 409) await load();
    } finally {
      saving = false;
    }
  }

  function number(f, e) {
    const v = e.currentTarget.valueAsNumber;
    if (!Number.isNaN(v)) draft[f.key] = v;
  }

  const fmt = (f, v) => (f.choices.length ? f.choices.find((c) => c.value === v)?.label ?? v : `${v}${f.unit ? ` ${f.unit}` : ""}`);
  const ago = (iso) => {
    const s = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000));
    return s < 90 ? `${s}s ago` : s < 5400 ? `${Math.round(s / 60)} min ago` : new Date(iso).toLocaleString();
  };
</script>

<div class="page-narrow">
  <h1 class="page-title">Service</h1>
  <p class="page-sub">
    Tune transcription and speaker detection for everyone. Settings are shared by all Mimikiku servers.
  </p>

  {#if loadError}
    <p class="form-error">{loadError}</p>
  {:else if state}
    {#if pendingRestart.length}
      <div class="card warn-card">
        <h2><Icon name="alert" size={15} /> Restart needed</h2>
        <p class="sub">
          Model settings were changed after {pendingRestart.length === 1 ? "this server" : "these servers"} started. They
          keep the old values until restarted (for Docker: <code>./docker-start.sh</code> or
          <code>docker restart mimikiku-server</code>). Restarting cuts off recordings in progress — check the active count
          first.
        </p>
      </div>
    {/if}

    <form
      onsubmit={(e) => {
        e.preventDefault();
        save();
      }}
    >
      {#each groups as [group, fields]}
        <section class="card">
          <div class="group-head">
            <h2>{group}</h2>
            {#if fields.every((f) => f.apply === "restart")}
              <span class="chip bad">Applies after a restart</span>
            {:else}
              <span class="chip accent">Applies to new recordings</span>
            {/if}
          </div>
          {#each fields as f (f.key)}
            <div class="setting">
              <div class="text">
                <label for="s-{f.key}">{f.label}</label>
                <p>{f.help}</p>
                {#if draft[f.key] !== state.defaults[f.key]}
                  <button type="button" class="reset" onclick={() => (draft[f.key] = state.defaults[f.key])}>
                    Reset to default ({fmt(f, state.defaults[f.key])})
                  </button>
                {/if}
              </div>
              <div class="control">
                {#if f.choices.length}
                  <select id="s-{f.key}" class="input" bind:value={draft[f.key]}>
                    {#each f.choices as c}<option value={c.value}>{c.label}</option>{/each}
                  </select>
                {:else}
                  <div class="num">
                    <input
                      id="s-{f.key}"
                      class="input"
                      type="number"
                      min={f.min}
                      max={f.max}
                      step={f.step ?? "any"}
                      value={draft[f.key]}
                      oninput={(e) => number(f, e)}
                    />
                    {#if f.unit}<span>{f.unit}</span>{/if}
                  </div>
                  <div class="range">{f.min}–{f.max}{f.unit ? ` ${f.unit}` : ""}</div>
                {/if}
              </div>
            </div>
          {/each}
        </section>
      {/each}

      <div class="savebar">
        <span class="muted">{dirty ? "Unsaved changes" : "All changes saved"}</span>
        <button type="button" class="btn" disabled={!dirty || saving} onclick={() => (draft = { ...state.values })}>Discard</button>
        <button class="btn primary" disabled={!dirty || saving}>{saving ? "Saving…" : "Save"}</button>
      </div>
    </form>

    <section class="card">
      <h2>Servers</h2>
      <p class="sub">
        Servers currently running. Each one shows whether it's using the latest settings or needs a restart. A server
        that stops responding disappears from this list within about 2 minutes.
      </p>
      {#if !state.instances.length}
        <p class="muted">No servers have checked in recently.</p>
      {/if}
      <ul class="instances">
        {#each state.instances as i}
          <li>
            <div>
              <div class="iname">{i.name}</div>
              <div class="meta">
                started {ago(i.startedAt)} · {i.activeRecordings}
                {i.activeRecordings === 1 ? "recording" : "recordings"} in progress
              </div>
            </div>
            {#if i.pendingRestart.length}
              <span class="chip bad" title={i.pendingRestart.join(", ")}>Restart needed: {i.pendingRestart.join(", ")}</span>
            {:else}
              <span class="chip ok">Up to date</span>
            {/if}
          </li>
        {/each}
      </ul>
      <button class="btn" onclick={load}>Refresh</button>
    </section>
  {/if}
</div>

<style>
  code {
    font-family: var(--mono);
    font-size: 12.5px;
  }

  .warn-card {
    margin-bottom: 20px;
    border-color: color-mix(in oklab, var(--warn) 50%, var(--border));
  }

  .warn-card .sub {
    margin-bottom: 0;
  }

  .warn-card h2 {
    display: flex;
    align-items: center;
    gap: 8px;
    color: var(--warn);
  }

  .group-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    margin-bottom: 4px;
  }

  .setting {
    display: flex;
    gap: 24px;
    padding: 16px 0;
    border-top: 1px solid var(--border);
  }

  .group-head + .setting {
    border-top: none;
  }

  .text {
    flex: 1;
    min-width: 0;
  }

  .text label {
    font-size: 14px;
    font-weight: 600;
  }

  .text p {
    margin: 3px 0 0;
    font-size: 13px;
    color: var(--text-2);
  }

  .reset {
    margin-top: 6px;
    padding: 0;
    border: none;
    background: none;
    color: var(--accent);
    font-size: 12.5px;
  }

  .control {
    flex: 0 0 220px;
  }

  .control select {
    width: 100%;
  }

  .num {
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 13px;
    color: var(--text-2);
  }

  .num .input {
    width: 110px;
  }

  .range {
    margin-top: 4px;
    font-size: 12px;
    color: var(--text-3);
  }

  .savebar {
    position: sticky;
    bottom: 0;
    z-index: 5;
    display: flex;
    align-items: center;
    justify-content: flex-end;
    gap: 8px;
    margin: 20px 0;
    padding: 12px 0;
    background: var(--bg);
    border-top: 1px solid var(--border);
  }

  .savebar .muted {
    margin-right: auto;
    font-size: 13px;
  }

  .chip.ok {
    background: color-mix(in oklab, var(--ok) 16%, transparent);
    color: var(--ok);
  }

  .instances {
    margin: 0 0 14px;
    padding: 0;
    list-style: none;
  }

  .instances li {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: space-between;
    gap: 8px 16px;
    padding: 10px 0;
    border-top: 1px solid var(--border);
  }

  .iname {
    font-weight: 600;
    font-size: 14px;
  }

  .meta {
    font-size: 12.5px;
    color: var(--text-3);
  }

  @media (max-width: 640px) {
    .setting {
      flex-direction: column;
      gap: 10px;
    }

    .control {
      flex-basis: auto;
    }

    .page-narrow {
      padding: 20px 16px 40px;
    }
  }
</style>
