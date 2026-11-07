export type AccountEnvironmentName = 'prod' | 'dev' | 'local';

export interface AccountEnvironmentConfig {
  readonly issuer: string;
  readonly accountApi: string;
  readonly syncApi: string;
  readonly dashboard: string;
  readonly clientId: string;
}

export interface AccountConfig {
  readonly schemaVersion: 1;
  readonly browserSessionMaxAgeSeconds: number;
  readonly environments: Readonly<Record<AccountEnvironmentName, AccountEnvironmentConfig>>;
}

export function parseAccountConfig(input: unknown): AccountConfig;
export const ACCOUNT_CONFIG: AccountConfig;
