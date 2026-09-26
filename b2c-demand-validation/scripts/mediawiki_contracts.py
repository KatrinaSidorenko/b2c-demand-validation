"""Contracts for the MediaWiki Action API, used to look up article titles.

Response models of `mediawiki_client`. Errors and the `Result` type are shared
with the pageviews contracts in `wiki_contracts`. No I/O here.

Reference: https://www.mediawiki.org/wiki/API:Query
"""

from __future__ import annotations

from dataclasses import dataclass, field

ARTICLE_NAMESPACE = 0
# Anonymous clients may pass at most this many titles in one query.
MAX_TITLES_PER_QUERY = 50


@dataclass(frozen=True)
class PageInfo:
    """One page the API returned for a title, after normalization and redirects."""

    title: str
    namespace: int | None = None
    missing: bool = False
    invalid: bool = False
    invalid_reason: str | None = None
    disambiguation: bool = False
    description: str | None = None


@dataclass(frozen=True)
class TitleLookup:
    """How the API resolved one requested title.

    `normalized` is the title after case and space fixes, `redirect` the
    target it redirects to, and `page` the final page.
    """

    requested: str
    normalized: str | None
    redirect: str | None
    page: PageInfo


@dataclass(frozen=True)
class SearchHit:
    title: str
    description: str | None
    disambiguation: bool


@dataclass(frozen=True)
class SearchResult:
    query: str
    hits: list[SearchHit] = field(default_factory=list)
    # "Did you mean" text for a likely typo in the query, if any.
    suggestion: str | None = None
