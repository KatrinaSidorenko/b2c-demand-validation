"""Articles CLI: find and check exact article titles before writing a spec.

    python articles.py search  --id <analysis id> --project en.wikipedia --query "meal kit" "hellofresh" [--limit 5]
    python articles.py resolve --id <analysis id> --project en.wikipedia --titles "meal kit" "Hello Fresh"

`search` lists the articles that match each query. `resolve` checks each
title: it follows case fixes and redirects, and rejects missing titles and
disambiguation pages, suggesting candidates for them. Every returned `title`
is ready to paste into the spec.

Each successful call uses one lookup round of the analysis, counted in its
`meta.json`. After MAX_LOOKUP_ROUNDS the calls are refused, so the model stops
guessing and reports the unresolved titles as not found.

Exit codes: 0 on success, 1 when the lookup failed (the round is not counted),
2 on invalid input or when no lookup rounds are left (nothing is fetched).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable

from data_resolver import is_retryable
from mediawiki_client import MediaWikiClient
from mediawiki_contracts import ARTICLE_NAMESPACE, SearchResult, TitleLookup
from metrics_contracts import SpecError
from wiki_contracts import ApiError, Result, T
from workspace import DEFAULT_ROOT, META_FILE, analysis_folder, read_json, write_json

EXIT_OK = 0
EXIT_LOOKUP_FAILED = 1
EXIT_INVALID_INPUT = 2

MAX_LOOKUP_ROUNDS = 3
MAX_QUERIES = 10
MAX_TITLES = 20
MAX_LIMIT = 10
SUGGESTIONS = 3
MAX_RETRIES = 2
RETRY_BACKOFF_S = 1.0
PROJECT_PATTERN = re.compile(r"^[a-z0-9-]+\.[a-z]+$")


class Status:
    OK = "ok"  # exists as typed
    NORMALIZED = "normalized"  # exists after a case or space fix
    REDIRECT = "redirect"  # redirects to another article: use that one
    DISAMBIGUATION = "disambiguation"  # lists several meanings: pick one of the suggestions
    MISSING = "missing"  # no such article
    INVALID = "invalid"  # not a valid article title


USABLE = {Status.OK, Status.NORMALIZED, Status.REDIRECT}


# ---------------------------------------------------------------------------
# Lookups
# ---------------------------------------------------------------------------


def to_spec_title(title: str) -> str:
    """Titles in the spec use underscores instead of spaces."""
    return title.strip().replace(" ", "_")


def classify(lookup: TitleLookup) -> tuple[str, str | None]:
    """The status of a looked-up title and the reason when it is not usable."""
    page = lookup.page
    if page.invalid:
        return Status.INVALID, page.invalid_reason or "Not a valid title."
    if page.namespace not in (None, ARTICLE_NAMESPACE):
        return Status.INVALID, "Not an article: the title points to another namespace (e.g. Category:, Talk:)."
    if page.missing:
        return Status.MISSING, "No article with this title."
    if page.disambiguation:
        return Status.DISAMBIGUATION, "A disambiguation page listing several meanings; pick one article."
    if lookup.redirect is not None:
        return Status.REDIRECT, None
    if to_spec_title(page.title) != to_spec_title(lookup.requested):
        return Status.NORMALIZED, None
    return Status.OK, None


def candidates(result: SearchResult, limit: int) -> list[dict[str, Any]]:
    """Search hits usable in a spec: disambiguation pages are left out."""
    return [
        {"title": to_spec_title(hit.title), "description": hit.description}
        for hit in result.hits
        if not hit.disambiguation
    ][:limit]


def with_retries(call: Callable[[], Result[T]]) -> Result[T]:
    """Run `call`, retrying transient errors like the data resolver does."""
    attempt = 0
    while True:
        data, error = call()
        if error is None or attempt >= MAX_RETRIES or not is_retryable(error):
            return data, error
        attempt += 1
        time.sleep(RETRY_BACKOFF_S)


def run_search(
    client: MediaWikiClient, project: str, queries: list[str], limit: int
) -> tuple[list[dict[str, Any]], ApiError | None]:
    results = []
    for query in queries:
        result, error = with_retries(lambda: client.search(project, query, limit))
        if error is not None:
            return [], error
        assert result is not None
        results.append({"query": query, "suggestion": result.suggestion, "candidates": candidates(result, limit)})
    return results, None


def run_resolve(
    client: MediaWikiClient, project: str, titles: list[str]
) -> tuple[list[dict[str, Any]], ApiError | None]:
    lookups, error = with_retries(lambda: client.lookup_titles(project, titles))
    if error is not None:
        return [], error
    assert lookups is not None

    results = []
    for lookup in lookups:
        status, reason = classify(lookup)
        entry: dict[str, Any] = {"input": lookup.requested, "status": status}
        if status in USABLE:
            entry["title"] = to_spec_title(lookup.page.title)
            entry["description"] = lookup.page.description
        else:
            entry["title"] = None
            entry["reason"] = reason
            # Suggestions come in the same round, so the model needs no extra search.
            found, error = with_retries(lambda: client.search(project, lookup.requested, SUGGESTIONS + 2))
            if error is not None:
                return [], error
            assert found is not None
            entry["suggestions"] = candidates(found, SUGGESTIONS)
        results.append(entry)
    return results, None


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def parse_items(raw: list[str], field: str, limit: int) -> tuple[list[str], list[SpecError]]:
    items = list(dict.fromkeys(" ".join(i.split()) for i in raw if i.strip()))
    errors = []
    if not items:
        errors.append(SpecError(field, "missing", f"Give at least one non-empty value for --{field}."))
    elif len(items) > limit:
        errors.append(
            SpecError(field, "too_many", f"{len(items)} values; at most {limit} per call. Keep the most important ones.")
        )
    return items, errors


def parse_project(value: str) -> tuple[str, list[SpecError]]:
    project = value.strip().lower()
    if PROJECT_PATTERN.match(project):
        return project, []
    return project, [SpecError("project", "invalid_project", f'Use a project like "en.wikipedia"; got {value!r}.')]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _print_json(payload: Any) -> None:
    print(json.dumps(payload, indent=2, ensure_ascii=False))


def _invalid(errors: list[SpecError]) -> int:
    _print_json({"status": "invalid_input", "errors": [asdict(e) for e in errors]})
    return EXIT_INVALID_INPUT


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--id", required=True, help="the analysis id from workspace.py")
    common.add_argument("--project", required=True, help='e.g. "en.wikipedia"')
    common.add_argument("--root", default=DEFAULT_ROOT, help="folder that holds all analyses")
    commands = parser.add_subparsers(dest="command", required=True)
    search = commands.add_parser("search", parents=[common], help="find articles that match each query")
    search.add_argument("--query", nargs="+", required=True, help=f"1 to {MAX_QUERIES} search queries")
    search.add_argument("--limit", type=int, default=5, help=f"candidates per query, 1 to {MAX_LIMIT}")
    resolve = commands.add_parser("resolve", parents=[common], help="check titles and get the exact ones")
    resolve.add_argument("--titles", nargs="+", required=True, help=f"1 to {MAX_TITLES} article titles")
    args = parser.parse_args(argv)

    project, errors = parse_project(args.project)
    if args.command == "search":
        items, item_errors = parse_items(args.query, "query", MAX_QUERIES)
        if not 1 <= args.limit <= MAX_LIMIT:
            item_errors.append(SpecError("limit", "out_of_range", f"Use 1 to {MAX_LIMIT}; got {args.limit}."))
    else:
        items, item_errors = parse_items(args.titles, "titles", MAX_TITLES)
    errors += item_errors
    root = Path(args.root)
    folder = analysis_folder(root, args.id)
    if folder is None:
        errors.append(SpecError("id", "unknown_id", f"No analysis {args.id!r} in {root}."))
    if errors:
        return _invalid(errors)
    assert folder is not None

    meta = read_json(folder / META_FILE)
    rounds = int(meta.get("lookup_rounds", 0))
    if rounds >= MAX_LOOKUP_ROUNDS:
        _print_json({
            "status": "attempts_exhausted",
            "rounds_used": rounds,
            "detail": (
                f"All {MAX_LOOKUP_ROUNDS} lookup rounds of this analysis are used. Stop looking up titles: "
                "keep the titles that resolved, drop the others, run the analysis, and report the dropped "
                "ones as not found on Wikipedia."
            ),
        })
        return EXIT_INVALID_INPUT

    client = MediaWikiClient()
    if args.command == "search":
        results, error = run_search(client, project, items, args.limit)
    else:
        results, error = run_resolve(client, project, items)
    if error is not None:
        _print_json({"status": "lookup_failed", "error": asdict(error), "rounds_used": rounds})
        return EXIT_LOOKUP_FAILED

    rounds += 1
    write_json(folder / META_FILE, {**meta, "lookup_rounds": rounds})
    payload: dict[str, Any] = {
        "status": "ok",
        "rounds_used": rounds,
        "rounds_left": MAX_LOOKUP_ROUNDS - rounds,
        "results": results,
    }
    if rounds >= MAX_LOOKUP_ROUNDS:
        payload["note"] = "This was the last lookup round: use the titles you have and drop the rest."
    _print_json(payload)
    return EXIT_OK


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
