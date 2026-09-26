import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import React from 'react';

import LLMSkillsPanel from './LLMSkillsPanel';

const skill = (id, extra = {}) => ({
  id,
  name_key: `settings.llmskills_${id}_name`,
  description_key: `settings.llmskills_${id}_desc`,
  enabled: true,
  provider_override: null,
  provider: 'groq',
  provider_display_name: 'Groq',
  provider_local: false,
  provider_source: 'active',
  ready: true,
  reason: null,
  ...extra,
});

const SKILLS = {
  skills: [
    skill('cinematic_translation'),
    skill('slot_fitting'),
    skill('glossary_extract'),
    skill('direction_parse'),
    skill('dictation_refinement'),
  ],
};

const PROVIDERS = {
  active: 'groq',
  providers: [
    {
      id: 'groq',
      display_name: 'Groq',
      local: false,
      configured: true,
    },
    {
      id: 'ollama',
      display_name: 'Ollama (local)',
      local: true,
      configured: true,
    },
    {
      id: 'openai',
      display_name: 'OpenAI',
      local: false,
      configured: false,
    },
  ],
};

function mockFetch(bodyByUrl, onPut) {
  return vi.fn(async (url, opts = {}) => {
    const body =
      (opts.method === 'PUT' ? onPut?.(url, JSON.parse(opts.body)) : null) ??
      bodyByUrl(String(url), opts);
    return {
      ok: true,
      status: 200,
      json: async () => body,
      text: async () => JSON.stringify(body),
    };
  });
}

const routes = (url) => (url.includes('llm-providers') ? PROVIDERS : SKILLS);

describe('LLMSkillsPanel', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('renders every skill with its localized name and ready badge', async () => {
    global.fetch = mockFetch(routes);
    render(<LLMSkillsPanel />);
    expect(await screen.findByText('Cinematic & Autofit translation')).toBeInTheDocument();
    expect(screen.getByText('Dictation cleanup')).toBeInTheDocument();
    expect(screen.getByTestId('llm-skill-ready-slot_fitting')).toBeInTheDocument();
    // local providers are tagged in the routing options
    const select = screen.getByTestId('llm-skill-provider-cinematic_translation');
    expect(select).toHaveTextContent('Ollama (local) · local');
    // unconfigured providers are not offered as routes
    expect(select).not.toHaveTextContent('OpenAI');
  });

  it('toggling a skill PUTs {enabled} and re-renders from the response', async () => {
    const put = vi.fn(() => ({
      skills: SKILLS.skills.map((s) =>
        s.id === 'dictation_refinement'
          ? { ...s, enabled: false, ready: false, reason: 'disabled' }
          : s,
      ),
    }));
    global.fetch = mockFetch(routes, put);
    render(<LLMSkillsPanel />);
    const toggle = await screen.findByTestId('llm-skill-toggle-dictation_refinement');
    fireEvent.click(toggle);
    await waitFor(() => expect(toggle).not.toBeChecked());
    const [url, body] = [put.mock.calls[0][0], put.mock.calls[0][1]];
    expect(url).toContain('/api/settings/llm-skills/dictation_refinement');
    expect(body).toEqual({ enabled: false });
  });

  it('changing the provider Select PUTs {provider_override}', async () => {
    const put = vi.fn(() => ({
      skills: SKILLS.skills.map((s) =>
        s.id === 'cinematic_translation'
          ? { ...s, provider_override: 'ollama', provider: 'ollama', provider_source: 'override' }
          : s,
      ),
    }));
    global.fetch = mockFetch(routes, put);
    render(<LLMSkillsPanel />);
    const select = await screen.findByTestId('llm-skill-provider-cinematic_translation');
    fireEvent.change(select, { target: { value: 'ollama' } });
    await waitFor(() => expect(select.value).toBe('ollama'));
    expect(put.mock.calls[0][1]).toEqual({ provider_override: 'ollama' });
  });

  it('the per-skill routing Select carries an accessible name', async () => {
    global.fetch = mockFetch(routes);
    render(<LLMSkillsPanel />);
    const select = await screen.findByTestId('llm-skill-provider-cinematic_translation');
    // Announced as "Provider for <skill>" — not an unlabeled combobox.
    expect(select).toHaveAccessibleName('Provider for Cinematic & Autofit translation');
  });

  it('shows the needs-setup badge + LLM Providers link when no provider resolves', async () => {
    const unready = {
      skills: SKILLS.skills.map((s) => ({
        ...s,
        provider: null,
        provider_display_name: null,
        provider_source: 'none',
        ready: false,
        reason: 'no_provider',
      })),
    };
    global.fetch = mockFetch((url) => (url.includes('llm-providers') ? PROVIDERS : unready));
    render(<LLMSkillsPanel />);
    expect(await screen.findByTestId('llm-skill-needs-setup-glossary_extract')).toBeInTheDocument();
    expect(screen.getByTestId('llm-skill-setup-glossary_extract')).toHaveTextContent(
      'Configure providers',
    );
  });
});
