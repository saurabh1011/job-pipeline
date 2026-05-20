import { authStore } from '../store/auth';

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
  get isUnauthorized() { return this.status === 401; }
}

export async function apiFetch<T>(
  method: string,
  path: string,
  body?: unknown,
): Promise<T> {
  const [baseUrl, token] = await Promise.all([
    authStore.getBaseUrl(),
    authStore.getToken(),
  ]);

  const headers: Record<string, string> = {};
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  if (token) headers['X-API-Key'] = token;

  const res = await fetch(`${baseUrl}${path}`, {
    method,
    headers,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });

  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new ApiError(res.status, err.detail || res.statusText);
  }
  return res.json() as T;
}

export async function apiUpload<T>(path: string, formData: FormData): Promise<T> {
  const [baseUrl, token] = await Promise.all([
    authStore.getBaseUrl(),
    authStore.getToken(),
  ]);
  const headers: Record<string, string> = {};
  if (token) headers['X-API-Key'] = token;
  const res = await fetch(`${baseUrl}${path}`, { method: 'POST', headers, body: formData });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new ApiError(res.status, err.detail || res.statusText);
  }
  return res.json() as T;
}
