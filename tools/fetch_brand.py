"""Saves the company's public brand assets (logos, icons, stylesheet colours) into brand/."""
import json, re, sys, urllib.request
from pathlib import Path
from urllib.parse import urljoin, urlsplit

BASE = sys.argv[1]
out = Path("brand"); out.mkdir(exist_ok=True)
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"}


def get(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=40) as r:
        return r.read(), r.geturl()


html, final = get(BASE)
text = html.decode("utf-8", "replace")
(out / "site.html").write_text(text[:400000], encoding="utf-8")
info = {"final_url": final,
        "title": re.findall(r"<title[^>]*>(.*?)</title>", text, re.S | re.I)[:1],
        "meta": re.findall(r"<meta[^>]+(?:name|property)=[\"']([^\"']+)[\"'][^>]+content=[\"']([^\"']*)[\"']", text, re.I)[:40],
        "headings": [re.sub(r"<[^>]+>", " ", h).strip()[:200] for h in re.findall(r"<h[1-3][^>]*>(.*?)</h[1-3]>", text, re.S | re.I)][:40],
        "copyright": re.findall(r"[^<>]{0,120}(?:&copy;|©|Copyright)[^<>]{0,160}", text, re.I)[:6]}
urls = set()
for m in re.findall(r"(?:src|href|content|data-src|srcset)=[\"']([^\"' ]+)", text, re.I):
    u = urljoin(final, m)
    if re.search(r"\.(png|jpe?g|svg|ico|webp)(\?|$)", u, re.I) and re.search(r"logo|icon|favicon|brand|dexterity", u, re.I):
        urls.add(u)
css = [urljoin(final, m) for m in re.findall(r"<link[^>]+href=[\"']([^\"']+\.css[^\"']*)", text, re.I)]
info["fonts"] = sorted(set(re.findall(r"fonts\.googleapis\.com/css2?\?family=([^\"'&]+)", text)))
colours = {}
for c in [text] + [get(u)[0].decode("utf-8", "replace") for u in css[:12] if urlsplit(u).netloc == urlsplit(final).netloc]:
    for h in re.findall(r"#[0-9a-fA-F]{6}\b", c):
        colours[h.lower()] = colours.get(h.lower(), 0) + 1
    for u in re.findall(r"url\([\"']?([^\"')]+)", c):
        u = urljoin(final, u)
        if re.search(r"logo", u, re.I) and re.search(r"\.(png|jpe?g|svg|webp)", u, re.I):
            urls.add(u)
info["colours_by_use"] = sorted(colours.items(), key=lambda kv: -kv[1])[:30]
info["css_vars"] = sorted(set(re.findall(r"(--[\w-]*(?:color|primary|accent|brand)[\w-]*)\s*:\s*([^;}{]{1,40})", text, re.I)))[:40]
saved = []
for i, u in enumerate(sorted(urls)[:25]):
    try:
        data, _ = get(u)
        if len(data) < 1500000:
            name = "%02d-%s" % (i, re.sub(r"[^\w.-]", "_", urlsplit(u).path.split("/")[-1])[-70:])
            (out / name).write_bytes(data); saved.append({"file": name, "url": u, "bytes": len(data)})
    except Exception as e:
        saved.append({"url": u, "error": str(e)})
# The site is a single-page app: names and logo files live in its script bundle.
mentions, assets = [], set()
for src in re.findall(r"<script[^>]+src=[\"']([^\"']+\.js)", text, re.I):
    u = urljoin(final, src)
    if urlsplit(u).netloc != urlsplit(final).netloc:
        continue
    js = get(u)[0].decode("utf-8", "replace")
    for m in re.finditer(r"[^\"'`<>{}]{0,90}(?:Dexterity|DEXTERITY)[^\"'`<>{}]{0,120}", js):
        t = m.group(0).strip()
        if t not in mentions:
            mentions.append(t)
    assets.update(urljoin(final, a) for a in re.findall(r"[\"'`](/?assets/[\w./-]+\.(?:png|svg|jpe?g|webp))", js))
    info.setdefault("js_colours", {})
    for h in re.findall(r"#[0-9a-fA-F]{6}\b", js):
        info["js_colours"][h.lower()] = info["js_colours"].get(h.lower(), 0) + 1
info["js_colours"] = sorted(info.get("js_colours", {}).items(), key=lambda kv: -kv[1])[:25]
info["mentions"] = mentions[:120]
info["all_assets"] = sorted(assets)
picked = sorted(assets, key=lambda a: (0 if re.search(r"logo|brand|icon|dexterity", a, re.I) else 1, a))[:30]
for i, u in enumerate(picked, start=30):
    try:
        data, _ = get(u)
        if len(data) < 900000:
            name = "%02d-%s" % (i, re.sub(r"[^\w.-]", "_", urlsplit(u).path.split("/")[-1])[-70:])
            (out / name).write_bytes(data); saved.append({"file": name, "url": u, "bytes": len(data)})
    except Exception as e:
        saved.append({"url": u, "error": str(e)})
info["images"] = saved
info["img_tags"] = re.findall(r"<img[^>]{0,400}>", text, re.I)[:30]
(out / "brand.json").write_text(json.dumps(info, indent=1), encoding="utf-8")
print(json.dumps({k: info[k] for k in ("final_url", "title", "copyright", "images")}, indent=1))
