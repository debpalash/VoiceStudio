import { CheckIcon } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { useTheme } from '@/hooks/use-theme';
import { type ThemeAppearance } from '@/lib/themes/t3-palettes';
import { AVAILABLE_PALETTES, paletteColors } from '@/lib/themes/theme-preferences';
import { cn } from '@/lib/utils';

export function PalettePicker({ appearance }: { appearance: ThemeAppearance }) {
  const { t } = useTranslation();
  const settings = useTheme();
  const options = [{ id: 'default', label: t('themeAppearance.default') }, ...AVAILABLE_PALETTES];
  return (
    <section className="p-4">
      <h3 id={`palette-${appearance}`} className="mb-3 text-sm font-medium">
        {t(`themeAppearance.${appearance}`)}
      </h3>
      <div
        role="group"
        aria-labelledby={`palette-${appearance}`}
        className="grid grid-cols-2 gap-2 lg:grid-cols-3"
      >
        {options.map((option) => {
          const colors = paletteColors(option.id, appearance);
          const studio = `--studio-${appearance}`;
          const background = colors?.canvas ?? `var(${studio}-canvas)`;
          const sidebar = colors?.sidebar ?? `var(${studio}-sidebar)`;
          const text = colors?.text ?? `var(${studio}-text)`;
          const accent = colors?.messageAction ?? `var(${studio}-action)`;
          const selected = settings[appearance] === option.id;
          return (
            <button
              type="button"
              key={option.id}
              aria-pressed={selected}
              onClick={() => settings.updateTheme({ [appearance]: option.id, mode: appearance })}
              className={cn(
                'overflow-hidden rounded-lg border border-border/70 text-left outline-none transition-colors hover:border-ring/60 focus-visible:ring-2 focus-visible:ring-ring',
                selected && 'border-ring ring-1 ring-ring',
              )}
            >
              <div aria-hidden="true" className="flex h-16" style={{ background, color: text }}>
                <div
                  className="flex w-1/4 flex-col gap-1.5 border-r border-black/5 p-2"
                  style={{ background: sidebar }}
                >
                  <span className="h-1 w-full rounded bg-current opacity-40" />
                  <span className="h-1 w-2/3 rounded bg-current opacity-20" />
                </div>
                <div className="flex flex-1 flex-col gap-1.5 p-3">
                  <span className="h-1 w-3/4 rounded bg-current opacity-60" />
                  <span className="h-1 w-1/2 rounded bg-current opacity-20" />
                  <span
                    className="mt-auto h-2 w-8 self-end rounded-sm"
                    style={{ background: accent }}
                  />
                </div>
              </div>
              <span className="flex items-center justify-between gap-2 bg-card/60 px-3 py-2 text-xs font-medium">
                {option.label}
                {selected && <CheckIcon aria-hidden="true" className="size-3.5 text-primary" />}
              </span>
            </button>
          );
        })}
      </div>
    </section>
  );
}
