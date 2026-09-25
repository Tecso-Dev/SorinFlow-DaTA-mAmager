// The one door to the backend. Same origin (Traefik routes /api to FastAPI in
// production, next.config rewrites it in development), the session rides in
// an httpOnly cookie, and every state-changing call carries the CSRF token
// the backend put in a readable cookie next to it.

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    public code?: string,
    public detail?: unknown,
  ) {
    super(message);
  }
}

const SAFE = new Set(["GET", "HEAD", "OPTIONS"]);

export function csrfToken(): string | undefined {
  if (typeof document === "undefined") return undefined;
  const m = document.cookie.match(/(?:^|;\s*)(?:__Host-)?sf_csrf=([^;]+)/);
  return m ? decodeURIComponent(m[1]) : undefined;
}

type Listener = (e: ApiError) => void;
const listeners = new Set<Listener>();
/** The shell subscribes to hear about 401 (session over) and 503 (maintenance). */
export function onApiError(fn: Listener) {
  listeners.add(fn);
  return () => {
    listeners.delete(fn);
  };
}

function messageOf(status: number, body: unknown): { message: string; code?: string } {
  const b = body as { detail?: unknown; code?: string } | null;
  const code = b?.code ?? (typeof b?.detail === "object" && b?.detail ? (b.detail as { code?: string }).code : undefined);
  if (typeof b?.detail === "string") return { message: b.detail, code };
  if (Array.isArray(b?.detail)) return { message: "بعضی از فیلدها درست پر نشده‌اند.", code: "validation" };
  if (typeof b?.detail === "object" && b?.detail && "message" in (b.detail as object)) {
    return { message: String((b.detail as { message: unknown }).message), code };
  }
  if (status === 429) return { message: "تعداد تلاش‌ها زیاد بود؛ کمی بعد دوباره امتحان کنید.", code };
  if (status >= 500) return { message: "خطای سرور؛ چند لحظهٔ دیگر دوباره امتحان کنید.", code };
  return { message: "درخواست انجام نشد.", code };
}

/** What a route answers when the caller's own phone must be verified first
 *  (app/auth/dependencies.require_verified_phone). */
export type PhoneGateDetail = { code: "phone_unverified"; message: string; phone: string | null };
type PhoneGate = (d: PhoneGateDetail) => Promise<boolean>;
let phoneGate: PhoneGate | null = null;
/** The shell's verification dialog: resolves true once the number is
 *  verified, and the refused call is then made again. */
export function setPhoneGate(g: PhoneGate | null) {
  phoneGate = g;
}

type ApiInit = Omit<RequestInit, "body"> & { json?: unknown; body?: BodyInit };

export async function api<T = unknown>(path: string, init: ApiInit = {}): Promise<T> {
  try {
    return await request<T>(path, init);
  } catch (e) {
    const d = e instanceof ApiError && e.status === 403 ? (e.detail as { detail?: PhoneGateDetail } | null)?.detail : null;
    if (d && typeof d === "object" && d.code === "phone_unverified" && phoneGate && (await phoneGate(d))) {
      return request<T>(path, init);
    }
    throw e;
  }
}

async function request<T>(path: string, init: ApiInit): Promise<T> {
  const method = (init.method ?? (init.json !== undefined ? "POST" : "GET")).toUpperCase();
  const headers = new Headers(init.headers);
  headers.set("Accept", "application/json");
  let body = init.body;
  if (init.json !== undefined) {
    headers.set("Content-Type", "application/json");
    body = JSON.stringify(init.json);
  }
  if (!SAFE.has(method)) {
    const t = csrfToken();
    if (t) headers.set("X-CSRF-Token", t);
  }

  let res: Response;
  try {
    res = await fetch(`/api${path}`, { ...init, method, headers, body, credentials: "same-origin" });
  } catch {
    throw new ApiError(0, "اتصال به سرور برقرار نشد؛ اینترنت را بررسی کنید.", "network");
  }

  const text = await res.text();
  let data: unknown = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = text;
    }
  }
  if (!res.ok) {
    const { message, code } = messageOf(res.status, data);
    const err = new ApiError(res.status, message, code, data);
    listeners.forEach((l) => l(err));
    throw err;
  }
  return data as T;
}
