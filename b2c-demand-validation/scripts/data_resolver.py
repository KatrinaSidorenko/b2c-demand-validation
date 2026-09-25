"""Data resolver between the pure wiki client and the metrics layer.

Turns business inputs (subjects, period, access) into wiki-client requests,
retries transient failures, sums articles by day and reports coverage. Errors
are never raised: everything is returned in the `SeriesBundle`.
"""

from __future__ import annotations

import time
from datetime import date, datetime, timedelta
from typing import Callable

from resolver_contracts import Coverage, DailySeries, Period, SeriesBundle, SeriesRequest, Subject
from wiki_client import WikiPageviewsClient
from wiki_contracts import (
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
        max_retries: int = 1,
        retry_backoff_s: float = 1.0,
    ) -> None:
        self.client = client
        self.max_retries = max(0, max_retries)
        self.retry_backoff_s = retry_backoff_s

    # -- public API ---------------------------------------------------------

    def get_subject_series(self, request: SeriesRequest) -> SeriesBundle:
        """Fetch every article of the subject and sum their views by day."""
        dates, error = _validate(request.period, request.granularity)
        if error is not None:
            return _empty_bundle(request.subject, dates, [error])

        errors: list[ApiError] = []
        per_article: dict[str, DailySeries] = {}
        returned: set[str] = set()

        for article in request.subject.articles:
            points, error = self._with_retries(
                lambda: self.client.get_article_pageviews(
                    ArticlePageviewsRequest(
                        project=request.project,
                        article=article,
                        start=request.period.start,
                        end=request.period.end,
                        granularity=request.granularity,
                        access=request.access,
                        agent=request.agent,
                    )
                )
            )
            if error is not None:
                errors.append(error)
                continue
            by_date = _views_by_date(points)
            returned.update(by_date)
            per_article[article] = _fill(dates, by_date)

        totals = [sum(s.views[i] for s in per_article.values()) for i in range(len(dates))]
        return SeriesBundle(
            subject=request.subject,
            series=DailySeries(dates=dates, views=totals),
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
    ) -> SeriesBundle:
        """Fetch total project views, the denominator for relative interest."""
        subject = Subject(label=project, articles=[])
        dates, error = _validate(period, Granularity.DAILY)
        if error is not None:
            return _empty_bundle(subject, dates, [error])

        points, error = self._with_retries(
            lambda: self.client.get_aggregate_pageviews(
                AggregatePageviewsRequest(
                    project=project,
                    start=period.start,
                    end=period.end,
                    access=access,
                    agent=agent,
                )
            )
        )
        if error is not None:
            return _empty_bundle(subject, dates, [error])

        by_date = _views_by_date(points)
        return SeriesBundle(
            subject=subject,
            series=_fill(dates, by_date),
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


def _validate(period: Period, granularity: Granularity) -> tuple[list[str], ApiError | None]:
    """Return the expected ISO dates of the period, or an INVALID_ARGUMENT error."""
    if granularity != Granularity.DAILY:
        return [], _invalid_argument(f"Only daily granularity is supported, got {granularity!r}")
    try:
        start = date.fromisoformat(period.start)
        end = date.fromisoformat(period.end)
    except ValueError as exc:
        return [], _invalid_argument(str(exc))
    if start > end:
        return [], _invalid_argument(f"Period start {period.start} is after end {period.end}")
    return [(start + timedelta(days=i)).isoformat() for i in range((end - start).days + 1)], None


def _invalid_argument(detail: str) -> ApiError:
    return ApiError(type=ErrorType.INVALID_ARGUMENT, status_code=None, title="Invalid argument", detail=detail)


def _to_iso_date(timestamp: str) -> str:
    """Convert the API's YYYYMMDDHH timestamp to an ISO date."""
    return datetime.strptime(timestamp[:8], "%Y%m%d").date().isoformat()


def _views_by_date(points: list[PageviewPoint] | list[AggregatePoint] | None) -> dict[str, int]:
    by_date: dict[str, int] = {}
    for point in points or []:
        day = _to_iso_date(point.timestamp)
        by_date[day] = by_date.get(day, 0) + point.views
    return by_date


def _fill(dates: list[str], by_date: dict[str, int]) -> DailySeries:
    """Align views to the expected dates, filling missing days with 0."""
    return DailySeries(dates=list(dates), views=[by_date.get(d, 0) for d in dates])


def _coverage(dates: list[str], returned: set[str]) -> Coverage:
    missing = [d for d in dates if d not in returned]
    return Coverage(expected_days=len(dates), returned_days=len(dates) - len(missing), missing_dates=missing)


def _empty_bundle(subject: Subject, dates: list[str], errors: list[ApiError]) -> SeriesBundle:
    return SeriesBundle(
        subject=subject,
        series=_fill(dates, {}),
        coverage=_coverage(dates, set()),
        errors=errors,
    )
