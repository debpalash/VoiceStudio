/**
 * useSegmentEditing — undo/redo stack + segment CRUD operations for the dub timeline.
 *
 * Extracted from App.jsx to reduce its useState/useRef/useCallback count.
 * All segment mutations go through this hook so undo tracking is automatic.
 */
import { useState, useRef, useCallback } from 'react';
import { useAppStore } from '../store';
import { askConfirm } from '../utils/dialog';
import { apiPost } from '../api/client';
import { segmentGenInputs, withoutCueSource } from '../utils/segments';
import { commitMoveResize } from '../utils/timeline';
import { buildPastePlan } from '../utils/pasteTranslations';
import {
  ATTRIBUTION_FIELDS,
  applyAttribution,
  attributionAt,
  attributionOf,
  clipParts,
  insertionSlot,
  keepParts,
  mergedOriginalParts,
  mergedParts,
  nextSegmentId,
  partsFor,
} from '../utils/segmentParts';

const MERGE_PART_FIELDS = new Set(['text', ...ATTRIBUTION_FIELDS]);

function clearStaleMergeParts(segment, fields) {
  if (!segment.merge_parts || !fields.some((field) => MERGE_PART_FIELDS.has(field))) return segment;
  const next = { ...segment };
  delete next.merge_parts;
  if (fields.some((field) => ATTRIBUTION_FIELDS.includes(field))) {
    delete next.merge_parts_original;
  }
  return next;
}

function trimmedTextRange(text, from, to) {
  const raw = text.slice(from, to);
  return {
    from: from + raw.length - raw.trimStart().length,
    to: from + raw.trimEnd().length,
    text: raw.trim(),
  };
}

// Stable empty map so `lastGenFingerprints` keeps a constant identity for a
// language with no stored hashes (avoids effect/callback churn).
const EMPTY_FINGERPRINTS = {};

export default function useSegmentEditing() {
  const dubSegments = useAppStore((s) => s.dubSegments);
  const setDubSegments = useAppStore((s) => s.setDubSegments);

  // ── Undo / Redo ──
  const undoStack = useRef([]);
  const redoStack = useRef([]);

  const pushUndo = (segments) => {
    undoStack.current.push(JSON.stringify(segments));
    if (undoStack.current.length > 50) undoStack.current.shift();
    redoStack.current = []; // clear redo on new edit
  };

  const undo = () => {
    if (undoStack.current.length === 0) return;
    redoStack.current.push(JSON.stringify(dubSegments));
    const prev = JSON.parse(undoStack.current.pop());
    setDubSegments(prev);
  };

  const redo = () => {
    if (redoStack.current.length === 0) return;
    undoStack.current.push(JSON.stringify(dubSegments));
    const next = JSON.parse(redoStack.current.pop());
    setDubSegments(next);
  };

  // Wrap setDubSegments calls that are user-edits with undo tracking
  const editSegments = (newSegs) => {
    pushUndo(dubSegments);
    setDubSegments(newSegs);
  };

  // Stable handlers for virtualized segment rows. Use functional updates so
  // they don't depend on dubSegments identity (avoids row re-renders).
  const segmentEditField = useCallback(
    (id, field, value) => {
      pushUndo(dubSegments);
      // P1.2 — a manual text edit is a translation edit for the CURRENT
      // target language: keep `translations[lang]` in lock-step with `text`
      // so switching languages and back never loses the edit.
      const lang = useAppStore.getState().dubLangCode;
      setDubSegments((prev) =>
        prev.map((s) => {
          if (s.id !== id) return s;
          const edited = field === 'text' ? withoutCueSource(s) : s;
          const next = clearStaleMergeParts({ ...edited, [field]: value }, [field]);
          if (field === 'text' && lang) {
            next.translations = { ...s.translations, [lang]: value };
          }
          if (field === 'text') {
            // The user rewrote the line — the machine-translation annotations
            // ("translation error", "polish pass skipped") describe text that
            // no longer exists. Leaving them makes the row wear a stale badge
            // over human-authored words.
            next.translate_error = undefined;
            next.translate_degraded = undefined;
          }
          return next;
        }),
      );
    },
    [dubSegments],
  );

  const segmentDelete = useCallback(
    (id) => {
      pushUndo(dubSegments);
      setDubSegments((prev) => prev.filter((s) => s.id !== id));
    },
    [dubSegments],
  );

  // Timeline drag/resize commit (#280, item 3). Called ONCE per gesture by
  // SegmentTrack (live drag positions stay in component state); keyboard
  // nudges coalesce by passing undo:false after the first nudge of a focus
  // session. String(id) match fixes the old parseInt('seg-3_a') bug that
  // edited the wrong segment after a split. commitMoveResize() preserves
  // fingerprint parity (move never touches generation inputs; resize only
  // changes `speed`, dropping the key at 1.0).
  const segmentMoveResize = useCallback(
    (id, { start, end }, opts = {}) => {
      const { undo = true } = opts;
      if (undo) pushUndo(dubSegments);
      setDubSegments((prev) =>
        prev.map((s) => (String(s.id) === String(id) ? commitMoveResize(s, { start, end }) : s)),
      );
    },
    [dubSegments],
  );

  // Insert a blank line after `id` (#1612 — "add a new phrase"). It takes the
  // silent gap before the next line when there is a usable one, otherwise a
  // default-length slot right after this row; either way the existing overlap
  // detection flags a collision rather than letting two lines play together
  // unannounced. The new row continues the same speaker, voice and language —
  // but carries no directorial note or gain, which described the OTHER line's
  // words. `/dub/generate` skips empty-text segments, so the row is inert
  // until the user writes it.
  const segmentInsert = useCallback(
    (id) => {
      pushUndo(dubSegments);
      setDubSegments((prev) => {
        const idx = prev.findIndex((s) => s.id === id);
        if (idx < 0) return prev;
        const seg = prev[idx];
        const slot = insertionSlot(seg, prev[idx + 1]);
        const created = {
          id: nextSegmentId(
            prev.map((s) => s.id),
            seg.id,
          ),
          start: slot.start,
          end: slot.end,
          text: '',
          text_original: '',
        };
        for (const f of ['speaker_id', 'profile_id', 'target_lang']) {
          if (seg[f] !== undefined && seg[f] !== null) created[f] = seg[f];
        }
        return [...prev.slice(0, idx + 1), created, ...prev.slice(idx + 1)];
      });
    },
    [dubSegments],
  );

  // Timeline selection — syncs the segment table (scroll + highlight).
  const [timelineSelSegId, setTimelineSelSegId] = useState(null);

  const segmentRestoreOriginal = useCallback(
    (id) => {
      pushUndo(dubSegments);
      // "Use the original text for this row" is a per-language decision like
      // any other text edit — record it under the current language so a
      // round-trip through another language doesn't resurrect the discarded
      // translation (P1.2).
      const lang = useAppStore.getState().dubLangCode;
      setDubSegments((prev) =>
        prev.map((s) => {
          if (s.id !== id) return s;
          const restored = s.text_original || s.text;
          const originalParts = s.merge_parts_original;
          return applyAttribution(
            {
              ...s,
              text: restored,
              ...(lang ? { translations: { ...s.translations, [lang]: restored } } : {}),
              translate_error: undefined,
              translate_degraded: undefined,
              merge_parts: originalParts,
            },
            originalParts ? attributionAt(originalParts, 0) : attributionOf(s),
          );
        }),
      );
    },
    [dubSegments],
  );

  // Paste a translation produced outside the app (ChatGPT / DeepL / a human)
  // onto the EXISTING segments. Same three duties as `segmentEditField`, run
  // across every matched row in one undo step:
  //   1. push undo   2. write `text` AND `translations[lang]` in lock-step
  //   3. clear the machine-translation badges the new words invalidate
  // and the same two prohibitions: `text_original` is never touched (it is
  // `handleTranslateAll`'s translate source — overwriting it would poison
  // every later re-translate), and no language other than the ACTIVE
  // `dubLangCode` is written. Changed `text` alone marks those segments
  // stale for regeneration via the existing per-language fingerprints
  // (services/incremental.py) — no extra flag needed.
  //
  // Mapping lives in utils/pasteTranslations so the preview dialog and this
  // applier derive an identical plan; unmatched rows are left exactly as
  // they were.
  const pasteTranslations = useCallback(
    (text, opts = {}) => {
      const plan = buildPastePlan(text, dubSegments, opts);
      const byId = new Map(plan.rows.filter((r) => r.matched).map((r) => [String(r.id), r.after]));
      if (!byId.size) return plan;
      pushUndo(dubSegments);
      const lang = useAppStore.getState().dubLangCode;
      setDubSegments((prev) =>
        prev.map((s) => {
          const next = byId.get(String(s.id));
          if (next === undefined) return s;
          // Pasted words are not the import's, even when they read the same.
          return {
            ...withoutCueSource(s),
            text: next,
            ...(lang ? { translations: { ...s.translations, [lang]: next } } : {}),
            translate_error: undefined,
            translate_degraded: undefined,
            merge_parts: undefined,
          };
        }),
      );
      return plan;
    },
    [dubSegments],
  );

  // Segment multi-select
  const [selectedSegIds, setSelectedSegIds] = useState(new Set());
  const lastSelectedIdxRef = useRef(null);

  const toggleSegSelect = useCallback(
    (id, idx, shift) => {
      setSelectedSegIds((prev) => {
        const next = new Set(prev);
        if (shift && lastSelectedIdxRef.current !== null) {
          const [a, b] = [lastSelectedIdxRef.current, idx].sort((x, y) => x - y);
          for (let i = a; i <= b; i++) {
            const s = dubSegments[i];
            if (s) next.add(s.id);
          }
        } else {
          if (next.has(id)) next.delete(id);
          else next.add(id);
          lastSelectedIdxRef.current = idx;
        }
        return next;
      });
    },
    [dubSegments],
  );

  const selectAllSegs = useCallback((segs) => {
    setSelectedSegIds(new Set(segs.map((s) => s.id)));
  }, []);

  const clearSegSelection = useCallback(() => setSelectedSegIds(new Set()), []);

  // Bulk actions
  const bulkApplyToSelected = useCallback(
    (patch) => {
      if (!selectedSegIds.size) return;
      pushUndo(dubSegments);
      setDubSegments((prev) =>
        prev.map((s) =>
          selectedSegIds.has(s.id)
            ? clearStaleMergeParts({ ...s, ...patch }, Object.keys(patch))
            : s,
        ),
      );
    },
    [dubSegments, selectedSegIds],
  );

  const bulkDeleteSelected = useCallback(async () => {
    if (!selectedSegIds.size) return;
    if (
      !(await askConfirm(
        `Delete ${selectedSegIds.size} selected segment${selectedSegIds.size === 1 ? '' : 's'}?`,
      ))
    )
      return;
    pushUndo(dubSegments);
    setDubSegments((prev) => prev.filter((s) => !selectedSegIds.has(s.id)));
    setSelectedSegIds(new Set());
  }, [dubSegments, selectedSegIds]);

  // Split at text cursor. Time split proportional to cursor position in text.
  const segmentSplit = useCallback(
    (id, cursorPos) => {
      pushUndo(dubSegments);
      setDubSegments((prev) => {
        const idx = prev.findIndex((s) => s.id === id);
        if (idx < 0) return prev;
        const seg = prev[idx];
        const text = seg.text || '';
        const pos = Math.max(1, Math.min(cursorPos, text.length - 1));
        const ratio = text.length > 0 ? pos / text.length : 0.5;
        const midT = seg.start + (seg.end - seg.start) * ratio;
        // Other languages' saved texts (P1.2) can't be split at a sensible
        // position for the halves — drop them; the halves are new segment ids
        // that need fresh TTS per language anyway.
        // Attribution follows the SPEAKER, not the parent row. When this
        // segment is the product of a merge, each half takes the attribution
        // recorded for the part covering its START, so words carried across a
        // speaker boundary are dubbed by whoever actually says them (#1612).
        // A never-merged segment has exactly one part, so both halves inherit
        // the parent unchanged — the old behaviour.
        // Attribution is looked up by TEXT OFFSET, not by time. `pos` is where
        // the user put the caret, and offsets cannot overlap — whereas two
        // merged parts can cover the same instant the moment someone retimes a
        // line past its neighbour, which then hands the words to whichever
        // speaker happened to be checked first (#1612).
        const parts = partsFor(seg);
        const leftRange = trimmedTextRange(text, 0, pos);
        const rightRange = trimmedTextRange(text, pos, text.length);
        const left = applyAttribution(
          {
            ...seg,
            id: `${seg.id}_a`,
            text: leftRange.text,
            end: midT,
            text_original: leftRange.text,
            translations: undefined,
            merge_parts: keepParts(clipParts(parts, leftRange.from, leftRange.to)),
            merge_parts_original: keepParts(clipParts(parts, leftRange.from, leftRange.to)),
          },
          attributionAt(parts, leftRange.from),
        );
        const right = applyAttribution(
          {
            ...seg,
            id: `${seg.id}_b`,
            text: rightRange.text,
            start: midT,
            text_original: rightRange.text,
            translations: undefined,
            merge_parts: keepParts(clipParts(parts, rightRange.from, rightRange.to)),
            merge_parts_original: keepParts(clipParts(parts, rightRange.from, rightRange.to)),
          },
          attributionAt(parts, rightRange.from),
        );
        return [...prev.slice(0, idx), left, right, ...prev.slice(idx + 1)];
      });
    },
    [dubSegments],
  );

  // Merge a segment with its previous or next sibling. Only "next" existed;
  // #1612 asked for both. Merging is index-based over the full list, so
  // `direction: 'prev'` is the same operation anchored one row earlier.
  const segmentMerge = useCallback(
    (id, direction = 'next') => {
      pushUndo(dubSegments);
      setDubSegments((prev) => {
        const at = prev.findIndex((s) => s.id === id);
        if (at < 0) return prev;
        const idx = direction === 'prev' ? at - 1 : at;
        if (idx < 0 || idx >= prev.length - 1) return prev;
        const a = prev[idx];
        const b = prev[idx + 1];
        // Merge per-language texts (P1.2) only where BOTH sides carry the
        // language — a half-known language would otherwise mix two languages
        // in one entry. Missing entries just mean "translate again".
        const ta = a.translations || {};
        const tb = b.translations || {};
        const mergedTranslations = {};
        for (const lang of Object.keys(ta)) {
          if (typeof ta[lang] === 'string' && typeof tb[lang] === 'string') {
            mergedTranslations[lang] = `${ta[lang]} ${tb[lang]}`.trim();
          }
        }
        const merged = {
          ...a,
          text: `${a.text || ''} ${b.text || ''}`.trim(),
          text_original:
            `${a.text_original || a.text || ''} ${b.text_original || b.text || ''}`.trim(),
          end: b.end,
          translations: Object.keys(mergedTranslations).length ? mergedTranslations : undefined,
          // Remember which attribution covered which span. The merged row
          // still presents as `a` (it starts with a's words), but a later
          // split can now hand b's words back to b's speaker instead of
          // dubbing them in a's voice (#1612).
          merge_parts: mergedParts(a, b),
          merge_parts_original: mergedOriginalParts(a, b),
        };
        return [...prev.slice(0, idx), merged, ...prev.slice(idx + 2)];
      });
    },
    [dubSegments],
  );

  // Direction editor state
  const [directionSegId, setDirectionSegId] = useState(null);
  const openDirection = useCallback((seg) => setDirectionSegId(seg.id), []);
  const closeDirection = useCallback(() => setDirectionSegId(null), []);
  const saveDirection = useCallback(
    (value) => {
      if (!directionSegId) return;
      pushUndo(dubSegments);
      setDubSegments((prev) =>
        prev.map((s) =>
          s.id === directionSegId
            ? clearStaleMergeParts({ ...s, direction: value || undefined }, ['direction'])
            : s,
        ),
      );
    },
    [directionSegId, dubSegments],
  );

  // Incremental plan — tracks which segments changed since last generate.
  // P1.3: fingerprints are stored PER LANGUAGE ({ lang: { segId: hash } }),
  // and `lastGenFingerprints` is the ACTIVE language's map — so "Regen N
  // changed" is judged against the track you're looking at, never against
  // whichever language happened to generate last. Switching to a language
  // that was never generated yields an empty map → no plan (no false
  // "all fresh" / "all stale" claims).
  const dubLangCode = useAppStore((s) => s.dubLangCode);
  const [fingerprintsByLang, setFingerprintsByLang] = useState({});
  const lastGenFingerprints = fingerprintsByLang[dubLangCode] || EMPTY_FINGERPRINTS;
  // Same call signature as before for existing single-track callers; the
  // optional `lang` pins the map to the track that produced the hashes
  // (e.g. each pick of the multi-language batch loop) instead of whatever
  // the store's selection is by the time the response lands.
  const setLastGenFingerprints = useCallback((map, lang) => {
    const key = lang || useAppStore.getState().dubLangCode;
    if (!key) return;
    setFingerprintsByLang((prev) => ({ ...prev, [key]: map || {} }));
  }, []);
  const [incrementalPlan, setIncrementalPlan] = useState(null);
  // Subscribed (not getState()) so the plan effect in App.jsx re-fires when
  // the Voice-match toggle flips — the badge refreshes to "N stale" at once.
  const voiceMatch = useAppStore((s) => s.voiceMatch);

  const recomputeIncremental = useCallback(async () => {
    if (!dubSegments.length || !Object.keys(lastGenFingerprints).length) {
      setIncrementalPlan(null);
      return;
    }
    try {
      // Same payload shape as the generate request (utils/segments.js) so
      // stored fingerprints actually match unchanged segments (#281). `lang`
      // must match the language the generate run hashed with — it's part of
      // the fingerprint now (P1.3).
      const res = await apiPost('/tools/incremental', {
        segments: dubSegments.map((s) => ({ id: String(s.id), ...segmentGenInputs(s) })),
        stored_hashes: lastGenFingerprints,
        lang: dubLangCode,
        // Voice-match mode is part of the fingerprint when non-default, so
        // flipping the toggle honestly reports every segment stale — the
        // audio really would render from a different reference (#281 class).
        voice_match: voiceMatch || 'per_line',
      });
      setIncrementalPlan({ stale: res.stale, fresh: res.fresh });
    } catch (e) {
      console.warn('incremental plan failed', e);
    }
  }, [dubSegments, lastGenFingerprints, dubLangCode, voiceMatch]);

  return {
    // Undo/Redo
    undo,
    redo,
    pushUndo,
    editSegments,
    // Per-segment operations
    segmentEditField,
    segmentDelete,
    segmentRestoreOriginal,
    pasteTranslations,
    segmentSplit,
    segmentMerge,
    segmentInsert,
    segmentMoveResize,
    // Timeline selection (waveform ↔ table sync)
    timelineSelSegId,
    setTimelineSelSegId,
    // Multi-select
    selectedSegIds,
    setSelectedSegIds,
    toggleSegSelect,
    selectAllSegs,
    clearSegSelection,
    bulkApplyToSelected,
    bulkDeleteSelected,
    // Direction editor
    directionSegId,
    openDirection,
    closeDirection,
    saveDirection,
    // Incremental plan
    lastGenFingerprints,
    setLastGenFingerprints,
    // Per-language fingerprint store (P1.3) — for project save/load and dub
    // history restore, which persist/rehydrate ALL tracks' hashes at once.
    fingerprintsByLang,
    setFingerprintsByLang,
    incrementalPlan,
    setIncrementalPlan,
    recomputeIncremental,
  };
}
