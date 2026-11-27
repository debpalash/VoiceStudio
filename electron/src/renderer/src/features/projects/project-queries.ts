import { queryOptions } from '@tanstack/react-query';
import { apiJson } from '@/lib/api/client';
import { projectLibrary } from '@/features/longform/project-library';
import type { DubProject } from './project-format';

export const dubProjectsQuery = queryOptions({
  queryKey: ['projects'],
  queryFn: ({ signal }) => apiJson<DubProject[]>('/projects', { signal }),
});

export const longformProjectsQuery = queryOptions({
  queryKey: ['longform-projects'],
  queryFn: projectLibrary.list,
});
