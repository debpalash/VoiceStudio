import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, fireEvent, screen, within } from '@testing-library/react';
import '../i18n';
import MultiLangPicker from './MultiLangPicker';
import { LANGUAGE_FLAGS } from './LanguageFlag';
import { LANG_CODES } from '../utils/languages';

const rect = (overrides = {}) => ({
  x: 40,
  y: 720,
  top: 720,
  right: 64,
  bottom: 744,
  left: 40,
  width: 24,
  height: 24,
  toJSON: () => {},
  ...overrides,
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('MultiLangPicker viewport-safe menu', () => {
  it('offers compact editor sizing without shrinking the default picker', () => {
    const { rerender } = render(<MultiLangPicker single compact onChange={() => {}} />);
    expect(screen.getByRole('button', { name: 'Manage languages' })).toHaveStyle({
      minHeight: '36px',
    });
    rerender(<MultiLangPicker single onChange={() => {}} />);
    expect(screen.getByRole('button', { name: 'Manage languages' })).toHaveStyle({
      minHeight: '44px',
    });
  });

  it('replaces a single selection, preserves Auto, and closes after choosing', () => {
    const onChange = vi.fn();
    render(
      <MultiLangPicker
        single
        selected={[{ lang: 'English', code: 'en' }]}
        options={[{ label: 'Auto', code: 'Auto' }, ...LANG_CODES]}
        onChange={onChange}
      />,
    );
    const trigger = screen.getByRole('button', { name: 'Manage languages' });
    expect(trigger).toHaveTextContent('English');
    fireEvent.click(trigger);
    expect(screen.queryByRole('button', { name: 'Remove English' })).not.toBeInTheDocument();
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'Auto' } });
    fireEvent.keyDown(screen.getByRole('textbox'), { key: 'Enter' });
    expect(onChange).toHaveBeenCalledWith([{ lang: 'Auto', code: 'Auto' }]);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });

  it('portals outside clipping ancestors and flips above a bottom-edge trigger', () => {
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: 1000 });
    Object.defineProperty(window, 'innerHeight', { configurable: true, value: 800 });
    const onChange = vi.fn();
    const { container } = render(
      <div data-testid="clipper" style={{ overflow: 'hidden', height: 40 }}>
        <MultiLangPicker selected={[]} onChange={onChange} />
      </div>,
    );
    const trigger = screen.getByRole('button', { name: 'Manage languages' });
    vi.spyOn(trigger, 'getBoundingClientRect').mockReturnValue(rect());

    fireEvent.click(trigger);

    const menu = screen.getByRole('dialog', { name: 'Manage languages' });
    expect(container).not.toContainElement(menu);
    expect(menu).toHaveStyle({ bottom: '84px', left: '40px', width: '320px' });
    expect(menu.style.top).toBe('');
    expect(menu.style.maxHeight).toBe('360px');

    fireEvent.click(screen.getAllByRole('button', { name: /Spanish/ })[0]);
    expect(onChange).toHaveBeenCalledWith([{ lang: 'Spanish', code: 'es' }]);
  });

  it('opens below when space permits and Escape closes then restores trigger focus', () => {
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: 1000 });
    Object.defineProperty(window, 'innerHeight', { configurable: true, value: 800 });
    render(<MultiLangPicker selected={[]} onChange={vi.fn()} />);
    const trigger = screen.getByRole('button', { name: 'Manage languages' });
    vi.spyOn(trigger, 'getBoundingClientRect').mockReturnValue(
      rect({ y: 20, top: 20, bottom: 44 }),
    );

    fireEvent.click(trigger);
    const menu = screen.getByRole('dialog', { name: 'Manage languages' });
    expect(menu).toHaveStyle({ top: '48px' });
    expect(menu.style.bottom).toBe('');

    fireEvent.keyDown(document, { key: 'Escape' });
    expect(screen.queryByRole('dialog', { name: 'Manage languages' })).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });

  it('keeps 56 selected languages collapsed and manages them through search', () => {
    const selected = Array.from({ length: 56 }, (_, index) => ({
      lang: `Language ${index}`,
      code: `l${index}`,
    }));
    const onChange = vi.fn();
    const progressByCode = Object.fromEntries(
      selected.map(({ code }, index) => [code, { ready: index === 0 ? 14 : 0, total: 14 }]),
    );
    render(
      <MultiLangPicker selected={selected} onChange={onChange} progressByCode={progressByCode} />,
    );

    expect(screen.getAllByRole('button')).toHaveLength(1);
    expect(screen.getByRole('button', { name: 'Manage languages' })).toHaveTextContent(
      'Done: 1 · Pending: 55',
    );
    expect(screen.queryByText('Language 55')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Manage languages' }));
    fireEvent.change(screen.getByRole('textbox', { name: 'Search languages…' }), {
      target: { value: 'Language 55' },
    });
    expect(screen.getByRole('button', { name: 'Remove Language 55' })).toHaveTextContent('0/14');

    fireEvent.click(screen.getByRole('button', { name: 'Remove Language 55' }));
    expect(onChange).toHaveBeenCalledWith(selected.slice(0, 55));
  });
});

describe('MultiLangPicker responsive language cards', () => {
  it('maps every supported language to a representative flag', () => {
    expect(Object.keys(LANGUAGE_FLAGS).sort()).toEqual(LANG_CODES.map(({ code }) => code).sort());
  });

  it('keeps selected languages compact and shows their flags in the manager', () => {
    render(
      <MultiLangPicker
        selected={[
          { lang: 'Spanish', code: 'es' },
          { lang: 'Japanese', code: 'ja' },
        ]}
        onChange={vi.fn()}
      />,
    );

    expect(screen.queryByTestId('language-flag-es')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Manage languages' }));
    const manager = screen.getByRole('dialog', { name: 'Manage languages' });
    expect(within(manager).getByTestId('language-flag-es')).toBeInTheDocument();
    expect(within(manager).getByTestId('language-flag-ja')).toBeInTheDocument();
  });

  it('shows flags in searchable language results', () => {
    render(<MultiLangPicker selected={[]} onChange={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: 'Manage languages' }));
    const manager = screen.getByRole('dialog', { name: 'Manage languages' });
    expect(within(manager).getByTestId('language-flag-af')).toBeInTheDocument();
  });
});
