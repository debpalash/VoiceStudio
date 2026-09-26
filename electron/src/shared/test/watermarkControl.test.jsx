/**
 * The app told users the watermark could be turned off in Settings → Privacy
 * long before anything there could do it. These pin the control's contract.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';

const { getStatus, setSettings } = vi.hoisted(() => ({
  getStatus: vi.fn(),
  setSettings: vi.fn(),
}));

vi.mock('../api/watermark', () => ({
  getWatermarkStatus: (...a) => getStatus(...a),
  setWatermarkSettings: (...a) => setSettings(...a),
}));
vi.mock('react-hot-toast', () => ({
  default: { success: vi.fn(), error: vi.fn() },
}));
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k, o) => o?.defaultValue ?? k }),
}));

import WatermarkControl from '../components/settings/WatermarkControl';

const AVAILABLE = {
  invisible_enabled: true,
  visible_audio_enabled: false,
  visible_video_enabled: true,
  audioseal_available: true,
};

describe('WatermarkControl', () => {
  beforeEach(() => {
    getStatus.mockReset();
    setSettings.mockReset();
  });

  it('reflects the backend state instead of assuming it', async () => {
    getStatus.mockResolvedValue({ ...AVAILABLE, invisible_enabled: false });
    render(<WatermarkControl />);
    const toggle = await screen.findByTestId('watermark-toggle');
    expect(toggle).not.toBeChecked();
  });

  it('turns the watermark off — the thing the FAQ promised', async () => {
    getStatus.mockResolvedValue(AVAILABLE);
    setSettings.mockResolvedValue({ ...AVAILABLE, invisible_enabled: false });
    render(<WatermarkControl />);

    const toggle = await screen.findByTestId('watermark-toggle');
    expect(toggle).toBeChecked();
    fireEvent.click(toggle);

    expect(setSettings).toHaveBeenCalledWith({ invisible_enabled: false });
    await waitFor(() => expect(toggle).not.toBeChecked());
  });

  it('turns it back on', async () => {
    getStatus.mockResolvedValue({ ...AVAILABLE, invisible_enabled: false });
    setSettings.mockResolvedValue(AVAILABLE);
    render(<WatermarkControl />);

    const toggle = await screen.findByTestId('watermark-toggle');
    fireEvent.click(toggle);
    expect(setSettings).toHaveBeenCalledWith({ invisible_enabled: true });
    await waitFor(() => expect(toggle).toBeChecked());
  });

  // An inert switch over a mark that cannot be embedded is the same lie in the
  // other direction.
  // Waits for the request to SETTLE, not merely to start: the initial render is
  // already empty, so asserting emptiness right after the call would pass even
  // if the control later appeared (CodeRabbit).
  it('renders nothing when AudioSeal is unavailable', async () => {
    let resolve;
    getStatus.mockReturnValue(
      new Promise((r) => {
        resolve = r;
      }),
    );
    const { container } = render(<WatermarkControl />);
    resolve({ ...AVAILABLE, audioseal_available: false });
    await waitFor(() => expect(getStatus).toHaveBeenCalled());
    await new Promise((r) => setTimeout(r, 0));
    expect(container).toBeEmptyDOMElement();
    expect(screen.queryByTestId('watermark-toggle')).toBeNull();
  });

  it('renders nothing when the backend cannot be reached', async () => {
    getStatus.mockRejectedValue(new Error('offline'));
    const { container } = render(<WatermarkControl />);
    // Two attempts: the retry has to fail as well before we give up.
    await waitFor(() => expect(getStatus).toHaveBeenCalledTimes(2), { timeout: 4000 });
    expect(container).toBeEmptyDOMElement();
  });

  // Greptile P1: a single shot meant opening Privacy during a restart hid the
  // control for the rest of the session — and being findable is its whole job.
  it('recovers when the first status fetch fails', async () => {
    getStatus
      .mockRejectedValueOnce(new Error('backend restarting'))
      .mockResolvedValueOnce(AVAILABLE);
    render(<WatermarkControl />);
    const toggle = await screen.findByTestId('watermark-toggle', {}, { timeout: 4000 });
    expect(toggle).toBeChecked();
  });

  it('keeps the previous state when the update fails', async () => {
    getStatus.mockResolvedValue(AVAILABLE);
    setSettings.mockRejectedValue(new Error('boom'));
    render(<WatermarkControl />);

    const toggle = await screen.findByTestId('watermark-toggle');
    fireEvent.click(toggle);
    await waitFor(() => expect(setSettings).toHaveBeenCalled());
    expect(toggle).toBeChecked(); // not optimistically flipped
  });
});
