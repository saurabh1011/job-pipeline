import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiFetch } from './client';

export interface Company { name: string; ats: string; board_slug?: string; playwright?: boolean; }
export interface Prefs {
  match_threshold: number; llm_provider: string; us_only: boolean;
  title_keywords: string[]; title_exclude_keywords: string[];
  preferred_locations: string[]; acceptable_locations: string[];
  excluded_location_keywords: string[];
}

export function useCompanies() {
  return useQuery({ queryKey: ['companies'], queryFn: () => apiFetch<Company[]>('GET', '/api/settings/companies') });
}

export function usePreferences() {
  return useQuery({ queryKey: ['prefs'], queryFn: () => apiFetch<Prefs>('GET', '/api/settings/preferences') });
}

export function useSavePreferences() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (prefs: Prefs) => apiFetch('PUT', '/api/settings/preferences', prefs),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['prefs'] }),
  });
}

export function useRemoveCompany() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (name: string) => apiFetch('DELETE', `/api/settings/companies/${encodeURIComponent(name)}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['companies'] }),
  });
}

export function useAddCompany() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (c: { name: string; ats: string; board_slug?: string }) =>
      apiFetch('POST', '/api/settings/companies', c),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['companies'] }),
  });
}

export function useDetectAts() {
  return useMutation({
    mutationFn: (name: string) => apiFetch<{ ats?: string; board_slug?: string; error?: string }>('POST', '/api/companies/detect', { name }),
  });
}

export function useLogs() {
  return useQuery({ queryKey: ['logs'], queryFn: () => apiFetch<any[]>('GET', '/api/logs') });
}

export function useLogFile(filename: string | null) {
  return useQuery({
    queryKey: ['log', filename],
    queryFn: () => apiFetch<{ content: string }>('GET', `/api/logs/${encodeURIComponent(filename!)}`),
    enabled: !!filename,
  });
}

export function useMe() {
  return useQuery({ queryKey: ['me'], queryFn: () => apiFetch<any>('GET', '/api/auth/me'), retry: false });
}
