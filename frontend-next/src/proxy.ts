import { NextResponse, type NextRequest } from "next/server";

// Strict CSP: every script needs this request's nonce, nothing inline runs
// without it, and 'strict-dynamic' lets only nonce-carrying scripts load
// more. Style *attributes* stay allowed (Radix and Recharts position things
// with style="…"); a style attribute cannot run code.
export function proxy(request: NextRequest) {
  const { pathname, search } = request.nextUrl;

  // public/sw.js, the per-scope manifests and the icon set it precaches:
  // static or self-contained generated files with nothing to gate and no
  // reason to carry a per-request nonce/CSP header.
  if (pathname === "/sw.js" || pathname.endsWith("/manifest.webmanifest") || pathname.startsWith("/icons/")) {
    return NextResponse.next();
  }

  // Optimistic gate only: no session cookie at all → the login page. Whether
  // the cookie is still valid is the backend's call on every API request.
  // /panel/offline is exempt too: public/sw.js serves it with no network at
  // all, so it must never redirect anywhere, session or not.
  const isOfflinePage = pathname === "/panel/offline" || pathname === "/portal/offline";
  if (pathname.startsWith("/panel") && !pathname.startsWith("/panel/login") && !isOfflinePage) {
    const hasSession = request.cookies.has("__Host-sf_session") || request.cookies.has("sf_session");
    if (!hasSession) {
      const url = request.nextUrl.clone();
      url.pathname = "/panel/login";
      url.search = `?next=${encodeURIComponent(pathname + search)}`;
      return NextResponse.redirect(url);
    }
  }

  const nonce = Buffer.from(crypto.randomUUID()).toString("base64");
  const dev = process.env.NODE_ENV === "development";
  const https = request.headers.get("x-forwarded-proto") === "https";

  const csp = [
    "default-src 'self'",
    `script-src 'self' 'nonce-${nonce}' 'strict-dynamic'${dev ? " 'unsafe-eval'" : ""}`,
    `style-src 'self' 'nonce-${nonce}'`,
    "style-src-attr 'unsafe-inline'",
    "img-src 'self' blob: data: https://*.divarcdn.com",
    "font-src 'self'",
    `connect-src 'self'${dev ? " ws:" : ""}`,
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
    "report-uri /api/public/csp-report",
    ...(https ? ["upgrade-insecure-requests"] : []),
  ].join("; ");

  const requestHeaders = new Headers(request.headers);
  requestHeaders.set("x-nonce", nonce);
  requestHeaders.set("Content-Security-Policy", csp);

  const response = NextResponse.next({ request: { headers: requestHeaders } });
  response.headers.set("Content-Security-Policy", csp);
  return response;
}

export const config = {
  matcher: [
    {
      source: "/((?!api|_next/static|_next/image|images|favicon.ico|fonts).*)",
      missing: [
        { type: "header", key: "next-router-prefetch" },
        { type: "header", key: "purpose", value: "prefetch" },
      ],
    },
  ],
};
