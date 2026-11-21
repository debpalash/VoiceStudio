import { render } from '@testing-library/react';
import { act } from 'react';
import { expect, it, vi } from 'vitest';

const loaded = vi.hoisted(() => vi.fn());
vi.mock('./voice-studio-agent', () => {
  loaded();
  return { VoiceStudioAgent: () => <div data-testid="agent" /> };
});

import { RepairAgentDock } from './repair-agent-dock';
import { translationActivity } from '@/features/dub/translation-activity';

it('stays deferred while the translation store is idle and mounts once a run exists', async () => {
  const view = render(<RepairAgentDock />);
  expect(view.queryByTestId('agent')).toBeNull();
  expect(loaded).not.toHaveBeenCalled();
  await act(async () => {
    translationActivity.setState((state) => ({
      ...state,
      runs: [{ jobId: 'job-1' } as never],
    }));
  });
  expect(await view.findByTestId('agent')).toBeTruthy();
});
