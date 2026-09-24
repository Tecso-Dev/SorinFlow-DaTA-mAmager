/**
 * SorinFlow Telegram relay — a Cloudflare Worker in front of
 * api.telegram.org, for a server that cannot always reach it directly.
 *
 * Proxies exactly two path shapes:
 *   /bot<token>/<method>        Bot API calls (sendMessage, sendDocument, ...)
 *   /file/bot<token>/<path>     file downloads
 * Everything else is 404 — the relay does not exist for anything but Telegram.
 *
 * Every request needs header X-Relay-Key equal to the RELAY_KEY secret
 * (401 otherwise), and the token's bot id (the part before ':') must be in
 * the comma-separated ALLOWED_BOTS var (403 otherwise). Both failures use a
 * relay-specific JSON body so the caller never confuses them with Telegram's
 * own 401/403.
 *
 * The request body streams straight through to Telegram (no buffering —
 * backups are up to 45 MB multipart uploads) and Telegram's response streams
 * straight back, status and body unchanged. A network failure or timeout
 * reaching Telegram is a 502 with its own relay-specific body.
 */

// ponytail: fixed ceiling, not per-request tunable. Raise this (or read it
// from an env var) if a slow line needs longer than 3 minutes per call.
const UPSTREAM_TIMEOUT_MS = 180_000;

const TELEGRAM_ORIGIN = "https://api.telegram.org";

// Never forwarded to Telegram, whatever the client sent.
const STRIPPED_PREFIXES = ["cf-", "x-forwarded-", "x-real-ip"];
const STRIPPED_EXACT = new Set(["x-relay-key", "cookie"]);

function relayJson(status, body) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

async function sha256(text) {
  const bytes = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return new Uint8Array(bytes);
}

// Constant-time compare: hash both sides first so the comparison is always
// over two 32-byte digests — no early exit on length, no early exit on the
// first differing byte.
async function relayKeyMatches(provided, expected) {
  if (!expected) return false;
  const [a, b] = await Promise.all([sha256(provided || ""), sha256(expected)]);
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a[i] ^ b[i];
  return diff === 0;
}

function matchPath(pathname) {
  const method = pathname.match(/^\/bot([^/]+)\/([^/]+)$/);
  if (method) return { token: method[1], upstreamPath: `/bot${method[1]}/${method[2]}` };
  const file = pathname.match(/^\/file\/bot([^/]+)\/(.+)$/);
  if (file) return { token: file[1], upstreamPath: `/file/bot${file[1]}/${file[2]}` };
  return null;
}

function allowedBotIds(env) {
  return (env.ALLOWED_BOTS || "")
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
}

function forwardHeaders(requestHeaders) {
  const out = new Headers();
  for (const [name, value] of requestHeaders) {
    const lower = name.toLowerCase();
    if (STRIPPED_EXACT.has(lower)) continue;
    if (STRIPPED_PREFIXES.some((p) => lower.startsWith(p))) continue;
    if (lower === "content-type" || lower === "content-length") out.set(lower, value);
  }
  return out;
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const match = matchPath(url.pathname);
    if (!match) {
      return relayJson(404, { ok: false, relay: "not_found" });
    }

    const providedKey = request.headers.get("X-Relay-Key");
    if (!(await relayKeyMatches(providedKey, env.RELAY_KEY))) {
      return relayJson(401, { ok: false, relay: "unauthorized" });
    }

    const botId = match.token.split(":")[0];
    if (!botId || !allowedBotIds(env).includes(botId)) {
      return relayJson(403, { ok: false, relay: "forbidden_bot" });
    }

    const upstreamUrl = `${TELEGRAM_ORIGIN}${match.upstreamPath}${url.search}`;
    const init = {
      method: request.method,
      headers: forwardHeaders(request.headers),
    };
    if (request.method !== "GET" && request.method !== "HEAD" && request.body) {
      init.body = request.body;
      init.duplex = "half"; // required by fetch when body is a stream
    }

    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), UPSTREAM_TIMEOUT_MS);
    try {
      const upstream = await fetch(upstreamUrl, { ...init, signal: controller.signal });
      // Telegram's own response — status and body pass through unchanged.
      return new Response(upstream.body, { status: upstream.status, headers: upstream.headers });
    } catch (err) {
      console.error(`[relay] upstream unreachable: ${err.name || "Error"}`);
      return relayJson(502, { ok: false, relay: "upstream_unreachable" });
    } finally {
      clearTimeout(timer);
    }
  },
};
