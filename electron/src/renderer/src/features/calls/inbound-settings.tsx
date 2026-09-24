import { useEffect, useId, useState } from 'react';
import { Link } from '@tanstack/react-router';
import { useTranslation } from 'react-i18next';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { describeError } from '@/lib/api/client';
import { saveCallSettings, type CallSettings, type InboundMode } from '@/lib/api/calls';
import { cn } from '@/lib/utils';

/** Minimal inbound controls; the number and credentials live on the Twilio page. */
export function InboundSettings({
  open,
  onOpenChange,
  settings,
  onSaved,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  settings: CallSettings | undefined;
  onSaved: (settings: CallSettings) => void;
}) {
  const { t } = useTranslation();
  const ids = useId();
  const [mode, setMode] = useState<InboundMode>('greeting');
  const [brief, setBrief] = useState('');
  const [saving, setSaving] = useState(false);
  useEffect(() => {
    if (open && settings) {
      setMode(settings.inbound_mode);
      setBrief(settings.inbound_brief ?? '');
    }
  }, [open, settings]);
  const save = async () => {
    setSaving(true);
    try {
      onSaved(await saveCallSettings({ inbound_mode: mode, inbound_brief: brief }));
      toast.success(t('nav.saved'));
      onOpenChange(false);
    } catch (error) {
      toast.error(describeError(error));
    } finally {
      setSaving(false);
    }
  };
  const modes: Array<[InboundMode, string, string]> = [
    ['greeting', 'calls.inbound_greeting', 'calls.inbound_greeting_desc'],
    ['agent', 'calls.inbound_agent', 'calls.inbound_agent_desc'],
  ];
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t('calls.inbound_title')}</DialogTitle>
          <DialogDescription>{t('calls.inbound_intro')}</DialogDescription>
        </DialogHeader>
        <fieldset disabled={!settings || saving} className="space-y-3">
          <legend className="sr-only">{t('calls.inbound_mode')}</legend>
          <div
            role="radiogroup"
            aria-label={t('calls.inbound_mode')}
            className="grid gap-2 sm:grid-cols-2"
          >
            {modes.map(([value, label, description]) => (
              <label
                key={value}
                className={cn(
                  'flex cursor-pointer gap-2 rounded-lg border p-3 text-sm has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-ring',
                  mode === value ? 'border-primary/60 bg-primary/5' : 'border-border',
                )}
              >
                <input
                  type="radio"
                  name={`${ids}-mode`}
                  value={value}
                  checked={mode === value}
                  onChange={() => setMode(value)}
                  className="mt-1 accent-[var(--primary)]"
                />
                <span>
                  <span className="block font-medium">{t(label)}</span>
                  <span className="block text-xs text-muted-foreground">{t(description)}</span>
                </span>
              </label>
            ))}
          </div>
          {mode === 'agent' && (
            <div className="space-y-1.5">
              <label htmlFor={`${ids}-brief`} className="text-sm font-medium">
                {t('calls.inbound_brief')}
              </label>
              <Textarea
                id={`${ids}-brief`}
                rows={4}
                value={brief}
                placeholder={t('calls.inbound_brief_placeholder')}
                onChange={(event) => setBrief(event.target.value)}
              />
            </div>
          )}
          <p className="text-xs text-muted-foreground">
            {t('calls.inbound_twilio_note')}{' '}
            <Link
              to="/integrations/$slug"
              params={{ slug: 'twilio' }}
              className="text-primary underline-offset-2 hover:underline"
            >
              {t('calls.setup_twilio')}
            </Link>
          </p>
        </fieldset>
        <DialogFooter>
          <DialogClose render={<Button variant="outline" />}>{t('common.cancel')}</DialogClose>
          <Button disabled={!settings || saving} aria-busy={saving} onClick={() => void save()}>
            {t(saving ? 'common.saving' : 'common.save')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
