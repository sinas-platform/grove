const API_BASE = import.meta.env.VITE_GROVE_API ?? '/api/v1';

const ACCESS_KEY = 'grove_access_token';
const REFRESH_KEY = 'grove_refresh_token';

export const tokens = {
  get access() {
    return localStorage.getItem(ACCESS_KEY);
  },
  get refresh() {
    return localStorage.getItem(REFRESH_KEY);
  },
  set(access: string, refresh: string) {
    localStorage.setItem(ACCESS_KEY, access);
    localStorage.setItem(REFRESH_KEY, refresh);
  },
  clear() {
    localStorage.removeItem(ACCESS_KEY);
    localStorage.removeItem(REFRESH_KEY);
  },
};

let refreshInFlight: Promise<string | null> | null = null;
let onUnauthenticated: (() => void) | null = null;

export function setUnauthenticatedHandler(fn: () => void) {
  onUnauthenticated = fn;
}

async function tryRefresh(): Promise<string | null> {
  if (!tokens.refresh) return null;
  if (refreshInFlight) return refreshInFlight;
  refreshInFlight = (async () => {
    try {
      const res = await fetch(`${API_BASE}/auth/refresh`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ refresh_token: tokens.refresh }),
      });
      if (!res.ok) return null;
      const body = (await res.json()) as { access_token: string };
      // Refresh response only returns a new access token.
      localStorage.setItem(ACCESS_KEY, body.access_token);
      return body.access_token;
    } catch {
      return null;
    } finally {
      refreshInFlight = null;
    }
  })();
  return refreshInFlight;
}

async function doFetch(path: string, init: RequestInit, accessToken: string | null): Promise<Response> {
  const headers = new Headers(init.headers ?? {});
  if (init.body && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json');
  }
  if (accessToken) headers.set('Authorization', `Bearer ${accessToken}`);
  return fetch(`${API_BASE}${path}`, { ...init, headers });
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  // Don't auto-refresh for the refresh endpoint itself.
  const isAuthCall = path.startsWith('/auth/');

  let res = await doFetch(path, init, tokens.access);

  if (res.status === 401 && !isAuthCall) {
    const fresh = await tryRefresh();
    if (fresh) {
      res = await doFetch(path, init, fresh);
    }
    if (res.status === 401) {
      tokens.clear();
      onUnauthenticated?.();
      throw new Error('unauthenticated');
    }
  }

  if (!res.ok) {
    const text = await res.text().catch(() => '');
    throw new Error(`${res.status} ${res.statusText}: ${text}`);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}
