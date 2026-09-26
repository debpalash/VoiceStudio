import { apiJson, apiPost, apiFetch } from './client';
import type { Profile, ProfileUsage, PersonaImportResult, PersonaBundleMeta } from './types';

export async function listProfiles(): Promise<Profile[]> {
  return apiJson<Profile[]>('/profiles');
}

export async function getProfile(id: string): Promise<Profile> {
  return apiJson<Profile>(`/profiles/${id}`);
}

export async function getProfileUsage(id: string): Promise<ProfileUsage> {
  return apiJson<ProfileUsage>(`/profiles/${id}/usage`);
}

export async function createProfile(formData: FormData): Promise<Profile> {
  return apiPost<Profile>('/profiles', formData);
}

export async function updateProfile(id: string, patch: Partial<Profile>): Promise<Profile> {
  const r = await apiFetch(`/profiles/${id}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(patch),
  });
  return r.json() as Promise<Profile>;
}

export async function deleteProfile(id: string): Promise<Response> {
  return apiFetch(`/profiles/${id}`, { method: 'DELETE' });
}

export async function recordConsent(id: string, formData: FormData): Promise<unknown> {
  return apiPost(`/profiles/${id}/consent`, formData);
}

export async function revokeConsent(id: string): Promise<Response> {
  return apiFetch(`/profiles/${id}/consent`, { method: 'DELETE' });
}

export async function lockProfile(id: string, formData: FormData): Promise<unknown> {
  return apiPost(`/profiles/${id}/lock`, formData);
}

export async function unlockProfile(id: string): Promise<unknown> {
  return apiPost(`/profiles/${id}/unlock`);
}

// ── Portable persona bundles (.ovsvoice, #29) ──────────────────────────────

/** Build + download a .ovsvoice persona bundle. Returns the ZIP blob. */
export async function exportPersona(
  id: string,
  opts?: { license_spdx?: string; tags?: string; include_reference?: boolean },
): Promise<Blob> {
  const q = new URLSearchParams();
  if (opts?.license_spdx) q.set('license_spdx', opts.license_spdx);
  if (opts?.tags) q.set('tags', opts.tags);
  if (opts?.include_reference === false) q.set('include_reference', 'false');
  const qs = q.toString();
  const r = await apiFetch(`/personas/export/${id}${qs ? `?${qs}` : ''}`, { method: 'POST' });
  if (!r.ok) throw new Error(String(r.status));
  return r.blob();
}

/** Import a .ovsvoice (or legacy .omnivoice) bundle → a new profile. */
export async function importPersona(formData: FormData): Promise<PersonaImportResult> {
  return apiPost<PersonaImportResult>('/personas/import', formData);
}

/** Read a bundle's manifest + consent summary without importing it. */
export async function inspectPersona(formData: FormData): Promise<PersonaBundleMeta> {
  return apiPost<PersonaBundleMeta>('/personas/inspect', formData);
}
