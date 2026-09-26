import React, { useEffect, useState } from 'react';
import {
  Cpu,
  Mic,
  MessageSquare,
  AlertTriangle,
  RefreshCw,
  Download,
  ChevronRight,
  Layers,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Badge, Button, Tabs } from '../ui';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { cn } from '@/lib/utils';
import EngineMark from './EngineMark';
import useEngineInventory, { FORCE_WAIT_TIMEOUT_MS } from './engines/useEngineInventory';
import EngineDetail from './engines/EngineDetail';
import {
  LABEL,
  LICENSE_DIALOGS,
  fmtDiskBytes,
  reasonMentionsLicense,
  runsOn,
  statusOf,
} from './engines/engineDisplay';

export { FORCE_WAIT_TIMEOUT_MS, fmtDiskBytes };

/**
 * Engine list + detail (the former "Engine Compatibility Matrix").
 *
 * One row per engine, three columns — Engine · Runs on · Status — and one
 * primary action (Use / Install). Everything else the row used to carry
 * (GPU compatibility chips, isolation, install hints, health and self-test
 * probes, install progress, setup snippet, disk usage, docs, license) lives
 * in the detail panel for the selected row; until a row is picked the panel
 * slot carries a one-line hint.
 *
 * All state and effects come from `useEngineInventory` (see its header for
 * the cross-platform contract); this file is layout only.
 *
 * Props: family, onSelect(family, id, modelId?), activeId, showFamilyTabs,
 *   onFamilyChange, reloadToken, catalogueLayout, sharedEngines, weights +
 *   downloads (the host's catalog rows and useModelDownloads result, so the
 *   panel lists the engine's weights), and the injectable api* seams.
 */
const FAMILY_META = {
  tts: { label: 'TTS', icon: Cpu },
  asr: { label: 'ASR', icon: Mic },
  llm: { label: 'LLM', icon: MessageSquare },
};

export default function EngineCompatibilityMatrix({
  family = 'tts',
  onSelect = null,
  activeId = null,
  showFamilyTabs = true,
  onFamilyChange = null,
  reloadToken = 0,
  catalogueLayout = false,
  sharedEngines = null,
  // The host's catalog rows + download machinery; the detail panel lists the
  // selected engine's weights with them. Absent → no weights section.
  weights = [],
  downloads = null,
  apiListEngines,
  apiGetEngineHealth,
  apiSelfTestEngine,
  apiListLoadedModels,
  apiUnloadModel,
  apiInstallEngine,
  apiInstallStatus,
  apiGetDiskUsage,
}) {
  const { t, i18n } = useTranslation();
  const inv = useEngineInventory({
    family,
    onSelect,
    reloadToken,
    sharedEngines,
    ...(apiListEngines && { apiListEngines }),
    ...(apiGetEngineHealth && { apiGetEngineHealth }),
    ...(apiSelfTestEngine && { apiSelfTestEngine }),
    ...(apiListLoadedModels && { apiListLoadedModels }),
    ...(apiUnloadModel && { apiUnloadModel }),
    ...(apiInstallEngine && { apiInstallEngine }),
    ...(apiInstallStatus && { apiInstallStatus }),
    ...(apiGetDiskUsage && { apiGetDiskUsage }),
  });
  const {
    data,
    loading,
    error,
    activeFamily,
    setActiveFamily,
    families,
    familyData,
    backends,
    reload,
  } = inv;

  // Which row the detail panel describes (null = none picked yet).
  const [selectedId, setSelectedId] = useState(null);
  const [diskOpenFor, setDiskOpenFor] = useState(null);
  useEffect(() => {
    setSelectedId(null); // a family switch starts from that family's active engine
    setDiskOpenFor(null);
  }, [activeFamily]);

  if (loading && !data) {
    return (
      <section className="engine-matrix flex flex-col items-center gap-2 p-4" aria-busy="true">
        <span className="text-[13px] text-muted-foreground">{t('engines.loading')}</span>
      </section>
    );
  }
  if (error && !data) {
    return (
      <section className="engine-matrix flex flex-col items-center gap-2 p-4" role="alert">
        <AlertTriangle size={14} />{' '}
        {t('engines.couldNotLoad', { message: error.message || String(error) })}
        <Button size="sm" variant="subtle" onClick={reload} leading={<RefreshCw size={11} />}>
          {t('engines.retry')}
        </Button>
      </section>
    );
  }
  if (!familyData) return null;

  const activeBackendId = activeId ?? familyData.active;
  const selected = backends.find((b) => b.id === selectedId) || null;
  const toggleSelected = (id) => setSelectedId((cur) => (cur === id ? null : id));
  const familyMeta = FAMILY_META[activeFamily] || FAMILY_META.tts;
  const TitleIcon = showFamilyTabs ? Layers : familyMeta.icon;
  const TitleHeading = catalogueLayout ? 'h2' : 'h3';
  const LicenseDialog = inv.licenseDialogFor ? LICENSE_DIALOGS[inv.licenseDialogFor] : null;

  return (
    <section className="engine-matrix flex min-h-0 flex-col gap-[18px] font-sans">
      <header className="flex flex-wrap items-center justify-between gap-3 px-[2px]">
        <TitleHeading className="m-0 inline-flex min-w-0 items-center gap-[10px] text-[length:var(--text-lg)] font-semibold text-foreground">
          <span className="inline-flex h-[30px] w-[30px] shrink-0 items-center justify-center rounded-[10px] bg-[color-mix(in_srgb,var(--chrome-accent)_11%,transparent)] text-[var(--chrome-accent)]">
            <TitleIcon size={15} aria-hidden="true" />
          </span>
          <span>
            {showFamilyTabs
              ? t('engines.matrixTitle')
              : t('engines.familyMatrixTitle', { family: familyMeta.label })}
          </span>
        </TitleHeading>
        <Button
          size="sm"
          variant="ghost"
          onClick={reload}
          loading={loading}
          leading={<RefreshCw size={11} />}
        >
          {t('engines.refresh')}
        </Button>
      </header>

      {showFamilyTabs && families.length > 1 && (
        <Tabs
          size="sm"
          variant="underline"
          className="w-fit gap-[28px]"
          value={activeFamily}
          onChange={(f) => {
            setActiveFamily(f);
            onFamilyChange?.(f);
          }}
          items={families.map((f) => {
            const FamilyIcon = FAMILY_META[f].icon;
            return {
              id: f,
              title: t('engines.activeEngine', {
                family: FAMILY_META[f].label,
                engine: data[f].active,
              }),
              label: (
                <span className="engine-matrix__tab-label inline-flex min-w-0 items-center justify-center gap-[6px] whitespace-nowrap px-[5px] py-[1px] leading-none">
                  <FamilyIcon size={12} className="shrink-0 opacity-70" aria-hidden="true" />
                  <span className="engine-matrix__tab-family text-[11px] font-bold tracking-[0.03em]">
                    {FAMILY_META[f].label}
                  </span>
                </span>
              ),
            };
          })}
        />
      )}

      <p
        className="m-0 max-w-[720px] px-[2px] text-[12px] leading-[1.55] text-muted-foreground"
        data-testid={`family-desc-${activeFamily}`}
      >
        {t(`engines.familyDesc_${activeFamily}`)}
      </p>

      <div className="grid min-w-0 grid-cols-1 items-start gap-[24px] @min-[1100px]/catalogue-shell:grid-cols-[minmax(0,1fr)_380px]">
        <Table
          className="table-fixed border-collapse font-sans"
          aria-label={t('engines.engineCompatLabel', { family: activeFamily })}
          data-testid="engine-list"
        >
          <TableHeader className="[&_tr]:border-border [&_tr]:hover:bg-transparent">
            <TableRow role="row">
              <TableHead scope="col" className={cn('h-8 px-3', LABEL)}>
                {t('engines.colEngine')}
              </TableHead>
              <TableHead scope="col" style={{ width: 104 }} className={cn('h-8 px-3', LABEL)}>
                {t('engines.colRunsOn')}
              </TableHead>
              <TableHead scope="col" style={{ width: 132 }} className={cn('h-8 px-3', LABEL)}>
                {t('engines.status')}
              </TableHead>
              <TableHead
                scope="col"
                style={{ width: 132 }}
                className={cn('h-8 px-3 text-right', LABEL)}
              >
                <span className="sr-only">{t('engines.colActions')}</span>
              </TableHead>
            </TableRow>
          </TableHeader>
          <TableBody data-testid="engine-list-scroll">
            {backends.map((b, index) => {
              const isActive = b.id === activeBackendId;
              const isSelected = selected?.id === b.id;
              const resident = inv.loadedByEngine[b.id] || null;
              const install = inv.installByEngine[b.id] || null;
              const installJob = install?.job || null;
              const installRunning = installJob?.state === 'running';
              const device = runsOn(t, b);
              const status = statusOf(t, b, install);
              const panelId = `engine-detail-${b.id}`;
              const caption =
                index === 0 || (backends[index - 1]?.available && !b.available)
                  ? b.available
                    ? t('engines.sectionReady')
                    : t('engines.sectionMore')
                  : null;
              return (
                <React.Fragment key={b.id}>
                  {caption && (
                    <TableRow role="row" className="border-0 hover:bg-transparent">
                      <TableCell colSpan={4} className={cn('px-3 pb-1 pt-4', LABEL)}>
                        {caption}
                      </TableCell>
                    </TableRow>
                  )}
                  <TableRow
                    role="row"
                    data-engine-id={b.id}
                    data-state={isSelected ? 'selected' : undefined}
                    aria-selected={isSelected}
                    onClick={() => setSelectedId(b.id)}
                    className={cn(
                      'group cursor-pointer border-border/70',
                      isActive && 'shadow-[inset_2px_0_0_var(--chrome-accent)]',
                    )}
                  >
                    <TableCell className="px-3 py-[9px]">
                      <span className="flex min-w-0 items-center gap-2">
                        <EngineMark
                          id={b.id}
                          size={18}
                          className={cn('shrink-0', !b.available && 'opacity-60')}
                        />
                        <button
                          type="button"
                          className={cn(
                            'min-w-0 cursor-pointer truncate border-0 bg-transparent p-0 text-left text-sm font-medium leading-[1.3] hover:text-accent focus-visible:outline-none focus-visible:shadow-[var(--focus-ring)]',
                            b.available ? 'text-foreground' : 'text-muted-foreground',
                          )}
                          title={b.display_name}
                          aria-expanded={isSelected}
                          aria-controls={panelId}
                          data-testid={`why-toggle-${b.id}`}
                          onClick={(e) => {
                            e.stopPropagation();
                            toggleSelected(b.id);
                          }}
                        >
                          {b.display_name}
                        </button>
                        {isActive && (
                          <Badge tone="brand" size="xs" className="shrink-0">
                            {t('engines.active')}
                          </Badge>
                        )}
                        {resident && (
                          <Badge
                            tone="info"
                            size="xs"
                            className="shrink-0"
                            title={t('engines.inMemoryTitle')}
                            data-testid={`resident-${b.id}`}
                          >
                            {t('engines.inMemory')}
                          </Badge>
                        )}
                        {activeFamily === 'tts' && b.supports_cloning && (
                          <span
                            className="inline-flex shrink-0 text-muted-foreground"
                            title={t('engines.cloneCapableTitle')}
                            data-testid={`clone-badge-${b.id}`}
                          >
                            <Mic size={11} aria-hidden="true" />
                            <span className="sr-only">{t('engines.cloneCapable')}</span>
                          </span>
                        )}
                        <ChevronRight
                          size={12}
                          aria-hidden="true"
                          className={cn(
                            'ml-auto shrink-0 text-muted-foreground opacity-0 transition-opacity group-hover:opacity-60',
                            isSelected && 'opacity-60',
                          )}
                        />
                      </span>
                    </TableCell>
                    <TableCell
                      className={cn(
                        'px-3 py-[9px] font-mono text-[11px] uppercase',
                        device.muted ? 'text-muted-foreground/70' : 'text-foreground',
                      )}
                      title={b.routing_reason || undefined}
                      data-testid={`runs-on-${b.id}`}
                    >
                      {device.text}
                    </TableCell>
                    <TableCell
                      className={cn('px-3 py-[9px] text-xs', status.cls)}
                      title={b.available ? b.routing_reason || undefined : b.reason || undefined}
                    >
                      {status.text}
                    </TableCell>
                    <TableCell
                      className="px-3 py-[6px] text-right"
                      onClick={(e) => e.stopPropagation()}
                    >
                      {onSelect && b.available && !isActive && (
                        <Button
                          size="sm"
                          variant="subtle"
                          onClick={() => inv.selectEngine(b.id)}
                          aria-label={t('engines.ariaUse', { engine: b.display_name })}
                        >
                          {t('engines.use')}
                        </Button>
                      )}
                      {/* Hidden while a license review is all that is left: the
                          engine is installed, and Accept (in the panel) is next. */}
                      {!b.available && b.one_click_install && !reasonMentionsLicense(b.reason) && (
                        <Button
                          size="sm"
                          variant="subtle"
                          onClick={() => {
                            setSelectedId(b.id); // progress renders in the panel
                            inv.startInstall(b.id);
                          }}
                          disabled={installRunning}
                          loading={installRunning}
                          leading={!installRunning && <Download size={11} />}
                          data-testid={`install-${b.id}`}
                          aria-label={t('engines.installAria', { engine: b.display_name })}
                        >
                          {installRunning
                            ? t('engines.installing')
                            : installJob?.state === 'failed'
                              ? t('engines.retryInstall')
                              : t('engines.install')}
                        </Button>
                      )}
                    </TableCell>
                  </TableRow>
                </React.Fragment>
              );
            })}
            {backends.length === 0 && (
              <TableRow role="row">
                <TableCell
                  colSpan={4}
                  className="p-6 text-center text-[13px] text-muted-foreground"
                >
                  {t('engines.noBackends')}
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>

        {!selected && (
          <aside
            data-testid="engine-detail-empty"
            className="flex min-h-[120px] items-center justify-center rounded-[14px] border border-dashed border-border p-[16px] text-center text-xs leading-[1.5] text-muted-foreground"
          >
            {t('engines.selectHint')}
          </aside>
        )}
        {selected && (
          <EngineDetail
            b={selected}
            family={activeFamily}
            isActive={selected.id === activeBackendId}
            inv={inv}
            onSelect={onSelect}
            t={t}
            i18n={i18n}
            weights={weights}
            downloads={downloads}
            diskOpen={diskOpenFor === selected.id}
            onToggleDisk={() => {
              const next = diskOpenFor === selected.id ? null : selected.id;
              setDiskOpenFor(next);
              if (next && !inv.diskByEngine[selected.id]) inv.loadDiskUsage(selected.id);
            }}
          />
        )}
      </div>

      {LicenseDialog && (
        <LicenseDialog
          open
          onClose={() => inv.setLicenseDialogFor(null)}
          onAccepted={() => {
            inv.setLicenseDialogFor(null);
            reload();
          }}
        />
      )}
    </section>
  );
}
