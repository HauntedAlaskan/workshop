"""Crawl the web from seed sites and report which sites don't link to a privacy policy.

Usage:
    python privacy_checker.py example.com another-site.org
    python privacy_checker.py -f seeds.txt -o results.csv --max-sites 200 --max-depth 2

Starting from the seed sites, it checks each site for a privacy policy, then
follows links on that site's homepage to other sites and checks those too,
breadth first, until it hits --max-sites or --max-depth.

For each site it fetches the homepage, looks for a link that points to a
privacy policy, and falls back to probing a few common paths (/privacy etc.).
It respects robots.txt and waits between requests.
"""

import argparse
import csv
import re
import sys
import time
import urllib.robotparser
from collections import deque
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

USER_AGENT = "PrivacyPolicyChecker/1.0 (+compliance audit)"

# Link text / URL fragments that usually mean "privacy policy".
PRIVACY_PATTERN = re.compile(
    r"privacy|privacidad|privacit|datenschutz|confidentialit|"
    r"privacybeleid|integritetspolicy|personvern|polityka prywatno",
    re.IGNORECASE,
)

COMMON_PATHS = [
    "/privacy",
    "/privacy-policy",
    "/privacy-policy/",
    "/privacy.html",
    "/legal/privacy",
    "/policies/privacy-policy",
]

FIELDS = ["site", "depth", "found_via", "has_privacy_policy", "policy_url", "note"]


def normalize(site):
    site = site.strip()
    if not urlparse(site).scheme:
        site = "https://" + site
    return site.rstrip("/")


def site_key(url):
    """Identify a site by host, so www.example.com and example.com count once."""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return host + (f":{parsed.port}" if parsed.port else "")


def robots_for(base, session):
    rp = urllib.robotparser.RobotFileParser()
    try:
        resp = session.get(base + "/robots.txt", timeout=10)
        rp.parse(resp.text.splitlines() if resp.ok else [])
    except requests.RequestException:
        rp.parse([])
    return rp


def find_policy_link(soup, page_url):
    for a in soup.find_all("a", href=True):
        text = a.get_text(" ", strip=True)
        href = a["href"]
        if PRIVACY_PATTERN.search(text) or PRIVACY_PATTERN.search(href):
            return urljoin(page_url, href)
    return None


def external_sites(soup, page_url):
    """Base URLs of other sites linked from this page."""
    own = site_key(page_url)
    found = {}
    for a in soup.find_all("a", href=True):
        url = urljoin(page_url, a["href"])
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            continue
        key = site_key(url)
        if key != own and key not in found:
            found[key] = f"{parsed.scheme}://{parsed.netloc}"
    return list(found.values())


def check_site(site, session, delay):
    """Return (result row, list of other sites linked from the homepage)."""
    base = normalize(site)
    result = {"site": site, "has_privacy_policy": "", "policy_url": "", "note": ""}
    rp = robots_for(base, session)

    if not rp.can_fetch(USER_AGENT, base + "/"):
        result["note"] = "homepage disallowed by robots.txt"
        return result, []

    try:
        resp = session.get(base + "/", timeout=15)
        resp.raise_for_status()
    except requests.RequestException as e:
        result["note"] = f"fetch failed: {e.__class__.__name__}"
        return result, []

    soup = BeautifulSoup(resp.text, "html.parser")
    links = external_sites(soup, resp.url)

    link = find_policy_link(soup, resp.url)
    if link:
        result.update(has_privacy_policy="yes", policy_url=link, note="linked from homepage")
        return result, links

    # No link on the homepage; try the usual locations.
    for path in COMMON_PATHS:
        url = base + path
        if not rp.can_fetch(USER_AGENT, url):
            continue
        time.sleep(delay)
        try:
            r = session.get(url, timeout=10, allow_redirects=True)
        except requests.RequestException:
            continue
        if r.ok and PRIVACY_PATTERN.search(r.text[:20000]):
            result.update(has_privacy_policy="yes", policy_url=r.url, note="found at common path")
            return result, links

    result.update(has_privacy_policy="no", note="no privacy policy link or page found")
    return result, links


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("sites", nargs="*", help="seed domains or URLs to start from")
    parser.add_argument("-f", "--file", help="text file with one seed site per line")
    parser.add_argument("-o", "--output", help="write results to this CSV file")
    parser.add_argument("--delay", type=float, default=1.0, help="seconds between requests (default 1)")
    parser.add_argument("--max-sites", type=int, default=50, help="stop after checking this many sites (default 50)")
    parser.add_argument("--max-depth", type=int, default=2,
                        help="how many links away from the seeds to go; 0 checks only the seeds (default 2)")
    parser.add_argument("--only-missing", action="store_true", help="only report sites without a policy")
    args = parser.parse_args()

    seeds = list(args.sites)
    if args.file:
        with open(args.file) as fh:
            seeds += [line.strip() for line in fh if line.strip() and not line.startswith("#")]
    if not seeds:
        parser.error("give at least one site or --file")

    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT

    queue = deque()
    seen = set()
    for s in seeds:
        key = site_key(normalize(s))
        if key not in seen:
            seen.add(key)
            queue.append((s, 0, ""))

    out = open(args.output, "w", newline="") if args.output else sys.stdout
    writer = csv.DictWriter(out, fieldnames=FIELDS)
    writer.writeheader()

    checked = missing = 0
    try:
        while queue and checked < args.max_sites:
            site, depth, via = queue.popleft()
            if checked:
                time.sleep(args.delay)
            res, links = check_site(site, session, args.delay)
            res.update(depth=depth, found_via=via)
            checked += 1
            missing += res["has_privacy_policy"] == "no"
            print(f"[{checked}/{args.max_sites}] {res['has_privacy_policy'] or '?':>3}  {site}  "
                  f"{res['policy_url'] or res['note']}", file=sys.stderr)

            if not args.only_missing or res["has_privacy_policy"] == "no":
                writer.writerow(res)
                out.flush()

            if depth < args.max_depth:
                for link in links:
                    key = site_key(link)
                    if key not in seen:
                        seen.add(key)
                        queue.append((link, depth + 1, site))
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
    finally:
        if args.output:
            out.close()

    print(f"checked {checked} sites, {missing} without a privacy policy, {len(queue)} left in queue",
          file=sys.stderr)


if __name__ == "__main__":
    main()
