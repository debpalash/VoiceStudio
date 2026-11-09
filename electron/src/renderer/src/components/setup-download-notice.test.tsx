import { render, screen } from '@testing-library/react';
import { expect, it } from 'vitest';
import i18n from '@/i18n';
import { SetupDownloadNotice } from './setup-download-notice';

// #3411: a slow or blocked host must not look like a frozen installer.
it('names the host of the active download', () => {
  render(
    <SetupDownloadNotice
      progress={{
        completedDownloads: 2,
        downloadsComplete: false,
        downloadHost: 'download-r2.pytorch.org',
      }}
    />,
  );
  expect(screen.getByTestId('setup-download-source')).toHaveTextContent(
    i18n.t('bootstrap.download_source', { host: 'download-r2.pytorch.org' }),
  );
  expect(screen.queryByTestId('setup-download-stalled')).not.toBeInTheDocument();
});

it('shows a stalled download with its host and what to do', () => {
  render(
    <SetupDownloadNotice
      progress={{
        completedDownloads: 2,
        downloadsComplete: false,
        downloadHost: 'files.pythonhosted.org',
        stalled: true,
        stalledSeconds: 52,
      }}
    />,
  );
  const notice = screen.getByRole('status');
  expect(notice).toHaveTextContent(
    i18n.t('bootstrap.download_stalled', { host: 'files.pythonhosted.org', seconds: 52 }),
  );
  expect(notice).toHaveTextContent(i18n.t('bootstrap.download_stalled_hint'));
  expect(screen.queryByTestId('setup-download-source')).not.toBeInTheDocument();
});

it('stays out of the way when no download is pending', () => {
  const { container, rerender } = render(<SetupDownloadNotice progress={undefined} />);
  expect(container).toBeEmptyDOMElement();
  rerender(
    <SetupDownloadNotice
      progress={{
        completedDownloads: 9,
        downloadsComplete: true,
        downloadHost: 'files.pythonhosted.org',
        stalled: true,
      }}
    />,
  );
  expect(container).toBeEmptyDOMElement();
  rerender(
    <SetupDownloadNotice
      progress={{
        completedDownloads: 0,
        downloadsComplete: false,
        stalled: true,
        stalledSeconds: 60,
      }}
    />,
  );
  expect(screen.getByRole('status')).toHaveTextContent(
    i18n.t('bootstrap.download_stalled_generic', { seconds: 60 }),
  );
});
