from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import requests
from bs4 import BeautifulSoup

USER_AGENT = "CareerSignalBot/0.1 (+https://careersignal.local)"


@dataclass
class FetchResult:
    status: str  # ok/failed/blocked
    http_status: Optional[int]
    error: Optional[str]
    html: Optional[str]


def fetch_html(url: str, timeout_s: int = 20) -> FetchResult:
    headers = {"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"}
    try:
        r = requests.get(url, headers=headers, timeout=timeout_s, allow_redirects=True)
        code = r.status_code
        if code in (403, 429):
            return FetchResult(status="blocked", http_status=code, error=f"HTTP {code}", html=None)
        if code >= 400:
            return FetchResult(status="failed", http_status=code, error=f"HTTP {code}", html=None)
        # Limit payload size (politeness)
        text = r.text
        if len(text) > 2_500_000:
            text = text[:2_500_000]
        return FetchResult(status="ok", http_status=code, error=None, html=text)
    except requests.RequestException as e:
        return FetchResult(status="failed", http_status=None, error=str(e), html=None)


def normalize_text(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    # remove scripts/styles
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = soup.get_text(" ", strip=True)
    # collapse whitespace
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()


def detect_ats(url: str, html: str) -> Optional[str]:
    u = (url or "").lower()
    h = (html or "").lower()
    if "greenhouse.io" in u or "boards.greenhouse.io" in u or "greenhouse" in h and "boards" in h:
        return "greenhouse"
    if "lever.co" in u or "jobs.lever.co" in u or "lever" in h and "jobs.lever" in h:
        return "lever"
    return None


def extract_links(url: str, html: str) -> Tuple[List[Dict[str, str]], Optional[str]]:
    """Best-effort job link extraction.

    Returns (links, ats)
      links: list of {url, title}
    """
    soup = BeautifulSoup(html, "lxml")
    ats = detect_ats(url, html)

    out: List[Dict[str, str]] = []

    def add(href: str, title: str) -> None:
        href = (href or "").strip()
        if not href:
            return
        # keep absolute only (MVP)
        if href.startswith("/"):
            # best-effort: build absolute from input url
            try:
                from urllib.parse import urljoin

                href_abs = urljoin(url, href)
            except Exception:
                href_abs = href
        else:
            href_abs = href
        if not href_abs.lower().startswith(("http://", "https://")):
            return
        t = (title or "").strip()[:160]
        out.append({"url": href_abs, "title": t})

    # ATS-specific
    if ats == "greenhouse":
        for a in soup.select("a[href]"):
            href = a.get("href", "")
            if "greenhouse.io" in href or "boards.greenhouse.io" in href:
                add(href, a.get_text(" ", strip=True))
    elif ats == "lever":
        for a in soup.select("a[href]"):
            href = a.get("href", "")
            if "jobs.lever.co" in href or "lever.co" in href:
                add(href, a.get_text(" ", strip=True))

    # Fallback heuristic (also runs for ATS pages to capture onsite links)
    jobish = re.compile(r"/(jobs|job|career|careers|position|positions|vacanc|stellen|karriere|bewerb)", re.I)
    for a in soup.select("a[href]"):
        href = a.get("href", "")
        if jobish.search(href):
            add(href, a.get_text(" ", strip=True))

    # De-dupe preserving order
    seen = set()
    dedup: List[Dict[str, str]] = []
    for item in out:
        u = item["url"].strip()
        if u in seen:
            continue
        seen.add(u)
        dedup.append(item)

    # cap samples
    return dedup[:60], ats


def keyword_hits(text: str, keywords: List[str]) -> Dict[str, int]:
    hits: Dict[str, int] = {}
    if not text:
        return hits
    low = text.lower()
    for kw in keywords:
        k = (kw or "").strip()
        if not k:
            continue
        c = low.count(k.lower())
        if c > 0:
            hits[k] = c
    return hits
