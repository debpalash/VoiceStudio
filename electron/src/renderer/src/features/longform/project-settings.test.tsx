import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import i18n from '@/i18n';
import { blankLongformDraft } from './longform-session';
import { projectLibrary } from './project-library';
import { ProjectSettings } from './project-settings';

afterEach(() => vi.restoreAllMocks());

function setup() {
  const saved = {
    id: 'book-1',
    name: 'First book',
    mode: 'audiobook' as const,
    draft: { ...blankLongformDraft(), script: 'Chapter one.' },
  };
  vi.spyOn(projectLibrary, 'list').mockResolvedValue([saved] as never);
  const save = vi.spyOn(projectLibrary, 'save');
  const remove = vi.spyOn(projectLibrary, 'remove');
  const onChange = vi.fn();
  const onReplace = vi.fn();
  render(
    <QueryClientProvider client={new QueryClient()}>
      <ProjectSettings
        bare
        mode="audiobook"
        draft={{ ...saved.draft, projectId: saved.id, title: 'First book' }}
        disabled={false}
        onChange={onChange}
        onReplace={onReplace}
        onBusy={() => {}}
      />
    </QueryClientProvider>,
  );
  return { onChange, onReplace, save, remove };
}

it('starts a blank project after confirmation without touching saved projects', async () => {
  const { onChange, onReplace, save, remove } = setup();
  const button = await screen.findByRole('button', { name: i18n.t('projectActions.new') });
  await waitFor(() => expect(button).toBeEnabled());
  fireEvent.click(button);
  expect(onChange).not.toHaveBeenCalled();
  expect(screen.getByText(i18n.t('projectActions.new_confirm'))).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: i18n.t('common.confirm') }));
  expect(onReplace).toHaveBeenCalledWith(blankLongformDraft());
  expect(onReplace.mock.calls[0][0].projectId).toBeNull();
  expect(save).not.toHaveBeenCalled();
  expect(remove).not.toHaveBeenCalled();
  expect(screen.getByLabelText(i18n.t('stories.projectName'))).toHaveValue('');
  // The saved project is still listed and can be reopened.
  expect(screen.getByRole('button', { name: 'First book' })).toBeInTheDocument();
});

it('cancelling keeps the current draft', async () => {
  const { onChange } = setup();
  const button = await screen.findByRole('button', { name: i18n.t('projectActions.new') });
  await waitFor(() => expect(button).toBeEnabled());
  fireEvent.click(button);
  fireEvent.click(screen.getByRole('button', { name: i18n.t('common.cancel') }));
  expect(onChange).not.toHaveBeenCalled();
  expect(screen.queryByText(i18n.t('projectActions.new_confirm'))).not.toBeInTheDocument();
});

it('opens the saved project through replacement without treating it as an incremental edit', async () => {
  const { onChange, onReplace } = setup();
  fireEvent.click(await screen.findByRole('button', { name: 'First book' }));
  fireEvent.click(screen.getByRole('button', { name: i18n.t('common.confirm') }));
  await waitFor(() =>
    expect(onReplace).toHaveBeenCalledWith(
      expect.objectContaining({ script: 'Chapter one.', projectId: 'book-1' }),
    ),
  );
  expect(onChange).not.toHaveBeenCalled();
});
