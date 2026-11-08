// App state: the transcript on screen (saved server-side) and the live recorder.
// Module-level so recording survives navigating to other routes.

import { get as storeValue } from "svelte/store";
import { location, push, replace } from "svelte-spa-router";
import { groupTurns } from "./format.js";
import { get, patch, del } from "./api.js";
import { auth } from "./auth.svelte.js";

const DEVICE_KEY = "mimikiku.device";
const SAMPLE_RATE = 16000;
const CHUNK = 2048; // samples per WS message, matching mimikiku-capture
export const PALETTE_SIZE = 8;

function readStorage(key) {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function writeStorage(key, value) {
  try {
    localStorage.setItem(key, value);
  } catch {
    /* private mode / quota: the device choice just won't stick */
  }
}

// ---------------------------------------------------------------------------
// Transcript
// ---------------------------------------------------------------------------

/**
 * One saved transcript. Segments come from the server as
 * {id, recording, speaker, text, start, end}, with times relative to the start
 * of their recording (one recording per WS connection). Speaker ids are only
 * meaningful within one recording (the diarizer restarts per connection), so a
 * speaker is identified by "recording:speaker" and can be renamed; speakers
 * given the same name are merged.
 *
 * A transcript without an id is new: the server creates it when the first
 * words of a recording arrive.
 */
export class Transcript {
  id = $state(null);
  title = $state("");
  createdAt = $state(null);
  segments = $state([]);
  allRecordings = $state([]); // [{id, startedAt, audio}]
  names = $state({}); // "recording:speaker" -> custom name
  status = $state("ready"); // ready | loading | missing | error
  error = $state("");

  constructor(id = null) {
    this.id = id;
    if (id) this.load();
  }

  async load() {
    this.status = "loading";
    try {
      const doc = await get(`/api/transcripts/${encodeURIComponent(this.id)}`);
      this.title = doc.title;
      this.createdAt = doc.createdAt;
      this.segments = doc.segments;
      this.allRecordings = doc.recordings;
      this.names = doc.names;
      this.status = "ready";
    } catch (e) {
      this.status = e.status === 404 ? "missing" : "error";
      this.error = e.message;
    }
  }

  get displayTitle() {
    if (this.title) return this.title;
    const at = this.allRecordings[0]?.startedAt ?? this.createdAt;
    return at ? new Date(at).toLocaleString([], { dateStyle: "medium", timeStyle: "short" }) : "New transcript";
  }

  /** Recordings that produced at least one segment, in order. */
  recordings = $derived.by(() => {
    const used = new Set(this.segments.map((s) => s.recording));
    return this.allRecordings.filter((r) => used.has(r.id));
  });

  /** key -> resolved speaker, and the merged speaker list with stats. */
  speakerIndex = $derived.by(() => {
    const recordingIdx = new Map(this.recordings.map((r, i) => [r.id, i]));
    const byKey = new Map();
    const byId = new Map();
    let colorSeq = 0;

    for (const seg of this.segments) {
      const key = `${seg.recording}:${seg.speaker}`;
      let sp = byKey.get(key);
      if (!sp) {
        const unknown = seg.speaker < 0;
        const idx = recordingIdx.get(seg.recording) ?? 0;
        const fallback = unknown ? "Unknown" : `Speaker ${seg.speaker + 1}${idx > 0 ? ` · R${idx + 1}` : ""}`;
        const name = (!unknown && this.names[key]?.trim()) || fallback;
        const id = unknown ? "unknown" : `name:${name.toLowerCase()}`;
        let group = byId.get(id);
        if (!group) {
          group = { id, name, unknown, color: unknown ? -1 : colorSeq++ % PALETTE_SIZE, keys: [], talk: 0, words: 0 };
          byId.set(id, group);
        }
        group.keys.push(key);
        sp = group;
        byKey.set(key, sp);
      }
      sp.talk += Math.max(0, seg.end - seg.start);
      sp.words += seg.text.split(/\s+/).filter(Boolean).length;
    }
    return { byKey, list: [...byId.values()] };
  });

  speakerOf = (seg) => this.speakerIndex.byKey.get(`${seg.recording}:${seg.speaker}`);

  turns = $derived(groupTurns(this.segments, this.speakerOf));

  get isEmpty() {
    return this.segments.length === 0;
  }

  get wordCount() {
    return this.speakerIndex.list.reduce((n, s) => n + s.words, 0);
  }

  /** The server saved a new recording into this transcript (creating it if new). */
  attach({ id, recording }) {
    if (!this.id) {
      this.id = id;
      this.createdAt = recording.startedAt;
    }
    if (!this.allRecordings.some((r) => r.id === recording.id)) this.allRecordings.push(recording);
  }

  add(items) {
    for (const it of items) {
      const text = String(it.text ?? "").trim();
      if (!text) continue;
      this.segments.push({
        id: it.id,
        recording: it.recording,
        speaker: it.speaker ?? -1,
        text,
        start: it.start,
        end: it.end,
      });
    }
  }

  #url(suffix = "") {
    return `/api/transcripts/${encodeURIComponent(this.id)}${suffix}`;
  }

  async rename(speaker, name) {
    const before = { ...this.names };
    const changes = Object.fromEntries(speaker.keys.map((k) => [k, name.trim()]));
    Object.assign(this.names, changes);
    try {
      await patch(this.#url(), { names: changes });
    } catch (e) {
      this.names = before;
      throw e;
    }
  }

  async setTitle(title) {
    const before = this.title;
    this.title = title.trim();
    try {
      await patch(this.#url(), { title: this.title });
    } catch (e) {
      this.title = before;
      throw e;
    }
  }

  /** Change a line's text; empty text deletes the line. */
  async editSegment(seg, text) {
    text = text.trim();
    if (text === seg.text) return;
    const idx = this.segments.findIndex((s) => s.id === seg.id);
    if (idx < 0) return;
    const before = this.segments[idx];
    const segPath = this.#url(`/segments/${encodeURIComponent(seg.id)}`);
    if (text) {
      this.segments[idx] = { ...before, text };
      try {
        await patch(segPath, { text });
      } catch (e) {
        this.segments[idx] = before;
        throw e;
      }
    } else {
      this.segments.splice(idx, 1);
      try {
        await del(segPath);
      } catch (e) {
        this.segments.splice(idx, 0, before);
        throw e;
      }
    }
  }

  async remove() {
    if (this.id) await del(this.#url());
  }
}

// ---------------------------------------------------------------------------
// Workspace: which transcript is on screen
// ---------------------------------------------------------------------------

class Workspace {
  current = $state(new Transcript());

  /** Show a saved transcript (the one being recorded, if it's that). */
  show(id) {
    if (this.current.id === id && this.current.status !== "error") return;
    this.current = recorder.target?.id === id ? recorder.target : new Transcript(id);
  }

  /** "/new" shows the transcript being recorded, or a fresh one. */
  showNew() {
    if (recorder.active && recorder.target) this.current = recorder.target;
    else if (this.current.id || !this.current.isEmpty) this.current = new Transcript();
  }

  confirmingNew = $state(false);

  /**
   * "Transcribe" / "New": leaving a stopped, saved transcript on screen asks
   * first, so it doesn't look like it was thrown away.
   */
  startNew() {
    const onSaved = storeValue(location).startsWith("/t/") && this.current.id && !recorder.active;
    if (onSaved) this.confirmingNew = true;
    else push("/new");
  }

  confirmNew() {
    this.confirmingNew = false;
    push("/new");
  }
}

export const workspace = new Workspace();

// ---------------------------------------------------------------------------
// Recorder
// ---------------------------------------------------------------------------

const WORKLET = `
class ChunkProcessor extends AudioWorkletProcessor {
  constructor() { super(); this.buf = new Float32Array(${CHUNK}); this.n = 0; }
  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (ch) for (let i = 0; i < ch.length; i++) {
      this.buf[this.n++] = ch[i];
      if (this.n === ${CHUNK}) { this.port.postMessage(this.buf.slice()); this.n = 0; }
    }
    return true;
  }
}
registerProcessor("chunk-processor", ChunkProcessor);
`;

/** Same origin as the page: the session cookie authenticates the socket. */
function wsUrl(transcriptId) {
  const proto = window.location.protocol === "https:" ? "wss" : "ws";
  const params = new URLSearchParams({ events: "1" });
  if (transcriptId) params.set("transcript", transcriptId);
  return `${proto}://${window.location.host}/listen?${params}`;
}

const CLOSE_REASONS = {
  4401: "Your session has ended. Sign in again to keep recording.",
  4404: "This transcript no longer exists.",
};

function encode(samples) {
  const bytes = new Uint8Array(samples.buffer);
  let bin = "";
  for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
  return btoa(bin);
}

// On stop, this much silence lets the server's pause endpoint commit the last
// words; we then wait briefly for them before closing the socket.
const FLUSH_SILENCE_S = 3.5;
const FLUSH_WAIT_MS = 3000;
const FLUSH_QUIET_MS = 500;

class Recorder {
  /** idle | connecting | loading | listening | paused | finishing | error */
  phase = $state("idle");
  error = $state("");
  level = $state(0); // 0..1 input level
  elapsed = $state(0); // seconds of audio sent this recording
  devices = $state([]);
  deviceId = $state(readStorage(DEVICE_KEY) ?? "");
  target = $state(null); // the Transcript this recording is saved into

  #stream = null;
  #ctx = null;
  #ws = null;
  #analyser = null;
  #raf = 0;
  #samplesSent = 0;
  #onFlushMessage = null;

  get active() {
    return ["connecting", "loading", "listening", "paused", "finishing"].includes(this.phase);
  }

  get capturing() {
    return ["connecting", "loading", "listening", "paused"].includes(this.phase);
  }

  async refreshDevices() {
    try {
      const all = await navigator.mediaDevices.enumerateDevices();
      this.devices = all.filter((d) => d.kind === "audioinput" && d.deviceId);
    } catch {
      this.devices = [];
    }
  }

  setDevice(id) {
    this.deviceId = id;
    writeStorage(DEVICE_KEY, id);
  }

  async start() {
    if (this.active) return;
    this.error = "";
    this.phase = "connecting";
    this.elapsed = 0;
    this.#samplesSent = 0;

    try {
      // Browser voice processing (tuned for calls) distorts speech onsets; the
      // models expect raw audio.
      const audio = { echoCancellation: false, noiseSuppression: false, autoGainControl: false };
      if (this.deviceId) audio.deviceId = { exact: this.deviceId };
      this.#stream = await navigator.mediaDevices.getUserMedia({ audio }).catch((e) => {
        // A remembered device may have been unplugged; fall back to the default.
        if (this.deviceId && e.name === "OverconstrainedError") {
          this.setDevice("");
          delete audio.deviceId;
          return navigator.mediaDevices.getUserMedia({ audio });
        }
        throw e;
      });
      this.refreshDevices(); // labels become available after permission is granted

      const ctx = (this.#ctx = new AudioContext({ sampleRate: SAMPLE_RATE }));
      const url = URL.createObjectURL(new Blob([WORKLET], { type: "application/javascript" }));
      await ctx.audioWorklet.addModule(url);
      URL.revokeObjectURL(url);

      const source = ctx.createMediaStreamSource(this.#stream);
      const node = new AudioWorkletNode(ctx, "chunk-processor");
      this.#analyser = Object.assign(ctx.createAnalyser(), { fftSize: 1024 });
      source.connect(this.#analyser);
      source.connect(node);
      node.connect(ctx.destination); // keeps the worklet pulled; it outputs silence

      node.port.onmessage = ({ data }) => {
        if (this.phase !== "listening" || this.#ws?.readyState !== WebSocket.OPEN) return;
        this.#ws.send(encode(data));
        this.#samplesSent += data.length;
        this.elapsed = this.#samplesSent / SAMPLE_RATE;
      };

      this.#meter();
      this.#connect();
    } catch (e) {
      this.#fail(micError(e));
    }
  }

  #connect() {
    const target = (this.target = workspace.current);
    const ws = (this.#ws = new WebSocket(wsUrl(target.id)));

    ws.onopen = () => {
      if (this.phase === "connecting") this.phase = "loading";
    };
    ws.onmessage = ({ data }) => {
      let msg;
      try {
        msg = JSON.parse(data);
      } catch {
        return;
      }
      if (Array.isArray(msg)) {
        if (!msg.length) return; // the server sends [] on every tick without new text
        target.add(msg);
        this.#onFlushMessage?.();
      } else if (msg?.type === "transcript") {
        const isNew = !target.id;
        target.attach(msg);
        // A new transcript on screen at "/new" gets its own URL.
        if (isNew && workspace.current === target && storeValue(location) === "/new") replace(`/t/${target.id}`);
      } else if (msg?.type === "status") {
        if (msg.state === "ready" && this.phase === "loading") this.phase = "listening";
        if (msg.state === "loading" && this.phase === "connecting") this.phase = "loading";
      }
    };
    ws.onclose = (ev) => {
      if (this.#ws !== ws) return;
      if (this.phase === "finishing" || this.phase === "idle") {
        this.#release();
        this.phase = "idle";
        return;
      }
      if (ev.code === 4401) auth.load();
      const reason =
        CLOSE_REASONS[ev.code] ||
        ev.reason ||
        (ev.code === 1006 ? "Could not reach the transcription server" : "Connection closed");
      this.#fail(reason);
    };
  }

  togglePause() {
    if (this.phase === "listening") this.phase = "paused";
    else if (this.phase === "paused") this.phase = "listening";
  }

  async stop() {
    if (!this.active || this.phase === "finishing") return;
    const ws = this.#ws;
    const wasStreaming = this.phase === "listening" || this.phase === "paused";
    this.#releaseAudio();

    if (!wasStreaming || ws?.readyState !== WebSocket.OPEN) {
      this.phase = "idle";
      this.#release();
      return;
    }

    this.phase = "finishing";
    const silence = new Float32Array(CHUNK);
    for (let i = 0; i < Math.ceil((FLUSH_SILENCE_S * SAMPLE_RATE) / CHUNK); i++) ws.send(encode(silence));

    await new Promise((resolve) => {
      let quiet;
      const max = setTimeout(resolve, FLUSH_WAIT_MS);
      this.#onFlushMessage = () => {
        clearTimeout(quiet);
        quiet = setTimeout(() => {
          clearTimeout(max);
          resolve();
        }, FLUSH_QUIET_MS);
      };
    });

    this.#onFlushMessage = null;
    if (this.#ws === ws) {
      this.phase = "idle";
      this.#release();
    }
  }

  dismissError() {
    if (this.phase === "error") this.phase = "idle";
    this.error = "";
  }

  #meter() {
    const buf = new Float32Array(this.#analyser.fftSize);
    const tick = () => {
      if (!this.#analyser) return;
      this.#analyser.getFloatTimeDomainData(buf);
      let sum = 0;
      for (let i = 0; i < buf.length; i++) sum += buf[i] * buf[i];
      const db = 10 * Math.log10(sum / buf.length + 1e-10);
      const target = this.phase === "paused" ? 0 : Math.min(1, Math.max(0, (db + 60) / 50));
      this.level = target > this.level ? target : this.level * 0.85 + target * 0.15;
      this.#raf = requestAnimationFrame(tick);
    };
    tick();
  }

  #fail(message) {
    this.#release();
    this.error = message;
    this.phase = "error";
  }

  #releaseAudio() {
    cancelAnimationFrame(this.#raf);
    this.#analyser = null;
    this.level = 0;
    this.#stream?.getTracks().forEach((t) => t.stop());
    this.#stream = null;
    if (this.#ctx && this.#ctx.state !== "closed") this.#ctx.close().catch(() => {});
    this.#ctx = null;
  }

  #release() {
    this.#releaseAudio();
    const ws = this.#ws;
    this.#ws = null;
    this.target = null;
    if (ws && ws.readyState <= WebSocket.OPEN) ws.close();
  }
}

function micError(e) {
  switch (e?.name) {
    case "NotAllowedError":
      return "Microphone access was denied. Allow it in your browser's site settings and try again.";
    case "NotFoundError":
      return "No microphone was found.";
    case "NotReadableError":
      return "The microphone is in use by another application.";
    default:
      return e?.message || String(e);
  }
}

export const recorder = new Recorder();

if (navigator.mediaDevices?.addEventListener) {
  navigator.mediaDevices.addEventListener("devicechange", () => recorder.refreshDevices());
}
