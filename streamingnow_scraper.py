#!/usr/bin/env python3
"""
streamingnow.mov scraper
Handles Cloudflare Turnstile via FlareSolverr or manual cf_clearance.

Usage:
  # Option A: pass fresh cf_clearance from browser
  python streamingnow_scraper.py --cf "cf_clearance=..."

  # Option B: use FlareSolverr (must be running)
  python streamingnow_scraper.py --flaresolverr http://localhost:8191

  # Option C: use browser cookie file (Netscape format)
  python streamingnow_scraper.py --cookies cookies.txt

  # Default URL is baked in; pass positional arg to override
  python streamingnow_scraper.py "https://streamingnow.mov/?play=..."
"""

import re
import sys
import json
import time
import argparse
import http.cookiejar
from urllib.parse import urlparse, parse_qs, urlencode, urljoin
from curl_cffi import requests as cffi_requests

# ── Constants ──────────────────────────────────────────────────────────────────

BASE_URL = "https://streamingnow.mov"
ORIGIN   = "https://streamingnow.mov"
UA       = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"

DEFAULT_URL = (
    "https://streamingnow.mov/?play=SW1PWFRUbUcxTWdmNHIvT1RGb0tTaXFRVStiR3NRdkZN"
    "cWlrOWtVc2ljYU5nQ1JPeDJwbmhFeTN3VHE3Q0drOGFiSUs0ZU9LUEo1TE9XcEhUTHJLOUt0"
    "cGc0aXZvTGhDUDhRV1IyMGR3Qnl4d0YyNERsQWRhOXBvK2tQanNvODBzc3FOQnVDRjl2UUJD"
    "VjJURjFqNjNER20="
)

# ── FlareSolverr helper ────────────────────────────────────────────────────────

def get_cf_via_flaresolverr(url, fs_url="http://localhost:8191"):
    """Call FlareSolverr to solve Turnstile and return (cf_clearance, user_agent)."""
    import urllib.request
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

    solution = data["solution"]
    cookies  = {c["name"]: c["value"] for c in solution.get("cookies", [])}
    cf_val   = cookies.get("cf_clearance", "")
    ua       = solution.get("userAgent", UA)
    print(f"[+] FlareSolverr: cf_clearance={'OK' if cf_val else 'NOT FOUND'}")
    return cf_val, ua

# ── Session setup ──────────────────────────────────────────────────────────────

def make_session(cf_clearance="", user_agent=UA, cookie_file=None):
    s = cffi_requests.Session(impersonate="chrome131")
    if cf_clearance:
        s.cookies.set("cf_clearance", cf_clearance, domain="streamingnow.mov")
    if cookie_file:
        jar = http.cookiejar.MozillaCookieJar(cookie_file)
        try:
            jar.load(ignore_discard=True, ignore_expires=True)
            for c in jar:
                if "streamingnow" in c.domain:
                    s.cookies.set(c.name, c.value, domain=c.domain)
                    print(f"[+] Loaded cookie: {c.name} from {c.domain}")
        except Exception as e:
            print(f"[!] Cookie file error: {e}")
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

# ── Step 1: Fetch main page ────────────────────────────────────────────────────

def fetch_main(session, ua, url):
    print(f"[*] GET {url}")
    r = session.get(url, headers=nav_headers(ua), timeout=30)
    r.raise_for_status()
    html = r.text

    # Detect Turnstile challenge
    if "challenges.cloudflare.com/turnstile" in html and "video_id" not in html:
        print("[!] Got Turnstile challenge page — cf_clearance is expired/missing.")
        print("    Fix: pass --cf <value> or --flaresolverr <url>")
        print("    To get cf_clearance manually:")
        print("      1. Open the URL in Chrome")
        print("      2. F12 → Application → Cookies → streamingnow.mov → cf_clearance")
        print("      3. Run: python streamingnow_scraper.py --cf \"<value>\"")
        sys.exit(1)

    servers = []

    # Try JS var pattern
    json_match = re.search(r'var\s+(?:servers|episodes|links)\s*=\s*(\[.*?\]);', html, re.DOTALL)
    if json_match:
        try:
            servers = json.loads(json_match.group(1))
            print(f"[+] Found {len(servers)} servers via JS var")
        except Exception:
            pass

    # Fallback: regex on individual fields
    if not servers:
        vid_ids   = re.findall(r'"video_id"\s*:\s*"([^"]+)"', html)
        srv_ids   = re.findall(r'"server_id"\s*:\s*"?(\d+)"?', html)
        qualities = re.findall(r'"quality"\s*:\s*"([^"]+)"', html)
        for i, (v, s) in enumerate(zip(vid_ids, srv_ids)):
            servers.append({
                "video_id":  v,
                "server_id": s,
                "quality":   qualities[i] if i < len(qualities) else "UNKNOWN",
            })
        if servers:
            print(f"[+] Found {len(servers)} servers via regex")

    if not servers:
        print("[!] No servers found. Page snippet:")
        print(html[:3000])
        sys.exit(1)

    qs = parse_qs(urlparse(url).query)
    play_token = qs.get("play", [""])[0]

    # Full token (may have suffix appended by JS in page)
    token_match = re.search(r'["\']token["\']\s*:\s*["\']([A-Za-z0-9+/=]{60,})["\']', html)
    if not token_match:
        token_match = re.search(r'token\s*=\s*["\']([A-Za-z0-9+/=]{60,})["\']', html)
    full_token = token_match.group(1) if token_match else play_token

    return html, servers, play_token, full_token

# ── Step 2: POST /response.php ─────────────────────────────────────────────────

def post_response_php(session, ua, url, full_token):
    endpoint = f"{BASE_URL}/response.php"
    print(f"[*] POST {endpoint}")
    r = session.post(endpoint, data={"token": full_token},
                     headers=xhr_headers(ua, url), timeout=30)
    print(f"    status={r.status_code}  body={r.text[:200]}")
    return r.text

# ── Step 3: GET /playvideo.php ─────────────────────────────────────────────────

def fetch_playvideo(session, ua, video_id, server_id, full_token, main_url, init=0):
    params = {"video_id": video_id, "server_id": server_id,
              "token": full_token, "init": str(init)}
    pv_url = f"{BASE_URL}/playvideo.php?" + urlencode(params)
    print(f"  [*] GET playvideo server={server_id} init={init}")
    r = session.get(pv_url, headers=iframe_headers(ua, main_url),
                    timeout=30, allow_redirects=True)
    return pv_url, r.text, r.status_code

# ── Step 4: GET /vipstream_vfx.php ────────────────────────────────────────────

def fetch_vipstream(session, ua, server_id, vfx_token, playvideo_url):
    vfx_url = f"{BASE_URL}/vipstream_vfx.php?s={server_id}&token={vfx_token}"
    print(f"  [*] GET vipstream_vfx server={server_id}")
    r = session.get(vfx_url, headers=iframe_headers(ua, playvideo_url), timeout=30)
    return vfx_url, r.text

# ── Extraction helpers ─────────────────────────────────────────────────────────

def extract_m3u8(text):
    return list(dict.fromkeys(re.findall(r'https?://[^\s\'"<>]+\.m3u8[^\s\'"<>]*', text)))

def extract_iframes(text):
    return re.findall(r'<iframe[^>]+src=["\']([^"\']+)["\']', text, re.IGNORECASE)

def extract_mp4(text):
    return list(dict.fromkeys(re.findall(r'https?://[^\s\'"<>]+\.mp4[^\s\'"<>]*', text)))

def extract_vfx_token(html, server_id):
    m = re.search(rf'vipstream_vfx\.php\?s={server_id}&token=([A-Za-z0-9+/=]+)', html)
    return m.group(1) if m else None

# ── External embed extractor ───────────────────────────────────────────────────

def extract_from_embed(session, ua, embed_url):
    results = {"m3u8": [], "mp4": []}
    parsed  = urlparse(embed_url)
    domain  = parsed.netloc
    print(f"    [~] External embed: {embed_url}")

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
        r = cffi_requests.get(embed_url, headers=ext_h, impersonate="chrome131", timeout=20)
        html = r.text
        results["m3u8"].extend(extract_m3u8(html))
        results["mp4"].extend(extract_mp4(html))

        # luluvdo / callistanise /dl endpoint
        file_code_m = re.search(r'/(?:e|embed)/([a-z0-9]+)', embed_url)
        if not results["m3u8"] and file_code_m:
            file_code = file_code_m.group(1)
            hash_m    = re.search(r'["\']hash["\']\s*[=:]\s*["\']([a-f0-9\-]+)["\']', html)
            hash_val  = hash_m.group(1) if hash_m else ""
            dl_url    = f"https://{domain}/dl?op=view&file_code={file_code}&hash={hash_val}&embed=1&referer=streamingnow.mov&adb=1&hls4=1"
            dl_h = {
                "content-cache": "no-cache",
                "x-requested-with": "XMLHttpRequest",
                "user-agent": ua,
                "accept": "*/*",
                "sec-fetch-site": "same-origin",
                "sec-fetch-mode": "cors",
                "sec-fetch-dest": "empty",
                "referer": embed_url,
                "accept-language": "en-US,en;q=0.9",
            }
            dr = cffi_requests.get(dl_url, headers=dl_h, impersonate="chrome131", timeout=20)
            results["m3u8"].extend(extract_m3u8(dr.text))
            results["mp4"].extend(extract_mp4(dr.text))
    except Exception as e:
        print(f"    [!] embed error: {e}")

    return results

# ── Main scraper ───────────────────────────────────────────────────────────────

def scrape(url, cf_clearance="", user_agent=UA, cookie_file=None,
           flaresolverr_url=""):
    if flaresolverr_url:
        print(f"[*] Using FlareSolverr at {flaresolverr_url}")
        cf_clearance, user_agent = get_cf_via_flaresolverr(url, flaresolverr_url)

    session, ua = make_session(cf_clearance, user_agent, cookie_file)

    results = {
        "url": url,
        "servers": [],
        "m3u8_urls": [],
        "iframe_urls": [],
        "mp4_urls": [],
    }

    html, servers, play_token, full_token = fetch_main(session, ua, url)

    print(f"\n[+] {len(servers)} servers:")
    for s in servers:
        print(f"    [{s['server_id']}] {s.get('quality','?')}  {s['video_id'][:24]}...")
    results["servers"] = servers

    if full_token:
        post_response_php(session, ua, url, full_token)
        time.sleep(0.5)

    print("\n[*] Processing servers...\n")
    seen_iframes = set()

    for srv in servers:
        vid  = srv["video_id"]
        sid  = srv["server_id"]
        qual = srv.get("quality", "?")
        print(f"── Server {sid} ({qual}) ──")

        for init in [1, 0]:
            pv_url, pv_html, status = fetch_playvideo(session, ua, vid, sid, full_token, url, init)
            time.sleep(0.3)
            if status != 200:
                print(f"  [!] status={status}")
                continue

            # VFX token for hi-quality servers
            vfx_token = extract_vfx_token(pv_html, sid)
            if vfx_token and int(sid) in [88, 89, 90]:
                _, vfx_html = fetch_vipstream(session, ua, sid, vfx_token, pv_url)
                for m in extract_m3u8(vfx_html):
                    if m not in results["m3u8_urls"]:
                        results["m3u8_urls"].append(m)
                        print(f"  [m3u8/vfx] {m}")

            for m in extract_m3u8(pv_html):
                if m not in results["m3u8_urls"]:
                    results["m3u8_urls"].append(m)
                    print(f"  [m3u8] {m}")

            for m in extract_mp4(pv_html):
                if m not in results["mp4_urls"]:
                    results["mp4_urls"].append(m)
                    print(f"  [mp4] {m}")

            for ifr in extract_iframes(pv_html):
                if not ifr.startswith("http"):
                    ifr = urljoin(BASE_URL, ifr)
                if ifr in seen_iframes:
                    continue
                seen_iframes.add(ifr)
                entry = {"server_id": sid, "quality": qual, "url": ifr}
                results["iframe_urls"].append(entry)
                print(f"  [iframe] {ifr}")

                if "streamingnow.mov" not in ifr:
                    ed = extract_from_embed(session, ua, ifr)
                    for m in ed["m3u8"]:
                        if m not in results["m3u8_urls"]:
                            results["m3u8_urls"].append(m)
                            print(f"    [m3u8<embed] {m}")
                    for m in ed["mp4"]:
                        if m not in results["mp4_urls"]:
                            results["mp4_urls"].append(m)
                            print(f"    [mp4<embed] {m}")

            if init == 1:
                time.sleep(0.4)

        time.sleep(0.5)

    return results

# ── Summary ────────────────────────────────────────────────────────────────────

def print_summary(results):
    print("\n" + "="*60)
    print("SUMMARY")
    print("="*60)
    print(f"\nServers ({len(results['servers'])}):")
    for s in results["servers"]:
        print(f"  [{s['server_id']}] {s.get('quality','?')}  {s['video_id']}")

    print(f"\nM3U8 ({len(results['m3u8_urls'])}):")
    for u in results["m3u8_urls"]:
        print(f"  {u}")

    print(f"\nIframes ({len(results['iframe_urls'])}):")
    for e in results["iframe_urls"]:
        print(f"  [{e['server_id']}] {e['quality']} -> {e['url']}")

    print(f"\nMP4 ({len(results['mp4_urls'])}):")
    for u in results["mp4_urls"]:
        print(f"  {u}")

# ── Entry point ────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser()
    p.add_argument("url", nargs="?", default=DEFAULT_URL)
    p.add_argument("--cf",            help="cf_clearance cookie value")
    p.add_argument("--cookies",       help="Netscape cookies.txt file")
    p.add_argument("--flaresolverr",  help="FlareSolverr URL (e.g. http://localhost:8191)")
    p.add_argument("--json",          action="store_true")
    args = p.parse_args()

    results = scrape(
        args.url,
        cf_clearance=args.cf or "",
        cookie_file=args.cookies,
        flaresolverr_url=args.flaresolverr or "",
    )

    if args.json:
        print(json.dumps(results, indent=2))
    else:
        print_summary(results)

if __name__ == "__main__":
    main()
