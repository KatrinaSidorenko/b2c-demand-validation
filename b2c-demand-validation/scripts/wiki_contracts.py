"""Shared contracts for the Wikimedia pageviews data source.

Enums, request models, response models and error types used by `wiki_client`
and by higher layers (controller, metrics). This module has no I/O and no
third-party dependencies, so it can be imported without the HTTP client.

Reference: https://doc.wikimedia.org/generated-data-platform/aqs/analytics-api/reference/page-views.html
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import TypeVar
from urllib.parse import quote

T = TypeVar("T")


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class Access(StrEnum):
    ALL_ACCESS = "all-access"
    DESKTOP = "desktop"
    MOBILE_APP = "mobile-app"
    MOBILE_WEB = "mobile-web"


class Agent(StrEnum):
    ALL_AGENTS = "all-agents"
    USER = "user"
    SPIDER = "spider"
    AUTOMATED = "automated"


class Granularity(StrEnum):
    """Per-article supports only DAILY and MONTHLY; aggregate also supports HOURLY."""

    HOURLY = "hourly"
    DAILY = "daily"
    MONTHLY = "monthly"


class ErrorType(StrEnum):
    INVALID_ARGUMENT = "invalid_argument"  # bad input, request was not sent
    NETWORK_ERROR = "network_error"  # connection failure or timeout
    HTTP_ERROR = "http_error"  # non-2xx response, see status_code
    PARSE_ERROR = "parse_error"  # 2xx response with unexpected body


# ---------------------------------------------------------------------------
# Errors and result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ApiError:
    type: ErrorType
    status_code: int | None
    title: str
    detail: str
    url: str | None = None


Result = tuple[T | None, ApiError | None]


# ---------------------------------------------------------------------------
# Request models (dates are ISO strings: "2024-01-01" or "2024-01-01T13:00")
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ArticlePageviewsRequest:
    project: str
    article: str
    start: str
    end: str
    granularity: Granularity = Granularity.DAILY
    access: Access = Access.ALL_ACCESS
    agent: Agent = Agent.USER


@dataclass(frozen=True)
class AggregatePageviewsRequest:
    project: str
    start: str
    end: str
    granularity: Granularity = Granularity.DAILY
    access: Access = Access.ALL_ACCESS
    agent: Agent = Agent.USER


@dataclass(frozen=True)
class TopArticlesRequest:
    project: str
    date: str
    all_days: bool = False
    access: Access = Access.ALL_ACCESS


@dataclass(frozen=True)
class TopCountriesRequest:
    """Only year and month of `date` are used."""

    project: str
    date: str
    access: Access = Access.ALL_ACCESS


@dataclass(frozen=True)
class TopArticlesByCountryRequest:
    country: str  # ISO 3166-1 alpha-2, e.g. "US"
    date: str
    all_days: bool = False
    access: Access = Access.ALL_ACCESS


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PageviewPoint:
    project: str
    article: str
    granularity: str
    timestamp: str  # YYYYMMDDHH as returned by the API
    access: str
    agent: str
    views: int


@dataclass(frozen=True)
class AggregatePoint:
    project: str
    granularity: str
    timestamp: str
    access: str
    agent: str
    views: int


@dataclass(frozen=True)
class TopArticle:
    article: str
    views: int
    rank: int


@dataclass(frozen=True)
class TopArticles:
    project: str
    access: str
    year: str
    month: str
    day: str  # "all-days" for a monthly ranking
    articles: list[TopArticle]


@dataclass(frozen=True)
class CountryViews:
    """`views` is the API's `views_ceil`: a rounded-up bucket, not an exact count."""

    country: str
    views: int
    rank: int


@dataclass(frozen=True)
class TopCountries:
    project: str
    access: str
    year: str
    month: str
    countries: list[CountryViews]


@dataclass(frozen=True)
class TopCountryArticle:
    """`views` is the API's `views_ceil`: a rounded-up bucket, not an exact count."""

    article: str
    project: str
    views: int
    rank: int


@dataclass(frozen=True)
class TopArticlesByCountry:
    country: str
    access: str
    year: str
    month: str
    day: str
    articles: list[TopCountryArticle]


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def build_project(language: str, family: str = "wikipedia") -> str:
    """Build a project name, e.g. ("en", "wikipedia") -> "en.wikipedia"."""
    return f"{language}.{family}"


def normalize_article_title(title: str) -> str:
    """Turn a page title into the URL path segment the API expects."""
    return quote(title.strip().replace(" ", "_"), safe="")


def to_api_timestamp(iso_date: str) -> str:
    """Convert an ISO date/datetime to the API format YYYYMMDDHH.

    Raises ValueError on invalid input.
    """
    return datetime.fromisoformat(iso_date).strftime("%Y%m%d%H")


def to_date_parts(iso_date: str) -> tuple[str, str, str]:
    """Split an ISO date into zero-padded (year, month, day).

    Raises ValueError on invalid input.
    """
    parsed = datetime.fromisoformat(iso_date)
    return f"{parsed.year:04d}", f"{parsed.month:02d}", f"{parsed.day:02d}"
