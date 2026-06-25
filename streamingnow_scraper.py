#!/usr/bin/env python3
"""
streamingnow.mov scraper
Usage:
  python streamingnow_scraper.py --flaresolverr http://localhost:8191
  python streamingnow_scraper.py --cf "<cf_clearance value>"
  python streamingnow_scraper.py --cookies cookies.txt
"""

import re
import sys
import json
import time
import argparse
import http.cookiejar
import urllib.request
from urllib.parse import urlparse, parse_qs, urlencode, urljoin
from curl_cffi import requests as cffi_requests

import sys as _sys
def eprint(*a, **k): print(*a, file=_sys.stderr, **k)

BASE_URL = "https://streamingnow.mov"
ORIGIN   = "https://streamingnow.mov"
UA       = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"

DEFAULT_URL = (
    "https://streamingnow.mov/?play=SW1PWFRUbUcxTWdmNHIvT1RGb0tTaXFRVStiR3NRdkZN"
    "cWlrOWtVc2ljYU5nQ1JPeDJwbmhFeTN3VHE3Q0drOGFiSUs0ZU9LUEo1TE9XcEhUTHJLOUt0"
    "cGc0aXZvTGhDUDhRV1IyMGR3Qnl4d0YyNERsQWRhOXBvK2tQanNvODBzc3FOQnVDRjl2UUJD"
    "VjJURjFqNjNER20="
)

# ── FlareSolverr ───────────────────────────────────────────────────────────────

def flaresolverr_get(url, fs_url="http://localhost:8191"):
    """
    Call FlareSolverr, return (html, all_cookies_dict, user_agent).
    Uses the HTML from FlareSolverr directly — no second request needed.
    """
    payload = json.dumps({
        "cmd": "request.get",
        "url": url,
        "maxTimeout": 60000,
    }).encode()
    req = urllib.request.Request(
        f"{fs_url}/v1",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=90) as resp:
        data = json.loads(resp.read())

    if data.get("status") != "ok":
        raise RuntimeError(f"FlareSolverr error: {data.get('message')}")

    solution  = data["solution"]
    html      = solution.get("response", "")
    ua        = solution.get("userAgent", UA)
    cookies   = {c["name"]: c["value"] for c in solution.get("cookies", [])}

    eprint(f"[+] FlareSolverr UA: {ua[:80]}")
    eprint(f"[+] FlareSolverr cookies: {list(cookies.keys())}")
    cf_val = cookies.get("cf_clearance", "")
    eprint(f"[+] cf_clearance: {'OK (' + cf_val[:20] + '...)' if cf_val else 'NOT FOUND'}")

    return html, cookies, ua

# ── Session ────────────────────────────────────────────────────────────────────

def make_session(cookies_dict, user_agent):
    s = cffi_requests.Session(impersonate="chrome131")
    for name, value in cookies_dict.items():
        s.cookies.set(name, value, domain="streamingnow.mov")
    return s, user_agent

def nav_headers(ua, referer=""):
    h = {
        "cache-control": "max-age=0",
        "sec-ch-ua": '"Google Chrome";v="131", "Not.A/Brand";v="8", "Chromium";v="131"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "upgrade-insecure-requests": "1",
        "user-agent": ua,
        "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
        "sec-fetch-site": "same-origin" if referer else "none",
        "sec-fetch-mode": "navigate",
        "sec-fetch-user": "?1",
        "sec-fetch-dest": "document",
        "accept-encoding": "gzip, deflate, br, zstd",
        "accept-language": "en-US,en;q=0.9",
        "priority": "u=0, i",
    }
    if referer:
        h["referer"] = referer
    return h

def iframe_headers(ua, referer):
    h = nav_headers(ua, referer)
    h["sec-fetch-dest"] = "iframe"
    return h

def xhr_headers(ua, referer):
    return {
        "sec-ch-ua": '"Google Chrome";v="131", "Not.A/Brand";v="8", "Chromium";v="131"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "x-requested-with": "XMLHttpRequest",
        "user-agent": ua,
        "accept": "*/*",
        "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
        "origin": ORIGIN,
        "sec-fetch-site": "same-origin",
        "sec-fetch-mode": "cors",
        "sec-fetch-dest": "empty",
        "referer": referer,
        "accept-encoding": "gzip, deflate, br, zstd",
        "accept-language": "en-US,en;q=0.9",
        "priority": "u=1, i",
    }

# ── Parsers ────────────────────────────────────────────────────────────────────

def parse_servers_and_token(html, url):
    servers = []

    m = re.search(r'var\s+(?:servers|episodes|links)\s*=\s*(\[.*?\]);', html, re.DOTALL)
    if m:
        try:
            servers = json.loads(m.group(1))
            eprint(f"[+] {len(servers)} servers via JS var")
        except Exception:
            pass

    if not servers:
        vids  = re.findall(r'"video_id"\s*:\s*"([^"]+)"', html)
        sids  = re.findall(r'"server_id"\s*:\s*"?(\d+)"?', html)
        quals = re.findall(r'"quality"\s*:\s*"([^"]+)"', html)
        for i, (v, s) in enumerate(zip(vids, sids)):
            servers.append({"video_id": v, "server_id": s,
                            "quality": quals[i] if i < len(quals) else "?"})
        if servers:
            eprint(f"[+] {len(servers)} servers via regex")

    qs         = parse_qs(urlparse(url).query)
    play_token = qs.get("play", [""])[0]

    tm = re.search(r'["\']token["\']\s*[=:]\s*["\']([A-Za-z0-9+/=]{60,})["\']', html)
    if not tm:
        tm = re.search(r'token\s*=\s*["\']([A-Za-z0-9+/=]{60,})["\']', html)
    full_token = tm.group(1) if tm else play_token

    return servers, play_token, full_token

def extract_m3u8(text):
    return list(dict.fromkeys(re.findall(r'https?://[^\s\'"<>]+\.m3u8[^\s\'"<>]*', text)))

def extract_iframes(text):
    return re.findall(r'<iframe[^>]+src=["\']([^"\']+)["\']', text, re.IGNORECASE)

def extract_mp4(text):
    return list(dict.fromkeys(re.findall(r'https?://[^\s\'"<>]+\.mp4[^\s\'"<>]*', text)))

def extract_vfx_token(html, server_id):
    m = re.search(rf'vipstream_vfx\.php\?s={server_id}&token=([A-Za-z0-9+/=]+)', html)
    return m.group(1) if m else None

# ── Steps ──────────────────────────────────────────────────────────────────────

def post_response_php(session, ua, url, token):
    ep = f"{BASE_URL}/response.php"
    eprint(f"[*] POST {ep}")
    r = session.post(ep, data={"token": token}, headers=xhr_headers(ua, url), timeout=30)
    eprint(f"    {r.status_code}  {r.text[:120]}")
    return r.text

def fetch_playvideo(session, ua, video_id, server_id, token, main_url, init=0):
    params = {"video_id": video_id, "server_id": server_id, "token": token, "init": str(init)}
    pv_url = f"{BASE_URL}/playvideo.php?" + urlencode(params)
    eprint(f"  [*] playvideo server={server_id} init={init}")
    r = session.get(pv_url, headers=iframe_headers(ua, main_url), timeout=30, allow_redirects=True)
    return pv_url, r.text, r.status_code

def fetch_vipstream(session, ua, server_id, vfx_token, pv_url):
    url = f"{BASE_URL}/vipstream_vfx.php?s={server_id}&token={vfx_token}"
    eprint(f"  [*] vipstream_vfx server={server_id}")
    r = session.get(url, headers=iframe_headers(ua, pv_url), timeout=30)
    return url, r.text

def extract_from_embed(ua, embed_url):
    results = {"m3u8": [], "mp4": []}
    parsed  = urlparse(embed_url)
    domain  = parsed.netloc
    eprint(f"    [~] embed: {embed_url}")
    ext_h = {
        "user-agent": ua,
        "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "accept-language": "en-US,en;q=0.9",
        "sec-fetch-dest": "iframe",
        "sec-fetch-mode": "navigate",
        "sec-fetch-site": "cross-site",
        "referer": f"{BASE_URL}/",
    }
    try:
        r    = cffi_requests.get(embed_url, headers=ext_h, impersonate="chrome131", timeout=20)
        html = r.text
        results["m3u8"].extend(extract_m3u8(html))
        results["mp4"].extend(extract_mp4(html))

        fc_m = re.search(r'/(?:e|embed)/([a-z0-9]+)', embed_url)
        if not results["m3u8"] and fc_m:
            fc      = fc_m.group(1)
            hm      = re.search(r'["\']hash["\']\s*[=:]\s*["\']([a-f0-9\-]+)["\']', html)
            hv      = hm.group(1) if hm else ""
            dl_url  = f"https://{domain}/dl?op=view&file_code={fc}&hash={hv}&embed=1&referer=streamingnow.mov&adb=1&hls4=1"
            dl_h    = {"content-cache": "no-cache", "x-requested-with": "XMLHttpRequest",
                       "user-agent": ua, "accept": "*/*", "sec-fetch-site": "same-origin",
                       "sec-fetch-mode": "cors", "sec-fetch-dest": "empty",
                       "referer": embed_url, "accept-language": "en-US,en;q=0.9"}
            dr = cffi_requests.get(dl_url, headers=dl_h, impersonate="chrome131", timeout=20)
            results["m3u8"].extend(extract_m3u8(dr.text))
            results["mp4"].extend(extract_mp4(dr.text))
    except Exception as e:
        eprint(f"    [!] {e}")
    return results

# ── Main ───────────────────────────────────────────────────────────────────────

def scrape(url, cookies_dict, ua):
    session, ua = make_session(cookies_dict, ua)
    results = {"url": url, "servers": [], "m3u8_urls": [], "iframe_urls": [], "mp4_urls": []}

    # If we already have the HTML from FlareSolverr, it's passed via cookies_dict["_html"]
    prefetched_html = cookies_dict.pop("_html", None)

    if prefetched_html:
        eprint(f"[+] Using HTML from FlareSolverr ({len(prefetched_html)} chars)")
        html = prefetched_html
        if "challenges.cloudflare.com/turnstile" in html and "video_id" not in html:
            eprint("[!] FlareSolverr returned challenge page — Turnstile not solved")
            sys.exit(1)
    else:
        eprint(f"[*] GET {url}")
        r = session.get(url, headers=nav_headers(ua), timeout=30)
        if r.status_code == 403:
            eprint(f"[!] 403 — cf_clearance rejected. Get a fresh one from browser.")
            sys.exit(1)
        r.raise_for_status()
        html = r.text
        if "challenges.cloudflare.com/turnstile" in html and "video_id" not in html:
            eprint("[!] Got Turnstile page — pass --cf or --flaresolverr")
            sys.exit(1)

    servers, play_token, full_token = parse_servers_and_token(html, url)

    if not servers:
        eprint("[!] No servers found. HTML snippet:")
        eprint(html[:2000])
        sys.exit(1)

    eprint(f"\n[+] {len(servers)} servers:")
    for s in servers:
        eprint(f"    [{s['server_id']}] {s.get('quality','?')}  {s['video_id'][:28]}...")
    results["servers"] = servers

    if full_token:
        post_response_php(session, ua, url, full_token)
        time.sleep(0.5)

    seen_iframes = set()
    eprint()

    for srv in servers:
        vid  = srv["video_id"]
        sid  = srv["server_id"]
        qual = srv.get("quality", "?")
        eprint(f"── Server {sid} ({qual}) ──")

        for init in [1, 0]:
            pv_url, pv_html, status = fetch_playvideo(session, ua, vid, sid, full_token, url, init)
            time.sleep(0.3)
            if status != 200:
                eprint(f"  [!] status={status}")
                continue

            vfx_token = extract_vfx_token(pv_html, sid)
            if vfx_token and int(sid) in [88, 89, 90]:
                _, vfx_html = fetch_vipstream(session, ua, sid, vfx_token, pv_url)
                for m in extract_m3u8(vfx_html):
                    if m not in results["m3u8_urls"]:
                        results["m3u8_urls"].append(m)
                        eprint(f"  [m3u8/vfx] {m}")

            for m in extract_m3u8(pv_html):
                if m not in results["m3u8_urls"]:
                    results["m3u8_urls"].append(m); print(f"  [m3u8] {m}")

            for m in extract_mp4(pv_html):
                if m not in results["mp4_urls"]:
                    results["mp4_urls"].append(m); print(f"  [mp4] {m}")

            for ifr in extract_iframes(pv_html):
                if not ifr.startswith("http"):
                    ifr = urljoin(BASE_URL, ifr)
                if ifr in seen_iframes:
                    continue
                seen_iframes.add(ifr)
                results["iframe_urls"].append({"server_id": sid, "quality": qual, "url": ifr})
                eprint(f"  [iframe] {ifr}")
                if "streamingnow.mov" not in ifr:
                    ed = extract_from_embed(ua, ifr)
                    for m in ed["m3u8"]:
                        if m not in results["m3u8_urls"]:
                            results["m3u8_urls"].append(m); print(f"    [m3u8<embed] {m}")
                    for m in ed["mp4"]:
                        if m not in results["mp4_urls"]:
                            results["mp4_urls"].append(m); print(f"    [mp4<embed] {m}")

            if init == 1:
                time.sleep(0.4)
        time.sleep(0.5)

    return results

def print_summary(r):
    print("\n" + "="*60 + "\nSUMMARY\n" + "="*60)
    print(f"\nServers ({len(r['servers'])}):")
    for s in r["servers"]:
        print(f"  [{s['server_id']}] {s.get('quality','?')}  {s['video_id']}")
    print(f"\nM3U8 ({len(r['m3u8_urls'])}):")
    for u in r["m3u8_urls"]: print(f"  {u}")
    print(f"\nIframes ({len(r['iframe_urls'])}):")
    for e in r["iframe_urls"]: print(f"  [{e['server_id']}] {e['quality']} -> {e['url']}")
    print(f"\nMP4 ({len(r['mp4_urls'])}):")
    for u in r["mp4_urls"]: print(f"  {u}")

def main():
    p = argparse.ArgumentParser()
    p.add_argument("url", nargs="?", default=DEFAULT_URL)
    p.add_argument("--cf",           help="cf_clearance cookie value")
    p.add_argument("--cookies",      help="Netscape cookies.txt")
    p.add_argument("--flaresolverr", help="FlareSolverr URL e.g. http://localhost:8191")
    p.add_argument("--json",         action="store_true")
    args = p.parse_args()

    cookies_dict = {}
    ua = UA

    if args.flaresolverr:
        eprint(f"[*] FlareSolverr: {args.flaresolverr}")
        html, cookies_dict, ua = flaresolverr_get(args.url or DEFAULT_URL, args.flaresolverr)
        cookies_dict["_html"] = html  # pass prefetched HTML to scrape()

    elif args.cf:
        cookies_dict["cf_clearance"] = args.cf

    elif args.cookies:
        jar = http.cookiejar.MozillaCookieJar(args.cookies)
        jar.load(ignore_discard=True, ignore_expires=True)
        for c in jar:
            if "streamingnow" in c.domain:
                cookies_dict[c.name] = c.value
                eprint(f"[+] Cookie: {c.name}")

    else:
        eprint("[!] No auth provided. Pass --cf, --flaresolverr, or --cookies.")
        eprint("    Example: python streamingnow_scraper.py --flaresolverr http://localhost:8191")
        sys.exit(1)

    results = scrape(args.url or DEFAULT_URL, cookies_dict, ua)

    if args.json:
        eprint(json.dumps(results, indent=2))
    else:
        print_summary(results)

if __name__ == "__main__":
    main()
