import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import en from '@/i18n/locales/en.json';
import { describeError } from '@/lib/api/client';
import {
  WORKFLOW_STORAGE_KEY,
  parseWorkflowLibrary,
  removedWorkflowData,
  repairWorkflowName,
  type WorkflowLibrary,
} from './workflow-model';
import { deleteWorkflowArtifacts } from './workflow-run-store';

function readLibrary(untitled: string): WorkflowLibrary {
  try {
    return parseWorkflowLibrary(window.localStorage.getItem(WORKFLOW_STORAGE_KEY), untitled);
  } catch {
    return parseWorkflowLibrary(null, untitled);
  }
}

/**
 * The saved workflow library: autosaved to localStorage, with removed source
 * media and runs queued for deletion in the same write and retried until done.
 */
export function useWorkflowLibrary() {
  const { t } = useTranslation();
  const untitled = t('workflows.untitled', { defaultValue: en.workflows.untitled });
  const [library, setStoredLibrary] = useState(() => readLibrary(untitled));
  const libraryRef = useRef(library);
  libraryRef.current = library;
  const setLibrary = useCallback((update: (current: WorkflowLibrary) => WorkflowLibrary) => {
    setStoredLibrary((current) => {
      const next = update(current);
      if (next === current) return current;
      const removed = removedWorkflowData(current, next);
      return removed.media.length || removed.runs.length
        ? { ...next, cleanup: [...(current.cleanup || []), removed] }
        : next;
    });
  }, []);
  const cleaning = useRef(false);
  const [cleanupError, setCleanupError] = useState('');
  const retryCleanup = useCallback(async () => {
    if (cleaning.current) return;
    cleaning.current = true;
    try {
      const current = libraryRef.current;
      // Draft changes and their deletion queue are one atomic localStorage write.
      window.localStorage.setItem(WORKFLOW_STORAGE_KEY, JSON.stringify(current));
      const batch = current.cleanup || [];
      for (const task of batch) await deleteWorkflowArtifacts(task);
      if (batch.length)
        setStoredLibrary((latest) => ({
          ...latest,
          cleanup: (latest.cleanup || []).filter((task) => !batch.includes(task)),
        }));
      setCleanupError('');
    } catch (error) {
      setCleanupError(describeError(error));
    } finally {
      cleaning.current = false;
    }
  }, []);
  const previousUntitled = useRef(untitled);
  useEffect(() => {
    const previous = previousUntitled.current;
    previousUntitled.current = untitled;
    setLibrary((current) => {
      let changed = false;
      const documents = current.documents.map((item) => {
        const repaired = repairWorkflowName(item.name, untitled);
        const localeChanged = previous !== untitled && (item.autoNamed || item.name === previous);
        const name = localeChanged ? untitled : repaired;
        if (name !== item.name) {
          changed = true;
          return { ...item, name, autoNamed: item.autoNamed || item.name === 'workflows.untitled' };
        }
        return item;
      });
      return changed ? { ...current, documents } : current;
    });
  }, [untitled, setLibrary]);
  useEffect(() => {
    try {
      window.localStorage.setItem(WORKFLOW_STORAGE_KEY, JSON.stringify(library));
    } catch (error) {
      setCleanupError(describeError(error));
      return;
    }
    void retryCleanup();
  }, [library, retryCleanup]);
  return { untitled, library, setLibrary, cleanupError, retryCleanup };
}
