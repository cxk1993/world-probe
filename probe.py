#!/usr/bin/env python3
"""Ningxi world probe — runs on GitHub runners (offshore egress), writes structured world state.

Why here: the local host cannot reach most offshore domains (http=000) and GDELT rate-limits
per IP. GH runners have clean egress, so this is the probe's home. Output is committed back
into data/ so the agent can read it over the GitHub API.
"""
import json, os, re, time, urllib.request, urllib.error
from datetime import datetime, timezone

UA = {"User-Agent": "ningxi-world-probe/1.0 (+https://github.com/cxk1993/world-probe)"}
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
KEYWORDS = [
    ("hormuz", "strait+of+hormuz"), ("tsmc", "tsmc"),
    ("ai", "%22artificial+intelligence%22"), ("semis", "semiconductor"),
    ("ceasefire", "ceasefire"), ("dedollar", "%22de-dollarization%22"),
    ("taiwan", "taiwan"), ("oil", "brent+oil"),
]
RSS = {
    "bbc_world": "https://feeds.bbci.co.uk/news/world/rss.xml",
    "bbc_business": "https://feeds.bbci.co.uk/news/business/rss.xml",
    "aljazeera": "https://www.aljazeera.com/xml/rss/all.xml",
    "un_news": "https://news.un.org/feed/subscribe/en/news/all/rss.xml",
}
PROBE_URLS = [
    "https://api.gdeltproject.org/api/v2/doc/doc?query=test&mode=artlist&maxrecords=1&format=json",
    "https://worldmonitor.app/llms.txt",
    "https://www.reuters.com/",
]


def get(url, timeout=25, tries=2):
    last = None
    for i in range(tries):
        try:
            return urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout).read()
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(2 + 3 * i)
    raise last


def gdelt(q):
    u = ("https://api.gdeltproject.org/api/v2/doc/doc?query=" + q
         + "&mode=timelinevol&format=json&timespan=30days")
    raw = get(u).decode("utf-8", "replace")
    pts = json.loads(raw)["timeline"][0]["data"]
    now = pts[-1]["value"]
    ref = pts[max(0, len(pts) - 6)]["value"]
    return {"now": round(now, 4), "ref": round(ref, 4),
            "chg_pct": round((now - ref) / ref * 100, 1) if ref else None,
            "points": len(pts), "asof": pts[-1]["date"]}


def rss(url):
    raw = get(url).decode("utf-8", "replace")
    items = re.findall(r"<item[^>]*>(.*?)</item>", raw, re.S)
    titles = []
    for it in items[:40]:
        m = re.search(r"<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>", it, re.S)
        if m:
            titles.append(re.sub(r"<[^>]+>", "", m.group(1)).strip())
    return {"count": len(items), "titles": titles[:12]}


def reach(u):
    try:
        r = urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=20)
        return {"url": u, "status": r.status, "bytes": len(r.read(4096))}
    except urllib.error.HTTPError as e:
        return {"url": u, "status": e.code, "err": "http"}
    except Exception as e:  # noqa: BLE001
        return {"url": u, "status": None, "err": type(e).__name__}


# ---- 境内行情（东财 push2，一次批量）----
ETF_BASKET = [
    ("1.510050","上证50","宽基"),("1.510300","沪深300","宽基"),
    ("1.510500","中证500","宽基"),("1.512100","中证1000","宽基"),
    ("1.512480","半导体","科技"),("0.159819","人工智能","科技"),("1.512660","军工","科技"),
    ("1.515790","光伏","制造"),("1.515030","新能源车","制造"),
    ("1.512400","有色金属","资源"),("1.515220","煤炭","资源"),
    ("1.512200","房地产","地产金融"),("1.512880","证券","地产金融"),("1.512800","银行","地产金融"),
    ("1.512010","医药","防御"),("0.159928","消费","防御"),
    ("1.513100","纳指","跨境"),("1.513520","日经","跨境"),("1.513130","恒生科技","跨境"),
    ("1.518880","黄金","商品"),("1.511260","十年国债","债券"),
]


def cn_market():
    ids = ",".join(x for x, _, _ in ETF_BASKET)
    u = ("https://push2.eastmoney.com/api/qt/ulist.np/get?secids=" + ids
         + "&fields=f12,f14,f3&fltt=2&invt=2")
    d = json.loads(get(u).decode("utf-8", "replace"))
    rows = []
    for it in (d.get("data") or {}).get("diff", []):
        code = str(it.get("f12"))
        grp = next((g for s2, n, g in ETF_BASKET if s2.endswith(code)), "?")
        try:
            rows.append({"name": it.get("f14"), "grp": grp, "chg": float(it.get("f3"))})
        except (TypeError, ValueError):
            continue
    if not rows:
        raise ValueError("empty cn payload")
    rows.sort(key=lambda r: -r["chg"])
    vals = sorted(r["chg"] for r in rows)
    return {"rows": rows, "median": round(vals[len(vals)//2], 2),
            "spread": round(rows[0]["chg"] - rows[-1]["chg"], 2),
            "strong": [rows[0]["name"], rows[0]["chg"]],
            "weak": [rows[-1]["name"], rows[-1]["chg"]]}


def main():
    os.makedirs(OUT, exist_ok=True)
    snap = {"ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "gdelt": {}, "rss": {}, "cn": {}, "reach": [], "errors": {}}

    for name, q in KEYWORDS:
        try:
            snap["gdelt"][name] = gdelt(q)
        except Exception as e:  # noqa: BLE001
            snap["errors"]["gdelt:" + name] = type(e).__name__ + ":" + str(e)[:80]
        time.sleep(30)  # GDELT: 429 even at 8s from GH runners; back off hard

    for name, url in RSS.items():
        try:
            snap["rss"][name] = rss(url)
        except Exception as e:  # noqa: BLE001
            snap["errors"]["rss:" + name] = type(e).__name__ + ":" + str(e)[:80]

    try:
        snap["cn"] = cn_market()
    except Exception as e:  # noqa: BLE001
        snap["errors"]["cn"] = type(e).__name__ + ":" + str(e)[:80]

    for u in PROBE_URLS:
        snap["reach"].append(reach(u))

    with open(os.path.join(OUT, "latest.json"), "w", encoding="utf-8") as f:
        json.dump(snap, f, ensure_ascii=False, indent=1)
    with open(os.path.join(OUT, "history.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": snap["ts"],
                            "gdelt": {k: v["now"] for k, v in snap["gdelt"].items()},
                            "rss_count": {k: v["count"] for k, v in snap["rss"].items()}},
                           ensure_ascii=False) + "\n")

    ok = sum(1 for r in snap["reach"] if r.get("status"))
    print("gdelt=%d/%d rss=%d/%d cn=%s reach=%d/%d errors=%d" % (
        len(snap["gdelt"]), len(KEYWORDS), len(snap["rss"]), len(RSS),
        ("%.2f/%.2f" % (snap["cn"]["median"], snap["cn"]["spread"])) if snap.get("cn") else "FAIL",
        ok, len(PROBE_URLS), len(snap["errors"])))
    for k, v in snap["errors"].items():
        print("  ERR", k, v)


if __name__ == "__main__":
    main()
