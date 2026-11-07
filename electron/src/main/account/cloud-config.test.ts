import { expect, it } from 'vitest';
import {
  cloudAccountsAvailable,
  cloudOrigins,
  dashboardLinks,
  resolveCloudEnvironment,
} from './cloud-config';

it('packaged builds are locked to production whatever the env var says', () => {
  expect(resolveCloudEnvironment({ packaged: true, requested: 'local' }).name).toBe('prod');
});

it('developer builds honour VOICESTUDIO_CLOUD_ENV and default to production', () => {
  expect(resolveCloudEnvironment({ packaged: false, requested: 'local' }).issuer).toBe(
    'http://localhost:8092',
  );
  expect(resolveCloudEnvironment({ packaged: false }).name).toBe('prod');
  expect(resolveCloudEnvironment({ packaged: false, requested: 'bogus' }).name).toBe('prod');
});

it('signs in with Zitadel on its own host, not the old Keycloak realm (vssaas ADR 0093)', () => {
  const prod = resolveCloudEnvironment({ packaged: true });
  expect(prod.issuer).toBe('https://id.voicestudio.sh');
  const dev = resolveCloudEnvironment({ packaged: false, requested: 'dev' });
  expect(dev.issuer).toBe('https://dev-id.voicestudio.sh');
  for (const requested of ['prod', 'dev', 'local'])
    expect(resolveCloudEnvironment({ packaged: false, requested }).issuer).not.toContain(
      '/realms/',
    );
});

it('uses the Zitadel client ID of each environment', () => {
  expect(resolveCloudEnvironment({ packaged: true }).clientId).toBe('398544795535933532');
  expect(resolveCloudEnvironment({ packaged: false }).clientId).toBe('398544795535933532');
  expect(resolveCloudEnvironment({ packaged: false, requested: 'dev' }).clientId).toBe(
    '396555644615786670',
  );
  // Each local stack registers its own client.
  expect(resolveCloudEnvironment({ packaged: false, requested: 'local' }).clientId).toBe('');
  expect(
    resolveCloudEnvironment({
      packaged: false,
      requested: 'local',
      clientId: ' 123 ',
    }).clientId,
  ).toBe('123');
});

it('ignores a client ID override in packaged builds', () => {
  expect(resolveCloudEnvironment({ packaged: true, clientId: 'attacker' }).clientId).not.toBe(
    'attacker',
  );
});

it('lists exactly the origins the app may contact', () => {
  expect([...cloudOrigins(resolveCloudEnvironment({ packaged: true }))].sort()).toEqual([
    'https://app.voicestudio.sh',
    'https://appapi.voicestudio.sh',
    'https://id.voicestudio.sh',
  ]);
  expect(
    [...cloudOrigins(resolveCloudEnvironment({ packaged: false, requested: 'dev' }))].sort(),
  ).toEqual([
    'https://dev-app.voicestudio.sh',
    'https://dev-appapi.voicestudio.sh',
    'https://dev-id.voicestudio.sh',
  ]);
});

it('lands on the dashboard after sign-in and links to its profile and plan pages', () => {
  const dev = resolveCloudEnvironment({ packaged: false, requested: 'dev' });
  expect(dev.signedInPage).toBe('https://dev-app.voicestudio.sh/desktop-signed-in');
  expect(dashboardLinks(resolveCloudEnvironment({ packaged: true }))).toEqual({
    profile: 'https://app.voicestudio.sh/settings/profile',
    deletion: 'https://app.voicestudio.sh/settings/deletion-review',
    plan: 'https://app.voicestudio.sh/settings/plan',
  });
});

it('offers sign-in in every build whose environment has a client, packaged included', () => {
  expect(cloudAccountsAvailable(resolveCloudEnvironment({ packaged: true }))).toBe(true);
  expect(cloudAccountsAvailable(resolveCloudEnvironment({ packaged: false }))).toBe(true);
  // A local stack without its own registered client cannot sign in.
  expect(
    cloudAccountsAvailable(resolveCloudEnvironment({ packaged: false, requested: 'local' })),
  ).toBe(false);
});

it('rejects a malformed account config instead of shipping a broken sign-in', async () => {
  const { parseAccountConfig, ACCOUNT_CONFIG } =
    await import('../../../../config/account-config.mjs');
  const good = JSON.parse(JSON.stringify(ACCOUNT_CONFIG));
  expect(() => parseAccountConfig(good)).not.toThrow();
  const broken = (mutate: (c: typeof good) => void) => {
    const copy = JSON.parse(JSON.stringify(good));
    mutate(copy);
    return () => parseAccountConfig(copy);
  };
  expect(broken((c) => (c.environments.prod.issuer = 'http://id.voicestudio.sh'))).toThrow();
  expect(broken((c) => (c.environments.prod.clientId = ''))).toThrow();
  expect(broken((c) => (c.environments.prod.dashboard = 'https://app.voicestudio.sh/'))).toThrow();
  expect(broken((c) => (c.environments.prod.secret = 'x'))).toThrow();
  expect(broken((c) => (c.browserSessionMaxAgeSeconds = 0))).toThrow();
});
