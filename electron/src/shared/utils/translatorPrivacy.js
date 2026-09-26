const ONLINE_PROVIDERS = ['google', 'deepl', 'mymemory', 'microsoft', 'openai'];
const OFFLINE_PROVIDERS = ['nllb', 'argos', 'libretranslate'];
export function translatorPrivacy(provider) {
  return ONLINE_PROVIDERS.includes(provider)
    ? 'online'
    : OFFLINE_PROVIDERS.includes(provider)
      ? 'offline'
      : 'unknown';
}
