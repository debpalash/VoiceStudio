import { randomBytes } from 'node:crypto';
import type { AccountSession } from './account-session';
import type { CloudActivity } from './cloud-activity';
import type { AvatarCache } from './avatar-cache';
import type { CloudEnvironment } from './cloud-config';

export interface AccountProfile {
  email: string | null;
  displayName: string | null;
  avatarUrl: string | null;
  /** Identifies the picture; the cache is keyed by it (the URL always expires). */
  avatarArtifactId: string | null;
  personalOrganizationId: string | null;
  /** The personal Organization's plan and storage, when /v1/account includes them. */
  summary: {
    planCode: string;
    planName: string;
    planOrigin: string;
    planEndsAt: string | null;
    storageUsedBytes: number;
    storageLimitBytes: number;
  } | null;
  organizations: Array<{
    id: string;
    slug: string;
    displayName: string;
    role: string;
    kind: string;
  }>;
}

interface RawAccount {
  user_id?: string | null;
  email?: string | null;
  display_name?: string | null;
  avatar_url?: string | null;
  avatar_artifact_id?: string | null;
  personal_organization_id?: string | null;
  plan?: { code?: string; name?: string; origin?: string; ends_at?: string | null } | null;
  storage?: { limit_bytes?: number; used_bytes?: number } | null;
  organizations?: Array<{
    id: string;
    slug: string;
    display_name: string;
    role: string;
    kind: string;
  }>;
}

function toProfile(a: RawAccount): AccountProfile {
  const organizations = (a.organizations ?? []).map((o) => ({
    id: o.id,
    slug: o.slug,
    displayName: o.display_name,
    role: o.role,
    kind: o.kind,
  }));
  const personal =
    organizations.find((o) => o.id === a.personal_organization_id) ??
    organizations.find((o) => o.kind === 'personal');
  return {
    email: a.email || null,
    // The server leaves out an empty name and an unverified email; the personal
    // workspace is named after the person, so it stands in.
    displayName: a.display_name || (!a.email && personal?.displayName) || null,
    avatarUrl: a.avatar_url ?? null,
    avatarArtifactId: a.avatar_artifact_id || null,
    personalOrganizationId: a.personal_organization_id ?? null,
    organizations,
    summary: a.plan
      ? {
          planCode: String(a.plan.code ?? ''),
          planName: String(a.plan.name || a.plan.code || ''),
          planOrigin: String(a.plan.origin ?? 'default'),
          planEndsAt: a.plan.ends_at ?? null,
          storageUsedBytes: Number(a.storage?.used_bytes ?? 0),
          storageLimitBytes: Number(a.storage?.limit_bytes ?? 0),
        }
      : null,
  };
}

/** The plan of an Organization, as the account screen shows it (entitlements API). */
export interface AccountPlan {
  planCode: string;
  planName: string;
  storageUsedBytes: number;
  storageLimitBytes: number;
  /** `sync.settings`: settings, shortcuts and favorites sync. */
  settingsSync: boolean;
  /** `pro.desktop`: VoiceStudio Pro features. */
  proDesktop: boolean;
}

interface RawEntitlements {
  plan?: { code?: string; name?: string };
  values?: Array<{ key?: string; value?: number }>;
  storage?: { limit_bytes?: number; used_bytes?: number };
}

const ORGANIZATION_ID = /^[A-Za-z0-9]{1,64}$/;

const AVATAR_TYPES = new Set(['image/png', 'image/jpeg', 'image/webp', 'image/gif']);
const AVATAR_MAX_BYTES = 2 * 1024 * 1024;

export class AccountApi {
  // Optional endpoint availability is not an entitlement grant. Avoid repeating
  // an absent deployment route for every mounted account subscriber.
  private entitlementLookup: {
    organizationId: string;
    generation: number;
    unavailableUntil: number;
    pending: Promise<AccountPlan | null> | null;
  } | null = null;

  constructor(
    private readonly env: CloudEnvironment,
    private readonly session: AccountSession,
    private readonly activity: CloudActivity,
    private readonly avatars?: AvatarCache,
  ) {}

  async get(): Promise<AccountProfile | null> {
    const generation = this.session.identityGeneration();
    const raw = await this.call(this.env.accountApi, { method: 'GET' });
    if (!raw || generation !== this.session.identityGeneration()) return null;
    const userId = (raw as RawAccount).user_id;
    const bound = this.session.bindIdentity(userId, generation);
    if (typeof userId === 'string' && /^[A-Za-z0-9]{16}$/.test(userId) && !bound) {
      this.session.endSession('signed_out_expired');
      return null;
    }
    return toProfile(raw as RawAccount);
  }

  /** Plan, storage and what the plan includes; null when signed out or unavailable. */
  async entitlements(organizationId: string): Promise<AccountPlan | null> {
    if (!ORGANIZATION_ID.test(organizationId)) return null;
    const generation = this.session.identityGeneration();
    if (
      this.entitlementLookup?.generation !== generation ||
      this.entitlementLookup.organizationId !== organizationId
    ) {
      this.entitlementLookup = { organizationId, generation, unavailableUntil: 0, pending: null };
    }
    const lookup = this.entitlementLookup;
    if (lookup.unavailableUntil > Date.now()) return null;
    if (lookup.pending) return lookup.pending;
    const pending = this.readEntitlements(organizationId, generation).catch((error: unknown) => {
      const status = (error as { status?: number } | null)?.status;
      if (status === 404 || status === 501) {
        lookup.unavailableUntil = Date.now() + 60_000;
        return null;
      }
      throw error;
    });
    lookup.pending = pending;
    try {
      return await pending;
    } finally {
      if (lookup.pending === pending) lookup.pending = null;
    }
  }

  private async readEntitlements(
    organizationId: string,
    generation: number,
  ): Promise<AccountPlan | null> {
    const raw = (await this.call(
      `${this.env.accountApi}/organizations/${organizationId}/entitlements`,
      {
        method: 'GET',
      },
    )) as RawEntitlements | null;
    if (!raw || generation !== this.session.identityGeneration()) return null;
    const flag = (key: string) =>
      (raw.values ?? []).some((v) => v.key === key && Number(v.value) > 0);
    return {
      planCode: String(raw.plan?.code ?? ''),
      planName: String(raw.plan?.name ?? raw.plan?.code ?? ''),
      storageUsedBytes: Number(raw.storage?.used_bytes ?? 0),
      storageLimitBytes: Number(raw.storage?.limit_bytes ?? 0),
      settingsSync: flag('sync.settings'),
      proDesktop: flag('pro.desktop'),
    };
  }

  /**
   * The profile photo as a `data:` URL for the renderer (its CSP allows no remote
   * images). Cached by `avatar_artifact_id`: downloaded once, and again only when
   * that ID changes; a cleared ID drops it. Downloaded through the Cloud gate, so
   * switching off stops it; only small raster images, never SVG; failures simply
   * show initials. The download link is never stored or logged.
   */
  async avatar(profile: {
    avatarArtifactId: string | null;
    avatarUrl: string | null;
  }): Promise<string | null> {
    const artifactId = profile.avatarArtifactId;
    if (!artifactId) {
      this.avatars?.clear();
      return null;
    }
    const cached = this.avatars?.get(artifactId);
    if (cached) return cached;
    let target: URL;
    try {
      if (!profile.avatarUrl) return null;
      target = new URL(profile.avatarUrl);
    } catch {
      return null;
    }
    if (target.protocol !== 'https:') return null;
    try {
      const res = await this.activity.fetch(
        target.toString(),
        {},
        { serverIssued: true, timeoutMs: 10_000 },
      );
      const type = (res.headers.get('content-type') ?? '').split(';')[0].trim().toLowerCase();
      if (!res.ok || !AVATAR_TYPES.has(type)) return null;
      if (Number(res.headers.get('content-length') ?? 0) > AVATAR_MAX_BYTES) return null;
      const bytes = Buffer.from(await res.arrayBuffer());
      if (bytes.length === 0 || bytes.length > AVATAR_MAX_BYTES) return null;
      const dataUrl = `data:${type};base64,${bytes.toString('base64')}`;
      this.avatars?.set(artifactId, dataUrl);
      return dataUrl;
    } catch {
      return null;
    }
  }

  async patch(changes: {
    display_name?: string;
    theme?: 'system' | 'light' | 'dark';
    language?: string;
  }): Promise<AccountProfile | null> {
    const raw = await this.call(this.env.accountApi, {
      method: 'PATCH',
      body: JSON.stringify(changes),
      headers: {
        'content-type': 'application/json',
        'idempotency-key': randomBytes(16).toString('hex'),
      },
    });
    return raw ? toProfile(raw as RawAccount) : null;
  }

  private async call(
    url: string,
    init: { method: string; body?: string; headers?: Record<string, string> },
  ): Promise<unknown> {
    for (let attempt = 0; attempt < 2; attempt++) {
      const token = await this.session.getAccessToken();
      if (!token) return null;
      const res = await this.activity.fetch(url, {
        ...init,
        headers: { ...init.headers, authorization: `Bearer ${token.access_token}` },
      });
      if (res.status === 401) {
        if (attempt === 0) this.session.invalidateAccessToken();
        continue;
      }
      if (!res.ok) {
        // Status and path only: never the token, never a query string.
        console.warn(`[account] ${init.method} ${new URL(url).pathname} -> ${res.status}`);
        throw Object.assign(new Error(`Account request failed (${res.status})`), {
          status: res.status,
        });
      }
      return await res.json();
    }
    this.session.endSession('signed_out_expired');
    return null;
  }
}
