import { apiJson, apiPost, apiFetch } from './client';
import type { GlossaryTerm, AutoExtractResponse, DeletedResponse } from './types';

export async function listGlossary(projectId: string): Promise<GlossaryTerm[]> {
  return apiJson<GlossaryTerm[]>(`/glossary/${encodeURIComponent(projectId)}`);
}

export async function addGlossaryTerm(
  projectId: string,
  term: Partial<GlossaryTerm>,
): Promise<GlossaryTerm> {
  return apiPost<GlossaryTerm>(`/glossary/${encodeURIComponent(projectId)}`, term);
}

export async function updateGlossaryTerm(
  projectId: string,
  termId: number,
  patch: Partial<GlossaryTerm>,
): Promise<GlossaryTerm> {
  const r = await apiFetch(`/glossary/${encodeURIComponent(projectId)}/${termId}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(patch),
  });
  return r.json() as Promise<GlossaryTerm>;
}

export async function deleteGlossaryTerm(
  projectId: string,
  termId: number,
): Promise<DeletedResponse> {
  const r = await apiFetch(`/glossary/${encodeURIComponent(projectId)}/${termId}`, {
    method: 'DELETE',
  });
  return r.json() as Promise<DeletedResponse>;
}

export async function clearGlossary(
  projectId: string,
  onlyAuto: boolean = false,
): Promise<DeletedResponse> {
  const qs = onlyAuto ? '?only_auto=true' : '';
  const r = await apiFetch(`/glossary/${encodeURIComponent(projectId)}${qs}`, {
    method: 'DELETE',
  });
  return r.json() as Promise<DeletedResponse>;
}

export interface AutoExtractArgs {
  sourceLang: string;
  targetLang: string;
  segments: { id: string; text: string }[];
  maxTerms?: number;
}

export async function autoExtractGlossary(
  projectId: string,
  { sourceLang, targetLang, segments, maxTerms = 40 }: AutoExtractArgs,
): Promise<AutoExtractResponse> {
  return apiPost<AutoExtractResponse>(`/glossary/${encodeURIComponent(projectId)}/auto-extract`, {
    source_lang: sourceLang,
    target_lang: targetLang,
    segments,
    max_terms: maxTerms,
  });
}
