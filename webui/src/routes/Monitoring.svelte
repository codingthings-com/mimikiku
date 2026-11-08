<svelte:options runes={true} />

<script>
  import { onMount } from "svelte";
  import { monitor, INTERVALS } from "../lib/monitor.svelte.js";
  import Icon from "../lib/Icon.svelte";

  // Re-renders "x s ago" / live durations between polls.
  let now = $state(Date.now());
  let confirmCleanup = $state(false);
  let cleanupBusy = $state(false);
  let cleanupResult = $state(null);

  onMount(() => {
    monitor.start();
    const tick = setInterval(() => (now = Date.now()), 1000);
    return () => {
      monitor.stop();
      clearInterval(tick);
    };
  });

  const status = $derived(monitor.data.status);
  const metrics = $derived(monitor.data.metrics);
  const pipelines = $derived(monitor.data.pipelines);
  const sessions = $derived(monitor.data.sessions);

  const models = $derived(Object.values(status?.models ?? {}));
  const connections = $derived(
    Object.entries(pipelines?.connections ?? {}).map(([id, c]) => ({
      id,
      ...c,
      session: sessions?.sessions?.[id] ?? null,
      queued: status?.queues?.[id] ?? 0,
    })),
  );
  const errorBreakdown = $derived(Object.entries(metrics?.error_breakdown ?? {}));
  const failedPanels = $derived(Object.entries(monitor.errors).filter(([, e]) => e));
  const sys = $derived(metrics?.system_metrics);
  const res = $derived(metrics?.resources);

  function duration(seconds) {
    if (seconds == null) return "—";
    const s = Math.max(0, Math.floor(seconds));
    const d = Math.floor(s / 86400);
    const h = Math.floor((s % 86400) / 3600);
    const m = Math.floor((s % 3600) / 60);
    if (d) return `${d}d ${h}h`;
    if (h) return `${h}h ${m}m`;
    if (m) return `${m}m ${s % 60}s`;
    return `${s}s`;
  }

  function mb(v) {
    if (v == null) return "—";
    return v >= 1024 ? `${(v / 1024).toFixed(1)} GB` : `${Math.round(v)} MB`;
  }

  function pct(v, digits = 0) {
    return v == null ? "—" : `${(v * 100).toFixed(digits)}%`;
  }

  function ago(iso) {
    if (!iso) return "—";
    return `${duration((now - new Date(iso).getTime()) / 1000)} ago`;
  }

  function shortId(id) {
    return id.length > 12 ? `${id.slice(0, 8)}…` : id;
  }

  const modelLabel = (m) => (m.model_type ?? m.model_id).toString().replace(/_/g, " ");

  function rateTone(r) {
    return r >= 0.95 ? "ok" : r >= 0.8 ? "warn" : "bad";
  }

  function pressureTone(p) {
    return p === "normal" ? "ok" : p === "warning" ? "warn" : "bad";
  }

  const stale = $derived(monitor.updatedAt && now - monitor.updatedAt > Math.max(monitor.interval * 3000, 15000));

  async function runCleanup() {
    cleanupBusy = true;
    cleanupResult = null;
    try {
      const r = await monitor.cleanup();
      cleanupResult = {
        ok: true,
        text:
          r.cleaned_count > 0
            ? `Unloaded ${r.cleaned_count} model(s): ${r.cleaned_models.join(", ")}`
            : r.protected_models?.length
              ? `Nothing unloaded — ${r.protected_models.length} model(s) protected by active sessions`
              : "Nothing to unload — models are in use or within their TTL",
      };
    } catch (e) {
      cleanupResult = { ok: false, text: `Cleanup failed: ${e.message}` };
    } finally {
      cleanupBusy = false;
      confirmCleanup = false;
      monitor.refresh();
    }
  }
</script>

<div class="page">
  <header class="head">
    <div>
      <h1>Monitoring</h1>
      <p class="sub">
        {#if monitor.updatedAt}
          Updated {duration((now - monitor.updatedAt) / 1000)} ago
          {#if stale}<span class="chip warn">stale</span>{/if}
        {:else}
          Loading…
        {/if}
      </p>
    </div>

    <div class="controls">
      <label class="check">
        <input
          type="checkbox"
          checked={monitor.auto}
          onchange={(e) => monitor.setAuto(e.currentTarget.checked)}
        />
        Auto-refresh
      </label>
      <select
        aria-label="Refresh interval"
        value={monitor.interval}
        disabled={!monitor.auto}
        onchange={(e) => monitor.setInterval(Number(e.currentTarget.value))}
      >
        {#each INTERVALS as s}
          <option value={s}>every {s}s</option>
        {/each}
      </select>
      <button class="btn" class:busy={monitor.loading} onclick={() => monitor.refresh(true)} aria-busy={monitor.loading}>
        Refresh
      </button>
    </div>
  </header>

  {#if failedPanels.length}
    <div class="banner" role="alert">
      <Icon name="alert" size={16} />
      <span>
        {#each failedPanels as [key, err], i}
          {i ? " · " : ""}<strong>{key}</strong>: {err}
        {/each}
      </span>
    </div>
  {/if}

{#if monitor.loaded}
  <section class="tiles">
    <div class="tile">
      <span class="label">Models loaded</span>
      <span class="value">{models.filter((m) => m.is_loaded).length}<small>/ {models.length}</small></span>
    </div>
    <div class="tile">
      <span class="label">Connections</span>
      <span class="value">{status?.connections ?? "—"}</span>
    </div>
    <div class="tile">
      <span class="label">Active pipelines</span>
      <span class="value">{pipelines?.active_pipelines ?? "—"}</span>
    </div>
    <div class="tile">
      <span class="label">Chunk success</span>
      <span class="value {pipelines && pipelines.total_chunks_processed ? rateTone(pipelines.average_success_rate) : ''}">
        {pipelines && pipelines.total_chunks_processed ? pct(pipelines.average_success_rate, 1) : "—"}
      </span>
    </div>
    <div class="tile">
      <span class="label">Errors</span>
      <span class="value {sys?.total_errors ? 'bad' : ''}">{sys?.total_errors ?? "—"}</span>
    </div>
    <div class="tile">
      <span class="label">Server uptime</span>
      <span class="value">{duration(metrics?.uptime_seconds)}</span>
    </div>
  </section>

  <section class="card">
    <h2>Models</h2>
    {#if models.length}
      <div class="scroll">
        <table>
          <thead>
            <tr>
              <th>Model</th>
              <th>State</th>
              <th class="num">Reserved</th>
              <th class="num">Load time</th>
              <th class="num">Accesses</th>
              <th class="num">Idle / TTL</th>
            </tr>
          </thead>
          <tbody>
            {#each models as m (m.model_id)}
              <tr>
                <td>
                  <div class="strong">{modelLabel(m)}</div>
                  <div class="mono dim">{m.model_id}</div>
                </td>
                <td>
                  <span class="chip {m.is_loaded ? 'ok' : ''}">{m.is_loaded ? "Loaded" : "Unloaded"}</span>
                  {#if m.is_protected}<span class="chip info" title="Active session — won't be unloaded">Protected</span>{/if}
                  {#if m.can_cleanup}<span class="chip warn">Past TTL</span>{/if}
                </td>
                <td class="num" title="Configured memory budget, not a live measurement">{mb(m.memory_usage_mb)}</td>
                <td class="num">{m.load_time_ms ? `${(m.load_time_ms / 1000).toFixed(1)}s` : "—"}</td>
                <td class="num">{m.access_count ?? "—"}</td>
                <td class="num">
                  {m.idle_seconds == null ? "—" : duration(m.idle_seconds + (now - monitor.updatedAt) / 1000)}
                  / {duration(m.ttl_seconds)}
                </td>
              </tr>
            {/each}
          </tbody>
        </table>
      </div>
    {:else}
      <p class="empty">{monitor.errors.status ? "Unavailable" : "No models loaded — they load on the first connection and unload after the idle TTL."}</p>
    {/if}
  </section>

  <section class="card">
    <h2>Connections</h2>
    {#if connections.length}
      <div class="scroll">
        <table>
          <thead>
            <tr>
              <th>Connection</th>
              <th>User</th>
              <th>Pipeline</th>
              <th>Session</th>
              <th class="num">Chunks</th>
              <th class="num">Success</th>
              <th class="num">Avg time</th>
              <th class="num">Queued</th>
              <th class="num">Connected</th>
              <th class="num">Last activity</th>
            </tr>
          </thead>
          <tbody>
            {#each connections as c (c.id)}
              <tr>
                <td class="mono" title={c.id}>{shortId(c.id)}</td>
                <td>{c.user ?? "—"}</td>
                <td>
                  <span class="chip {c.state === 'active' ? 'ok' : c.state === 'error' ? 'bad' : ''}">{c.state}</span>
                  {#if c.error_handling?.is_degraded}<span class="chip warn">Degraded</span>{/if}
                </td>
                <td>
                  {#if c.session}
                    <span class="chip {c.session.state === 'active' ? 'ok' : ''}">
                      {c.session.state}{c.session.in_grace_period ? " · grace" : ""}
                    </span>
                  {:else}—{/if}
                </td>
                <td class="num">
                  {c.total_chunks.toLocaleString()}
                  {#if c.failed_chunks}<span class="bad">({c.failed_chunks} failed)</span>{/if}
                </td>
                <td class="num {c.total_chunks ? rateTone(c.success_rate) : ''}">{c.total_chunks ? pct(c.success_rate, 1) : "—"}</td>
                <td class="num">{c.successful_chunks ? `${Math.round(c.average_processing_time_ms)} ms` : "—"}</td>
                <td class="num">{c.queued}</td>
                <td class="num">{duration(c.uptime_seconds + (now - monitor.updatedAt) / 1000)}</td>
                <td class="num">{ago(c.last_activity)}</td>
              </tr>
              {#if c.error_count && c.last_error}
                <tr class="sub-row">
                  <td></td>
                  <td colspan="9" class="bad mono">{c.error_count} error(s), last: {c.last_error}</td>
                </tr>
              {/if}
            {/each}
          </tbody>
        </table>
      </div>
      {#if sessions}
        <p class="foot">
          {sessions.active_sessions} active · {sessions.grace_period_sessions} in grace period ·
          {sessions.disconnected_sessions} disconnected · {sessions.protected_models} model(s) protected ·
          session protection {sessions.protection_enabled ? "on" : "off"}
        </p>
      {/if}
    {:else}
      <p class="empty">{monitor.errors.pipelines ? "Unavailable" : "No connections."}</p>
    {/if}
  </section>

  <div class="two">
    <section class="card">
      <h2>Host resources</h2>
      {#if res}
        <div class="meter-row">
          <span>RAM</span>
          <div class="meter"><i class={pressureTone(res.pressure)} style="width:{Math.min(100, res.ram_percent)}%"></i></div>
          <span class="num">{mb(res.ram_used_mb)} / {mb(res.ram_total_mb)}</span>
        </div>
        {#if res.gpu_total_mb}
          <div class="meter-row">
            <span>GPU</span>
            <div class="meter"><i class={pressureTone(res.pressure)} style="width:{Math.min(100, res.gpu_percent)}%"></i></div>
            <span class="num">{mb(res.gpu_used_mb)} / {mb(res.gpu_total_mb)}</span>
          </div>
        {:else}
          <p class="foot">No GPU memory reading available to the server process.</p>
        {/if}
        <p class="foot">Pressure: <span class="chip {pressureTone(res.pressure)}">{res.pressure}</span></p>
      {:else}
        <p class="empty">{monitor.errors.metrics ? "Unavailable" : "—"}</p>
      {/if}
    </section>

    <section class="card">
      <h2>Model lifecycle</h2>
      {#if sys}
        <dl>
          <dt>Cache hit rate</dt>
          <dd>{sys.cache_hit_rate == null ? "—" : `${sys.cache_hit_rate.toFixed(1)}%`}</dd>
          <dt>Models created</dt>
          <dd>{sys.total_models_created}</dd>
          <dt>Avg load time</dt>
          <dd>{metrics.performance_metrics.average_load_time_ms ? `${(metrics.performance_metrics.average_load_time_ms / 1000).toFixed(1)}s` : "—"}</dd>
          <dt>Cleanup runs</dt>
          <dd>{sys.cleanup_runs} <span class="dim">({sys.models_cleaned_up} models unloaded)</span></dd>
        </dl>
        {#if errorBreakdown.length}
          <h3>Errors by type</h3>
          <dl>
            {#each errorBreakdown as [type, n]}
              <dt>{type}</dt>
              <dd>{n}</dd>
            {/each}
          </dl>
        {/if}
      {:else}
        <p class="empty">{monitor.errors.metrics ? "Unavailable" : "—"}</p>
      {/if}
    </section>
  </div>

  <section class="card">
    <h2>Maintenance</h2>
    <p class="foot top">
      Unload models that are past their idle TTL and not protected by an active session. They reload on the next connection.
    </p>
    <div class="actions">
      {#if confirmCleanup}
        <button class="btn danger" onclick={runCleanup} disabled={cleanupBusy}>
          {cleanupBusy ? "Cleaning up…" : "Confirm cleanup"}
        </button>
        <button class="btn" onclick={() => (confirmCleanup = false)} disabled={cleanupBusy}>Cancel</button>
      {:else}
        <button class="btn" onclick={() => ((confirmCleanup = true), (cleanupResult = null))}>Clean up idle models</button>
      {/if}
      {#if cleanupResult}
        <span class={cleanupResult.ok ? "dim" : "bad"} role="status">{cleanupResult.text}</span>
      {/if}
    </div>
  </section>
{/if}
</div>

<style>
  .page {
    width: 100%;
    max-width: 1100px;
    margin: 0 auto;
    padding: 24px 16px 48px;
    display: flex;
    flex-direction: column;
    gap: 16px;
  }

  .head {
    display: flex;
    flex-wrap: wrap;
    gap: 12px 24px;
    align-items: flex-end;
    justify-content: space-between;
  }

  h1 {
    margin: 0;
    font-size: 22px;
    letter-spacing: -0.01em;
  }

  .sub {
    margin: 2px 0 0;
    color: var(--text-3);
    font-size: 13px;
    font-variant-numeric: tabular-nums;
  }

  .controls {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 10px;
  }

  .check {
    display: inline-flex;
    align-items: center;
    gap: 7px;
    font-size: 13.5px;
    color: var(--text-2);
    cursor: pointer;
  }

  select,
  .btn {
    font: inherit;
    font-size: 13px;
    color: var(--text);
    background: var(--surface-2);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 6px 12px;
  }

  select:disabled {
    opacity: 0.5;
  }

  .btn {
    cursor: pointer;
  }

  .btn:hover:not(:disabled) {
    background: var(--surface-3);
  }

  .btn.busy {
    opacity: 0.6;
  }

  .btn:disabled {
    opacity: 0.6;
    cursor: default;
  }

  .btn.danger {
    color: var(--danger);
    border-color: color-mix(in oklab, var(--danger) 50%, var(--border));
  }

  .banner {
    display: flex;
    gap: 10px;
    align-items: flex-start;
    padding: 10px 14px;
    border-radius: var(--radius);
    font-size: 13px;
    color: var(--danger);
    background: color-mix(in oklab, var(--danger) 12%, transparent);
  }

  .tiles {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
    gap: 12px;
  }

  .tile,
  .card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: var(--radius);
  }

  .tile {
    padding: 12px 14px;
    display: flex;
    flex-direction: column;
    gap: 4px;
  }

  .label {
    font-size: 12px;
    color: var(--text-3);
  }

  .value {
    font-size: 24px;
    font-weight: 650;
    letter-spacing: -0.02em;
    font-variant-numeric: tabular-nums;
  }

  .value small {
    font-size: 13px;
    font-weight: 500;
    color: var(--text-3);
    margin-left: 4px;
  }

  .card {
    padding: 14px 16px;
  }

  h2 {
    margin: 0 0 10px;
    font-size: 13px;
    font-weight: 600;
    color: var(--text-2);
    text-transform: uppercase;
    letter-spacing: 0.04em;
  }

  h3 {
    margin: 14px 0 6px;
    font-size: 12px;
    color: var(--text-3);
  }

  .scroll {
    overflow-x: auto;
  }

  table {
    width: 100%;
    border-collapse: collapse;
    font-size: 13.5px;
  }

  th {
    text-align: left;
    font-weight: 500;
    font-size: 12px;
    color: var(--text-3);
    padding: 0 10px 8px 0;
    white-space: nowrap;
  }

  td {
    padding: 8px 10px 8px 0;
    border-top: 1px solid var(--border);
    vertical-align: top;
  }

  .sub-row td {
    border-top: 0;
    padding-top: 0;
    font-size: 12px;
  }

  .num {
    text-align: right;
    font-variant-numeric: tabular-nums;
    white-space: nowrap;
  }

  .mono {
    font-family: var(--mono);
    font-size: 12px;
  }

  .strong {
    font-weight: 600;
    text-transform: capitalize;
  }

  .dim {
    color: var(--text-3);
  }

  .ok {
    color: var(--ok);
  }
  .warn {
    color: var(--warn);
  }
  .bad {
    color: var(--danger);
  }

  .chip {
    display: inline-block;
    padding: 1px 8px;
    margin-right: 4px;
    border-radius: 999px;
    font-size: 12px;
    font-weight: 500;
    color: var(--text-2);
    background: var(--surface-3);
  }

  .chip.ok,
  .chip.warn,
  .chip.bad {
    background: color-mix(in oklab, currentColor 15%, transparent);
  }

  .chip.info {
    color: var(--accent);
    background: color-mix(in oklab, var(--accent) 15%, transparent);
  }

  .empty,
  .foot {
    margin: 0;
    font-size: 13px;
    color: var(--text-3);
  }

  .foot {
    margin-top: 10px;
  }

  .foot.top {
    margin: 0 0 12px;
  }

  .two {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
    gap: 16px;
  }

  .meter-row {
    display: grid;
    grid-template-columns: 36px 1fr auto;
    align-items: center;
    gap: 10px;
    font-size: 13px;
    margin-bottom: 8px;
  }

  .meter {
    height: 8px;
    border-radius: 4px;
    background: var(--surface-3);
    overflow: hidden;
  }

  .meter i {
    display: block;
    height: 100%;
    background: currentColor;
    transition: width 0.4s;
  }

  dl {
    display: grid;
    grid-template-columns: 1fr auto;
    gap: 6px 16px;
    margin: 0;
    font-size: 13.5px;
  }

  dt {
    color: var(--text-2);
  }

  dd {
    margin: 0;
    text-align: right;
    font-variant-numeric: tabular-nums;
  }

  .actions {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 10px;
    font-size: 13px;
  }
</style>
