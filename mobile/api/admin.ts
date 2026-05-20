import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiFetch } from './client';

export function useAllowedEmails() {
  return useQuery({ queryKey: ['admin-emails'], queryFn: () => apiFetch<any[]>('GET', '/api/admin/allowed-emails') });
}

export function useAdminUsers() {
  return useQuery({ queryKey: ['admin-users'], queryFn: () => apiFetch<any[]>('GET', '/api/admin/users') });
}

export function useAddEmail() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (email: string) => apiFetch('POST', '/api/admin/allowed-emails', { email }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['admin-emails'] }),
  });
}

export function useRemoveEmail() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (email: string) => apiFetch('DELETE', `/api/admin/allowed-emails/${encodeURIComponent(email)}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['admin-emails'] }),
  });
}
