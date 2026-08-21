#!/usr/bin/env python3
"""Design gallery digest — 取得〜HTML生成までの一括ビルド。

  python3 build.py            直近1ヶ月ぶんを取得して dist/index.html を生成
  python3 build.py --no-fetch  取得済みキャッシュだけで再生成

出力: dist/index.html（サムネイルは data URI で埋め込み・外部リクエストなし）
"""
import os, re, sys, json, html, base64, struct, hashlib, subprocess, colorsys, datetime, shutil
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin

ROOT = os.path.dirname(os.path.abspath(__file__))
CACHE, DIST = os.path.join(ROOT, "cache"), os.path.join(ROOT, "dist")
IMG, SMALL = os.path.join(CACHE, "img"), os.path.join(CACHE, "small")
for d in (CACHE, DIST, IMG, SMALL): os.makedirs(d, exist_ok=True)

# ---------------------------------------------------------------- 画像変換
# macOS では sips、Linux(CI) では Pillow を使う。出力は同じ。
HAS_SIPS = shutil.which("sips") is not None
try:
    from PIL import Image
except ImportError:
    Image = None

def _fit(im, n):
    w, h = im.size
    if max(w, h) > n:
        r = n / max(w, h)
        im = im.resize((max(1, round(w*r)), max(1, round(h*r))), Image.LANCZOS)
    return im

def img_jpeg(src, dst, n=280, q=48):
    """長辺 n px に縮小して JPEG 化。成功なら True。"""
    if HAS_SIPS:
        return subprocess.run(["sips", "-s", "format", "jpeg", "-s", "formatOptions", str(q),
                               "-Z", str(n), src, "--out", dst],
                              capture_output=True).returncode == 0
    if Image is None: return False
    try:
        with Image.open(src) as im:
            _fit(im.convert("RGB"), n).save(dst, "JPEG", quality=q)
        return True
    except Exception:
        return False

def img_bmp24(src, dst, n=24):
    """長辺 n px の 24bit BMP を書く。配色判定用。成功なら True。"""
    if HAS_SIPS:
        return subprocess.run(["sips", "-s", "format", "bmp", "-Z", str(n), src, "--out", dst],
                              capture_output=True).returncode == 0
    if Image is None: return False
    try:
        with Image.open(src) as im:
            _fit(im.convert("RGB"), n).save(dst, "BMP")
        return True
    except Exception:
        return False

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
NOFETCH = "--no-fetch" in sys.argv
# 実行環境のタイムゾーンに依存させない（GitHub Actions のランナーは UTC）
JST = datetime.timezone(datetime.timedelta(hours=9))
def now_jst(): return datetime.datetime.now(JST)
TODAY = now_jst().date()
LO = (TODAY - datetime.timedelta(days=31)).isoformat()
HI = TODAY.isoformat()

def log(*a): print(*a, flush=True)

# ブラウザ相当のヘッダ。データセンターIPからの素の curl を弾くサイトへの対策。
CURL_HDRS = [
    "-H", "Accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "-H", "Accept-Language: ja,en-US;q=0.9,en;q=0.8",
    "-H", "Upgrade-Insecure-Requests: 1",
    "-H", "Sec-Fetch-Dest: document",
    "-H", "Sec-Fetch-Mode: navigate",
    "-H", "Sec-Fetch-Site: none",
    "-H", "Sec-Fetch-User: ?1",
    "--compressed",
]

def get(name, url, force=False):
    """URLを取得して cache/<name> に保存。失敗時はキャッシュを使う。"""
    p = os.path.join(CACHE, name)
    if NOFETCH and os.path.exists(p):
        return open(p, encoding="utf-8", errors="replace").read()
    if os.path.exists(p) and not force and NOFETCH:
        return open(p, encoding="utf-8", errors="replace").read()
    # url を空で呼ぶのは「取得済みキャッシュだけ読む」用途（creators など）
    if not url:
        return open(p, encoding="utf-8", errors="replace").read() if os.path.exists(p) else ""
    # 接続方式を変えながら試す。HTTP/2 の握手を拒否するサイトがあるため。
    attempts = (
        ([], 40),
        (["--http1.1", "--retry", "2", "--retry-all-errors", "--retry-delay", "3"], 60),
        (["--http1.1", "--tlsv1.2", "--ipv4"], 60),
    )
    for i, (extra, tmo) in enumerate(attempts, 1):
        try:
            r = subprocess.run(["curl", "-sS", "-L", "-A", UA, *CURL_HDRS, *extra,
                                "--max-time", str(tmo),
                                "-w", "\n<<<HTTP:%{http_code}>>>", url],
                               capture_output=True, timeout=tmo + 30)
            body = r.stdout.decode("utf-8", "replace")
            code = "?"
            m = re.search(r"\n<<<HTTP:(\d+)>>>$", body)
            if m:
                code, body = m.group(1), body[:m.start()]
            if len(body) > 200:
                if i > 1: log(f"  ~ {i}回目で成功 {url}")
                open(p, "w", encoding="utf-8").write(body)
                return body
            err = r.stderr.decode("utf-8", "replace").strip().replace("\n", " ")[:160]
            log(f"  ! 取得失敗({i}) HTTP {code} / {len(body)}B / curl rc={r.returncode} {err}  {url}")
        except Exception as e:
            log(f"  ! 取得失敗({i}) {type(e).__name__} {e}  {url}")
    return open(p, encoding="utf-8", errors="replace").read() if os.path.exists(p) else ""

def cl(x):  return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", str(x)))).strip()
def U(u):
    if not u: return ""
    u = u.split("?")[0].split("#")[0]
    u = re.sub(r"^https?://", "", u); u = re.sub(r"^www\.", "", u)
    return u.rstrip("/").lower()
def pick(tag, base=""):
    for at in ("data-src-img", "data-src", "data-lazy-src", "data-original"):
        m = re.search(at + r'\s*=\s*"([^"]+)"', tag, re.I)
        if m and not m.group(1).startswith("data:"): return urljoin(base, html.unescape(m.group(1)))
    m = re.search(r'srcset\s*=\s*"([^"]+)"', tag, re.I)
    if m:
        c = [x.strip().split(" ")[0] for x in html.unescape(m.group(1)).split(",") if x.strip()]
        c = [x for x in c if not x.startswith("data:")]
        if c: return urljoin(base, c[min(1, len(c) - 1)])
    m = re.search(r'\bsrc\s*=\s*"([^"]+)"', tag, re.I)
    if m and not m.group(1).startswith("data:"): return urljoin(base, html.unescape(m.group(1)))
    return None

# ---------------------------------------------------------------- 出典
S = {
 "sankou":     {"n":"SANKOU!",           "c":"jp",    "home":"https://sankoudesign.com/"},
 "wdc":        {"n":"Web Design Clip",   "c":"jp",    "home":"https://webdesignclip.com/"},
 "muuuuu":     {"n":"MUUUUU.ORG",        "c":"jp",    "home":"https://muuuuu.org/"},
 "io3000":     {"n":"I/O 3000",          "c":"jp",    "home":"https://io3000.com/"},
 "g1uu":       {"n":"1GUU",              "c":"jp",    "home":"https://1guu.jp/"},
 "choodoi":    {"n":"ちょうどいいデザイン","c":"jp",   "home":"https://choooodoii.com/"},
 "dnk":        {"n":"デザインのこと",     "c":"jp",    "home":"https://designnokoto.com/"},
 "w81":        {"n":"81-web.com",        "c":"jp",    "home":"https://81-web.com/"},
 "s5":         {"n":"S5-Style",          "c":"jp",    "home":"https://www.s5-style.com/all/"},
 "aaa11y":     {"n":"AAA11Y",            "c":"a11y",  "home":"https://www.aaa11y.com/"},
 "awwwards":   {"n":"Awwwards",          "c":"award", "home":"https://www.awwwards.com/"},
 "cssda":      {"n":"CSS Design Awards", "c":"award", "home":"https://www.cssdesignawards.com/"},
 "fwa":        {"n":"The FWA",           "c":"award", "home":"https://thefwa.com/"},
 "siteinspire":{"n":"siteinspire",       "c":"intl",  "home":"https://www.siteinspire.com/"},
 "hover":      {"n":"hoverstat.es",      "c":"intl",  "home":"https://www.hoverstat.es/"},
 "siiimple":   {"n":"siiimple",          "c":"intl",  "home":"https://siiimple.com/"},
 "klik":       {"n":"Klikkenthéke",      "c":"intl",  "home":"https://klikkentheke.com/catalogue/"},
 "gems":       {"n":"Internet Gems",     "c":"intl",  "home":"https://ilovecreatives.com/internet-gems"},
}

E = []      # [date, src, title, url, badge]
TH_URL = {} # key -> thumbnail url
CRD = {}    # key -> [creators]
RAWTYPE = defaultdict(list)  # key -> [raw category labels]

def add(d, s, t, u, badge="", thumb=None, credit=None, types=None):
    if not (LO <= d <= HI) or not u or not t: return
    k = s + "|" + u
    E.append([d, s, cl(t), u, badge])
    if thumb: TH_URL.setdefault(k, thumb)
    if credit:
        c = [cl(x) for x in credit if cl(x)]
        if c: CRD.setdefault(k, c[:4])
    if types: RAWTYPE[k].extend(types)

# ---------------------------------------------------------------- 取得
def collect():
    log("== 取得 ==")
    # SANKOU!
    for i, url in enumerate(["https://sankoudesign.com/",
                             "https://sankoudesign.com/page/2/",
                             "https://sankoudesign.com/page/3/"]):
        h = get(f"sankou_{i+1}.html", url)
        for b in re.split(r"(?=<figure>)", h)[1:]:
            ext = re.search(r'<a href="(https?://(?!sankoudesign)[^"]+)" target="_blank"', b)
            ttl = re.search(r'<div class="site_more[^"]*">\s*<p><a href="[^"]*"[^>]*>(.*?)</a>', b, re.S)
            dt  = re.search(r"(20\d\d)/(\d\d)/(\d\d)", b)
            im  = re.search(r"<img\b[^>]*>", b)
            if ext and ttl and dt:
                add(f"{dt.group(1)}-{dt.group(2)}-{dt.group(3)}", "sankou", ttl.group(1), ext.group(1),
                    thumb=pick(im.group(0), "https://sankoudesign.com/") if im else None)
    # Web Design Clip
    for i, url in enumerate(["https://webdesignclip.com/", "https://webdesignclip.com/page/2/"]):
        h = get(f"wdc_{i+1}.html", url)
        for b in re.split(r'(?=<li class="post_li)', h)[1:]:
            tm  = re.search(r'datetime="(\d{4}-\d{2}-\d{2})', b)
            ext = re.search(r'class="post_url"><a href="([^"]+)"', b)
            ttl = re.search(r'<figcaption class="post_title"><h2><a[^>]*title="([^"]*)"', b)
            im  = re.search(r"<img\b[^>]*>", b)
            cats = re.findall(r'class="post_inner--(?:category|tec)"><a[^>]*>(.*?)</a>', b, re.S)
            if tm and ext and ttl:
                add(tm.group(1), "wdc", ttl.group(1), ext.group(1),
                    thumb=pick(im.group(0), "https://webdesignclip.com/") if im else None, types=cats)
    # MUUUUU.ORG
    h = get("muuuuu_1.html", "https://muuuuu.org/")
    for b in re.split(r'(?=<li class="c-post-list__item)', h)[1:]:
        tm  = re.search(r'<time datetime="(\d{4}-\d{2}-\d{2})"', b)
        ext = re.search(r'<a href="(https?://(?!muuuuu\.org)[^"]+)"\s+class="c-post-list__link"', b)
        ttl = re.search(r'class="c-post-list__title-link[^"]*"><span[^>]*>(.*?)</span>', b, re.S)
        im  = re.search(r"<img\b[^>]*>", b)
        cred = re.search(r'<div class="c-post-list__credit">(.*?)</div>\s*</li>', b, re.S)
        names = re.findall(r'<span\s+class="c-linelink__txt">(.*?)</span>', cred.group(1), re.S) if cred else []
        if tm and ext and ttl:
            add(tm.group(1), "muuuuu", ttl.group(1), ext.group(1),
                thumb=pick(im.group(0), "https://muuuuu.org/") if im else None, credit=names)
    # I/O 3000
    for i, url in enumerate(["https://io3000.com/", "https://io3000.com/page/2/"]):
        h = get(f"io3000_{i+1}.html", url)
        for b in re.split(r'(?=<li class="list-index__item")', h)[1:]:
            tm  = re.search(r'<time datetime="(\d{4}-\d{2}-\d{2})', b)
            ext = re.search(r'<a href="(https?://(?!io3000)[^"]+)"[^>]*class="list-index__target"', b)
            ttl = re.search(r'class="list-index__title"[^>]*>(.*?)</div>', b, re.S)
            im  = re.search(r"<img\b[^>]*>", b)
            if tm and ext and ttl:
                add(tm.group(1), "io3000", ttl.group(1), ext.group(1),
                    thumb=pick(im.group(0), "https://io3000.com/") if im else None)
    # 81-web.com
    for pg in (1, 2, 3):
        raw = get(f"w81_{pg}.json", f"https://81-web.com/api/toppage_v2?paged={pg}")
        try: d = json.loads(raw)
        except Exception: continue
        for p in d.get("posts", []):
            y, m, dd = p["date"].split(".")
            add(f"{y}-{int(m):02d}-{int(dd):02d}", "w81", html.unescape(p["title"]), p["siteUrl"],
                thumb=(p.get("pcThumb") or {}).get("url") or (p.get("ogp") or {}).get("url"),
                credit=p.get("catCredit") or [],
                types=(p.get("catType") or []) + (p.get("catCat") or []))
    # The FWA
    raw = get("fwa_timeline.json", "https://thefwa.com/api/timeline/?limit=80")
    try: d = json.loads(raw)
    except Exception: d = {"items": []}
    for u in d.get("items", []):
        if u.get("type") != "awards": continue
        it = u.get("item") or {}; sd = u.get("sortDate") or ""
        th = it.get("thumbnail") or {}; path = None
        for size in ("1364", "958", "718", "480"):
            if size in th:
                v = th[size]; path = v.get("span4") or v.get("span3") or next(iter(v.values())); break
        cats = [c.get("name") if isinstance(c, dict) else c for c in (it.get("categories") or [])]
        badge = "FOTD" if u.get("title") == "FWA of the Day" else ("FOTM" if u.get("title") == "FWA of the Month" else "")
        if it.get("slug"):
            add(sd, "fwa", it.get("title") or "", "https://thefwa.com/cases/" + it["slug"], badge,
                thumb=("https://thefwa.com" + path.replace("\\/", "/")) if path else None,
                credit=[p.get("name") for p in (it.get("profiles") or [])], types=cats)
    # Awwwards（Site of the Day のみ）
    for pg in (1, 2, 3, 4):
        h = get(f"aww_{pg}.html", f"https://www.awwwards.com/websites/?page={pg}")
        for b in re.split(r'(?=<li class="col-3 js-collectable")', h)[1:]:
            if "site of the day" not in b.lower(): continue
            m = re.search(r'data-collectable-model-value="([^"]+)"', b)
            if not m: continue
            try: d = json.loads(html.unescape(m.group(1)))
            except Exception: continue
            if not d.get("createdAt"): continue
            iso = datetime.datetime.fromtimestamp(d["createdAt"], datetime.timezone.utc).strftime("%Y-%m-%d")
            ext = re.search(r'class="figure-rollover__bt"\s*href="(https?://[^"]+)"', b)
            img = d.get("collectableImage")
            add(iso, "awwwards", d.get("collectableTitle") or "",
                ext.group(1) if ext else "https://www.awwwards.com/sites/" + (d.get("slug") or ""),
                "SOTD",
                thumb=("https://assets.awwwards.com/awards/" + img.replace("\\/", "/")) if img else None,
                types=d.get("tags") or [])
    # CSS Design Awards（Website of the Day のみ）
    MON = {m: i + 1 for i, m in enumerate(
        ["JAN","FEB","MAR","APR","MAY","JUN","JUL","AUG","SEP","OCT","NOV","DEC"])}
    for pg in (1, 2, 3):
        u = "https://www.cssdesignawards.com/wotd-award-winners" + ("" if pg == 1 else f"?page={pg}")
        h = get(f"cssda_{pg}.html", u)
        for b in re.split(r'(?=<article class="single-project)', h)[1:]:
            dm  = re.search(r'sp__meta__date">([A-Z]{3})\s*(\d{1,2})<', b)
            ttl = re.search(r'single-project__title"><a[^>]*>(.*?)</a>', b, re.S)
            ext = re.search(r'href="(https?://[^"]+)"[^>]*class="sp__project-link"', b)
            cat = re.search(r'sp__meta__category">(.*?)<', b)
            im  = re.search(r'<img src="([^"]+)"', b)
            if not (dm and ttl and ext and cat and cl(cat.group(1)) == "WOTD"): continue
            mth = MON[dm.group(1)]
            yr = TODAY.year if mth <= TODAY.month else TODAY.year - 1
            th = im.group(1) if im else None
            if th and not th.startswith("http"):
                th = "https://www.cssdesignawards.com/" + th.lstrip("/")
            add(f"{yr}-{mth:02d}-{int(dm.group(2)):02d}", "cssda", ttl.group(1), ext.group(1), "WOTD", thumb=th)
    # AAA11Y
    h = get("aaa11y.html", "https://www.aaa11y.com/")
    for b in re.split(r'(?=<li><div class="Card-module)', h)[1:]:
        dm  = re.search(r"(20\d\d)\.(\d\d)\.(\d\d)", b)
        ext = re.search(r'<a href="(https?://[^"]+)"[^>]*class="Card-module[^"]*__title"', b)
        ttl = re.search(r'__labelInner">(.*?)<span', b, re.S)
        im  = re.search(r"<img\b[^>]*>", b)
        if dm and ext and ttl:
            add(f"{dm.group(1)}-{dm.group(2)}-{dm.group(3)}", "aaa11y", ttl.group(1), ext.group(1),
                thumb=pick(im.group(0), "https://www.aaa11y.com/") if im else None)
    # hoverstat.es（一覧に静止画がないので作品ページの og:image）
    h = get("hover.html", "https://www.hoverstat.es/")
    MON2 = {m: i + 1 for i, m in enumerate(
        ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"])}
    for b in re.split(r'(?=<figure class="feature")', h)[1:]:
        dm  = re.search(r"<h5>(\d{1,2}) ([A-Z][a-z]{2}) (20\d\d)</h5>", b)
        ext = re.search(r'href="(https?://[^"]+)"[^>]*class="link truncate"', b)
        fp  = re.search(r'href="(/features/[a-z0-9-]+/)"', b)
        cr  = re.search(r"Credits\s*→(.*?)</h5>", b, re.S)
        if not (dm and ext): continue
        iso = f"{dm.group(3)}-{MON2[dm.group(2)]:02d}-{int(dm.group(1)):02d}"
        if not (LO <= iso <= HI): continue
        th = None
        if fp:
            fh = get("hoverf_" + fp.group(1).strip("/").replace("/", "_") + ".html",
                     "https://www.hoverstat.es" + fp.group(1))
            mm = re.search(r'<meta[^>]+property="og:image"[^>]+content="([^"]+)"', fh)
            if mm: th = html.unescape(mm.group(1))
        add(iso, "hover", U(ext.group(1)), ext.group(1), thumb=th,
            credit=[cl(x) for x in re.findall(r"<a[^>]*>(.*?)</a>", cr.group(1), re.S)] if cr else [])
    # 1GUU（フィード）
    for pg in (1, 2, 3):
        x = get(f"g1uu_feed{pg}.xml", "https://1guu.jp/feed/" + ("" if pg == 1 else f"?paged={pg}"))
        for it in re.findall(r"<item>(.*?)</item>", x, re.S):
            t = re.search(r"<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>", it, re.S)
            l = re.search(r"<link>(.*?)</link>", it, re.S)
            p = re.search(r"<pubDate>(.*?)</pubDate>", it, re.S)
            if t and l and p:
                try: dt = datetime.datetime.strptime(p.group(1).strip()[:25], "%a, %d %b %Y %H:%M:%S")
                except Exception: continue
                add(dt.strftime("%Y-%m-%d"), "g1uu", t.group(1), l.group(1).strip())
    log(f"  日付つき {len(E)} 件")

# ------------------------------------------------- 詳細ページから制作者
def creators():
    log("== 制作者 ==")
    jobs = {}
    for i in range(3):
        h = get(f"sankou_{i+1}.html", "")
        for b in re.split(r"(?=<figure>)", h)[1:]:
            ext = re.search(r'<a href="(https?://(?!sankoudesign)[^"]+)" target="_blank"', b)
            det = re.search(r'href="(https://sankoudesign\.com/web/[^"]+/)"', b)
            if ext and det and "sankou|" + ext.group(1) in {e[1] + "|" + e[3] for e in E}:
                jobs["sankou|" + ext.group(1)] = det.group(1)
    for pg in (1, 2, 3):
        h = get(f"cssda_{pg}.html", "")
        for b in re.split(r'(?=<article class="single-project)', h)[1:]:
            ext = re.search(r'href="(https?://[^"]+)"[^>]*class="sp__project-link"', b)
            det = re.search(r'href="(/sites/[a-z0-9\-]+/\d+/)"', b)
            if ext and det and "cssda|" + ext.group(1) in {e[1] + "|" + e[3] for e in E}:
                jobs["cssda|" + ext.group(1)] = "https://www.cssdesignawards.com" + det.group(1)
    for pg in (1, 2, 3, 4):
        h = get(f"aww_{pg}.html", "")
        for b in re.split(r'(?=<li class="col-3 js-collectable")', h)[1:]:
            if "site of the day" not in b.lower(): continue
            m = re.search(r'data-collectable-model-value="([^"]+)"', b)
            if not m: continue
            try: d = json.loads(html.unescape(m.group(1)))
            except Exception: continue
            ext = re.search(r'class="figure-rollover__bt"\s*href="(https?://[^"]+)"', b)
            u = ext.group(1) if ext else "https://www.awwwards.com/sites/" + (d.get("slug") or "")
            if d.get("slug") and "awwwards|" + u in {e[1] + "|" + e[3] for e in E}:
                jobs["awwwards|" + u] = "https://www.awwwards.com/sites/" + d["slug"]

    def one(kv):
        k, u = kv
        name = "det_" + hashlib.md5(k.encode()).hexdigest()[:14] + ".html"
        h = get(name, u)
        src = k.split("|")[0]; names = []
        if src == "sankou":
            m = re.search(r'<div class="create_citation">.*?<blockquote>(.*?)</blockquote>', h, re.S)
            names = [cl(x) for x in re.findall(r"<li>(.*?)</li>", m.group(1), re.S)] if m else []
        elif src == "cssda":
            m = re.search(r'class="single-website__author__name"[^>]*>(.*?)</a>', h, re.S)
            names = [cl(m.group(1))] if m else []
        elif src == "awwwards":
            m = re.search(r'<div class="users-credits\s*">(.*?)</ul>', h, re.S)
            names = [cl(x) for x in re.findall(r'<strong class="link-underlined[^"]*">(.*?)</strong>',
                                               m.group(1), re.S)] if m else []
        names = [n for n in dict.fromkeys(names) if n and 1 < len(n) < 70]
        return k, names[:4]

    with ThreadPoolExecutor(max_workers=6) as ex:
        for k, names in ex.map(one, jobs.items()):
            if names: CRD[k] = names
    log(f"  制作者 {len(CRD)} 件")

# --------------------------------- サムネイルが無い分は og:image で補う
def fallback_thumbs(keys):
    """ギャラリーが画像を出していない掲載は、掲載先サイト自身の og:image を使う。
    解決結果は cache/og.json に残すので2回目以降は取りに行かない。"""
    log("== サムネイル補完（og:image）==")
    p = os.path.join(CACHE, "og.json")
    known = json.load(open(p, encoding="utf-8")) if os.path.exists(p) else {}
    todo = [(k, u) for k, u in keys if k not in TH_URL and k not in known]
    def one(kv):
        k, url = kv
        try:
            r = subprocess.run(["curl", "-sL", "-A", UA, "--max-time", "20",
                                "--max-filesize", "3000000", url], capture_output=True, timeout=30)
            h = r.stdout.decode("utf-8", "replace")
        except Exception:
            return k, None
        for pat in (r'<meta[^>]+property="og:image"[^>]+content="([^"]+)"',
                    r'<meta[^>]+content="([^"]+)"[^>]+property="og:image"',
                    r'<meta[^>]+name="twitter:image"[^>]+content="([^"]+)"'):
            m = re.search(pat, h, re.I)
            if m: return k, urljoin(url, html.unescape(m.group(1)))
        return k, ""
    if todo:
        with ThreadPoolExecutor(max_workers=6) as ex:
            for k, v in ex.map(one, todo): known[k] = v or ""
        json.dump(known, open(p, "w"), ensure_ascii=False)
    n = 0
    for k, _ in keys:
        if k not in TH_URL and known.get(k):
            TH_URL[k] = known[k]; n += 1
    log(f"  補完 {n} 件（新規取得 {len(todo)} 件）")

# ------------------------------------------------------------ サムネイル
def thumbs():
    log("== サムネイル ==")
    def dl(kv):
        k, url = kv
        p = os.path.join(IMG, hashlib.md5(k.encode()).hexdigest()[:16] + ".bin")
        if os.path.exists(p) and os.path.getsize(p) > 1000: return
        subprocess.run(["curl", "-sL", "-A", UA, "-e", "https://www.google.com/",
                        "--max-time", "30", "--max-filesize", "12000000", "-o", p, url],
                       capture_output=True)
    with ThreadPoolExecutor(max_workers=8) as ex:
        list(ex.map(dl, TH_URL.items()))
    def rs(k):
        i = hashlib.md5(k.encode()).hexdigest()[:16]
        src, dst = os.path.join(IMG, i + ".bin"), os.path.join(SMALL, i + ".jpg")
        if os.path.exists(dst) and os.path.getsize(dst) > 500: return
        if not os.path.exists(src): return
        img_jpeg(src, dst, 280, 48)
    with ThreadPoolExecutor(max_workers=8) as ex:
        list(ex.map(rs, TH_URL.keys()))
    out = {}
    for k in TH_URL:
        p = os.path.join(SMALL, hashlib.md5(k.encode()).hexdigest()[:16] + ".jpg")
        if os.path.exists(p):
            out[k] = "data:image/jpeg;base64," + base64.b64encode(open(p, "rb").read()).decode()
    log(f"  埋め込み {len(out)} 件 / {sum(len(v) for v in out.values())/1e6:.2f} MB")
    return out

# ------------------------------------------------------------------ 配色
def bucket(r, g, b):
    h, s, v = colorsys.rgb_to_hsv(r/255, g/255, b/255); hd = h*360
    if v < 0.20: return "ブラック"
    if s < 0.10: return "ホワイト" if v > 0.86 else "グレー"
    if s < 0.30 and v > 0.78 and 15 <= hd <= 55: return "ベージュ･クリーム"
    if 15 <= hd < 45 and v < 0.62 and s >= 0.25: return "ブラウン･キャメル"
    if hd < 10 or hd >= 345: return "ピンク" if (s < 0.55 and v > 0.72) else "レッド･ワインレッド･バーミリオン"
    if hd < 45:  return "オレンジ"
    if hd < 68:  return "イエロー"
    if hd < 160: return "グリーン･イエローグリーン"
    if hd < 196: return "ブルーグリーン"
    if hd < 250: return "ネイビー" if v < 0.45 else "ブルー"
    if hd < 300: return "パープル･ラベンダー"
    return "ピンク"
ACHRO = {"ブラック", "ホワイト", "グレー"}

def palette(keys):
    log("== 配色判定 ==")
    tmp = os.path.join(CACHE, "_c.bmp"); out = {}
    for k in keys:
        p = os.path.join(SMALL, hashlib.md5(k.encode()).hexdigest()[:16] + ".jpg")
        if not os.path.exists(p): continue
        if not img_bmp24(p, tmp, 24): continue
        try:
            d = open(tmp, "rb").read()
            off = struct.unpack_from("<I", d, 10)[0]
            w, hh = struct.unpack_from("<ii", d, 18); bpp = struct.unpack_from("<H", d, 28)[0]
            if bpp != 24: continue
            hh = abs(hh); row = (w*3 + 3)//4*4
            c = Counter()
            for y in range(hh):
                base = off + y*row
                for x in range(w):
                    i = base + x*3
                    c[bucket(d[i+2], d[i+1], d[i])] += 1
        except Exception:
            continue
        n = sum(c.values())
        if not n: continue
        achro = [(a, b) for a, b in c.most_common() if a in ACHRO]
        chro  = [(a, b) for a, b in c.most_common() if a not in ACHRO]
        res = []
        if achro and achro[0][1]/n >= 0.25: res.append(achro[0][0])
        for a, b in chro[:2]:
            if b/n >= 0.10: res.append(a)
        if not res: res.append(c.most_common(1)[0][0])
        out[k] = res[:3]
    log(f"  配色 {len(out)} 件")
    return out

# ------------------------------------------------------------------ タイプ
RULES = [
 ("採用サイト", ["採用","recruit","仕事･アルバイト","job"]),
 ("ECサイト", ["ec","オンラインショップ","e-commerce","ecommerce","shop","store","通販"]),
 ("ポートフォリオ･プロフィールサイト", ["ポートフォリオ","portfolio","personal","プロフィール"]),
 ("周年サイト･記念サイト", ["周年","anniversary","記念"]),
 ("特設サイト･プロモーションサイト", ["特設","キャンペーン","プロモーション","promotion","campaign","promotional"]),
 ("LP", ["ランディング","landing"]),
 ("メディア･情報サイト", ["メディア","media","magazine","news","editorial","情報サイト","blog"]),
 ("プラットフォーム･コミュニティサイト", ["プラットフォーム","platform","コミュニティ","community","saas","webアプリ"]),
 ("BtoBのサイト", ["btob","b2b"]),
 ("コーポレートサイト", ["コーポレート","corporate","企業サイト","会社","事務所","business","agency","制作会社"]),
 ("ブランドサイト･サービスサイト", ["ブランド","brand","サービスサイト","プロダクト","product","service"]),
]
def to_type(label):
    s = str(label).lower()
    for name, keys in RULES:
        for k in keys:
            if k in s: return name
    return None

# ------------------------------ bookmarks.txt のURLをメタ情報つきで取り込む
PIN = []
def manual_bookmarks():
    p = os.path.join(ROOT, "bookmarks.txt")
    if not os.path.exists(p): return
    urls = []
    for line in open(p, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#"): continue
        if not re.match(r"^https?://", line, re.I): line = "https://" + line
        if line not in urls: urls.append(line)
    if not urls: return
    log("== 固定ブックマーク ==")
    cache = os.path.join(CACHE, "pinned.json")
    known = json.load(open(cache, encoding="utf-8")) if os.path.exists(cache) else {}

    def one(url):
        if url in known: return url, known[url]
        try:
            r = subprocess.run(["curl", "-sL", "-A", UA, "--max-time", "25",
                                "--max-filesize", "4000000", url], capture_output=True, timeout=35)
            h = r.stdout.decode("utf-8", "replace")
        except Exception:
            return url, None
        def meta(*pats):
            for pat in pats:
                m = re.search(pat, h, re.I | re.S)
                if m: return cl(m.group(1))
            return None
        title = meta(r'<meta[^>]+property="og:title"[^>]+content="([^"]+)"',
                     r'<meta[^>]+name="twitter:title"[^>]+content="([^"]+)"',
                     r"<title[^>]*>(.*?)</title>") or U(url)
        img = meta(r'<meta[^>]+property="og:image"[^>]+content="([^"]+)"',
                   r'<meta[^>]+content="([^"]+)"[^>]+property="og:image"',
                   r'<meta[^>]+name="twitter:image"[^>]+content="([^"]+)"')
        if img: img = urljoin(url, html.unescape(img))
        return url, {"t": title[:120], "th": img}

    with ThreadPoolExecutor(max_workers=5) as ex:
        for url, info in ex.map(one, urls):
            if info: known[url] = info
    json.dump(known, open(cache, "w", encoding="utf-8"), ensure_ascii=False)

    for url in urls:
        info = known.get(url) or {"t": U(url), "th": None}
        if info.get("th"): TH_URL["manual|" + url] = info["th"]
        PIN.append({"u": url, "t": info.get("t") or U(url)})
    got = sum(1 for u in urls if (known.get(u) or {}).get("th"))
    log(f"  {len(PIN)} 件（og:image 取得 {got} 件）")

# ------------------------------------------------------------------ 出力
UNDATED = json.load(open(os.path.join(ROOT, "undated.json"), encoding="utf-8"))

def main():
    collect()
    creators()
    manual_bookmarks()
    allkeys = [(e[1] + "|" + e[3], e[3]) for e in E]
    allkeys += [(g["s"] + "|" + it[1], it[1]) for g in UNDATED for it in g["items"]]
    fallback_thumbs(list(dict.fromkeys(allkeys)))
    TH = thumbs()
    CO = palette(list(TH_URL.keys()))

    seen = set(); rows = []
    for e in sorted(E, key=lambda x: (x[0], x[1]), reverse=True):
        k = (e[1], e[3], e[0])
        if k in seen: continue
        seen.add(k); rows.append(e)

    TAG = {}
    for e in rows:
        k = e[1] + "|" + e[3]
        ty = []
        for l in RAWTYPE.get(k, []):
            t = to_type(l)
            if t and t not in ty: ty.append(t)
        rec = {}
        if ty: rec["t"] = ty[:2]
        if CO.get(k): rec["c"] = CO[k]
        if rec: TAG[k] = rec
    for g in UNDATED:
        for it in g["items"]:
            k = g["s"] + "|" + it[1]
            if CO.get(k): TAG.setdefault(k, {})["c"] = CO[k]

    ds = sorted({r[0] for r in rows})
    tpl = open(os.path.join(ROOT, "template.html"), encoding="utf-8").read()
    J = lambda o: json.dumps(o, ensure_ascii=False, separators=(",", ":"))
    out = (tpl.replace("__TH__", J(TH)).replace("__CRD__", J(CRD)).replace("__TAG__", J(TAG))
              .replace("__S__", J(S)).replace("__E__", J(rows)).replace("__U__", J(UNDATED)).replace("__PIN__", J(PIN))
              .replace("__TODAY__", HI)
              .replace("__BUILT__", now_jst().strftime("%Y-%m-%d %H:%M")))
    p = os.path.join(DIST, "index.html")
    open(p, "w", encoding="utf-8").write(out)

    # --- スマホアプリ用 JSON（画像は data URI ではなく元URLのまま渡す）---
    app = {
        "generatedAt": HI,
        "range": [ds[0], ds[-1]],
        "sources": S,
        "dated": [{"d": d, "s": src, "t": t, "u": u, "badge": b,
                   "th": TH_URL.get(src + "|" + u),
                   "cr": CRD.get(src + "|" + u, []),
                   "ty": (TAG.get(src + "|" + u, {}) or {}).get("t", []),
                   "co": (TAG.get(src + "|" + u, {}) or {}).get("c", [])}
                  for d, src, t, u, b in rows],
        "undated": [{"s": g["s"], "note": g["note"],
                     "items": [{"t": it[0], "u": it[1], "badge": it[2] if len(it) > 2 else "",
                                "th": TH_URL.get(g["s"] + "|" + it[1]),
                                "cr": CRD.get(g["s"] + "|" + it[1], []),
                                "ty": (TAG.get(g["s"] + "|" + it[1], {}) or {}).get("t", []),
                                "co": (TAG.get(g["s"] + "|" + it[1], {}) or {}).get("c", [])}
                               for it in g["items"]]}
                    for g in UNDATED],
    }
    jp = os.path.join(DIST, "data.json")
    json.dump(app, open(jp, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    log(f"  {jp}  ({os.path.getsize(jp)/1e6:.2f} MB)")

    # Netlify へドラッグする一式を deploy/ に揃える（関数と設定は据え置き）
    dep = os.path.join(ROOT, "deploy")
    if os.path.isdir(dep):
        import shutil
        for f in ("index.html", "og.png", "data.json"):
            src_f = os.path.join(DIST, f)
            if os.path.exists(src_f): shutil.copyfile(src_f, os.path.join(dep, f))
        log("  deploy/ を更新")

    # スマホアプリ（Expo）にも同じデータを配る
    appdir = os.path.join(os.path.dirname(ROOT), "gallery-app", "assets", "data")
    if os.path.isdir(appdir):
        import shutil
        shutil.copyfile(jp, os.path.join(appdir, "digest.json"))
        log(f"  gallery-app へコピー済み")
    log(f"\n== 完了 ==\n  {p}  ({len(out.encode())/1e6:.2f} MB)")
    log(f"  掲載 {len(rows)} 件 / {len(ds)} 日 ({ds[0]}〜{ds[-1]})")
    log(f"  サムネイル {len(TH)} / 制作者 {len(CRD)} / タグ {len(TAG)}")
    log("  内訳: " + ", ".join(f"{S[s]['n']} {n}" for s, n in Counter(r[1] for r in rows).most_common()))

    # 1件も取れなかったギャラリーを目立たせる（サイト構造の変更や遮断の検知）
    got = Counter(r[1] for r in rows)
    undated_src = {u["s"] for u in UNDATED}
    dead = [S[k]["n"] for k in S if k not in got and k not in undated_src]
    if dead:
        log("  !! 取得0件: " + ", ".join(dead))

if __name__ == "__main__":
    main()
