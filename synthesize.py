"""Daily Semis Substack Digest — orchestrator.

Run manually:
    python -m src.main --since-days 1
or via the GitHub Actions "Run workflow" button.

Required env vars:
    ANTHROPIC_API_KEY
    NOTION_TOKEN
    NOTION_PARENT_DB_ID
    SUBSTACK_COOKIE   (optional; required to read paywalled bodies)
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import yaml

from src import extract, fetch, notion_sink, state, summarize, synthesize

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("main")

ROOT = Path(__file__).resolve().parents[1]
AUTHORS_FILE = ROOT / "authors.yml"


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--since-days", type=int, default=1,
                   help="Look back this many days for new posts (default 1).")
    p.add_argument("--authors", type=str, default="",
                   help="Comma-separated author names to run only (blank = all).")
    p.add_argument("--dry-run", action="store_true",
                   help="Do everything except writing to Notion or updating seen.json.")
    p.add_argument("--skip-synthesis", action="store_true",
                   help="Skip the Opus cross-article synthesis step.")
    return p.parse_args(argv)


def load_authors() -> list[dict]:
    return yaml.safe_load(AUTHORS_FILE.read_text())


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    authors = load_authors()
    authors_filter = {a.strip() for a in args.authors.split(",") if a.strip()} or None

    session = fetch.make_session()
    cookie_ok, msg = fetch.canary_check_cookie(session)
    log.info("cookie canary: %s", msg)
    if not cookie_ok:
        log.error("cookie check failed — paywalled posts will only show previews")

    seen = state.load_seen()
    log.info("loaded %d previously-seen post URLs", len(seen))

    new_posts = fetch.discover_new_posts(
        authors, session, seen, since_days=args.since_days, authors_filter=authors_filter,
    )
    log.info("discovered %d new posts", len(new_posts))
    if not new_posts:
        log.info("nothing to do.")
        return 0

    # Build author-name -> tickers_focus lookup
    tickers_by_author = {a["name"]: a.get("tickers_focus") or [] for a in authors}

    article_summaries: list[dict] = []
    for post in new_posts:
        log.info("fetching %s", post.url)
        fetch.fetch_post_html(post, session)
        text, paywalled = extract.extract(post.html)
        wc = extract.word_count(text)
        if wc < 80 and not paywalled:
            log.warning("extracted only %d words from %s — skipping", wc, post.url)
            continue
        if wc < 80 and paywalled:
            log.info("paywalled preview only (%d words) for %s — summarizing what we have", wc, post.url)

        prior = state.read_recent_summaries(post.author_name, limit=5)
        try:
            summary = summarize.summarize_article(
                author=post.author_name,
                title=post.title,
                publication_url=post.publication_url,
                post_url=post.url,
                published_at=post.published_at,
                article_text=text,
                prior_summaries=prior,
                tickers_focus=tickers_by_author.get(post.author_name, []),
                is_paywalled_truncated=bool(paywalled),
            )
        except Exception as e:
            log.exception("summarization failed for %s: %s", post.url, e)
            continue

        article_summaries.append({
            "author": post.author_name,
            "title": post.title,
            "url": post.url,
            "published_at": post.published_at,
            "summary": summary,
        })

        if not args.dry_run:
            state.write_cache(post.author_name, post.url, {
                "author": post.author_name,
                "title": post.title,
                "url": post.url,
                "published_at": post.published_at,
                "paywalled_truncated": bool(paywalled),
                "word_count": wc,
                "summary": summary,
                "article_text_preview": text[:2000],
            })

    if not article_summaries:
        log.warning("all candidate posts failed to summarize.")
        return 0

    synthesis_md = "_(synthesis skipped)_"
    if not args.skip_synthesis:
        log.info("synthesizing %d articles with Opus", len(article_summaries))
        try:
            synthesis_md = synthesize.synthesize(article_summaries)
        except Exception as e:
            log.exception("synthesis failed: %s", e)
            synthesis_md = f"_(synthesis failed: {e})_"

    if args.dry_run:
        log.info("dry-run: skipping Notion write and seen.json update")
        print("=" * 60)
        print("SYNTHESIS")
        print("=" * 60)
        print(synthesis_md)
        for s in article_summaries:
            print("=" * 60)
            print(f"{s['author']} — {s['title']}")
            print("=" * 60)
            print(s["summary"])
        return 0

    try:
        result = notion_sink.write_digest(
            synthesis_md=synthesis_md,
            article_summaries=article_summaries,
        )
        log.info("notion digest written: %s", result.get("parent_url"))
    except Exception as e:
        log.exception("notion write failed: %s", e)
        log.warning("not updating seen.json — articles will be retried next run")
        return 1

    for s in article_summaries:
        state.mark_seen(seen, s["url"], s["author"], s["title"])
    state.save_seen(seen)
    log.info("saved %d entries to seen.json", len(seen))
    return 0


if __name__ == "__main__":
    sys.exit(main())
