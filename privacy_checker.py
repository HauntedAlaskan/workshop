"""Check a list of websites and report which ones don't link to a privacy policy.

Usage:
    python privacy_checker.py example.com another-site.org
    python privacy_checker.py -f sites.txt -o results.csv

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


def normalize(site):
    site = site.strip()
    if not urlparse(site).scheme:
        site = "https://" + site
    return site.rstrip("/")


def robots_for(base, session):
    rp = urllib.robotparser.RobotFileParser()
    try:
        resp = session.get(base + "/robots.txt", timeout=10)
        rp.parse(resp.text.splitlines() if resp.ok else [])
    except requests.RequestException:
        rp.parse([])
    return rp


def find_policy_link(html, page_url):
    soup = BeautifulSoup(html, "html.parser")
    for a in soup.find_all("a", href=True):
        text = a.get_text(" ", strip=True)
        href = a["href"]
        if PRIVACY_PATTERN.search(text) or PRIVACY_PATTERN.search(href):
            return urljoin(page_url, href)
    return None


def check_site(site, session, delay):
    base = normalize(site)
    result = {"site": site, "has_privacy_policy": "", "policy_url": "", "note": ""}
    rp = robots_for(base, session)

    if not rp.can_fetch(USER_AGENT, base + "/"):
        result["note"] = "homepage disallowed by robots.txt"
        return result

    try:
        resp = session.get(base + "/", timeout=15)
        resp.raise_for_status()
    except requests.RequestException as e:
        result["note"] = f"fetch failed: {e.__class__.__name__}"
        return result

    link = find_policy_link(resp.text, resp.url)
    if link:
        result.update(has_privacy_policy="yes", policy_url=link, note="linked from homepage")
        return result

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
            return result

    result.update(has_privacy_policy="no", note="no privacy policy link or page found")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("sites", nargs="*", help="domains or URLs to check")
    parser.add_argument("-f", "--file", help="text file with one site per line")
    parser.add_argument("-o", "--output", help="write results to this CSV file")
    parser.add_argument("--delay", type=float, default=1.0, help="seconds between requests (default 1)")
    parser.add_argument("--only-missing", action="store_true", help="only report sites without a policy")
    args = parser.parse_args()

    sites = list(args.sites)
    if args.file:
        with open(args.file) as fh:
            sites += [line.strip() for line in fh if line.strip() and not line.startswith("#")]
    if not sites:
        parser.error("give at least one site or --file")

    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT

    results = []
    for i, site in enumerate(sites):
        if i:
            time.sleep(args.delay)
        res = check_site(site, session, args.delay)
        results.append(res)
        print(f"{res['has_privacy_policy'] or '?':>3}  {site}  {res['policy_url'] or res['note']}", file=sys.stderr)

    if args.only_missing:
        results = [r for r in results if r["has_privacy_policy"] == "no"]

    out = open(args.output, "w", newline="") if args.output else sys.stdout
    writer = csv.DictWriter(out, fieldnames=["site", "has_privacy_policy", "policy_url", "note"])
    writer.writeheader()
    writer.writerows(results)
    if args.output:
        out.close()


if __name__ == "__main__":
    main()
