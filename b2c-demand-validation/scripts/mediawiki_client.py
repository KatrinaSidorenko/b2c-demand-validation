"""Pure client for the MediaWiki Action API, used to look up article titles.

Checks whether titles exist (following case fixes and redirects) and searches
a project for articles. Like `wiki_client`, it never retries: every call
makes exactly one HTTP request and returns `(data, error)`.

Reference: https://www.mediawiki.org/wiki/API:Query
"""

from __future__ import annotations

from typing import Any, Callable

import requests

from mediawiki_contracts import (
    ARTICLE_NAMESPACE,
    MAX_TITLES_PER_QUERY,
    PageInfo,
    SearchHit,
    SearchResult,
    TitleLookup,
)
from wiki_contracts import ApiError, ErrorType, Result, T

PAGE_PROPS = "disambiguation|wikibase-shortdesc"


class MediaWikiClient:
    def __init__(
        self,
        app_name: str = "b2c-demand-validation",
        app_version: str = "0.1.0",
        contact_email: str = "some@gmail.com",
        timeout: float = 30.0,
        session: requests.Session | None = None,
    ) -> None:
        self.timeout = timeout
        self.session = session or requests.Session()
        # Wikimedia requires a descriptive User-Agent with contact info.
        self.session.headers["User-Agent"] = f"{app_name}/{app_version} ({contact_email})"
        self.session.headers["Accept"] = "application/json"

    # -- public API ---------------------------------------------------------

    def lookup_titles(self, project: str, titles: list[str]) -> Result[list[TitleLookup]]:
        """GET action=query&titles=...&redirects: one lookup per requested title, in order."""
        if not titles or len(titles) > MAX_TITLES_PER_QUERY:
            return None, _invalid_argument(f"Pass 1 to {MAX_TITLES_PER_QUERY} titles; got {len(titles)}.")
        params = {
            "titles": "|".join(titles),
            "redirects": 1,
            "prop": "pageprops",
            "ppprop": PAGE_PROPS,
        }
        return self._fetch(project, params, lambda body: _parse_lookup(body, titles))

    def search(self, project: str, query: str, limit: int = 5) -> Result[SearchResult]:
        """GET action=query&generator=search: articles matching `query`, best first."""
        if not query.strip():
            return None, _invalid_argument("The search query is empty.")
        params = {
            "generator": "search",
            "gsrsearch": query,
            "gsrnamespace": ARTICLE_NAMESPACE,
            "gsrlimit": limit,
            "prop": "pageprops",
            "ppprop": PAGE_PROPS,
            # A one-hit list search in the same request carries the "did you mean" suggestion.
            "list": "search",
            "srsearch": query,
            "srnamespace": ARTICLE_NAMESPACE,
            "srlimit": 1,
            "srprop": "",
            "srinfo": "suggestion",
        }
        return self._fetch(project, params, lambda body: _parse_search(body, query))

    # -- transport ----------------------------------------------------------

    def _fetch(self, project: str, params: dict[str, Any], parse: Callable[[dict[str, Any]], T]) -> Result[T]:
        url = api_url(project)
        try:
            response = self.session.get(
                url,
                params={"action": "query", "format": "json", "formatversion": 2, **params},
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            return None, ApiError(
                type=ErrorType.NETWORK_ERROR, status_code=None, title=type(exc).__name__, detail=str(exc), url=url
            )

        if not response.ok:
            return None, ApiError(
                type=ErrorType.HTTP_ERROR,
                status_code=response.status_code,
                title=response.reason or "HTTP error",
                detail=response.text[:500],
                url=response.url,
            )
        try:
            body = response.json()
        except ValueError as exc:
            return None, _parse_failure(response, "Response is not valid JSON", exc)

        # The Action API reports errors in a 200 body.
        if isinstance(body, dict) and "error" in body:
            error = body["error"]
            return None, ApiError(
                type=ErrorType.HTTP_ERROR,
                status_code=response.status_code,
                title=str(error.get("code") or "api_error"),
                detail=str(error.get("info") or ""),
                url=response.url,
            )
        try:
            return parse(body), None
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            return None, _parse_failure(response, "Unexpected response body", exc)


def api_url(project: str) -> str:
    """`en.wikipedia` -> https://en.wikipedia.org/w/api.php"""
    return f"https://{project.strip().lower()}.org/w/api.php"


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------


def _invalid_argument(detail: str) -> ApiError:
    return ApiError(type=ErrorType.INVALID_ARGUMENT, status_code=None, title="Invalid argument", detail=detail)


def _parse_failure(response: requests.Response, title: str, exc: Exception) -> ApiError:
    return ApiError(
        type=ErrorType.PARSE_ERROR,
        status_code=response.status_code,
        title=title,
        detail=f"{type(exc).__name__}: {exc}",
        url=response.url,
    )


def _page_info(page: dict[str, Any]) -> PageInfo:
    props = page.get("pageprops") or {}
    return PageInfo(
        title=page["title"],
        namespace=page.get("ns"),
        missing=bool(page.get("missing")),
        invalid=bool(page.get("invalid")),
        invalid_reason=page.get("invalidreason"),
        disambiguation="disambiguation" in props,
        description=props.get("wikibase-shortdesc"),
    )


def _parse_lookup(body: dict[str, Any], titles: list[str]) -> list[TitleLookup]:
    query = body.get("query") or {}
    normalized = {n["from"]: n["to"] for n in query.get("normalized", [])}
    redirects = {r["from"]: r["to"] for r in query.get("redirects", [])}
    pages = {p["title"]: _page_info(p) for p in query.get("pages", [])}

    lookups = []
    for requested in titles:
        current = normalized.get(requested, requested)
        target = redirects.get(current)
        final = target or current
        page = pages.get(final) or PageInfo(title=final, missing=True)
        lookups.append(
            TitleLookup(
                requested=requested,
                normalized=current if current != requested else None,
                redirect=target,
                page=page,
            )
        )
    return lookups


def _parse_search(body: dict[str, Any], query: str) -> SearchResult:
    data = body.get("query") or {}
    pages = sorted(data.get("pages", []), key=lambda p: p.get("index", 0))
    # A namespace prefix in the query ("Category:...") overrides `gsrnamespace`, so filter again.
    hits = [
        SearchHit(title=info.title, description=info.description, disambiguation=info.disambiguation)
        for info in map(_page_info, pages)
        if info.namespace == ARTICLE_NAMESPACE
    ]
    suggestion = (data.get("searchinfo") or {}).get("suggestion")
    return SearchResult(query=query, hits=hits, suggestion=suggestion)
