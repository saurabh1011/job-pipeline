import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiFetch } from './client';

export interface Profile { profile_id: string; name: string; is_legacy: boolean; }

export function useProfiles() {
  return useQuery({ queryKey: ['profiles'], queryFn: () => apiFetch<Profile[]>('GET', '/api/profiles') });
}

export function useCreateProfile() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (name: string) => apiFetch<Profile>('POST', '/api/profiles', { name }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['profiles'] }),
  });
}

export function useRenameProfile() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, name }: { id: string; name: string }) =>
      apiFetch('PATCH', `/api/profiles/${id}`, { name }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['profiles'] }),
  });
}

export function useDeleteProfile() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => apiFetch('DELETE', `/api/profiles/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['profiles'] }),
  });
}

export function useSchedule(profileId: string | null) {
  return useQuery({
    queryKey: ['schedule', profileId],
    queryFn: async () => {
      const { authStore } = await import('../store/auth');
      const [base, token] = await Promise.all([authStore.getBaseUrl(), authStore.getToken()]);
      const res = await fetch(`${base}/api/profiles/${profileId}/schedule`, {
        headers: { 'X-API-Key': token ?? '' },
      });
      if (res.status === 404) return null;
      if (!res.ok) throw new Error(res.statusText);
      return res.json();
    },
    enabled: !!profileId,
  });
}

export function useSaveSchedule(profileId: string | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { time_1: string | null; time_2: string | null; timezone: string; enabled: boolean }) =>
      apiFetch('PUT', `/api/profiles/${profileId}/schedule`, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['schedule', profileId] }),
  });
}

export function useClearSchedule(profileId: string | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => apiFetch('DELETE', `/api/profiles/${profileId}/schedule`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['schedule', profileId] }),
  });
}

export function useResumeInfo() {
  return useQuery({
    queryKey: ['resume'],
    queryFn: async () => {
      const { authStore } = await import('../store/auth');
      const [base, token] = await Promise.all([authStore.getBaseUrl(), authStore.getToken()]);
      const res = await fetch(`${base}/api/resume`, { headers: token ? { 'X-API-Key': token } : {} });
      if (res.status === 404) return null;
      if (!res.ok) throw new Error(res.statusText);
      return res.json() as Promise<{ filename: string; size_bytes: number; extension: string }>;
    },
  });
}

export function useDeleteResume() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => apiFetch('DELETE', '/api/resume'),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['resume'] }),
  });
}
