const ORIGIN_BASE_URL = "https://s2.nnqnn.tech";

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    if (url.pathname.startsWith("/add/")) {
      const parts = url.pathname.split("/").filter(Boolean);
      if (parts.length !== 3) {
        return textResponse("not found", 404);
      }

      const product = encodeURIComponent(decodeURIComponent(parts[1]));
      const token = encodeURIComponent(decodeURIComponent(parts[2]));
      const subscriptionUrl = `${url.origin}/sub/${product}/${token}`;
      const happUrl = `happ://add/${subscriptionUrl}`;
      return new Response(
        `<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="refresh" content="0; url=${escapeHtml(happUrl)}"><title>Open Happ</title></head><body><a href="${escapeHtml(happUrl)}">Open Happ</a></body></html>`,
        {
          status: 200,
          headers: {
            "content-type": "text/html; charset=utf-8",
            "cache-control": "no-store"
          }
        }
      );
    }

    if (!url.pathname.startsWith("/sub/")) {
      return textResponse("not found", 404);
    }

    const originUrl = new URL(url.pathname + url.search, ORIGIN_BASE_URL);
    const headers = new Headers(request.headers);
    headers.set("host", new URL(ORIGIN_BASE_URL).host);

    if (env.ORIGIN_SECRET) {
      headers.set("x-tgvpn-origin-secret", env.ORIGIN_SECRET);
    }

    return fetch(originUrl, {
      method: request.method,
      headers,
      body: request.body,
      redirect: "manual"
    });
  }
};

function textResponse(text, status) {
  return new Response(text, {
    status,
    headers: {
      "content-type": "text/plain; charset=utf-8",
      "cache-control": "no-store"
    }
  });
}

function escapeHtml(value) {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll('"', "&quot;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}
