function resolveApiBase(): string {
  if (import.meta.env.VITE_API_URL) {
    return import.meta.env.VITE_API_URL.replace(/\/$/, '')
  }
  if (typeof window !== 'undefined' && window.location?.hostname) {
    return `${window.location.protocol}//${window.location.hostname}:8000`
  }
  return 'http://localhost:8000'
}

const BASE = resolveApiBase();
export const apiUrl = (path: string) => `${BASE}${path}`;
export async function api<T = any>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers || {});
  if (!(init.body instanceof FormData) && init.body && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json');
  const token = sessionStorage.getItem('cortex_token');
  if (token) headers.set('Authorization', `Bearer ${token}`);
  const response = await fetch(apiUrl(path), { ...init, headers, credentials: 'include' });
  if (response.status === 401) { sessionStorage.removeItem('cortex_token'); window.dispatchEvent(new Event('cortex:unauthorized')); }
  if (!response.ok) {
    let message = `Request failed (${response.status})`;
    try { const body = await response.json(); message = body.detail || body.message || message; } catch {}
    throw new Error(message);
  }
  if (response.status === 204) return undefined as T;
  return response.json();
}
export const post = <T=any>(path:string, body?:unknown) => api<T>(path,{method:'POST',body:body instanceof FormData ? body : JSON.stringify(body ?? {})});
export const patch = <T=any>(path:string, body:unknown) => api<T>(path,{method:'PATCH',body:JSON.stringify(body)});
export const del = <T=any>(path:string) => api<T>(path,{method:'DELETE'});
