/** Voice traits come from Electron, just like mobile theme and language data. */
import { CATEGORIES } from '../electron/src/shared/utils/constants.js';
const output = new URL('../mobile/contract/design.json', import.meta.url);
const data = {
  categories: ['Gender', 'Age', 'Pitch', 'Style'].map((id) => ({
    id, label: `clone.cat_${id}`,
    options: CATEGORIES[id].map((token: string) => ({
      token: token === 'Auto' ? '' : token,
      label: `clone.opt_${token.replaceAll(' ', '_').replaceAll('-', '_')}`,
    })),
  })),
};
const rendered = JSON.stringify(data, null, 2) + '\n';
if (process.argv.includes('--check')) {
  if (await Bun.file(output).text() !== rendered) throw new Error('Run bun scripts/export-mobile-design.ts');
} else { await Bun.write(output, rendered); }
