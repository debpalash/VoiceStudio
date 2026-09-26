import React, { useEffect, useState } from 'react';
import { Keyboard } from 'lucide-react';
import { toast } from 'react-hot-toast';
import { Trans, useTranslation } from 'react-i18next';
import { Button } from '../../ui';
import { useEffectiveDictationShortcut } from '../../hooks/useEffectiveDictationShortcut';
import { SettingsSection, SettingRow } from './primitives';
import { isTauri } from './native';

// Convert a KeyboardEvent into a tauri-plugin-global-shortcut accelerator
// string, e.g. "CmdOrCtrl+Shift+Space". Returns null when only modifiers
// are held (the user hasn't picked a "real" key yet).
function keyEventToAccelerator(e) {
  const isMacLike =
    typeof navigator !== 'undefined' && /Mac|iPad|iPhone|iPod/.test(navigator.platform || '');
  const mods = [];
  if (e.metaKey) mods.push(isMacLike ? 'Cmd' : 'Super');
  if (e.ctrlKey) mods.push('Ctrl');
  if (e.altKey) mods.push('Alt');
  if (e.shiftKey) mods.push('Shift');

  // e.code is the physical key — already in the shape tauri expects for
  // Letter/Digit/Function keys ("KeyA", "Digit1", "F5"). Strip the prefix
  // so we get "A" / "1" / "F5" which matches the accelerator grammar.
  let key = e.code;
  if (!key) return null;
  if (key.startsWith('Key')) key = key.slice(3);
  else if (key.startsWith('Digit')) key = key.slice(5);
  // Skip pure modifier keys — we want the user to pick a real trigger.
  if (/^(Meta|Control|Alt|Shift|OS)(Left|Right)?$/.test(key)) return null;

  if (mods.length === 0) return null;
  return [...mods, key].join('+');
}

// A pure modifier press means the user is still building the chord — stay
// quiet. Anything else that fails to produce an accelerator (a bare letter,
// F5, Space…) is a real rejection and deserves visible feedback.
function isPureModifierEvent(e) {
  return /^(Meta|Control|Alt|Shift|OS)/.test(e.key || '');
}

export default function HotkeyTab() {
  const { t } = useTranslation();
  const [recording, setRecording] = useState(false);
  const [pending, setPending] = useState('');
  // True after a modifier-less press while recording — drives the inline
  // "add a modifier" feedback instead of listening forever in silence.
  const [rejected, setRejected] = useState(false);
  const [saving, setSaving] = useState(false);
  const tauri = isTauri();
  const { info: shortcut, error: shortcutError } = useEffectiveDictationShortcut(tauri);
  const current = shortcut.accelerator;

  useEffect(() => {
    if (!shortcutError) return;
    toast.error(
      t('settings.shortcut_load_failed', {
        message: shortcutError?.message || shortcutError,
      }),
    );
  }, [shortcutError, t]);

  // While recording, swallow keystrokes globally and convert the next real
  // press into an accelerator string. Escape cancels; losing window focus
  // cancels too so a stray click outside doesn't leave a global
  // key-swallowing listener armed forever.
  useEffect(() => {
    if (!recording) return;
    const onKeyDown = (e) => {
      e.preventDefault();
      e.stopPropagation();
      if (e.key === 'Escape') {
        setRecording(false);
        setPending('');
        setRejected(false);
        return;
      }
      const accel = keyEventToAccelerator(e);
      if (accel) {
        setPending(accel);
        setRecording(false);
        setRejected(false);
        return;
      }
      if (!isPureModifierEvent(e)) setRejected(true);
    };
    const onBlur = () => {
      setRecording(false);
      setRejected(false);
    };
    window.addEventListener('keydown', onKeyDown, true);
    window.addEventListener('blur', onBlur);
    return () => {
      window.removeEventListener('keydown', onKeyDown, true);
      window.removeEventListener('blur', onBlur);
    };
  }, [recording]);

  const save = async () => {
    if (!pending || pending === current) return;
    setSaving(true);
    try {
      const { invoke } = await import('@tauri-apps/api/core');
      const saved = await invoke('set_dictation_shortcut', { accelerator: pending });
      setPending('');
      toast.success(t('settings.shortcut_set', { shortcut: saved.display || saved.accelerator }));
    } catch (e) {
      // Common cause: the OS or another app already owns the combo. Surface
      // the raw error so the user can pick something else.
      toast.error(t('settings.shortcut_register_failed', { message: e?.message || e }));
    } finally {
      setSaving(false);
    }
  };

  const resetDefault = async () => {
    setSaving(true);
    try {
      const { invoke } = await import('@tauri-apps/api/core');
      await invoke('set_dictation_shortcut', {
        accelerator: 'CmdOrCtrl+Shift+Space',
      });
      setPending('');
      toast.success(t('settings.shortcut_reset'));
    } catch (e) {
      toast.error(t('settings.shortcut_reset_failed', { message: e?.message || e }));
    } finally {
      setSaving(false);
    }
  };

  return (
    <SettingsSection icon={Keyboard} title={t('settings.shortcut')}>
      {!tauri && (
        <p className="settings-prose m-0 mb-[var(--space-5)] font-sans text-[var(--text-md)] leading-[1.6] text-[var(--chrome-fg-muted)]">
          <Trans i18nKey="capture.desc" components={{ 1: <kbd /> }} />
        </p>
      )}

      <SettingRow title={t('capture.active_shortcut')} control={shortcut.display || '—'} mono />
      <SettingRow
        title={recording ? t('capture.press_key') : t('capture.new_shortcut')}
        hint={<Trans i18nKey="capture.desc_detail" components={{ 1: <code />, 2: <code /> }} />}
        control={
          recording
            ? (rejected && t('capture.needs_modifier')) || t('capture.listening')
            : pending || '—'
        }
        mono
      />

      <div className="settings-actions-row mt-[var(--space-5)] flex flex-wrap gap-[var(--space-4)]">
        <Button
          size="sm"
          variant="subtle"
          onClick={() => {
            // Toggle: while recording, the same button cancels (Esc still
            // works too) — re-clicking must not silently re-arm the recorder.
            setPending('');
            setRejected(false);
            setRecording(!recording);
          }}
          disabled={!tauri || saving}
          leading={<Keyboard size={12} />}
        >
          {recording ? t('common.cancel') : t('capture.record_shortcut')}
        </Button>
        <Button
          size="sm"
          onClick={save}
          disabled={!tauri || !pending || pending === current}
          loading={saving}
        >
          {t('capture.save')}
        </Button>
        <Button size="sm" variant="subtle" onClick={resetDefault} disabled={!tauri || saving}>
          {t('capture.reset_default')}
        </Button>
      </div>
    </SettingsSection>
  );
}
