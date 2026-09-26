/**
 * Settings → General → Pronunciation dictionary panel.
 *
 * Verifies: entries render (term → replacement + scope/type badges), Add posts
 * the new entry (click or Enter), delete removes it, the enable toggle PUTs
 * with a per-entry accessible name, and the test field previews the
 * substitution — language-scoped (the POST carries the selected preview
 * language), sequence-guarded (a slow stale response never overwrites a newer
 * one), error-surfacing, and re-run after mutations. Plus the JSON
 * export/import affordance over the backend's export/import endpoints.
 * All over mocked api/client.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { I18nextProvider } from 'react-i18next';
import i18n from '../i18n';

const apiJson = vi.fn();
const apiFetch = vi.fn();
vi.mock('../api/client', () => ({
  apiJson: (...a) => apiJson(...a),
  apiFetch: (...a) => apiFetch(...a),
}));

import PronunciationPanel from '../components/settings/PronunciationPanel';

const ENTRIES = [
  {
    id: 'e1',
    term: 'GIF',
    replacement: 'jiff',
    type: 'respelling',
    language: '*',
    scope: '*',
    enabled: true,
  },
  {
    id: 'e2',
    term: 'Nevada',
    replacement: 'Nuh-VAD-uh',
    type: 'respelling',
    language: 'en',
    scope: 'en',
    enabled: false,
  },
];

function withI18n(node) {
  return <I18nextProvider i18n={i18n}>{node}</I18nextProvider>;
}

describe('PronunciationPanel', () => {
  beforeEach(() => {
    apiJson.mockReset();
    apiFetch.mockReset();
    apiFetch.mockResolvedValue({ ok: true });
  });

  it('lists entries with term, replacement and scope badge', async () => {
    apiJson.mockResolvedValueOnce(ENTRIES);
    render(withI18n(<PronunciationPanel />));
    expect(await screen.findByText('GIF')).toBeInTheDocument();
    expect(screen.getByText('Nevada')).toBeInTheDocument();
    // The scope also appears as a preview-language <option>, so scope the badge
    // queries to non-option elements.
    expect(screen.getAllByText('en').some((el) => el.tagName !== 'OPTION')).toBe(true);
    expect(screen.getAllByText('Global').some((el) => el.tagName !== 'OPTION')).toBe(true);
  });

  it('shows the empty hint when there are no entries', async () => {
    apiJson.mockResolvedValueOnce([]);
    render(withI18n(<PronunciationPanel />));
    expect(await screen.findByTestId('pron-empty')).toBeInTheDocument();
  });

  it('POSTs a new entry on Add', async () => {
    apiJson.mockResolvedValue([]);
    render(withI18n(<PronunciationPanel />));
    await screen.findByTestId('pron-add');
    fireEvent.change(screen.getByTestId('pron-term'), { target: { value: 'SQL' } });
    fireEvent.change(screen.getByTestId('pron-replacement'), { target: { value: 'sequel' } });
    fireEvent.click(screen.getByTestId('pron-add'));
    await waitFor(() => expect(apiFetch).toHaveBeenCalled());
    const [path, opts] = apiFetch.mock.calls[0];
    expect(path).toBe('/pronunciation');
    expect(opts.method).toBe('POST');
    const body = JSON.parse(opts.body);
    expect(body).toMatchObject({
      term: 'SQL',
      replacement: 'sequel',
      type: 'respelling',
      language: '*',
      enabled: true,
    });
  });

  it('disables Add (and never POSTs) while the term is blank', async () => {
    apiJson.mockResolvedValue([]);
    render(withI18n(<PronunciationPanel />));
    const addBtn = await screen.findByTestId('pron-add');
    expect(addBtn).toBeDisabled();
    fireEvent.click(addBtn);
    expect(apiFetch).not.toHaveBeenCalled();
    // Typing a term enables it.
    fireEvent.change(screen.getByTestId('pron-term'), { target: { value: 'SQL' } });
    expect(addBtn).not.toBeDisabled();
  });

  it('Enter in an add-form field submits the entry', async () => {
    apiJson.mockResolvedValue([]);
    render(withI18n(<PronunciationPanel />));
    await screen.findByTestId('pron-term');
    fireEvent.change(screen.getByTestId('pron-term'), { target: { value: 'SQL' } });
    fireEvent.change(screen.getByTestId('pron-replacement'), { target: { value: 'sequel' } });
    fireEvent.keyDown(screen.getByTestId('pron-replacement'), { key: 'Enter' });
    await waitFor(() => expect(apiFetch).toHaveBeenCalled());
    const [path, opts] = apiFetch.mock.calls[0];
    expect(path).toBe('/pronunciation');
    expect(opts.method).toBe('POST');
    expect(JSON.parse(opts.body)).toMatchObject({ term: 'SQL', replacement: 'sequel' });
  });

  it('DELETEs an entry', async () => {
    apiJson.mockResolvedValue(ENTRIES);
    render(withI18n(<PronunciationPanel />));
    await screen.findByText('GIF');
    fireEvent.click(screen.getByTestId('pron-del-e1'));
    await waitFor(() =>
      expect(apiFetch).toHaveBeenCalledWith('/pronunciation/e1', { method: 'DELETE' }),
    );
  });

  it('PUTs the enabled toggle', async () => {
    apiJson.mockResolvedValue(ENTRIES);
    render(withI18n(<PronunciationPanel />));
    await screen.findByText('Nevada');
    fireEvent.click(screen.getByTestId('pron-toggle-e2'));
    await waitFor(() => {
      const call = apiFetch.mock.calls.find(([p]) => p === '/pronunciation/e2');
      expect(call).toBeTruthy();
      expect(JSON.parse(call[1].body)).toEqual({ enabled: true });
    });
  });

  it('gives every enable toggle a per-entry accessible name', async () => {
    apiJson.mockResolvedValue(ENTRIES);
    render(withI18n(<PronunciationPanel />));
    await screen.findByText('GIF');
    // Each switch names its entry — a screen reader can tell them apart.
    expect(screen.getByRole('switch', { name: 'Enable GIF' })).toBeInTheDocument();
    expect(screen.getByRole('switch', { name: 'Enable Nevada' })).toBeInTheDocument();
  });

  it('previews the substitution via /pronunciation/test', async () => {
    apiJson.mockImplementation((path) => {
      if (path === '/pronunciation/test') {
        return Promise.resolve({ substituted: 'a jiff', changed: true });
      }
      return Promise.resolve(ENTRIES);
    });
    render(withI18n(<PronunciationPanel />));
    await screen.findByTestId('pron-test-input');
    fireEvent.change(screen.getByTestId('pron-test-input'), { target: { value: 'a GIF' } });
    expect(await screen.findByTestId('pron-test-out')).toHaveTextContent('a jiff');
  });

  it('sends the selected preview language so language-scoped entries apply', async () => {
    const testBodies = [];
    apiJson.mockImplementation((path, opts) => {
      if (path === '/pronunciation/test') {
        testBodies.push(JSON.parse(opts.body));
        return Promise.resolve({ substituted: 'Nuh-VAD-uh', changed: true });
      }
      return Promise.resolve(ENTRIES);
    });
    render(withI18n(<PronunciationPanel />));
    await screen.findByTestId('pron-test-language');

    // Pick the 'en' scope (offered because an entry is scoped to it), then type.
    fireEvent.change(screen.getByTestId('pron-test-language'), { target: { value: 'en' } });
    fireEvent.change(screen.getByTestId('pron-test-input'), { target: { value: 'Nevada' } });
    await screen.findByTestId('pron-test-out');
    expect(testBodies.at(-1)).toEqual({ text: 'Nevada', language: 'en' });

    // Switching back to Global re-runs the preview WITHOUT a language field.
    fireEvent.change(screen.getByTestId('pron-test-language'), { target: { value: '*' } });
    await waitFor(() => expect(testBodies.at(-1)).toEqual({ text: 'Nevada' }));
  });

  it('hints that Global previews skip language-scoped entries', async () => {
    apiJson.mockResolvedValue([{ ...ENTRIES[1], enabled: true }]);
    render(withI18n(<PronunciationPanel />));
    await screen.findByText('Nevada');
    expect(screen.getByText(/Previewing Global entries only/)).toBeInTheDocument();
    fireEvent.change(screen.getByTestId('pron-test-language'), { target: { value: 'en' } });
    expect(screen.queryByText(/Previewing Global entries only/)).not.toBeInTheDocument();
  });

  it('discards a stale preview response that resolves after a newer one', async () => {
    const pending = [];
    apiJson.mockImplementation((path, opts) => {
      if (path === '/pronunciation/test') {
        return new Promise((resolve) => pending.push({ resolve, body: JSON.parse(opts.body) }));
      }
      return Promise.resolve(ENTRIES);
    });
    render(withI18n(<PronunciationPanel />));
    const input = await screen.findByTestId('pron-test-input');

    fireEvent.change(input, { target: { value: 'one' } });
    await waitFor(() => expect(pending).toHaveLength(1));
    fireEvent.change(input, { target: { value: 'one two' } });
    await waitFor(() => expect(pending).toHaveLength(2));

    // The newer request resolves first…
    pending[1].resolve({ substituted: 'NEWER', changed: true });
    expect(await screen.findByTestId('pron-test-out')).toHaveTextContent('NEWER');

    // …then the stale one lands late and must NOT overwrite it.
    pending[0].resolve({ substituted: 'STALE', changed: true });
    await new Promise((r) => setTimeout(r, 0));
    expect(screen.getByTestId('pron-test-out')).toHaveTextContent('NEWER');
  });

  it('surfaces a preview failure instead of silently blanking', async () => {
    apiJson.mockImplementation((path) => {
      if (path === '/pronunciation/test') return Promise.reject(new Error('down'));
      return Promise.resolve(ENTRIES);
    });
    render(withI18n(<PronunciationPanel />));
    await screen.findByTestId('pron-test-input');
    fireEvent.change(screen.getByTestId('pron-test-input'), { target: { value: 'a GIF' } });
    expect(await screen.findByTestId('pron-test-error')).toBeInTheDocument();
  });

  it('re-runs the preview after adding an entry (no stale result)', async () => {
    const testBodies = [];
    apiJson.mockImplementation((path, opts) => {
      if (path === '/pronunciation/test') {
        testBodies.push(JSON.parse(opts.body));
        return Promise.resolve({ substituted: 'x', changed: false });
      }
      return Promise.resolve(ENTRIES);
    });
    render(withI18n(<PronunciationPanel />));
    await screen.findByTestId('pron-test-input');
    fireEvent.change(screen.getByTestId('pron-test-input'), { target: { value: 'a GIF' } });
    await waitFor(() => expect(testBodies).toHaveLength(1));

    fireEvent.change(screen.getByTestId('pron-term'), { target: { value: 'SQL' } });
    fireEvent.click(screen.getByTestId('pron-add'));
    // The successful POST triggers an immediate preview re-run with the same text.
    await waitFor(() => expect(testBodies).toHaveLength(2));
    expect(testBodies[1]).toMatchObject({ text: 'a GIF' });
  });

  it('exports the dictionary as a JSON download', async () => {
    apiJson.mockImplementation((path) => {
      if (path === '/pronunciation/export') {
        return Promise.resolve({ entries: [{ term: 'GIF', replacement: 'jiff' }] });
      }
      return Promise.resolve(ENTRIES);
    });
    const createObjectURL = vi.fn(() => 'blob:pron');
    const revokeObjectURL = vi.fn();
    const orig = {
      create: URL.createObjectURL,
      revoke: URL.revokeObjectURL,
    };
    URL.createObjectURL = createObjectURL;
    URL.revokeObjectURL = revokeObjectURL;
    try {
      render(withI18n(<PronunciationPanel />));
      fireEvent.click(await screen.findByTestId('pron-export'));
      await waitFor(() => expect(createObjectURL).toHaveBeenCalled());
      expect(apiJson).toHaveBeenCalledWith('/pronunciation/export');
      const blob = createObjectURL.mock.calls[0][0];
      expect(blob.type).toBe('application/json');
      expect(JSON.parse(await blob.text())).toEqual({
        entries: [{ term: 'GIF', replacement: 'jiff' }],
      });
    } finally {
      URL.createObjectURL = orig.create;
      URL.revokeObjectURL = orig.revoke;
    }
  });

  it('imports a JSON file, asking replace-vs-merge when entries exist', async () => {
    const importBodies = [];
    apiJson.mockImplementation((path, opts) => {
      if (path === '/pronunciation/import') {
        importBodies.push(JSON.parse(opts.body));
        return Promise.resolve({ imported: 2, replaced: true });
      }
      return Promise.resolve(ENTRIES);
    });
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true);
    try {
      render(withI18n(<PronunciationPanel />));
      const fileInput = await screen.findByTestId('pron-import-file');
      const payload = {
        entries: [
          { term: 'A', replacement: 'ay', type: 'respelling', language: '*', enabled: true },
          { term: 'B', replacement: 'bee', type: 'respelling', language: 'en', enabled: true },
        ],
      };
      const file = { name: 'dict.json', text: async () => JSON.stringify(payload) };
      fireEvent.change(fileInput, { target: { files: [file] } });

      expect(await screen.findByTestId('pron-import-done')).toHaveTextContent('2');
      expect(confirmSpy).toHaveBeenCalled(); // entries existed → replace-vs-merge prompt
      expect(importBodies[0]).toEqual({ entries: payload.entries, replace: true });
    } finally {
      confirmSpy.mockRestore();
    }
  });

  it('rejects a non-export JSON file with a clear error and no request', async () => {
    apiJson.mockResolvedValue(ENTRIES);
    render(withI18n(<PronunciationPanel />));
    const fileInput = await screen.findByTestId('pron-import-file');
    const file = { name: 'junk.json', text: async () => '{"not": "an export"}' };
    fireEvent.change(fileInput, { target: { files: [file] } });
    expect(await screen.findByRole('alert')).toHaveTextContent(/import/i);
    expect(apiJson).not.toHaveBeenCalledWith('/pronunciation/import', expect.anything());
  });

  // #1949 / #2002: an IPA or CMU row saves, validates and toggles on, then is
  // dropped before term matching — Phase 1 only substitutes respelling. #1984
  // said so in the test preview, which the user only sees if they run a test.
  // The list is where they look at what they saved, so it says so there too.
  it('badges an entry that is stored but not applied yet', async () => {
    apiJson.mockResolvedValue([
      ...ENTRIES,
      {
        id: 'e3',
        term: 'Nginx',
        replacement: 'ˈɛndʒɪnˈɛks',
        type: 'ipa',
        language: '*',
        scope: '*',
        enabled: true,
      },
      {
        id: 'e4',
        term: 'SQL',
        replacement: 'S IY1 K W AH0 L',
        type: 'cmu',
        language: '*',
        scope: '*',
        enabled: true,
      },
    ]);
    render(withI18n(<PronunciationPanel />));

    expect(await screen.findByTestId('pron-not-applied-e3')).toHaveTextContent(/not applied/i);
    expect(screen.getByTestId('pron-not-applied-e4')).toBeInTheDocument();
    // A respelling row DOES take effect — badging it would be the opposite lie.
    expect(screen.queryByTestId('pron-not-applied-e1')).not.toBeInTheDocument();
    expect(screen.queryByTestId('pron-not-applied-e2')).not.toBeInTheDocument();
  });

  // Both review bots, independently: a row the user switched OFF is also not
  // applied, but for a reason the toggle already shows. Badging it reads as a
  // defect rather than their own choice, and the backend's inert set excludes
  // disabled rows for the same reason.
  it('does not badge an IPA entry the user has switched off', async () => {
    apiJson.mockResolvedValue([
      {
        id: 'e5',
        term: 'Nginx',
        replacement: 'ˈɛndʒɪnˈɛks',
        type: 'ipa',
        language: '*',
        scope: '*',
        enabled: false,
      },
    ]);
    render(withI18n(<PronunciationPanel />));

    expect(await screen.findByText('Nginx')).toBeInTheDocument();
    expect(screen.queryByTestId('pron-not-applied-e5')).not.toBeInTheDocument();
  });

  // The type test mirrors the backend's: everything that is not respelling is
  // inert today. A notation added later must be badged on arrival, not
  // silently omitted because nobody added it to a list — that omission IS the
  // invisibility #1949 is about.
  it('badges an unfamiliar notation rather than assuming it works', async () => {
    apiJson.mockResolvedValue([
      {
        id: 'e6',
        term: 'Nginx',
        replacement: 'whatever-comes-next',
        type: 'sampa',
        language: '*',
        scope: '*',
        enabled: true,
      },
    ]);
    render(withI18n(<PronunciationPanel />));

    expect(await screen.findByTestId('pron-not-applied-e6')).toBeInTheDocument();
  });
});
