import { chromium } from 'playwright';

const base = process.env.VOICESTUDIO_UI_URL || 'http://localhost:3912';
const routes = [
  '/',
  '/clone',
  '/design',
  '/personas',
  '/gallery',
  '/transcriptions',
  '/calls',
  '/stories',
  '/audiobook',
  '/dub',
  '/batch',
  '/projects',
  '/tools',
  '/settings/general',
  '/settings/appearance',
  '/settings/models',
  '/settings/models/tts',
  '/settings/models/asr',
  '/settings/models/dictation',
  '/settings/models/diarisation',
  '/settings/models/translation',
  '/settings/models/llm',
  '/settings/performance',
  '/settings/pronunciation',
  '/settings/media',
  '/settings/network',
  '/settings/sharing',
  '/settings/credentials',
  '/settings/workers',
  '/settings/permissions',
  '/settings/privacy',
  '/settings/storage',
  '/settings/usage',
  '/settings/logs',
  '/settings/diagnostics',
  '/settings/openapi',
  '/settings/updates',
  '/settings/support',
];

const browser = await chromium.launch({ channel: 'msedge', headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
const cdp = await page.context().newCDPSession(page);
await page.addInitScript(() => localStorage.setItem('voicestudio.setup.complete.v1', '1'));
const findings = [];
const viewports = [
  { width: 1440, height: 900 },
  { width: 640, height: 720 },
];

try {
  for (const viewport of viewports) {
    await page.setViewportSize(viewport);
    for (const route of routes) {
      await page.goto(`${base}/#${route}`, { waitUntil: 'domcontentloaded' });
      await page.waitForTimeout(600);
      const routeFindings = await page.evaluate(() => {
        const issues = [];
        const visible = (element) => {
          const style = getComputedStyle(element);
          const rect = element.getBoundingClientRect();
          return (
            style.display !== 'none' &&
            style.visibility !== 'hidden' &&
            rect.width > 0 &&
            rect.height > 0
          );
        };
        const describe = (element) => {
          const cls =
            typeof element.className === 'string'
              ? `.${element.className.trim().split(/\s+/).slice(0, 2).join('.')}`
              : '';
          return `${element.tagName.toLowerCase()}${element.id ? `#${element.id}` : cls}`;
        };
        const refText = (value) =>
          (value || '')
            .split(/\s+/)
            .filter(Boolean)
            .map((id) => document.getElementById(id)?.textContent || '')
            .join(' ')
            .trim();
        const name = (element) => {
          const explicit = element.getAttribute('aria-label')?.trim();
          if (explicit) return explicit;
          const labelled = refText(element.getAttribute('aria-labelledby'));
          if (labelled) return labelled;
          if ('labels' in element) {
            const labels = [...(element.labels || [])]
              .map((label) => label.textContent || '')
              .join(' ')
              .trim();
            if (labels) return labels;
          }
          const alt = element.getAttribute('alt')?.trim();
          if (alt) return alt;
          const title = element.getAttribute('title')?.trim();
          if (title) return title;
          if (
            element instanceof HTMLInputElement &&
            ['button', 'submit', 'reset'].includes(element.type) &&
            element.value.trim()
          )
            return element.value.trim();
          return (element.textContent || '').replace(/\s+/g, ' ').trim();
        };
        const interactive =
          'button,a[href],input:not([type=hidden]),select,textarea,[role=button],[role=link],[role=checkbox],[role=radio],[role=switch],[role=slider],[role=tab],[role=menuitem],[tabindex]';
        for (const element of document.querySelectorAll(interactive)) {
          if (
            !visible(element) ||
            element.closest('[aria-hidden=true]') ||
            element.matches('[disabled],[aria-disabled=true],[tabindex="-1"]')
          )
            continue;
          if (!name(element))
            issues.push(
              `unnamed interactive ${describe(element)} ${element.outerHTML.slice(0, 500)}`,
            );
        }
        for (const element of document.querySelectorAll('img')) {
          if (visible(element) && !element.hasAttribute('alt'))
            issues.push(`image missing alt ${describe(element)}`);
        }
        const ids = new Map();
        for (const element of document.querySelectorAll('[id]')) {
          if (element.closest('[data-slot="scalar-reference"]')) continue;
          const count = (ids.get(element.id) || 0) + 1;
          ids.set(element.id, count);
        }
        for (const [id, count] of ids) if (count > 1) issues.push(`duplicate id #${id} (${count})`);
        for (const element of document.querySelectorAll(
          '[aria-labelledby],[aria-describedby],[aria-controls]',
        )) {
          if (element.closest('[data-slot="scalar-reference"]')) continue;
          for (const attr of ['aria-labelledby', 'aria-describedby', 'aria-controls']) {
            const value = element.getAttribute(attr);
            for (const id of (value || '').split(/\s+/).filter(Boolean)) {
              if (!document.getElementById(id))
                issues.push(`${describe(element)} has broken ${attr}=${id}`);
            }
          }
        }
        for (const element of document.querySelectorAll(
          'button button,button a[href],a[href] button,a[href] a[href]',
        )) {
          if (visible(element)) issues.push(`nested interactive ${describe(element)}`);
        }
        const main = [...document.querySelectorAll('main:not([role]),[role="main"]')].filter(
          visible,
        ).length;
        if (main !== 1) issues.push(`expected one visible main landmark; found ${main}`);
        return [...new Set(issues)];
      });
      const { nodes } = await cdp.send('Accessibility.getFullAXTree');
      const namedRoles = new Set([
        'button',
        'checkbox',
        'combobox',
        'dialog',
        'link',
        'listbox',
        'menuitem',
        'radio',
        'slider',
        'switch',
        'tab',
        'textbox',
      ]);
      for (const node of nodes) {
        const role = node.role?.value;
        if (!node.ignored && namedRoles.has(role) && !node.name?.value?.trim()) {
          let detail = `node ${node.backendDOMNodeId}`;
          if (node.backendDOMNodeId) {
            const { outerHTML } = await cdp.send('DOM.getOuterHTML', {
              backendNodeId: node.backendDOMNodeId,
            });
            detail = outerHTML.slice(0, 2000);
          }
          routeFindings.push(`unnamed accessibility-tree ${role} ${detail}`);
        }
      }
      for (const issue of routeFindings) findings.push(`${route} at ${viewport.width}px: ${issue}`);
    }
  }
  console.log(
    findings.length
      ? findings.join('\n')
      : `${routes.length} routes passed the DOM accessibility audit at ${viewports.map(({ width }) => width).join('/')}px.`,
  );
  process.exitCode = findings.length ? 1 : 0;
} finally {
  await browser.close();
}
