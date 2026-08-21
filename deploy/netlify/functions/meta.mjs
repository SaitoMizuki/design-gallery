import { getStore } from "@netlify/blobs";

/* 貼られたURLのタイトルと og:image を取り出す。
   GET /api/meta?u=<URL> -> { title, image }
   一度調べた結果は保存して使い回す。 */

const UA =
  "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 " +
  "(KHTML, like Gecko) Chrome/126.0 Safari/537.36";

/* 社内ネットワークなどを踏ませないための最低限の制限 */
const BLOCKED = [
  /^localhost$/i, /^127\./, /^10\./, /^192\.168\./, /^169\.254\./,
  /^172\.(1[6-9]|2\d|3[01])\./, /^\[?::1\]?$/, /\.local$/i, /^0\./,
];

const dec = (s) =>
  String(s || "")
    .replace(/&quot;/g, '"').replace(/&#0?39;/g, "'").replace(/&apos;/g, "'")
    .replace(/&amp;/g, "&").replace(/&lt;/g, "<").replace(/&gt;/g, ">")
    .replace(/&nbsp;/g, " ").replace(/\s+/g, " ").trim();

const pick = (html, patterns) => {
  for (const re of patterns) {
    const m = html.match(re);
    if (m && m[1] && m[1].trim()) return dec(m[1]);
  }
  return null;
};

export default async (req) => {
  const json = (b, s = 200) =>
    new Response(JSON.stringify(b), {
      status: s,
      headers: { "content-type": "application/json", "cache-control": "no-store" },
    });

  const raw = (new URL(req.url).searchParams.get("u") || "").trim();
  if (!raw) return json({ error: "no url" }, 400);

  let target;
  try {
    target = new URL(/^https?:\/\//i.test(raw) ? raw : "https://" + raw);
  } catch { return json({ error: "bad url" }, 400); }
  if (!/^https?:$/.test(target.protocol)) return json({ error: "bad scheme" }, 400);
  if (BLOCKED.some((re) => re.test(target.hostname))) return json({ error: "blocked host" }, 400);

  const store = getStore({ name: "meta", consistency: "strong" });
  const cacheKey = target.href.slice(0, 400);
  const hit = await store.get(cacheKey, { type: "json" });
  if (hit) return json(hit);

  let html = "";
  try {
    const ctl = AbortSignal.timeout(9000);
    const r = await fetch(target.href, {
      headers: { "user-agent": UA, accept: "text/html,*/*" },
      redirect: "follow",
      signal: ctl,
    });
    if (!r.ok) return json({ title: null, image: null, error: "status " + r.status });
    const buf = await r.arrayBuffer();
    html = new TextDecoder("utf-8").decode(buf.slice(0, 600000));
  } catch (e) {
    return json({ title: null, image: null, error: "fetch failed" });
  }

  const title = pick(html, [
    /<meta[^>]+property=["']og:title["'][^>]+content=["']([^"']+)["']/i,
    /<meta[^>]+content=["']([^"']+)["'][^>]+property=["']og:title["']/i,
    /<meta[^>]+name=["']twitter:title["'][^>]+content=["']([^"']+)["']/i,
    /<title[^>]*>([\s\S]*?)<\/title>/i,
  ]);
  let image = pick(html, [
    /<meta[^>]+property=["']og:image:secure_url["'][^>]+content=["']([^"']+)["']/i,
    /<meta[^>]+property=["']og:image["'][^>]+content=["']([^"']+)["']/i,
    /<meta[^>]+content=["']([^"']+)["'][^>]+property=["']og:image["']/i,
    /<meta[^>]+name=["']twitter:image["'][^>]+content=["']([^"']+)["']/i,
  ]);
  if (image) { try { image = new URL(image, target.href).href; } catch { image = null; } }

  const out = { title: title ? title.slice(0, 160) : null, image };
  await store.setJSON(cacheKey, out);
  return json(out);
};

export const config = { path: "/api/meta" };
