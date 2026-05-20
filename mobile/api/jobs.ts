import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiFetch } from './client';

export interface Job {
  company: string; job_id: string; title: string; location: string;
  status: string; match_score: number | null; date_seen: string;
  date_posted: string | null; date_last_sourced: string | null;
  description: string | null; apply_url: string | null;
  cover_letter: string | null; resume_diff: string | null;
  match_summary: string | null; match_strengths: string[] | null;
  match_gaps: string[] | null; match_requirements: any[] | null;
  match_resume_suggestions: string[] | null;
}

export function useJobs() {
  return useQuery({
    queryKey: ['jobs'],
    queryFn: () => apiFetch<{ jobs: Job[] }>('GET', '/api/jobs').then(r => r.jobs),
    staleTime: 30_000,
  });
}

export function useJob(company: string, jobId: string) {
  return useQuery({
    queryKey: ['job', company, jobId],
    queryFn: () => apiFetch<Job>('GET', `/api/jobs/${company}/${jobId}`),
  });
}

export function usePatchJob() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ company, jobId, status }: { company: string; jobId: string; status: string }) =>
      apiFetch('PATCH', `/api/jobs/${company}/${jobId}`, { status }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['jobs'] }); },
  });
}

export function useBulkStatus() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ jobs, status }: { jobs: { company: string; job_id: string }[]; status: string }) =>
      apiFetch('POST', '/api/jobs/bulk-status', { jobs, status }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['jobs'] }); },
  });
}

export function useSaveCoverLetter() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ company, jobId, content }: { company: string; jobId: string; content: string }) =>
      apiFetch('PUT', `/api/jobs/${company}/${jobId}/cover-letter`, { content }),
    onSuccess: (_d, v) => { qc.invalidateQueries({ queryKey: ['job', v.company, v.jobId] }); },
  });
}

export function useRescoreJob() {
  return useMutation({
    mutationFn: ({ company, jobId }: { company: string; jobId: string }) =>
      apiFetch<{ task_id: string }>('POST', `/api/jobs/${company}/${jobId}/rescore`),
  });
}

export function useAnalyzeJob() {
  return useMutation({
    mutationFn: ({ company, jobId }: { company: string; jobId: string }) =>
      apiFetch<{ task_id: string }>('POST', `/api/jobs/${company}/${jobId}/analyze`),
  });
}

export function useGenerateCoverLetter() {
  return useMutation({
    mutationFn: ({ company, jobId }: { company: string; jobId: string }) =>
      apiFetch<{ task_id: string }>('POST', `/api/jobs/${company}/${jobId}/generate-cover-letter`),
  });
}
