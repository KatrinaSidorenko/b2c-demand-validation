"""Sandbox scenarios: small, hand-editable calls into the skill scripts.

Each scenario is a function without arguments that returns whatever you want
to inspect. Register it with `@scenario` and it shows up in `run.py`.
Change the inputs freely: this file is a playground, not production code.
"""

from __future__ import annotations

from typing import Any, Callable

from data_resolver import DataResolver
from resolver_contracts import Period, SeriesRequest, Subject
from wiki_client import WikiPageviewsClient
from wiki_contracts import (
    Access,
    AggregatePageviewsRequest,
    ApiError,
    ArticlePageviewsRequest,
    ErrorType,
    Granularity,
    PageviewPoint,
    Result,
    TopArticlesByCountryRequest,
    TopArticlesRequest,
    TopCountriesRequest,
)

SCENARIOS: dict[str, Callable[[], Any]] = {}


def scenario(func: Callable[[], Any]) -> Callable[[], Any]:
    """Register `func` under its name with dashes, e.g. `wiki_top` -> `wiki-top`."""
    SCENARIOS[func.__name__.replace("_", "-")] = func
    return func


# Shared inputs: tweak these to try other projects and dates.
PROJECT = "en.wikipedia"
PERIOD = Period(start="2026-08-01", end="2026-08-07")

client = WikiPageviewsClient()


# ---------------------------------------------------------------------------
# wiki_client: one HTTP call per scenario, returns (data, error)
# ---------------------------------------------------------------------------


@scenario
def wiki_article() -> Result:
    """Daily views of one article."""
    return client.get_article_pageviews(
        ArticlePageviewsRequest(project=PROJECT, article="Air fryer", start=PERIOD.start, end=PERIOD.end)
    )


@scenario
def wiki_aggregate() -> Result:
    """Daily views of the whole project, mobile web only."""
    return client.get_aggregate_pageviews(
        AggregatePageviewsRequest(project=PROJECT, start=PERIOD.start, end=PERIOD.end, access=Access.MOBILE_WEB)
    )


@scenario
def wiki_top() -> Result:
    """Most viewed articles of a month."""
    return client.get_top_articles(TopArticlesRequest(project=PROJECT, date="2026-08-01", all_days=True))


@scenario
def wiki_top_countries() -> Result:
    """Countries that view the project most."""
    return client.get_top_countries(TopCountriesRequest(project=PROJECT, date="2026-08-01"))


@scenario
def wiki_top_by_country() -> Result:
    """Most viewed articles in one country on one day."""
    return client.get_top_articles_by_country(TopArticlesByCountryRequest(country="DE", date="2026-08-01"))


@scenario
def wiki_not_found() -> Result:
    """Error path: an article that does not exist returns an HTTP_ERROR."""
    return client.get_article_pageviews(
        ArticlePageviewsRequest(project=PROJECT, article="No such page 12345", start=PERIOD.start, end=PERIOD.end)
    )


# ---------------------------------------------------------------------------
# data_resolver: business inputs in, aligned series + coverage out
# ---------------------------------------------------------------------------

resolver = DataResolver(client)


@scenario
def resolver_subject() -> Any:
    """Sum synonyms of one subject into a single daily series."""
    return resolver.get_subject_series(
        SeriesRequest(
            project=PROJECT,
            subject=Subject(label="air fryer", articles=["Air fryer", "Deep fryer"]),
            period=PERIOD,
        )
    )


@scenario
def resolver_subject_monthly() -> Any:
    """Monthly series over a year."""
    return resolver.get_subject_series(
        SeriesRequest(
            project=PROJECT,
            subject=Subject(label="standing desk", articles=["Standing desk"]),
            period=Period(start="2025-08-01", end="2026-07-31"),
            granularity=Granularity.MONTHLY,
        )
    )


@scenario
def resolver_project() -> Any:
    """Total project views, the denominator for relative interest."""
    return resolver.get_project_series(PROJECT, PERIOD)


@scenario
def resolver_invalid() -> Any:
    """Validation: hourly is not supported per article, nothing is fetched."""
    return resolver.get_subject_series(
        SeriesRequest(
            project=PROJECT,
            subject=Subject(label="air fryer", articles=["Air fryer"]),
            period=PERIOD,
            granularity=Granularity.HOURLY,
        )
    )


# ---------------------------------------------------------------------------
# Offline: a fake client to test resolver logic without the network
# ---------------------------------------------------------------------------


class FlakyClient:
    """Fails with HTTP 503 `failures` times, then returns a single point on the start day."""

    def __init__(self, failures: int) -> None:
        self.failures = failures
        self.calls = 0

    def get_article_pageviews(self, request: ArticlePageviewsRequest) -> Result:
        self.calls += 1
        if self.calls <= self.failures:
            return None, ApiError(type=ErrorType.HTTP_ERROR, status_code=503, title="Unavailable", detail="fake")
        day = request.start.replace("-", "")
        point = PageviewPoint(PROJECT, request.article, "daily", f"{day}00", "all-access", "user", 42)
        return [point], None


@scenario
def offline_retries() -> Any:
    """Resolver retries a 503 twice, then fills gaps with 0 (no network)."""
    fake = FlakyClient(failures=2)
    bundle = DataResolver(fake, retry_backoff_s=0).get_subject_series(
        SeriesRequest(project=PROJECT, subject=Subject(label="fake", articles=["Fake"]), period=PERIOD)
    )
    return {"calls": fake.calls, "bundle": bundle}
