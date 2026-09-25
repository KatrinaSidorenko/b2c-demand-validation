"""Pure client for the Wikimedia pageviews API.

Fetches raw pageview data for the given request and maps it to contract
models. It knows nothing about metrics and never retries: every call makes
exactly one HTTP request and returns `(data, error)`. The caller decides what
to do with the error.

Reference: https://doc.wikimedia.org/generated-data-platform/aqs/analytics-api/reference/page-views.html

TODO: in the future these calls are better exposed as MCP tools.
"""

from __future__ import annotations

from typing import Any, Callable

import requests

from wiki_contracts import (
    AggregatePageviewsRequest,
    AggregatePoint,
    ApiError,
    ArticlePageviewsRequest,
    CountryViews,
    ErrorType,
    PageviewPoint,
    Result,
    T,
    TopArticle,
    TopArticles,
    TopArticlesByCountry,
    TopArticlesByCountryRequest,
    TopArticlesRequest,
    TopCountries,
    TopCountriesRequest,
    TopCountryArticle,
    normalize_article_title,
    to_api_timestamp,
    to_date_parts,
)

ALL_DAYS = "all-days"


class WikiPageviewsClient:
    def __init__(
        self,
        api_version: str = "rest_v1",
        host: str = "https://wikimedia.org",
        app_name: str = "b2c-demand-validation",
        app_version: str = "0.1.0",
        contact_email: str = "some@gmail.com",
        timeout: float = 30.0,
        session: requests.Session | None = None,
    ) -> None:
        self.base_url = f"{host.rstrip('/')}/api/{api_version}/metrics/pageviews"
        self.timeout = timeout
        self.session = session or requests.Session()
        # Wikimedia requires a descriptive User-Agent with contact info.
        self.session.headers["User-Agent"] = f"{app_name}/{app_version} ({contact_email})"
        self.session.headers["Accept"] = "application/json"

    # -- public API ---------------------------------------------------------

    def get_article_pageviews(self, request: ArticlePageviewsRequest) -> Result[list[PageviewPoint]]:
        """GET /per-article/{project}/{access}/{agent}/{article}/{granularity}/{start}/{end}"""
        try:
            start = to_api_timestamp(request.start)
            end = to_api_timestamp(request.end)
        except ValueError as exc:
            return None, _invalid_argument(str(exc))

        article = normalize_article_title(request.article)
        path = (
            f"/per-article/{request.project}/{request.access}/{request.agent}"
            f"/{article}/{request.granularity}/{start}/{end}"
        )
        return self._fetch(path, _parse_article_pageviews)

    def get_aggregate_pageviews(self, request: AggregatePageviewsRequest) -> Result[list[AggregatePoint]]:
        """GET /aggregate/{project}/{access}/{agent}/{granularity}/{start}/{end}"""
        try:
            start = to_api_timestamp(request.start)
            end = to_api_timestamp(request.end)
        except ValueError as exc:
            return None, _invalid_argument(str(exc))

        path = (
            f"/aggregate/{request.project}/{request.access}/{request.agent}"
            f"/{request.granularity}/{start}/{end}"
        )
        return self._fetch(path, _parse_aggregate_pageviews)

    def get_top_articles(self, request: TopArticlesRequest) -> Result[TopArticles]:
        """GET /top/{project}/{access}/{year}/{month}/{day|all-days}"""
        try:
            year, month, day = to_date_parts(request.date)
        except ValueError as exc:
            return None, _invalid_argument(str(exc))

        day = ALL_DAYS if request.all_days else day
        path = f"/top/{request.project}/{request.access}/{year}/{month}/{day}"
        return self._fetch(path, _parse_top_articles)

    def get_top_countries(self, request: TopCountriesRequest) -> Result[TopCountries]:
        """GET /top-by-country/{project}/{access}/{year}/{month}"""
        try:
            year, month, _ = to_date_parts(request.date)
        except ValueError as exc:
            return None, _invalid_argument(str(exc))

        path = f"/top-by-country/{request.project}/{request.access}/{year}/{month}"
        return self._fetch(path, _parse_top_countries)

    def get_top_articles_by_country(self, request: TopArticlesByCountryRequest) -> Result[TopArticlesByCountry]:
        """GET /top-per-country/{country}/{access}/{year}/{month}/{day|all-days}"""
        try:
            year, month, day = to_date_parts(request.date)
        except ValueError as exc:
            return None, _invalid_argument(str(exc))

        day = ALL_DAYS if request.all_days else day
        path = f"/top-per-country/{request.country}/{request.access}/{year}/{month}/{day}"
        return self._fetch(path, _parse_top_articles_by_country)

    # -- transport ----------------------------------------------------------

    def _fetch(self, path: str, parse: Callable[[dict[str, Any]], T]) -> Result[T]:
        body, error = self._get(path)
        if error is not None:
            return None, error
        try:
            return parse(body), None
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            return None, ApiError(
                type=ErrorType.PARSE_ERROR,
                status_code=200,
                title="Unexpected response body",
                detail=f"{type(exc).__name__}: {exc}",
                url=self.base_url + path,
            )

    def _get(self, path: str) -> tuple[dict[str, Any] | None, ApiError | None]:
        """Single GET request, no retries."""
        url = self.base_url + path
        try:
            response = self.session.get(url, timeout=self.timeout)
        except requests.RequestException as exc:
            return None, ApiError(
                type=ErrorType.NETWORK_ERROR,
                status_code=None,
                title=type(exc).__name__,
                detail=str(exc),
                url=url,
            )

        if not response.ok:
            return None, _parse_error(response)

        try:
            return response.json(), None
        except ValueError as exc:
            return None, ApiError(
                type=ErrorType.PARSE_ERROR,
                status_code=response.status_code,
                title="Response is not valid JSON",
                detail=str(exc),
                url=url,
            )


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------


def _invalid_argument(detail: str) -> ApiError:
    return ApiError(type=ErrorType.INVALID_ARGUMENT, status_code=None, title="Invalid argument", detail=detail)


def _parse_error(response: requests.Response) -> ApiError:
    """Read a Wikimedia problem+json body, falling back to raw text."""
    try:
        body = response.json()
    except ValueError:
        body = None

    if isinstance(body, dict):
        return ApiError(
            type=ErrorType.HTTP_ERROR,
            status_code=response.status_code,
            title=str(body.get("title") or response.reason),
            detail=str(body.get("detail") or ""),
            url=response.url,
        )
    return ApiError(
        type=ErrorType.HTTP_ERROR,
        status_code=response.status_code,
        title=response.reason or "HTTP error",
        detail=response.text[:500],
        url=response.url,
    )


def _parse_article_pageviews(body: dict[str, Any]) -> list[PageviewPoint]:
    return [
        PageviewPoint(
            project=item["project"],
            article=item["article"],
            granularity=item["granularity"],
            timestamp=item["timestamp"],
            access=item["access"],
            agent=item["agent"],
            views=int(item["views"]),
        )
        for item in body["items"]
    ]


def _parse_aggregate_pageviews(body: dict[str, Any]) -> list[AggregatePoint]:
    return [
        AggregatePoint(
            project=item["project"],
            granularity=item["granularity"],
            timestamp=item["timestamp"],
            access=item["access"],
            agent=item["agent"],
            views=int(item["views"]),
        )
        for item in body["items"]
    ]


def _parse_top_articles(body: dict[str, Any]) -> TopArticles:
    item = body["items"][0]
    return TopArticles(
        project=item["project"],
        access=item["access"],
        year=item["year"],
        month=item["month"],
        day=item["day"],
        articles=[
            TopArticle(article=a["article"], views=int(a["views"]), rank=int(a["rank"]))
            for a in item["articles"]
        ],
    )


def _parse_top_countries(body: dict[str, Any]) -> TopCountries:
    item = body["items"][0]
    return TopCountries(
        project=item["project"],
        access=item["access"],
        year=item["year"],
        month=item["month"],
        countries=[
            CountryViews(country=c["country"], views=int(c["views_ceil"]), rank=int(c["rank"]))
            for c in item["countries"]
        ],
    )


def _parse_top_articles_by_country(body: dict[str, Any]) -> TopArticlesByCountry:
    item = body["items"][0]
    return TopArticlesByCountry(
        country=item["country"],
        access=item["access"],
        year=item["year"],
        month=item["month"],
        day=item["day"],
        articles=[
            TopCountryArticle(
                article=a["article"],
                project=a["project"],
                views=int(a["views_ceil"]),
                rank=int(a["rank"]),
            )
            for a in item["articles"]
        ],
    )
