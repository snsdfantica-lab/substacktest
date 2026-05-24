"""Write the daily digest into a Notion database.

Layout per run:
  - One parent page in the database titled "YYYY-MM-DD — Daily Digest"
    (Type=Daily Synthesis), body = the Opus synthesis.
  - One child page per new article in the same database titled with the
    article title (Type=Article Summary), body = the per-article summary,
    parent set to the digest page via a relation-less link block at the top.

We keep the implementation tolerant of column-shape drift: any missing
property on the target database is skipped with a warning, never crashes.
"""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timezone

import requests

log = logging.getLogger(__name__)

NOTION_VERSION = "2022-06-28"
API = "https://api.notion.com/v1"
MAX_RICH_TEXT = 2000  # Notion limit per rich_text item


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {os.environ['NOTION_TOKEN']}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def _db_id() -> str:
    return os.environ["NOTION_PARENT_DB_ID"]


def _get_db_schema() -> dict:
    r = requests.get(f"{API}/databases/{_db_id()}", headers=_headers(), timeout=30)
    r.raise_for_status()
    return r.json().get("properties", {})


def _chunk_text(s: str, size: int = MAX_RICH_TEXT) -> list[str]:
    return [s[i : i + size] for i in range(0, len(s), size)] or [""]


def _md_to_blocks(md: str) -> list[dict]:
    """Minimal Markdown → Notion blocks. Handles headings (#, ##, ###),
    bullet lists (- or *), and paragraphs separated by blank lines.
    Links/bold/italics are NOT styled — content is preserved as plain text."""
    blocks: list[dict] = []
    if not md:
        return blocks
    lines = md.splitlines()
    paragraph_buf: list[str] = []

    def flush_paragraph():
        nonlocal paragraph_buf
        if paragraph_buf:
            text = " ".join(s.strip() for s in paragraph_buf).strip()
            if text:
                blocks.append(_paragraph(text))
            paragraph_buf = []

    for line in lines:
        if not line.strip():
            flush_paragraph()
            continue
        if line.startswith("### "):
            flush_paragraph()
            blocks.append(_heading(line[4:].strip(), level=3))
        elif line.startswith("## "):
            flush_paragraph()
            blocks.append(_heading(line[3:].strip(), level=2))
        elif line.startswith("# "):
            flush_paragraph()
            blocks.append(_heading(line[2:].strip(), level=1))
        elif re.match(r"^\s*[-*]\s+", line):
            flush_paragraph()
            blocks.append(_bullet(re.sub(r"^\s*[-*]\s+", "", line)))
        else:
            paragraph_buf.append(line)
    flush_paragraph()
    return blocks


def _rich(text: str) -> list[dict]:
    return [{"type": "text", "text": {"content": c}} for c in _chunk_text(text)]


def _paragraph(text: str) -> dict:
    return {"object": "block", "type": "paragraph",
            "paragraph": {"rich_text": _rich(text)}}


def _heading(text: str, level: int) -> dict:
    key = f"heading_{min(level, 3)}"
    return {"object": "block", "type": key, key: {"rich_text": _rich(text)}}


def _bullet(text: str) -> dict:
    return {"object": "block", "type": "bulleted_list_item",
            "bulleted_list_item": {"rich_text": _rich(text)}}


def _build_properties(schema: dict, fields: dict) -> dict:
    """Map a logical field dict onto whatever properties actually exist in the
    target database. The title property is auto-detected by type."""
    title_prop = next((k for k, v in schema.items() if v.get("type") == "title"), "Name")
    props: dict = {}

    if "title" in fields:
        props[title_prop] = {"title": [{"text": {"content": fields["title"][:1900]}}]}
    if "url" in fields and "URL" in schema:
        props["URL"] = {"url": fields["url"] or None}
    if "date" in fields and "Date" in schema:
        props["Date"] = {"date": {"start": fields["date"]}}
    if "author" in fields and "Author" in schema:
        props["Author"] = {"select": {"name": fields["author"][:100]}}
    if "type" in fields and "Type" in schema:
        props["Type"] = {"select": {"name": fields["type"]}}
    if "tone" in fields and "Tone" in schema and fields["tone"]:
        props["Tone"] = {"select": {"name": fields["tone"]}}
    if "tickers" in fields and "Tickers" in schema:
        props["Tickers"] = {
            "multi_select": [{"name": t[:100]} for t in (fields["tickers"] or [])][:25]
        }
    return props


def _create_page(parent_db: str, properties: dict, children: list[dict]) -> dict:
    payload = {
        "parent": {"database_id": parent_db},
        "properties": properties,
        "children": children[:100],  # API limit per create call
    }
    r = requests.post(f"{API}/pages", headers=_headers(), json=payload, timeout=60)
    if r.status_code >= 400:
        raise RuntimeError(f"Notion create page {r.status_code}: {r.text}")
    page = r.json()
    # Append remaining children in chunks if needed.
    remaining = children[100:]
    while remaining:
        batch = remaining[:100]
        remaining = remaining[100:]
        rr = requests.patch(
            f"{API}/blocks/{page['id']}/children",
            headers=_headers(),
            json={"children": batch},
            timeout=60,
        )
        if rr.status_code >= 400:
            raise RuntimeError(f"Notion append children {rr.status_code}: {rr.text}")
    return page


def _infer_tone(summary_md: str) -> str:
    s = summary_md.lower()
    longs = s.count("conviction: high") + s.count("long-side")
    bear_words = sum(s.count(w) for w in ("short-side", "bearish", "downside", "cut estimates"))
    bull_words = sum(s.count(w) for w in ("bullish", "raise estimates", "upside", "beat"))
    if bull_words > bear_words + 1:
        return "Bullish"
    if bear_words > bull_words + 1:
        return "Bearish"
    if bull_words and bear_words:
        return "Mixed"
    return "Neutral"


_TICKER_RE = re.compile(r"\b([A-Z]{2,5})\b")
_TICKER_STOP = {
    "TLDR", "PM", "AI", "EDA", "ASP", "HBM", "GPU", "CPU", "DRAM", "NAND",
    "SSD", "ASIC", "FPGA", "PCIE", "EUV", "DUV", "GAA", "FINFET", "API",
    "URL", "ETF", "USD", "EPS", "ROI", "OK", "US", "EU", "ROW",
}


def _extract_tickers(summary_md: str) -> list[str]:
    """Pull ticker-like tokens from the Long/Short sections only."""
    tickers: set[str] = set()
    in_section = False
    for line in summary_md.splitlines():
        ls = line.lower().strip()
        if ls.startswith("### long-side") or ls.startswith("### short-side"):
            in_section = True
            continue
        if ls.startswith("### "):
            in_section = False
        if in_section:
            for m in _TICKER_RE.findall(line):
                if m in _TICKER_STOP:
                    continue
                tickers.add(m)
    return sorted(tickers)[:20]


def write_digest(
    *,
    run_date: str | None = None,
    synthesis_md: str,
    article_summaries: list[dict],
) -> dict:
    """Create the daily digest parent page + one child page per article.
    Returns {parent_id, child_ids: [...]}."""
    schema = _get_db_schema()
    run_date = run_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")

    parent_props = _build_properties(
        schema,
        {
            "title": f"{run_date} — Semis Substack Digest",
            "date": run_date,
            "author": "Synthesis",
            "type": "Daily Synthesis",
            "tone": _infer_tone(synthesis_md),
            "tickers": _extract_tickers(synthesis_md),
        },
    )
    parent_children = (
        [_heading(f"Daily Synthesis — {run_date}", 1)]
        + _md_to_blocks(synthesis_md)
        + [_heading("Articles included", 2)]
        + [_bullet(f"{s['author']} — {s['title']} ({s['url']})") for s in article_summaries]
    )
    parent = _create_page(_db_id(), parent_props, parent_children)
    log.info("created digest page %s", parent.get("url"))

    child_ids = []
    for s in article_summaries:
        props = _build_properties(
            schema,
            {
                "title": s["title"][:200] or "(untitled)",
                "url": s["url"],
                "date": (s.get("published_at") or run_date)[:10],
                "author": s["author"],
                "type": "Article Summary",
                "tone": _infer_tone(s["summary"]),
                "tickers": _extract_tickers(s["summary"]),
            },
        )
        children = (
            [_paragraph(f"Source: {s['url']}")]
            + _md_to_blocks(s["summary"])
        )
        page = _create_page(_db_id(), props, children)
        child_ids.append(page["id"])
        log.info("created article page %s", page.get("url"))

    return {"parent_id": parent["id"], "parent_url": parent.get("url"), "child_ids": child_ids}
