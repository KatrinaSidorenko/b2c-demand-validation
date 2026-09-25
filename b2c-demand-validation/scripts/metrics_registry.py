"""Registry of every metric the runner and the catalog know about.

Each metric step adds its `MetricDefinition` here, keyed by its id.
"""

from __future__ import annotations

from metrics_contracts import MetricDefinition

REGISTRY: dict[str, MetricDefinition] = {}
