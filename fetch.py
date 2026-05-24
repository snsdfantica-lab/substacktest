"""Cross-article daily synthesis using Claude Opus 4.7."""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path

from anthropic import Anthropic, APIStatusError, RateLimitError

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]
PROMPT_PATH = ROOT / "prompts" / "synthesis.md"

MODEL = "claude-opus-4-7"
MAX_TOKENS = 4000


def _client() -> Anthropic:
    return Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])


def synthesize(article_summaries: list[dict]) -> str:
    if not article_summaries:
        return "_No new articles today._"
    client = _client()
    blocks = []
    for s in article_summaries:
        blocks.append(
            f"### {s['author']} — {s['title']}\n"
            f"URL: {s['url']}\n"
            f"Published: {s.get('published_at', '')}\n\n"
            f"{s['summary']}\n"
        )
    user_msg = (
        "Today's per-article notes are below. Produce the synthesis.\n\n"
        + "\n---\n".join(blocks)
    )
    return _call_with_retry(client, PROMPT_PATH.read_text(), user_msg)


def _call_with_retry(client: Anthropic, system: str, user: str, attempts: int = 4) -> str:
    delay = 4
    last_err: Exception | None = None
    for i in range(attempts):
        try:
            resp = client.messages.create(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                system=system,
                messages=[{"role": "user", "content": user}],
            )
            chunks = [b.text for b in resp.content if getattr(b, "type", None) == "text"]
            return "\n".join(chunks).strip()
        except (RateLimitError, APIStatusError) as e:
            last_err = e
            log.warning("Opus API error (attempt %d/%d): %s", i + 1, attempts, e)
            time.sleep(delay)
            delay *= 2
    raise RuntimeError(f"Opus API failed after {attempts} attempts: {last_err}")
