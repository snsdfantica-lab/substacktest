"""One-shot backfill: pull the last N posts from each author, summarize them
cheaply (no Opus, no Notion writes), and seed state/cache/ so subsequent
daily runs have 'change-in-tone' context to compare against.

Usage:
    python -m src.backfill --per-author 15
"""

from __future__ import annotations

import argparse
import logging
import sys

from src import extract, fetch, state, summarize
from src.main import load_authors

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("backfill")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--per-author", type=int, default=15)
    p.add_argument("--authors", type=str, default="")
    args = p.parse_args(argv or sys.argv[1:])

    filt = {a.strip() for a in args.authors.split(",") if a.strip()} or None
    authors = load_authors()
    session = fetch.make_session()

    for a in authors:
        if filt and a["name"] not in filt:
            continue
        log.info("backfilling %s", a["name"])
        try:
            posts = fetch.list_posts_for_author(
                a["name"], a["url"], session, archive_limit=args.per_author,
            )
        except Exception as e:
            log.exception("listing failed for %s: %s", a["name"], e)
            continue
        posts = posts[: args.per_author]
        for post in posts:
            cache_p = state.cache_path(post.author_name, post.url)
            if cache_p.exists():
                continue
            fetch.fetch_post_html(post, session)
            text, paywalled = extract.extract(post.html)
            if extract.word_count(text) < 80:
                log.info("skipping %s (insufficient content)", post.url)
                continue
            try:
                summary = summarize.summarize_article(
                    author=post.author_name,
                    title=post.title,
                    publication_url=post.publication_url,
                    post_url=post.url,
                    published_at=post.published_at,
                    article_text=text,
                    prior_summaries=state.read_recent_summaries(post.author_name, limit=3),
                    tickers_focus=a.get("tickers_focus") or [],
                    is_paywalled_truncated=bool(paywalled),
                )
            except Exception as e:
                log.exception("summary failed for %s: %s", post.url, e)
                continue
            state.write_cache(post.author_name, post.url, {
                "author": post.author_name,
                "title": post.title,
                "url": post.url,
                "published_at": post.published_at,
                "paywalled_truncated": bool(paywalled),
                "word_count": extract.word_count(text),
                "summary": summary,
                "article_text_preview": text[:2000],
            })
            log.info("cached %s", post.url)
    return 0


if __name__ == "__main__":
    sys.exit(main())
