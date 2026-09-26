import { apiJson, apiPost, apiFetch } from './client';
import type { ProjectSummary, ProjectDetail } from './types';

export async function listProjects(): Promise<ProjectSummary[]> {
  return apiJson<ProjectSummary[]>('/projects');
}

export async function saveProject(
  body: Record<string, unknown>,
  id?: string,
): Promise<ProjectDetail> {
  if (id) return apiPost<ProjectDetail>(`/projects/${id}`, body, { method: 'PUT' });
  return apiPost<ProjectDetail>('/projects', body);
}

export async function loadProject(id: string): Promise<ProjectDetail> {
  return apiJson<ProjectDetail>(`/projects/${id}`);
}

export async function renameProject(id: string, name: string): Promise<ProjectDetail> {
  return apiPost<ProjectDetail>(`/projects/${id}`, { name }, { method: 'PATCH' });
}

export async function deleteProject(id: string): Promise<Response> {
  return apiFetch(`/projects/${id}`, { method: 'DELETE' });
}
