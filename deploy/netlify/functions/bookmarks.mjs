import { getStore } from "@netlify/blobs";

/* ブックマークを合言葉ごとに保存する。
   GET  /api/bookmarks?k=合言葉        -> { marks:[], seen:[], custom:[], at }
   POST /api/bookmarks?k=合言葉  body   -> 保存して同じものを返す
   合言葉は端末をまたいで同じ内容を見るための鍵で、認証ではありません。 */
const KEY_RE = /^[A-Za-z0-9_-]{4,64}$/;

export default async (req) => {
  const url = new URL(req.url);
  const key = (url.searchParams.get("k") || "").trim();
  const json = (body, status = 200) =>
    new Response(JSON.stringify(body), {
      status,
      headers: { "content-type": "application/json", "cache-control": "no-store" },
    });

  if (!KEY_RE.test(key))
    return json({ error: "合言葉は英数字・ハイフン・アンダースコアで4〜64文字にしてください" }, 400);

  const store = getStore({ name: "bookmarks", consistency: "strong" });

  if (req.method === "GET") {
    const saved = await store.get(key, { type: "json" });
    return json(saved || { marks: [], seen: [], custom: [], at: null });
  }

  if (req.method === "POST") {
    let body;
    try { body = await req.json(); } catch { return json({ error: "不正な形式です" }, 400); }
    const arr = (v) => (Array.isArray(v) ? v.filter((x) => typeof x === "string").slice(0, 5000) : []);
    const data = {
      marks: arr(body.marks),
      seen: arr(body.seen),
      custom: Array.isArray(body.custom)
        ? body.custom.filter((c) => c && typeof c.u === "string").slice(0, 2000)
        : [],
      at: new Date().toISOString(),
    };
    await store.setJSON(key, data);
    return json(data);
  }

  return json({ error: "method not allowed" }, 405);
};

export const config = { path: "/api/bookmarks" };
