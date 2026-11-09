import { expect, it } from 'vitest';
import en from '../../i18n/locales/en.json';

function* strings(node: unknown, path: string[] = []): Generator<[string, string]> {
  if (typeof node === 'string') yield [path.join('.'), node];
  else if (node && typeof node === 'object')
    for (const [key, child] of Object.entries(node)) yield* strings(child, [...path, key]);
}

it('names Settings pages as they appear in the app', () => {
  // The phone app's strings are generated into mobile/contract and change there.
  const desktop = Object.fromEntries(Object.entries(en).filter(([key]) => key !== 'mobile'));
  const stale = [...strings(desktop)].filter(([, text]) =>
    /Model Catalogue|Settings → Engines|LLM_DEFAULT_PROVIDER|OMNIVOICE_LLM_BACKEND/.test(text),
  );
  expect(stale).toEqual([]);
});
