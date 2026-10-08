const MAX_CRASH_TAIL_CHARS = 1200;
const CHAIN_MARKER_RE =
  /^(?:The above exception was the direct cause of the following exception|During handling of the above exception, another exception occurred):?$/;

/** The error line that ends the FIRST block of a chained traceback — i.e. the
 *  original cause. Empty string when `text` is not a chained traceback. */
export function rootCauseLine(text) {
  const lines = text.split('\n');
  const marker = lines.findIndex((l) => CHAIN_MARKER_RE.test(l.trim()));
  if (marker <= 0) return '';
  // Walk back past the marker's blank line to the last non-indented line —
  // traceback frames are indented, the exception line is not.
  for (let i = marker - 1; i >= 0; i -= 1) {
    const line = lines[i];
    if (line.trim() && !/^\s/.test(line)) return line.trim();
  }
  return '';
}

// Below this much room for actual log output, the root-cause header stops being
// worth its cost — a labelled line with almost nothing under it is harder to
// act on than the raw newest output.
const MIN_TAIL_CHARS = 400;

const NATIVE_HEADER_RE = /^(?:Fatal Python error:|Windows fatal exception:)/;
const SECTION_RE = /^(?:Current thread\b|Thread\b|Stack \(most recent call first\))/;
const FRAME_RE = /^\s*File "([^"]*)", line (\d+) in (.+?)\s*$/;
const REPEAT_RE = /^\s*\[Previous line repeated (\d+) more times?\]\s*$/;
const ELIDED_RE = /^\s*… (\d+) more frames?\s*$/;
/** Innermost frames always kept, whatever code they belong to. */
const INNER_FRAMES = 3;
/** Frames kept when a thread has no VoiceStudio frame to anchor on. */
const LIBRARY_ONLY_FRAMES = 6;

/** A frame path shortened to what identifies it, and whether it is library
 *  code. Install prefixes (`.venv/Lib/site-packages/`, a uv-managed stdlib)
 *  are hundreds of characters of noise per frame in a size-capped report; a
 *  shortened library path keeps a `…/` prefix so it still reads as library
 *  code when an excerpt is condensed again. */
export function describeFramePath(path) {
  const slashed = path.replace(/\\/g, '/');
  // `<frozen runpy>`, and paths this function already shortened.
  if (slashed.startsWith('<') || slashed.startsWith('…/')) return { path, library: true };
  const lib =
    slashed.match(/\/(?:site|dist)-packages\/(.+)$/) ||
    slashed.match(/\/lib\/python3[\d.]*\/(.+)$/i) ||
    slashed.match(/\/Lib\/(.+)$/);
  if (lib) {
    return /^(?:backend|omnivoice)\//.test(lib[1])
      ? { path: lib[1], library: false }
      : { path: `…/${lib[1]}`, library: true };
  }
  const own = slashed.match(/(?:^|\/)((?:backend|omnivoice)\/.+)$/);
  return { path: own ? own[1] : path, library: false };
}

/** The latest fault's thread sections: frames, collapsed repeats and elisions. */
function parseNativeDump(text) {
  const lines = text.split('\n');
  const start = lines.findLastIndex((line) => NATIVE_HEADER_RE.test(line.trim()));
  if (start < 0) return null;
  const header = lines[start].trim();
  const sections = [];
  for (const raw of lines.slice(start + 1)) {
    const line = raw.trim();
    if (line.startsWith('Extension modules:')) break;
    if (SECTION_RE.test(line)) {
      sections.push({ title: line, current: !line.startsWith('Thread'), entries: [] });
      continue;
    }
    const section = sections.at(-1);
    if (!section) continue;
    const last = section.entries.at(-1);
    const frame = line.match(FRAME_RE);
    const repeat = line.match(REPEAT_RE);
    const elided = line.match(ELIDED_RE);
    if (frame) {
      const { path, library } = describeFramePath(frame[1]);
      const text = `  File "${path}", line ${frame[2]} in ${frame[3]}`;
      if (last?.text === text) last.repeats += 1;
      else section.entries.push({ text, library, repeats: 0 });
    } else if (repeat && last?.text) {
      last.repeats += Number(repeat[1]);
    } else if (elided) {
      section.entries.push({ elided: Number(elided[1]) });
    }
    // Anything else is unrelated output interleaved with the dump.
  }
  return { header, sections };
}

const elision = (count) => `  … ${count} more frame${count === 1 ? '' : 's'}`;

/** Render a thread keeping only `keep(frameIndex, entry)` frames; every run of
 *  dropped frames becomes one "… N more frames" line. Re-rendering the result
 *  keeps the same frames, so a stored excerpt survives a second pass intact. */
function renderSection(section, keep) {
  const out = [section.title];
  let dropped = 0;
  let index = 0;
  for (const entry of section.entries) {
    if (entry.elided) {
      dropped += entry.elided;
      continue;
    }
    if (keep(index++, entry)) {
      if (dropped) out.push(elision(dropped));
      dropped = 0;
      out.push(entry.text);
      if (entry.repeats) out.push(`  [Previous line repeated ${entry.repeats} more times]`);
    } else {
      dropped += 1 + entry.repeats;
    }
  }
  if (dropped) out.push(elision(dropped));
  return out;
}

/** Python's native-fault dump puts the failing frame FIRST, unlike a regular
 *  traceback. The excerpt leads with the faulting thread and keeps what
 *  identifies the crash inside the report's size cap (#2382): its innermost
 *  frames, every VoiceStudio frame (which load or job was running), deep
 *  recursion collapsed to one line, and, for each other thread, its innermost
 *  and first VoiceStudio frame, because a native fault is often two threads
 *  touching the same model. Extension-module lists are dropped. */
export function nativeCrashExcerpt(text) {
  const dump = parseNativeDump(text);
  if (!dump) return '';
  const { header, sections } = dump;
  const current = sections.find((section) => section.current) ?? sections[0];
  const out = [header];
  if (current) {
    const frames = current.entries.filter((entry) => !entry.elided);
    const anchored = frames.some((entry) => !entry.library);
    out.push(
      ...renderSection(current, (index, entry) =>
        anchored ? index < INNER_FRAMES || !entry.library : index < LIBRARY_ONLY_FRAMES,
      ),
    );
  }
  for (const section of sections) {
    if (section === current) continue;
    const firstOwn = section.entries
      .filter((entry) => !entry.elided)
      .findIndex((entry) => !entry.library);
    out.push(...renderSection(section, (index) => index === 0 || index === firstOwn));
  }
  return out.join('\n').trim();
}

/** One line naming a native fault and where it happened, for failure messages
 *  and issue titles. Empty when `text` holds no native-fault dump. */
export function nativeFaultSummary(text) {
  const dump = parseNativeDump(text);
  if (!dump) return '';
  const current = dump.sections.find((section) => section.current) ?? dump.sections[0];
  const frames = (current?.entries ?? []).filter((entry) => !entry.elided);
  const where = (entry) => {
    const [, path, line, fn] = entry.text.match(FRAME_RE);
    return `${path}:${line} in ${fn}`;
  };
  if (!frames.length) return dump.header;
  const own = frames.find((entry) => !entry.library);
  return `${dump.header} at ${where(frames[0])}${own && own !== frames[0] ? ` (from ${where(own)})` : ''}`;
}

/** Bound the crash stderr to `max` characters, keeping the newest end AND — for
 *  a chained traceback — the root cause that would otherwise be cut.
 *
 *  The result never exceeds `max`: the prefix is budgeted for BEFORE slicing,
 *  not added on top of a full-size tail. */
export function clampCrashTail(text, max = MAX_CRASH_TAIL_CHARS) {
  const native = nativeCrashExcerpt(text);
  if (native) {
    const suffix = '\n… (truncated)';
    return native.length <= max
      ? native
      : (native.slice(0, Math.max(0, max - suffix.length)) + suffix).slice(0, max);
  }
  if (text.length <= max) return text;

  const PLAIN = '… (truncated)';
  const plainTail = () => `${PLAIN}\n${text.slice(-Math.max(0, max - PLAIN.length - 1))}`;

  const root = rootCauseLine(text);
  if (!root) return plainTail();

  const prefix = `${root}\n… (truncated — chained traceback; the line above is the original cause)\n`;
  const room = max - prefix.length;
  if (room < MIN_TAIL_CHARS) return plainTail();

  const kept = text.slice(-room);
  // Already visible in what we keep — repeating it is noise, and the budget is
  // better spent on more log.
  if (kept.includes(root)) return plainTail();
  return prefix + kept;
}
