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


def main():
    os.makedirs(OUT, exist_ok=True)
    snap = {"ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "gdelt": {}, "rss": {}, "reach": [], "errors": {}}

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
    print("gdelt=%d/%d rss=%d/%d reach=%d/%d errors=%d" % (
        len(snap["gdelt"]), len(KEYWORDS), len(snap["rss"]), len(RSS),
        ok, len(PROBE_URLS), len(snap["errors"])))
    for k, v in snap["errors"].items():
        print("  ERR", k, v)


if __name__ == "__main__":
    main()
