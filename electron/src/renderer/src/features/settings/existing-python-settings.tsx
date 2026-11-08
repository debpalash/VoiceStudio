import { useEffect, useState } from 'react';
import { CheckCircle2Icon, FolderOpenIcon, Loader2Icon, RotateCcwIcon, SearchIcon, TerminalIcon } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui/button';
import { getBridge } from '@/components/bridge';
import { SettingsRow, SettingsSection } from './settings-layout';

type DiscoveryResult = Awaited<ReturnType<NonNullable<ReturnType<typeof getBridge>>['backend']['discoverPythonRuntimes']>>;
type Candidate = DiscoveryResult['candidates'][number];
type Validation = Awaited<ReturnType<NonNullable<ReturnType<typeof getBridge>>['backend']['choosePythonRuntime']>>;
type RepairReview = Awaited<ReturnType<NonNullable<ReturnType<typeof getBridge>>['backend']['planPythonRepair']>>;

export function ExistingPythonSettings() {
  const { t } = useTranslation();
  const [selection, setSelection] = useState<{ pythonPath: string } | null>(null);
  const [overrideActive, setOverrideActive] = useState(false);
  const [staged, setStaged] = useState<Validation | null>(null);
  const [repair, setRepair] = useState<RepairReview | null>(null);
  const [installConsent, setInstallConsent] = useState(false);
  const [installing, setInstalling] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [searching, setSearching] = useState(false);
  const [discovery, setDiscovery] = useState<DiscoveryResult | null>(null);

  const refresh = async () => {
    const backend = getBridge()?.backend;
    if (!backend) return;
    const result = await backend.getPythonRuntime();
    setSelection(result.selection);
    setOverrideActive(result.overrideActive);
  };
  useEffect(() => { void refresh(); }, []);

  const stage = (result: Validation) => {
    setRepair(null);
    setInstallConsent(false);
    setStaged(result.pythonPath ? result : null);
    setMessage(result.ok
      ? t('settings.python_runtime_ready')
      : result.missingModules?.length
        ? t('settings.python_runtime_missing', { modules: result.missingModules.join(', ') })
        : t('settings.python_runtime_invalid', { detail: result.detail ?? '' }));
  };

  const reviewInstall = async () => {
    const backend = getBridge()?.backend;
    if (!backend || !staged?.pythonPath || busy || searching) return;
    setBusy(true);
    setMessage(null);
    setInstallConsent(false);
    setRepair(null);
    try {
      if (!staged.token) throw new Error('Python runtime selection expired; choose it again');
      const result = await backend.planPythonRepair(staged.token);
      if (result.ok) {
        stage(result);
      } else if (result.repairToken && result.repairPlan?.packages.length) {
        setRepair(result);
      } else {
        stage(result);
        setMessage(t('settings.python_runtime_repair_blocked'));
      }
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  };

  const install = async () => {
    const backend = getBridge()?.backend;
    if (!backend || !repair?.repairToken || !repair.repairPlan || !installConsent || busy || searching) return;
    setBusy(true);
    setInstalling(true);
    setMessage(null);
    const { repairToken, repairPlan } = repair;
    setRepair(null);
    setInstallConsent(false);
    try {
      const result = await backend.installPythonRepair({
        token: repairToken, pythonPath: repairPlan.interpreter, packages: [...repairPlan.packages],
      });
      stage(result);
      if (!result.ok && !result.canceled) setMessage(`${t('settings.python_runtime_repair_failed')}${result.outputTail ? `\n${result.outputTail}` : ''}`);
    } catch (error) {
      setMessage(`${t('settings.python_runtime_repair_failed')}\n${error instanceof Error ? error.message : String(error)}`);
    } finally {
      setInstalling(false);
      setBusy(false);
    }
  };

  const search = async () => {
    if (busy || searching) return;
    const backend = getBridge()?.backend;
    if (!backend) return;
    setMessage(null);
    setSearching(true);
    setDiscovery(null);
    setStaged(null);
    setRepair(null);
    setInstallConsent(false);
    try {
      setDiscovery(await backend.discoverPythonRuntimes());
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setSearching(false);
    }
  };

  const useCandidate = async (candidate: Candidate) => {
    if (busy || searching) return;
    const backend = getBridge()?.backend;
    if (!backend) return;
    setMessage(null);
    setStaged(null);
    setRepair(null);
    setInstallConsent(false);
    setBusy(true);
    try {
      const result = await backend.useDiscoveredPythonRuntime(candidate.pythonPath);
      stage({ ...result, pythonPath: result.pythonPath ?? candidate.pythonPath, version: result.version ?? candidate.version });
    } catch (error) {
      setMessage(error instanceof Error && /not discovered|search again/i.test(error.message)
        ? t('settings.python_runtime_search_again')
        : error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  };

  const choose = async () => {
    if (busy || searching) return;
    setMessage(null);
    const backend = getBridge()?.backend;
    if (!backend) return;
    setBusy(true);
    setStaged(null);
    setRepair(null);
    setInstallConsent(false);
    try {
      const result = await backend.choosePythonRuntime(t('settings.python_runtime_path_title'));
      if (result.canceled) return;
      stage(result);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  };

  const confirm = async () => {
    const backend = getBridge()?.backend;
    if (!backend || !staged?.ok || !staged.token || busy || searching) return;
    setBusy(true);
    setMessage(null);
    try {
      const result = await backend.confirmPythonRuntime(staged.token);
      if (!result.ok) {
        setStaged(null);
        setMessage(t('settings.python_runtime_invalid', { detail: result.detail ?? '' }));
        return;
      }
      setStaged(null);
      await refresh();
      setMessage(t('settings.python_runtime_saved', { version: result.version ?? staged.version ?? '' }));
    } catch (error) {
      setStaged(null);
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  };

  const reset = async () => {
    if (busy || searching) return;
    const backend = getBridge()?.backend;
    if (!backend) return;
    setBusy(true);
    try {
      await backend.useManagedPython();
      setSelection(null);
      setStaged(null);
      setMessage(t('settings.python_runtime_managed_saved'));
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  };

  const warningText = discovery?.warnings.some((warning) => warning.reason === 'permission_denied')
    ? t('settings.python_runtime_permission_warning')
    : discovery?.partial ? t('settings.python_runtime_partial_warning') : null;

  return (
    <SettingsSection icon={TerminalIcon} title={t('settings.python_runtime_title')}>
      {overrideActive && (
        <SettingsRow id="python-runtime-override" title={t('settings.python_runtime_override_title')} description={t('settings.python_runtime_override_help')}>
          <span className="text-xs text-amber-600">{t('settings.python_runtime_override_active')}</span>
        </SettingsRow>
      )}
      <SettingsRow id="python-runtime" title={t('settings.python_runtime_path_title')} description={selection?.pythonPath ?? t('settings.python_runtime_managed_help')}>
        <div className="flex flex-wrap items-center gap-2">
          {selection && <CheckCircle2Icon aria-label={t('settings.python_runtime_valid')} className="size-4 text-emerald-600" />}
          <Button data-testid="python-runtime-quick-search" size="sm" variant="outline" disabled={busy || searching} onClick={() => void search()}>
            {searching ? <Loader2Icon className="mr-2 size-4 animate-spin" /> : <SearchIcon className="mr-2 size-4" />}
            {searching ? t('settings.python_runtime_searching') : t('settings.python_runtime_quick_search')}
          </Button>
          <Button size="sm" variant="outline" disabled={busy || searching} onClick={() => void choose()}>
            <FolderOpenIcon className="mr-2 size-4" />{t('settings.python_runtime_choose')}
          </Button>
          {selection && <Button size="sm" variant="ghost" disabled={busy || searching} onClick={() => void reset()}>
            <RotateCcwIcon className="mr-2 size-4" />{t('settings.python_runtime_use_managed')}
          </Button>}
        </div>
      </SettingsRow>
      <div className="px-4 pb-2 text-xs text-muted-foreground">{t('settings.python_runtime_search_scope')}</div>
      {searching && <div className="px-4 py-2 text-xs text-muted-foreground" role="status">{t('settings.python_runtime_searching')}</div>}
      {discovery && (
        <div className="max-h-56 overflow-y-auto px-4 pb-2" aria-live="polite">
          {warningText && <p className="pb-2 text-xs text-amber-600">{warningText}</p>}
          {discovery.partial && !warningText && <p className="pb-2 text-xs text-amber-600">{t('settings.python_runtime_partial_warning')}</p>}
          {discovery.candidates.length === 0 ? (
            <p className="text-xs text-muted-foreground">{t('settings.python_runtime_empty')}</p>
          ) : (
            <div className="space-y-1">
              {discovery.candidates.map((candidate) => (
                <button
                  key={candidate.pythonPath}
                  type="button"
                  data-testid="python-runtime-candidate"
                  data-python-path={candidate.pythonPath}
                  disabled={busy}
                  onClick={() => void useCandidate(candidate)}
                  className="block w-full rounded-md border px-3 py-2 text-left text-xs hover:bg-muted disabled:opacity-50"
                >
                  <span className="block break-all">{candidate.pythonPath}</span>
                  {candidate.version && <span className="text-muted-foreground">{candidate.version}</span>}
                </button>
              ))}
            </div>
          )}
        </div>
      )}
      {staged && (
        <div className="space-y-2 px-4 py-3 text-xs" data-testid="python-runtime-staged">
          <p className="break-all">{staged.pythonPath}</p>
          {staged.version && <p>{staged.version}</p>}
          {staged.ok && <Button data-testid="python-runtime-confirm" size="sm" disabled={busy || searching || !staged.token} onClick={() => void confirm()}>{t('settings.python_runtime_confirm')}</Button>}
          {!staged.ok && staged.missingModules?.length ? (
            staged.repairPlan ? <Button size="sm" variant="outline" disabled={busy || searching} onClick={() => void reviewInstall()}>{t('settings.python_runtime_review_install')}</Button>
              : <p>{t('settings.python_runtime_repair_blocked')}</p>
          ) : null}
          <Button size="sm" variant="outline" disabled={busy || searching} onClick={() => void choose()}>{t('settings.python_runtime_choose_another')}</Button>
        </div>
      )}
      {repair?.repairPlan && (
        <div className="space-y-2 px-4 py-3 text-xs" data-testid="python-runtime-repair-plan">
          <p className="break-all">{repair.repairPlan.interpreter}</p>
          <ul className="list-inside list-disc">{repair.repairPlan.packages.map((pkg) => <li key={pkg}>{pkg}</li>)}</ul>
          <label className="flex items-start gap-2">
            <input type="checkbox" checked={installConsent} disabled={busy || searching} onChange={(event) => setInstallConsent(event.target.checked)} />
            {t('settings.python_runtime_install_consent')}
          </label>
          <div className="flex flex-wrap gap-2">
            <Button size="sm" disabled={busy || searching || !installConsent} onClick={() => void install()}>{t('settings.python_runtime_install_confirm')}</Button>
            <Button size="sm" variant="outline" disabled={busy || searching} onClick={() => { setRepair(null); setInstallConsent(false); }}>{t('common.cancel')}</Button>
          </div>
        </div>
      )}
      {busy && <div className="px-4 py-2 text-xs text-muted-foreground" role="status">{installing ? t('settings.python_runtime_installing') : t('settings.python_runtime_validating')}</div>}
      {message && <div className="px-4 py-3 text-xs text-muted-foreground" role="status">{message}</div>}
    </SettingsSection>
  );
}
