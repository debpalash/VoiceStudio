import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { UploadIcon, XIcon } from 'lucide-react';
import { useProfiles } from '@/hooks/use-profiles';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { importScript, SCRIPT_ACCEPT } from '@/lib/import-script';
import { LANGUAGES } from '@/lib/languages';
// Shared application controls are JavaScript; keep their established voice/value contracts.
// @ts-expect-error shared JSX component has no declaration file
import VoiceSelector from '@shared/components/VoiceSelector';
// @ts-expect-error shared JSX component has no declaration file
import SearchableSelect from '@shared/components/SearchableSelect';
import { deleteWorkflowArtifacts, saveWorkflowMedia } from './workflow-run-store';
import type { WorkflowStep } from './workflow-model';

export function WorkflowInputs({ step, onChange }: { step: WorkflowStep; onChange(change: Partial<WorkflowStep>): void }) {
  const { t } = useTranslation();
  const profiles = useProfiles();
  const input = useRef<HTMLInputElement>(null);
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  const [error, setError] = useState(false);
  const [importing, setImporting] = useState(false);
  if (step.kind === 'speak' || step.kind === 'convert') return <>
    <label>{t('convert.target_voice')}</label>
    <VoiceSelector value={step.voiceId || ''} onChange={(voiceId: string) => onChange({ voiceId })}
      profiles={profiles.data || []} gallery={false} engineDefault={false} menuPortal menuClassName="workflow-shared-select" buttonClassName="workflow-select-trigger"
      ariaLabel={t('convert.target_voice')} placeholder={t('convert.pick_voice')} />
    {step.kind === 'speak' && <><label>{t('clone.language')}</label>
    <SearchableSelect value={step.language || 'Auto'} onChange={(language: string) => onChange({ language })}
      options={LANGUAGES.map((value) => ({ value, label: value === 'Auto' ? t('clone.auto') : value }))}
      ariaLabel={t('clone.language')} menuPortal menuClassName="workflow-shared-select" buttonClassName="workflow-select-trigger" />
    <label htmlFor="workflow-speed">{t('clone.speed')}</label>
    <Input id="workflow-speed" type="number" min={0.5} max={2} step={0.05} value={step.speed ?? 1}
      onChange={(event) => { const speed = Number(event.target.value); if (Number.isFinite(speed)) onChange({ speed: Math.max(0.5, Math.min(2, speed)) }); }} />
    <p className="text-xs text-muted-foreground">{t('workflowRun.normalized')}</p></>}
  </>;
  if (step.kind === 'transcribe') return <><label>{t('clone.language')}</label><p className="text-xs text-muted-foreground">{t('clone.auto')}</p></>;
  if (step.kind === 'translate') return <>
    {step.kind === 'translate' && <>
      <label>{t('workflowRun.source_language')}</label>
      <SearchableSelect value={step.sourceLanguage || ''} onChange={(sourceLanguage: string) => onChange({ sourceLanguage })}
        options={LANGUAGES.filter((language) => language !== 'Auto')}
        ariaLabel={t('workflowRun.source_language')} menuPortal menuClassName="workflow-shared-select" buttonClassName="workflow-select-trigger" />
      <label>{t('twilioIntegration.engine')}</label>
      <SearchableSelect value={step.provider || 'argos'} onChange={(provider: 'argos' | 'nllb') => onChange({ provider })}
        options={[{ value: 'argos', label: 'Argos' }, { value: 'nllb', label: 'NLLB-200' }]}
        ariaLabel={t('twilioIntegration.engine')} menuPortal menuClassName="workflow-shared-select" buttonClassName="workflow-select-trigger" />
      <p className="text-xs text-muted-foreground">{t('workflowRun.local_translation')}</p>
    </>}
    <label>{t('clone.language')}</label>
    <SearchableSelect value={step.language || 'Auto'} onChange={(language: string) => onChange({ language })}
      options={LANGUAGES.filter((language) => language !== 'Auto').map((value) => ({ value, label: value === 'Auto' ? t('clone.auto') : value }))}
      ariaLabel={t('clone.language')} menuPortal menuClassName="workflow-shared-select" buttonClassName="workflow-select-trigger" />
  </>;
  if (step.kind === 'audio') return <>
    <p className="text-xs text-muted-foreground">{t('workflowRun.audio_hint')}</p>
    {step.media?.map((file) => <div key={file.id} className="flex items-center justify-between text-xs gap-2">
      <span className="truncate">{file.name}</span><Button size="icon-xs" variant="ghost" disabled={importing} aria-label={t('common.delete')}
        onClick={() => onChange({ media: step.media!.filter((entry) => entry.id !== file.id) })}><XIcon /></Button>
    </div>)}
    <input ref={input} type="file" multiple accept=".wav,.mp3,.m4a,.ogg,.flac,.webm" className="hidden" aria-label={t('workflowRun.add_audio')} onChange={async (event) => {
      const files = Array.from(event.target.files || []); event.target.value = '';
      setError(false); setImporting(true);
      const uploaded: string[] = [];
      try {
        if (files.length + (step.media?.length || 0) > 50 || files.some((file) => !file.size || file.size > 64 * 1024 * 1024))
          throw new Error('limit');
        const media = [];
        for (const file of files) {
          const id = crypto.randomUUID();
          await saveWorkflowMedia(id, file);
          uploaded.push(id);
          media.push({ id, name: file.name, type: file.type, size: file.size });
        }
        if (!mounted.current) throw new Error('cancelled');
        onChange({ media: [...(step.media || []), ...media] });
      } catch { await deleteWorkflowArtifacts({ media: uploaded, runs: [] }).catch(() => {}); if (mounted.current) setError(true); }
      finally { setImporting(false); }
    }} />
    <Button variant="outline" disabled={importing} onClick={() => input.current?.click()}><UploadIcon />{t('workflowRun.add_audio')}</Button>
    {error && <p role="alert" className="text-xs text-destructive">{t('workflowRun.invalid_media')}</p>}
  </>;
  if (step.kind === 'normalize') return <>
    <label htmlFor="workflow-peak">{t('workflowRun.peak')}</label>
    <Input id="workflow-peak" type="number" min={-24} max={-1} step={1} value={step.targetDb ?? -2}
      onChange={(event) => { const targetDb = Number(event.target.value); if (Number.isFinite(targetDb)) onChange({ targetDb: Math.max(-24, Math.min(-1, targetDb)) }); }} />
  </>;
  if (step.kind !== 'start') return null;
  return <>
    <label htmlFor="workflow-scripts">{t('workflowRun.scripts')}</label>
    <p className="text-xs text-muted-foreground">{t('workflowRun.scripts_hint')}</p>
    {!step.scripts?.length && <Textarea id="workflow-scripts" value={step.text} rows={9} maxLength={20_000}
      onChange={(event) => onChange({ text: event.target.value })} />}
    {step.scripts?.map((script, index) => <div key={index} className="flex items-center justify-between text-xs gap-2">
      <span className="truncate">{script.name}</span><Button size="icon-xs" variant="ghost" aria-label={t('common.delete')} onClick={() => onChange({ scripts: step.scripts!.filter((_, i) => i !== index) })}><XIcon /></Button>
    </div>)}
    <input ref={input} type="file" multiple accept={SCRIPT_ACCEPT} className="hidden" aria-label={t('workflowRun.import')} onChange={async (event) => {
      const files = Array.from(event.target.files || []); event.target.value = '';
      setError(false); setImporting(true);
      try {
        if (files.length + (step.scripts?.length ?? 0) > 50) throw new Error('limit');
        const scripts = [];
        for (const file of files) {
          const text = await importScript(file);
          if (text.length > 20_000) throw new Error('limit');
          scripts.push({ name: file.name.replace(/\.[^.]+$/, ''), text });
        }
        onChange({ scripts: [...(step.scripts || []), ...scripts] });
      } catch { setError(true); }
      finally { setImporting(false); }
    }} />
    <Button variant="outline" disabled={importing} onClick={() => input.current?.click()}><UploadIcon />{t('workflowRun.import')}</Button>
    {error && <p role="alert" className="text-xs text-destructive">{t('workflowRun.invalid_scripts')}</p>}
  </>;
}
