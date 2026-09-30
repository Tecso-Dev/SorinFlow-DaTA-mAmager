// GET /api/proxies (`ProxyResponse` in app/schemas) does not carry
// exit_country/exit_ip/is_hosting — those are only ever returned by
// POST /proxies/{id}/test and POST /proxies/test-all, and only stick to a
// row once that probe has run. Every place that reads them must treat them
// as possibly absent, never assume "untested" means "not Iranian".
export type Proxy = {
  id: number;
  address: string;
  port: number;
  protocol: "http" | "https" | "socks5" | string;
  is_active: boolean;
  is_working: boolean;
  fail_count: number;
  success_count: number;
  avg_response_time: number | null;
  last_checked: string | null;
  exit_country?: string | null;
  exit_ip?: string | null;
  is_hosting?: boolean | null;
};

export type ProxyList = { items: Proxy[]; total: number };

/** POST /proxies/{id}/test and one entry of POST /proxies/test-all's results. */
export type ProxyProbe = {
  proxy_id?: number;
  address?: string;
  success: boolean;
  response_time?: number;
  status_code?: number;
  message?: string;
  error?: string;
  exit_country?: string | null;
  exit_ip?: string | null;
  is_hosting?: boolean | null;
};

export type TestAllResult = { total: number; working: number; iranian: number; results: ProxyProbe[] };

export type ImportResult = { imported: number; skipped: number; refused: number; tested: { tested: number; working: number } | null };
