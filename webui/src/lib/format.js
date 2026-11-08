// Pure helpers for timestamps, turn grouping and transcript export.

/** 75.3 -> "1:15", 3725 -> "1:02:05" */
export function clock(seconds) {
  const s = Math.max(0, Math.floor(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const ss = String(s % 60).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${ss}` : `${m}:${ss}`;
}

/** 75.3 -> "00:01:15,300" (SRT) or "00:01:15.300" (VTT) */
function cueTime(seconds, sep) {
  const ms = Math.max(0, Math.round(seconds * 1000));
  const h = Math.floor(ms / 3_600_000);
  const m = Math.floor((ms % 3_600_000) / 60_000);
  const s = Math.floor((ms % 60_000) / 1000);
  const pad = (n, w = 2) => String(n).padStart(w, "0");
  return `${pad(h)}:${pad(m)}:${pad(s)}${sep}${pad(ms % 1000, 3)}`;
}

/** Consecutive segments from one speaker become a turn, unless a long pause intervenes. */
export const TURN_GAP_S = 10;

export function groupTurns(segments, speakerOf) {
  const turns = [];
  let cur = null;
  for (const seg of segments) {
    const speaker = speakerOf(seg);
    const continues =
      cur &&
      cur.recording === seg.recording &&
      cur.speaker.id === speaker.id &&
      seg.start - cur.end < TURN_GAP_S;
    if (continues) {
      cur.segments.push(seg);
      cur.end = seg.end;
    } else {
      cur = { id: seg.id, recording: seg.recording, speaker, start: seg.start, end: seg.end, segments: [seg] };
      turns.push(cur);
    }
  }
  return turns;
}

/** Start offset of each recording on a single continuous timeline (for subtitle export). */
function recordingOffsets(segments) {
  const ends = new Map();
  for (const s of segments) ends.set(s.recording, Math.max(ends.get(s.recording) ?? 0, s.end));
  const offsets = new Map();
  let acc = 0;
  for (const [recording, end] of ends) {
    offsets.set(recording, acc);
    acc += end + 1;
  }
  return offsets;
}

export function toText(turns, { timestamps = true } = {}) {
  return turns
    .map((t) => {
      const text = t.segments.map((s) => s.text).join(" ");
      return `${timestamps ? `[${clock(t.start)}] ` : ""}${t.speaker.name}: ${text}`;
    })
    .join("\n\n");
}

export function toMarkdown(turns, recordings, title = "Transcript") {
  const out = [`# ${title}`, ""];
  let recording = null;
  for (const t of turns) {
    if (t.recording !== recording && recordings.length > 1) {
      recording = t.recording;
      const idx = recordings.findIndex((r) => r.id === recording);
      const at = recordings[idx]?.startedAt;
      out.push(`## Recording ${idx + 1}${at ? ` — ${new Date(at).toLocaleString()}` : ""}`, "");
    }
    out.push(`**${t.speaker.name}** \`${clock(t.start)}\``, "", t.segments.map((s) => s.text).join(" "), "");
  }
  return out.join("\n");
}

function cues(segments, speakerOf, sep) {
  const offsets = recordingOffsets(segments);
  return segments.map((s, i) => {
    const off = offsets.get(s.recording) ?? 0;
    return {
      n: i + 1,
      range: `${cueTime(s.start + off, sep)} --> ${cueTime(s.end + off, sep)}`,
      speaker: speakerOf(s).name,
      text: s.text,
    };
  });
}

export function toSrt(segments, speakerOf) {
  return cues(segments, speakerOf, ",")
    .map((c) => `${c.n}\n${c.range}\n${c.speaker}: ${c.text}\n`)
    .join("\n");
}

export function toVtt(segments, speakerOf) {
  const body = cues(segments, speakerOf, ".")
    .map((c) => `${c.range}\n<v ${c.speaker}>${c.text}\n`)
    .join("\n");
  return `WEBVTT\n\n${body}`;
}

export function toJson(segments, speakerOf, recordings, title = "") {
  return JSON.stringify(
    {
      title,
      recordings: recordings.map((r, i) => ({ index: i + 1, started_at: new Date(r.startedAt).toISOString() })),
      segments: segments.map((s) => ({
        recording: recordings.findIndex((r) => r.id === s.recording) + 1,
        speaker: speakerOf(s).name,
        start: +s.start.toFixed(2),
        end: +s.end.toFixed(2),
        text: s.text,
      })),
    },
    null,
    2,
  );
}

export function download(filename, text, type = "text/plain") {
  const url = URL.createObjectURL(new Blob([text], { type: `${type};charset=utf-8` }));
  const a = Object.assign(document.createElement("a"), { href: url, download: filename });
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
