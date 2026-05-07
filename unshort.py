#!/usr/bin/env python3
"""
unshort — Unwrap monetized shortlinks to their real destination.
=================================================================
HYBRID APPROACH: Stealth browser loads the page (Cloudflare clearance + session
cookies), then curl_cffi (Chrome TLS impersonation) makes the GraphQL bypass
calls. Together they crack both Cloudflare AND the anti-bot.

Currently supports Linkvertise. More shorteners coming.

Usage:
    python unshort.py <url>
    python unshort.py -q <url>              # quiet (URL only)
    python unshort.py --visible             # show browser

Install:
    pip install playwright curl_cffi
    playwright install chromium
"""

import sys, json, re, time, argparse
from typing import Any

C = {k: f"\033[{v}m" for k, v in {"*": "0", "b": "1", "d": "2", "r": "31",
     "g": "32", "y": "33", "c": "36", "m": "35"}.items()}
def S(c, s): return f"{C.get(c,'')}{s}{C['*']}"

GQL = "https://publisher.linkvertise.com/graphql"
HEAD = {"Content-Type": "application/json", "Accept": "application/json",
        "Origin": "https://linkvertise.com", "Referer": "https://linkvertise.com/"}


def load_runtime_deps() -> tuple[Any, Any]:
    missing = []
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        missing.append("playwright")
        sync_playwright = None
    try:
        from curl_cffi import requests as crequests
    except ImportError:
        missing.append("curl_cffi")
        crequests = None
    if missing:
        sys.exit(f"\033[31mMissing: {', '.join(missing)}\033[0m\n"
                 "\033[36mpip install playwright curl_cffi && playwright install chromium\033[0m")
    return sync_playwright, crequests


def gql(session, query: str) -> dict:
    r = session.post(GQL, json={"query": query}, headers=HEAD)
    if r.status_code != 200:
        raise SystemExit(f"\n  {S('r','X')}  HTTP {r.status_code}")
    body = r.json()
    if body.get("errors"):
        raise SystemExit(f"\n  {S('r','X')}  {body['errors'][0]['message']}")
    return body["data"]


def bypass(url: str, quiet=False, visible=False) -> str:
    sync_playwright, crequests = load_runtime_deps()
    pid, slug = parse_url(url)

    if not quiet:
        print(f"\n  {S('m','*')}  {S('b','Unshort')} {S('d','-')} {S('y',pid+'/'+slug)}")
        print(f"  {S('d','|')}  {S('d','Launching browser')}", end="", flush=True)

    # ── Phase 1: Browser → Cloudflare clearance + session cookies ──
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=not visible,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"])
        ctx = browser.new_context(viewport={"width": 1920, "height": 1080},
                                  locale="en-US", timezone_id="America/Chicago")
        page = ctx.new_page()
        page.goto(f"https://linkvertise.com/{pid}/{slug}?o=sharing",
                  wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(4000)
        cookies = ctx.cookies()
        browser.close()

    if not quiet:
        print(f" {S('g','OK')}")
        print(f"  {S('d','|')}  {S('d','Resolving')}", end="", flush=True)

    # ── Phase 2: curl_cffi with browser cookies → GraphQL bypass ──
    session = crequests.Session(impersonate="chrome")
    for c in cookies:
        name, value = c.get("name"), c.get("value")
        if name is not None and value is not None:
            session.cookies.set(name, value, domain=c.get("domain", ""))

    data = gql(session, (
        f'{{ linkByIdentifier(linkIdentificationInput: {{'
        f' userIdAndUrl: {{ user_id: "{pid}", url: "{slug}" }} }}) {{'
        f' id target_host title }} }}'
    ))
    link = data["linkByIdentifier"]
    lid = link["id"]

    if not quiet:
        print(f" {S('g','OK')}")
        print(f"  {S('d','|')}  {S('d',link.get('title','?') + ' - ' + link.get('target_host','?'))}")
        print(f"  {S('d','|')}  {S('c','Bypassing')}", end="", flush=True)

    for _ in range(25):
        data = gql(session, (
            f'{{ getContent(input: {{ id: {{ id: "{lid}" }} }}) {{'
            f' __typename'
            f' ... on ContentAccessTaskSet {{'
            f'   tasks {{ __typename'
            f'     ... on WaitTask {{ id status }}'
            f'     ... on AdTask {{ id status adIndex adsTotal }}'
            f'     ... on PremiumTask {{ id status }}'
            f'   }}'
            f' }}'
            f' ... on DetailPageTargetData {{ type url }}'
            f'}} }}'
        ))
        content = data["getContent"]

        if content["__typename"] == "DetailPageTargetData":
            if not quiet: print(f" {S('g','OK')}")
            return content["url"]

        task = next((t for t in content.get("tasks", [])
                     if t["__typename"] != "PremiumTask"
                     and t["status"] in ("IN_PROGRESS", "OPEN")), None)

        if not task:
            time.sleep(0.5)
            if not quiet: print(f" {S('d','.')}", end="", flush=True)
            continue

        gql(session, (
            f'mutation {{ completeTask('
            f'input: {{ id: {{ id: "{lid}" }} }}, '
            f'task_id: "{task["id"]}"'
            f') {{ __typename ... on WaitTask {{ id status }} ... on AdTask {{ id status }} }}'
            f'}}'
        ))

        if not quiet:
            m = f"{task['adIndex']}/{task.get('adsTotal','?')}" if task["__typename"] == "AdTask" else "."
            print(f" {S('d',m)}", end="", flush=True)

    raise SystemExit(f"\n  {S('r','X')}  Bypass failed. Try --visible to debug.")


def parse_url(url):
    # Handle dynamic?r= (base64 encoded target)
    if "dynamic?r=" in url or "dynamic/?r=" in url:
        import base64 as b64
        m = re.search(r'[?&/]r=([^&]+)', url)
        if m:
            try: url = b64.b64decode(m.group(1)).decode()
            except: pass
    url = url.replace("&o=sharing", "").replace("?o=sharing", "")
    m = re.search(r"linkvertise\.com(?:/[^/]+)?/(\d+)/([^/?]+)", url)
    if not m: raise SystemExit(f"\n  {S('r','X')}  Invalid URL: {url}")
    return m.group(1), m.group(2)


def main():
    p = argparse.ArgumentParser(description="Unwrap monetized shortlinks to their real destination.")
    p.add_argument("url", nargs="?")
    p.add_argument("-q", "--quiet", action="store_true")
    p.add_argument("--visible", action="store_true")
    p.add_argument("--no-color", action="store_true")
    a = p.parse_args()
    if a.no_color:
        for k in C: C[k] = ""
    url = a.url
    if not url:
        print(f"\n  {S('b',S('m','unshort'))}  {S('d','-- unwrap monetized shortlinks')}")
        print(f"  {S('d','-'*45)}\n")
        try: url = input(f"  {S('c','>')}  Paste shortlink URL: ").strip()
        except (KeyboardInterrupt, EOFError): print(); sys.exit(0)
        if not url: raise SystemExit(f"\n  {S('r','X')}  No URL provided.")
    t0 = time.time()
    result = bypass(url, quiet=a.quiet, visible=a.visible)
    if a.quiet: print(result)
    else: print(f"\n  {S('g','OK')}  {S('b',result)}\n  {S('d',f'Done in {time.time()-t0:.2f}s')}\n")


if __name__ == "__main__":
    main()
