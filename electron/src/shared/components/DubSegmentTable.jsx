import React, { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { List, useDynamicRowHeight } from 'react-window';
import DubSegmentRow from './DubSegmentRow';
import { Table } from '../ui';
import { Headphones } from 'lucide-react';
import SearchableSelect from './SearchableSelect';
import DubToggle from './dub/DubToggle';
import { useAppStore } from '../store';
import { visibleMergeAvailability } from '../utils/segmentParts';
import useDubLivePreview from '../hooks/useDubLivePreview';

export default function DubSegmentTable({
  segments,
  profiles,
  speakerClones,
  dubStep,
  dubProgress,
  previewLoadingId,
  selectedIds,
  onSelect,
  onSelectAll,
  onClearSelection,
  onEditField,
  onDelete,
  onRestore,
  onPreview,
  onSplit,
  onMerge,
  onInsert,
  onMoveResize,
  onDirect,
  onSeek,
  timelineSelectedId = null,
}) {
  const { t } = useTranslation();
  const disabled = dubStep === 'generating' || dubStep === 'stopping';
  const [query, setQuery] = useState('');
  const [speakerFilter, setSpeakerFilter] = useState('');
  // ID of the segment under the playhead. Subscribed via selector so the
  // table re-renders only when the playing segment changes, not on every
  // timeupdate tick.
  const currentSegId = useAppStore((s) => s.dubCurrentSegId);

  // Opt-in live dub preview (default off): editing a row's translated text
  // streams that line over /ws/tts. Suspended while a dub generation runs —
  // the pipeline already owns the TTS admission slot.
  const livePreviewOn = useAppStore((s) => s.dubLivePreview);
  const setDubLivePreview = useAppStore((s) => s.setDubLivePreview);
  const liveEnabled = livePreviewOn && !disabled;
  const { liveSegId, onLiveEdit, onLiveToggle } = useDubLivePreview({ enabled: liveEnabled });

  // Imperative handle for react-window v2 so we can auto-scroll the row
  // containing the playhead into view. (The scroll effect itself lives
  // below the `filtered` memo so it can depend on it without TDZ.)
  const listRef = useRef(null);

  const bodyRef = useRef(null);
  const [bodyHeight, setBodyHeight] = useState(0);
  useLayoutEffect(() => {
    if (!bodyRef.current) return;
    const measure = () => {
      const h = bodyRef.current?.clientHeight || 0;
      setBodyHeight((prev) => (Math.abs(prev - h) > 1 ? h : prev));
    };
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(bodyRef.current);
    return () => ro.disconnect();
  }, []);

  const speakers = useMemo(() => {
    const s = new Set(segments.map((x) => x.speaker_id).filter(Boolean));
    return Array.from(s).sort();
  }, [segments]);

  const filtered = useMemo(() => {
    if (!query && !speakerFilter) return segments;
    const q = query.trim().toLowerCase();
    return segments.filter((s) => {
      if (speakerFilter && s.speaker_id !== speakerFilter) return false;
      if (!q) return true;
      return (
        (s.text && s.text.toLowerCase().includes(q)) ||
        (s.text_original && s.text_original.toLowerCase().includes(q))
      );
    });
  }, [segments, query, speakerFilter]);

  // Auto-scroll the playing row into view as the playhead advances. Uses
  // align='smart' so an already-visible row doesn't trigger a jump.
  // Placed after `filtered` so it can depend on it without TDZ.
  useEffect(() => {
    if (!currentSegId || !listRef.current) return;
    const filteredIdx = filtered.findIndex((s) => s.id === currentSegId);
    if (filteredIdx < 0) return;
    try {
      listRef.current.scrollToRow({
        index: filteredIdx,
        align: 'smart',
        behavior: 'smooth',
      });
    } catch (_) {
      /* react-window may not be ready yet */
    }
  }, [currentSegId, filtered]);

  // Timeline → table sync (#280, item 3): clicking a segment box on the
  // waveform timeline scrolls its row into view and highlights it.
  useEffect(() => {
    if (timelineSelectedId == null || !listRef.current) return;
    const filteredIdx = filtered.findIndex((s) => String(s.id) === String(timelineSelectedId));
    if (filteredIdx < 0) return;
    try {
      listRef.current.scrollToRow({
        index: filteredIdx,
        align: 'smart',
        behavior: 'smooth',
      });
    } catch (_) {
      /* react-window may not be ready yet */
    }
  }, [timelineSelectedId, filtered]);

  // Wrapped controls and translated status badges can change a row's height.
  const rowHeight = useDynamicRowHeight({ defaultRowHeight: 160 });

  const rowProps = useMemo(
    () => ({
      filtered,
      profiles,
      speakerClones,
      disabled,
      dubStep,
      dubProgress,
      previewLoadingId,
      selectedIds,
      onSelect,
      onEditField,
      onDelete,
      onRestore,
      onPreview,
      onSplit,
      onMerge,
      onInsert,
      onMoveResize,
      onDirect,
      onSeek,
      segments,
      currentSegId,
      timelineSelectedId,
      liveEnabled,
      liveSegId,
      onLiveEdit,
      onLiveToggle,
    }),
    [
      filtered,
      profiles,
      speakerClones,
      disabled,
      dubStep,
      dubProgress,
      previewLoadingId,
      selectedIds,
      onSelect,
      onEditField,
      onDelete,
      onRestore,
      onPreview,
      onSplit,
      onMerge,
      onInsert,
      onMoveResize,
      onDirect,
      onSeek,
      segments,
      currentSegId,
      timelineSelectedId,
      liveEnabled,
      liveSegId,
      onLiveEdit,
      onLiveToggle,
    ],
  );

  const Row = useCallback(
    ({
      index,
      style,
      ariaAttributes,
      filtered: fl,
      profiles: profs,
      speakerClones: clones,
      disabled: dis,
      dubProgress: prog,
      dubStep: step,
      previewLoadingId: previewId,
      selectedIds: sel,
      onSelect: pick,
      onEditField: edit,
      onDelete: del,
      onRestore: rest,
      onPreview: prev,
      onSplit: split,
      onMerge: merge,
      onInsert: insert,
      onMoveResize: moveResize,
      onDirect: direct,
      onSeek: seek,
      segments: segs,
      currentSegId: curId,
      timelineSelectedId: tlSel,
      liveEnabled: liveOn,
      liveSegId: liveId,
      onLiveEdit: liveEdit,
      onLiveToggle: liveToggle,
    }) => {
      const seg = fl[index];
      if (!seg) return null;
      const absoluteIndex = segs.indexOf(seg);
      const isActive =
        (step === 'generating' || step === 'stopping') && prog.current === absoluteIndex + 1;
      const isDone =
        (step === 'generating' || step === 'stopping') && prog.current > absoluteIndex + 1;
      const isPlaying = curId === seg.id;
      const timelineSelected = tlSel != null && String(tlSel) === String(seg.id);
      // Merge operates on source neighbors. Hide the action when a filter
      // hides that neighbor so the user cannot mutate an unseen subtitle.
      const { canMerge, canMergePrev } = visibleMergeAvailability(segs, fl, seg);
      const previous = segs[absoluteIndex - 1];
      const next = segs[absoluteIndex + 1];
      const hasOverlap =
        (previous && Number(previous.end) > Number(seg.start) + 0.001) ||
        (next && Number(seg.end) > Number(next.start) + 0.001);
      return (
        <DubSegmentRow
          seg={seg}
          idx={index}
          style={style}
          ariaAttributes={ariaAttributes}
          disabled={dis}
          isActive={isActive}
          isDone={isDone}
          isPlaying={isPlaying}
          timelineSelected={timelineSelected}
          hasOverlap={hasOverlap}
          previewLoading={previewId === seg.id}
          selected={sel && sel.has(seg.id)}
          canMerge={canMerge}
          profiles={profs}
          speakerClones={clones}
          onEditField={edit}
          onDelete={del}
          onRestore={rest}
          onPreview={prev}
          onSelect={pick}
          onSplit={split}
          onMerge={merge}
          onInsert={insert}
          onMoveResize={moveResize}
          canMergePrev={canMergePrev}
          onDirect={direct}
          onSeek={seek}
          liveEnabled={liveOn}
          liveActive={liveOn && liveId === seg.id}
          onLiveEdit={liveEdit}
          onLiveToggle={liveToggle}
        />
      );
    },
    [],
  );

  const allFilteredSelected =
    filtered.length > 0 && filtered.every((s) => selectedIds && selectedIds.has(s.id));
  const selCount = selectedIds?.size ?? 0;
  const meta = (
    <>
      {filtered.length}/{segments.length}
      {selCount > 0 && (
        <span className="dub-segment-table__sel-count">
          {' '}
          · {t('segment.sel_count', { count: selCount })}
        </span>
      )}
    </>
  );

  return (
    <Table className="segment-table">
      <Table.Toolbar
        search={query}
        onSearch={setQuery}
        searchPlaceholder={t('segment.search_placeholder')}
        meta={meta}
      >
        <DubToggle
          label={t('dub.live_preview')}
          title={t('dub.live_preview_title')}
          Icon={Headphones}
          checked={livePreviewOn}
          onChange={setDubLivePreview}
        />
        {speakers.length > 1 && (
          <SearchableSelect
            menuPortal
            ariaLabel={t('segment.all_speakers')}
            value={speakerFilter}
            onChange={setSpeakerFilter}
            buttonClassName="min-h-10 rounded-lg border-0 px-3 text-sm bg-[var(--chrome-hover-bg)] text-[var(--chrome-fg)]"
            options={[
              { value: '', label: t('segment.all_speakers') },
              ...speakers.map((s) => ({ value: s, label: s })),
            ]}
          />
        )}
      </Table.Toolbar>

      <Table.Header
        className="dub-segment-table__header"
        columns={[{ key: 'text', label: t('segment.text'), flex: 1 }]}
        leading={
          <span className="dub-segment-table__select-all">
            <input
              type="checkbox"
              checked={allFilteredSelected}
              onChange={(e) => (e.target.checked ? onSelectAll(filtered) : onClearSelection())}
              title={t('segment.select_all_title')}
            />
          </span>
        }
      />

      <div className="dub-segment-table__body" ref={bodyRef}>
        {bodyHeight > 0 && (
          <List
            listRef={listRef}
            rowCount={filtered.length}
            rowHeight={rowHeight}
            rowComponent={Row}
            rowProps={rowProps}
            overscanCount={6}
            style={{ height: bodyHeight, width: '100%' }}
          />
        )}
      </div>
    </Table>
  );
}
