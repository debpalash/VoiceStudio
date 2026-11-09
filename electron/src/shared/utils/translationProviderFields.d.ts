export interface ProviderField {
  key: string;
  labelKey: string;
  placeholder?: string;
  placeholderKey?: string;
  isPassword?: boolean;
}

export interface ProviderGroup {
  id: string;
  name: string;
  fields: readonly ProviderField[];
}

export const PROVIDER_GROUPS: readonly ProviderGroup[];
