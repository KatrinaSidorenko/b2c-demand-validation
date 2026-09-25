"""Data resolver between the pure wiki client and the metrics layer.

Turns business inputs (subjects, period, access) into wiki-client requests,
retries transient failures, sums articles per time bucket and reports
coverage. Errors are never raised: everything is returned in the
`SeriesBundle`.
"""

from __future__ import annotations

import time
from datetime import date, datetime, timedelta
from typing import Callable

from resolver_contracts import Coverage, Period, SeriesBundle, SeriesRequest, Subject, TimeSeries
from wiki_client import WikiPageviewsClient
from wiki_contracts import (
    AGGREGATE_GRANULARITIES,
    ARTICLE_GRANULARITIES,
    PAGEVIEWS_MIN_DATE,
    Access,
    AggregatePageviewsRequest,
    AggregatePoint,
    Agent,
    ApiError,
    ArticlePageviewsRequest,
    ErrorType,
    Granularity,
    PageviewPoint,
    Result,
    T,
)


class DataResolver:
    def __init__(
        self,
        client: WikiPageviewsClient,
        max_retries: int = 2,
        retry_backoff_s: float = 1.0,
    ) -> None:
        self.client = client
        self.max_retries = max(0, max_retries)
        self.retry_backoff_s = retry_backoff_s

    # -- public API ---------------------------------------------------------

    def get_subject_series(self, request: SeriesRequest) -> SeriesBundle:
        """Fetch every article of the subject and sum their views per bucket.

        Supports daily and monthly granularity (per-article endpoint limits).
        """
        dates, error = _validate(request.period, request.granularity, ARTICLE_GRANULARITIES)
        if error is not None:
            return _empty_bundle(request.subject, request.granularity, dates, [error])

        errors: list[ApiError] = []
        per_article: dict[str, TimeSeries] = {}
        returned: set[str] = set()

        # Articles are fetched one by one. They are independent, so they could
        # run in parallel (e.g. a ThreadPoolExecutor) if subjects grow large;
        # keep concurrency low to respect Wikimedia rate limits.
        for article in request.subject.articles:
            points, error = self._with_retries(
                lambda: self.client.get_article_pageviews(
                    ArticlePageviewsRequest(
                        project=request.project,
                        article=article,
                        start=request.period.start,
                        end=_api_end(request.period.end, request.granularity),
                        granularity=request.granularity,
                        access=request.access,
                        agent=request.agent,
                    )
                )
            )
            if error is not None:
                errors.append(error)
                continue
            by_date = _views_by_date(points, request.granularity)
            returned.update(by_date)
            per_article[article] = _fill(request.granularity, dates, by_date)

        totals = [sum(s.views[i] for s in per_article.values()) for i in range(len(dates))]
        return SeriesBundle(
            subject=request.subject,
            series=TimeSeries(granularity=request.granularity, dates=dates, views=totals),
            coverage=_coverage(dates, returned),
            errors=errors,
            per_article=per_article,
        )

    def get_project_series(
        self,
        project: str,
        period: Period,
        access: Access = Access.ALL_ACCESS,
        agent: Agent = Agent.USER,
        granularity: Granularity = Granularity.DAILY,
    ) -> SeriesBundle:
        """Fetch total project views, the denominator for relative interest.

        Supports hourly, daily and monthly granularity.
        """
        subject = Subject(label=project, articles=[])
        dates, error = _validate(period, granularity, AGGREGATE_GRANULARITIES)
        if error is not None:
            return _empty_bundle(subject, granularity, dates, [error])

        # A single request. It does not depend on the subject series, so a
        # caller can run it in parallel with `get_subject_series` calls.
        points, error = self._with_retries(
            lambda: self.client.get_aggregate_pageviews(
                AggregatePageviewsRequest(
                    project=project,
                    start=period.start,
                    end=_api_end(period.end, granularity),
                    granularity=granularity,
                    access=access,
                    agent=agent,
                )
            )
        )
        if error is not None:
            return _empty_bundle(subject, granularity, dates, [error])

        by_date = _views_by_date(points, granularity)
        return SeriesBundle(
            subject=subject,
            series=_fill(granularity, dates, by_date),
            coverage=_coverage(dates, set(by_date)),
        )

    # -- retries ------------------------------------------------------------

    def _with_retries(self, call: Callable[[], Result[T]]) -> Result[T]:
        """Run `call`, retrying transient errors up to `max_retries` times."""
        attempt = 0
        while True:
            data, error = call()
            if error is None or attempt >= self.max_retries or not _is_retryable(error):
                return data, error
            attempt += 1
            time.sleep(self.retry_backoff_s)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_retryable(error: ApiError) -> bool:
    if error.type == ErrorType.NETWORK_ERROR:
        return True
    if error.type == ErrorType.HTTP_ERROR and error.status_code is not None:
        return error.status_code == 429 or error.status_code >= 500
    return False


def _validate(
    period: Period,
    granularity: Granularity,
    supported: frozenset[Granularity],
) -> tuple[list[str], ApiError | None]:
    """Return the expected bucket keys of the period, or an INVALID_ARGUMENT error."""
    if granularity not in supported:
        allowed = ", ".join(sorted(supported))
        return [], _invalid_argument(f"Granularity {str(granularity)!r} is not supported here, use one of: {allowed}")
    try:
        start = date.fromisoformat(period.start)
        end = date.fromisoformat(period.end)
    except ValueError as exc:
        return [], _invalid_argument(str(exc))
    if start > end:
        return [], _invalid_argument(f"Period start {period.start} is after end {period.end}")
    if start < date.fromisoformat(PAGEVIEWS_MIN_DATE):
        return [], _invalid_argument(
            f"Period start {period.start} is before {PAGEVIEWS_MIN_DATE}, the earliest date with pageviews data"
        )
    return _expected_dates(start, end, granularity), None


def _expected_dates(start: date, end: date, granularity: Granularity) -> list[str]:
    """Every bucket key the period covers, inclusive of both ends."""
    if granularity == Granularity.MONTHLY:
        keys, year, month = [], start.year, start.month
        while (year, month) <= (end.year, end.month):
            keys.append(date(year, month, 1).isoformat())
            year, month = (year + 1, 1) if month == 12 else (year, month + 1)
        return keys

    days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    if granularity == Granularity.HOURLY:
        return [f"{d.isoformat()}T{h:02d}:00" for d in days for h in range(24)]
    return [d.isoformat() for d in days]


def _api_end(end: str, granularity: Granularity) -> str:
    """Hourly requests must reach the last hour of the end day."""
    return f"{end}T23:00" if granularity == Granularity.HOURLY else end


def _invalid_argument(detail: str) -> ApiError:
    return ApiError(type=ErrorType.INVALID_ARGUMENT, status_code=None, title="Invalid argument", detail=detail)


def _to_bucket_key(timestamp: str, granularity: Granularity) -> str:
    """Convert the API's YYYYMMDDHH timestamp to the series' ISO bucket key."""
    parsed = datetime.strptime(timestamp[:10], "%Y%m%d%H")
    if granularity == Granularity.HOURLY:
        return parsed.strftime("%Y-%m-%dT%H:00")
    if granularity == Granularity.MONTHLY:
        return parsed.date().replace(day=1).isoformat()
    return parsed.date().isoformat()


def _views_by_date(
    points: list[PageviewPoint] | list[AggregatePoint] | None,
    granularity: Granularity,
) -> dict[str, int]:
    by_date: dict[str, int] = {}
    for point in points or []:
        key = _to_bucket_key(point.timestamp, granularity)
        by_date[key] = by_date.get(key, 0) + point.views
    return by_date


def _fill(granularity: Granularity, dates: list[str], by_date: dict[str, int]) -> TimeSeries:
    """Align views to the expected buckets, filling missing ones with 0."""
    return TimeSeries(granularity=granularity, dates=list(dates), views=[by_date.get(d, 0) for d in dates])


def _coverage(dates: list[str], returned: set[str]) -> Coverage:
    missing = [d for d in dates if d not in returned]
    return Coverage(expected_points=len(dates), returned_points=len(dates) - len(missing), missing_dates=missing)


def _empty_bundle(
    subject: Subject,
    granularity: Granularity,
    dates: list[str],
    errors: list[ApiError],
) -> SeriesBundle:
    return SeriesBundle(
        subject=subject,
        series=_fill(granularity, dates, {}),
        coverage=_coverage(dates, set()),
        errors=errors,
    )
