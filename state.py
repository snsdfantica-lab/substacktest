"""Convert a Substack post HTML into clean readable text + a paywall signal."""

from __future__ import annotations

import logging
import re

import trafilatura
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

PAYWALL_MARKERS = (
    "this post is for paid subscribers",
    "this post is for paying subscribers",
    "subscribe to keep reading",
    "upgrade to paid",
    "become a paid subscriber",
)


def extract(html: str) -> tuple[str, bool]:
    """Return (clean_text, is_truncated_by_paywall)."""
    if not html:
        return "", False

    text = trafilatura.extract(
        html,
        include_comments=False,
        include_tables=True,
        favor_recall=True,
    ) or ""

    if not text.strip():
        # trafilatura sometimes returns nothing on Substack's React shell;
        # fall back to a BeautifulSoup pass.
        soup = BeautifulSoup(html, "lxml")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        text = soup.get_text("\n", strip=True)

    paywalled = _looks_paywalled(text, html)

    # Tidy: collapse 3+ blank lines.
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text, paywalled


def _looks_paywalled(text: str, html: str) -> bool:
    lower_tail = (text[-2000:] if text else "").lower()
    for marker in PAYWALL_MARKERS:
        if marker in lower_tail:
            return True
    if "data-component-name=\"PaywallUI\"" in html or "Paywall_paywall" in html:
        return True
    return False


def word_count(text: str) -> int:
    return len(text.split())
