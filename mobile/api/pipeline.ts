import { useMutation, useQuery } from '@tanstack/react-query';
import { apiFetch } from './client';

export interface TaskState {
  id: string; status: 'pending' | 'running' | 'done' | 'error';
  logs: string[]; started_at: string | null; ended_at: string | null;
}

export function useTriggerRun() {
  return useMutation({
    mutationFn: (body: { action?: string; group?: string; companies?: string[] }) =>
      apiFetch<{ task_id: string }>('POST', '/api/pipeline/run', body),
  });
}

export function useTask(taskId: string | null) {
  return useQuery({
    queryKey: ['task', taskId],
    queryFn: () => apiFetch<TaskState>('GET', `/api/tasks/${taskId}`),
    enabled: !!taskId,
    refetchInterval: (query) => {
      const s = query.state.data?.status;
      return s === 'done' || s === 'error' ? false : 1500;
    },
  });
}

export function useRuns() {
  return useQuery({
    queryKey: ['runs'],
    queryFn: () => apiFetch<any[]>('GET', '/api/runs?limit=30'),
  });
}

export function useCompanyList() {
  return useQuery({
    queryKey: ['company-list'],
    queryFn: () => apiFetch<any[]>('GET', '/api/companies'),
  });
}
