/**
 * Online translator credentials, grouped by provider so Settings shows one
 * card and one Save per provider. Provider names are brands (not translated).
 */
export const PROVIDER_GROUPS = [
  {
    id: 'google',
    name: 'Google Cloud',
    fields: [
      {
        key: 'GOOGLE_TRANSLATE_API_KEY',
        labelKey: 'settings.llmp_api_key',
        isPassword: true,
      },
    ],
  },
  {
    id: 'deepl',
    name: 'DeepL',
    fields: [
      {
        key: 'DEEPL_API_KEY',
        labelKey: 'settings.llmp_api_key',
        placeholderKey: 'credentials.deepl_key_placeholder',
        isPassword: true,
      },
      {
        key: 'DEEPL_BASE_URL',
        labelKey: 'credentials.base_url',
        placeholder: 'https://api.deepl.com/v2',
      },
    ],
  },
  {
    id: 'microsoft',
    name: 'Microsoft Azure',
    fields: [
      {
        key: 'MICROSOFT_API_KEY',
        labelKey: 'settings.llmp_api_key',
        placeholderKey: 'credentials.microsoft_key_placeholder',
        isPassword: true,
      },
      { key: 'MICROSOFT_REGION', labelKey: 'credentials.region' },
      {
        key: 'MICROSOFT_BASE_URL',
        labelKey: 'credentials.base_url',
        placeholder: 'https://api.cognitive.microsofttranslator.com',
      },
    ],
  },
  {
    id: 'aws',
    name: 'AWS',
    fields: [
      { key: 'AWS_PROFILE', labelKey: 'credentials.aws_profile' },
      { key: 'AWS_REGION', labelKey: 'credentials.region' },
    ],
  },
];
