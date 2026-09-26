export function hasCompleteTranslation(segments, languageCode) {
  return (
    !!languageCode &&
    segments.length > 0 &&
    segments.every((segment) => {
      const text = segment.translations?.[languageCode];
      return typeof text === 'string' && text.trim().length > 0;
    })
  );
}

export function multiLangTargets(activeLanguage, activeCode, selected) {
  const targets = [];
  const seen = new Set();
  const add = (lang, code) => {
    if (!code || code === 'und' || seen.has(code)) return;
    seen.add(code);
    targets.push({ lang: lang || code.toUpperCase(), code });
  };
  if (activeLanguage && activeLanguage !== 'Auto') add(activeLanguage, activeCode);
  for (const item of selected || []) add(item?.lang, item?.code);
  return targets;
}

export function translationProgressByCode(segments, targets) {
  return Object.fromEntries(
    (targets || []).map(({ code }) => {
      let ready = 0;
      let total = 0;
      for (const segment of segments || []) {
        const source = segment.text_original || segment.text || '';
        if (!String(source).trim()) continue;
        total += 1;
        const translated = segment.translations?.[code];
        if (typeof translated === 'string' && translated.trim()) {
          ready += 1;
        }
      }
      return [code, { ready, total }];
    }),
  );
}
