import { test } from "node:test";
import assert from "node:assert/strict";
import worker from "./worker.js";

const TOKEN = "123456789:AAAAtestFakeTokenNotReal12345";
const ENV = { RELAY_KEY: "correct-relay-key", ALLOWED_BOTS: "123456789, 987654321" };
const BASE = "https://relay.example.com";

function withMockFetch(handler, fn) {
  const original = globalThis.fetch;
  globalThis.fetch = handler;
  return fn().finally(() => {
    globalThis.fetch = original;
  });
}

function req(path, init = {}) {
  const headers = new Headers(init.headers || {});
  if (!("X-Relay-Key" in (init.headers || {})) && init.withKey !== false) {
    headers.set("X-Relay-Key", ENV.RELAY_KEY);
  }
  return new Request(BASE + path, { ...init, headers });
}

// ── path shape: 404 for anything else ───────────────────────────────────────

test("404 on paths that are not the two Telegram shapes", async () => {
  for (const path of ["/", "/webhook", "/bot" + TOKEN, "/file/bot" + TOKEN + "/", `/bot${TOKEN}/sendMessage/extra`]) {
    const res = await worker.fetch(req(path), ENV);
    assert.equal(res.status, 404, `expected 404 for ${path}`);
    const body = await res.json();
    assert.equal(body.ok, false);
    assert.equal(body.relay, "not_found");
  }
});

// ── shared secret ────────────────────────────────────────────────────────────

test("401 relay-specific body when X-Relay-Key is missing", async () => {
  const res = await worker.fetch(req(`/bot${TOKEN}/getMe`, { withKey: false }), ENV);
  assert.equal(res.status, 401);
  assert.deepEqual(await res.json(), { ok: false, relay: "unauthorized" });
});

test("401 relay-specific body when X-Relay-Key is wrong", async () => {
  const res = await worker.fetch(req(`/bot${TOKEN}/getMe`, { headers: { "X-Relay-Key": "nope" } }), ENV);
  assert.equal(res.status, 401);
  assert.deepEqual(await res.json(), { ok: false, relay: "unauthorized" });
});

test("401 when RELAY_KEY itself is unset (fail closed, never open access)", async () => {
  const res = await worker.fetch(req(`/bot${TOKEN}/getMe`, { headers: { "X-Relay-Key": "" } }), { ALLOWED_BOTS: "123456789" });
  assert.equal(res.status, 401);
});

// ── bot allow-list ───────────────────────────────────────────────────────────

test("403 relay-specific body for a bot id not in ALLOWED_BOTS", async () => {
  const res = await worker.fetch(req(`/bot555555555:otherbot/getMe`), ENV);
  assert.equal(res.status, 403);
  assert.deepEqual(await res.json(), { ok: false, relay: "forbidden_bot" });
});

// ── successful proxying ──────────────────────────────────────────────────────

test("proxies a Bot API method call to the right URL and strips disallowed headers", async () => {
  let seenUrl, seenInit;
  await withMockFetch(
    async (url, init) => {
      seenUrl = url;
      seenInit = init;
      return new Response(JSON.stringify({ ok: true, result: { username: "sorinflow_bot" } }), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    },
    async () => {
      const res = await worker.fetch(
        req(`/bot${TOKEN}/getMe`, {
          headers: {
            "X-Relay-Key": ENV.RELAY_KEY,
            "CF-Connecting-IP": "1.2.3.4",
            "X-Forwarded-For": "1.2.3.4",
            "X-Real-IP": "1.2.3.4",
            Cookie: "session=abc",
            "Content-Type": "application/json",
          },
        }),
        ENV
      );
      assert.equal(res.status, 200);
      const body = await res.json();
      assert.equal(body.ok, true);
    }
  );
  assert.equal(seenUrl, `https://api.telegram.org/bot${TOKEN}/getMe`);
  const h = seenInit.headers;
  assert.equal(h.get("cf-connecting-ip"), null);
  assert.equal(h.get("x-forwarded-for"), null);
  assert.equal(h.get("x-real-ip"), null);
  assert.equal(h.get("x-relay-key"), null);
  assert.equal(h.get("cookie"), null);
  assert.equal(h.get("content-type"), "application/json");
});

test("proxies the /file/bot<token>/<path> shape unchanged", async () => {
  let seenUrl;
  await withMockFetch(
    async (url) => {
      seenUrl = url;
      return new Response("binary-bytes", { status: 200 });
    },
    async () => {
      const res = await worker.fetch(req(`/file/bot${TOKEN}/documents/file_1.jpg`), ENV);
      assert.equal(res.status, 200);
      assert.equal(await res.text(), "binary-bytes");
    }
  );
  assert.equal(seenUrl, `https://api.telegram.org/file/bot${TOKEN}/documents/file_1.jpg`);
});

test("Telegram's own 4xx/5xx pass through unchanged, status and body", async () => {
  const telegramBody = JSON.stringify({ ok: false, error_code: 400, description: "Bad Request: chat not found" });
  await withMockFetch(
    async () => new Response(telegramBody, { status: 400, headers: { "content-type": "application/json" } }),
    async () => {
      const res = await worker.fetch(req(`/bot${TOKEN}/sendMessage`, { method: "POST", body: "{}" }), ENV);
      assert.equal(res.status, 400);
      assert.equal(await res.text(), telegramBody);
    }
  );
});

// ── upstream failure ─────────────────────────────────────────────────────────

test("502 relay-specific body when the upstream fetch fails (network error)", async () => {
  await withMockFetch(
    async () => {
      throw new Error("getaddrinfo ENOTFOUND api.telegram.org");
    },
    async () => {
      const res = await worker.fetch(req(`/bot${TOKEN}/getMe`), ENV);
      assert.equal(res.status, 502);
      assert.deepEqual(await res.json(), { ok: false, relay: "upstream_unreachable" });
    }
  );
});

// ── streaming, not buffering ─────────────────────────────────────────────────

test("a multipart body passes through as the same stream, unread by the worker", async () => {
  let seenInit;
  let receivedBytes = 0;
  await withMockFetch(
    async (url, init) => {
      seenInit = init;
      // Simulate the upstream actually reading the streamed body through,
      // the way a real HTTP client sending the request would.
      const reader = init.body.getReader();
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        receivedBytes += value.byteLength;
      }
      return new Response(JSON.stringify({ ok: true }), { status: 200, headers: { "content-type": "application/json" } });
    },
    async () => {
      const chunk = new Uint8Array(700 * 1024).fill(65);
      const totalChunks = 3;
      let sent = 0;
      const body = new ReadableStream({
        pull(controller) {
          if (sent >= totalChunks) return controller.close();
          sent += 1;
          controller.enqueue(chunk);
        },
      });
      const request = new Request(`${BASE}/bot${TOKEN}/sendDocument`, {
        method: "POST",
        headers: { "X-Relay-Key": ENV.RELAY_KEY, "Content-Type": "multipart/form-data; boundary=x" },
        body,
        duplex: "half",
      });
      const requestBodyRef = request.body;
      const res = await worker.fetch(request, ENV);
      assert.equal(res.status, 200);
      // the exact same stream object was handed to fetch — nothing buffered
      // or copied it in between.
      assert.equal(seenInit.body, requestBodyRef);
      assert.equal(receivedBytes, chunk.byteLength * totalChunks);
    }
  );
});

test("a 45 MB-ish declared content-length is forwarded, not recomputed from a buffer", async () => {
  let seenInit;
  await withMockFetch(
    async (url, init) => {
      seenInit = init;
      return new Response(JSON.stringify({ ok: true }), { status: 200, headers: { "content-type": "application/json" } });
    },
    async () => {
      const bigLength = String(45 * 1024 * 1024);
      const body = new ReadableStream({
        start(controller) {
          controller.enqueue(new Uint8Array([1, 2, 3]));
          controller.close();
        },
      });
      const request = new Request(`${BASE}/bot${TOKEN}/sendDocument`, {
        method: "POST",
        headers: {
          "X-Relay-Key": ENV.RELAY_KEY,
          "Content-Type": "multipart/form-data; boundary=x",
          "Content-Length": bigLength,
        },
        body,
        duplex: "half",
      });
      const res = await worker.fetch(request, ENV);
      assert.equal(res.status, 200);
    }
  );
  assert.equal(seenInit.headers.get("content-length"), String(45 * 1024 * 1024));
});
