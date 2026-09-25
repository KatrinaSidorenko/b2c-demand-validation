---
name: b2c-demand-validation
description: Validate consumer demand for a product area with Wikipedia pageviews. Use when a business team asks how big, growing, stable or seasonal interest in a topic is, or which option gets the most attention.
---

# B2C demand validation

Code computes, you decide. You choose the metrics and the business inputs; the scripts fetch the data, do all the arithmetic and grade how reliable each number is. Never compute metric values yourself and never build API requests.

## Metrics flow
Run the scripts from `scripts/`.

1. See which metrics exist:
   `python metrics.py catalog --format short` for one line per metric, or `python metrics.py catalog` for the full JSON (when to use each metric, its inputs, how to read its value, its limitations).
2. Write a `spec.json`:
   ```json
   {
     "project": "en.wikipedia",
     "subjects": [{"label": "Meal kits", "articles": ["Meal_kit"]}],
     "period": {"start": "2025-09-01", "end": "2026-08-31"},
     "baseline": "year_over_year",
     "metrics": ["<metric id from the catalog>"]
   }
   ```
   - A subject groups the article titles whose views are summed, for example synonyms.
   - `baseline` is `previous_period` or `year_over_year`; only metrics that compare periods use it.
3. Run it: `python metrics.py run --spec spec.json`.
   - Exit code 2 with `"status": "invalid_spec"`: nothing was fetched. Fix every listed error (`field`, `code`, `detail`) and run again.
   - Exit code 0: a report with one result per metric (and per subject for per-subject metrics), and a `summary` listing the results by reliability level.

## Available metrics
- `interest_volume`: how big is the interest in a subject? Total, average and median daily views, banded niche / moderate / significant / mass by the median. Needs a period of at least 28 days; 12 months recommended. Don't compare its values across language projects.

## Reliability rules
Every result has `value`, `unit`, a code-generated `interpretation`, and `reliability` with a `level` (`high`, `medium`, `low`, `invalid`), `usable_for_claims`, and the `checks` behind it.

- Never make a claim from an `invalid` result. Say the metric could not be measured and why, using the failed checks.
- Call a `low` result a "weak signal".
- Always name the reliability level in findings and recommendations.
- Base the wording on the `interpretation`; use the check details to explain a `medium` or `low` level.
