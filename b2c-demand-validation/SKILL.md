---
name: b2c-demand-validation
description: Measure public interest in products, topics, brands or ideas to validate demand, using public attention data (currently Wikipedia pageviews). Use when someone wants to know whether people care about something, how much, whether interest is changing, or how several options compare, for example before building, launching or prioritising a product.
---

# B2C demand validation

Code computes, you decide. You turn the user's question into an analysis spec: the goal, the subjects, the metrics and the period. The scripts fetch the data, do all the arithmetic and grade how reliable each number is. Never compute metric values yourself and never build API requests.

The only data source today is Wikipedia pageviews: how often people read the articles about a subject. It is a proxy for attention, not for sales or purchase intent. Say so when it matters for the user's decision.

## Workflow
Follow these steps in order. Paths are relative to this skill's folder.

### 1. Understand the request
From the user's message, work out:
- **Goal**: the decision behind the question, e.g. "is this niche big enough to build for?", "which of these options should we focus on?".
- **Question type**: what the user needs measured. See the table in step 2.
- **Subjects**: the things to measure: products, categories, brands, options. Each one becomes a subject.
- **Audience**: which language the target customers read. This picks the project: `en.wikipedia` for English, `de.wikipedia` for German, and so on.
- **Time frame**: a period the user named, or none.

Only ask the user a question if you cannot tell what the subjects are. For everything else, pick a sensible default and state it in the report.

### 2. Pick the metrics
Run `python scripts/metrics.py catalog --format short` to see the metrics that exist now. Run `python scripts/metrics.py catalog` for the full entries: when to use each metric, when not to, and how to read its value.

| question type | metric |
|---|---|
| How big is the interest? | `interest_volume` |
| Is interest growing or falling? | `growth_rate` (not available yet) |
| Is interest steady or news-driven? | `volatility` (not available yet) |
| Which option gets the most attention? | `share_of_voice` (not available yet) |
| When in the year is interest highest? | `seasonality` (not available yet) |

Only use metric ids that the catalog lists. If the question needs a metric that doesn't exist yet, run the ones that help and say in the report what could not be measured.

### 3. Fill the spec
Write a `spec.json` file:
```json
{
  "project": "en.wikipedia",
  "subjects": [
    {"label": "Meal kits", "articles": ["Meal_kit", "HelloFresh", "Blue_Apron"]}
  ],
  "period": {"start": "2025-09-01", "end": "2026-08-31"},
  "metrics": ["interest_volume"]
}
```
- **subjects**: give each subject a readable `label` and one or more `articles`. The views of all its articles are summed, so add synonyms and the main brands of the category. Titles must be exact Wikipedia article titles: underscores instead of spaces, and the same capitalisation (`Meal_kit`, not `meal kit`).
- **period**: by default, the last 12 full months, ending before today. It must be at least as long as each metric's `min_period`.
- **baseline**: `previous_period` or `year_over_year`. Only metrics that compare two periods use it. Leave it out otherwise.

### 4. Run it
`python scripts/metrics.py run --spec spec.json`
- **Exit code 2, `"status": "invalid_spec"`**: nothing was fetched. Fix every listed error (`field`, `code`, `detail`) and run again.
- **Exit code 0**: the report has one result per metric (and per subject for per-subject metrics), and a `summary` of results by reliability level.
- If `fetch_errors` shows an article with HTTP 404, the title is wrong. Correct it, or remove it, and run again once. Don't keep guessing titles.

### 5. Report in the chat
Keep the report short and in plain language:

```
**Answer:** <one or two sentences that answer the user's question, with the reliability level>

| subject | result | reliability |
|---|---|---|
| <label> | <the key number, e.g. ~283 views/day, moderate interest> | high |

**What this means:** <how the result bears on the user's goal>
**Caveats:** <medium/low/invalid results and their reason, articles not found, the data being a proxy>
**Setup:** <project, period, articles per subject, and the defaults you chose>
**Not measured yet:** <parts of the question that need metrics that don't exist yet, if any>
```

## Reliability rules
Every result has `value`, `unit`, a code-generated `interpretation`, and `reliability` with a `level` (`high`, `medium`, `low`, `invalid`), `usable_for_claims`, and the `checks` behind it.

- Never make a claim from an `invalid` result. Say the metric could not be measured and why, using the failed checks.
- Call a `low` result a "weak signal".
- Always name the reliability level in findings and recommendations.
- Base the wording on the `interpretation`; use the check details to explain a `medium` or `low` level.
- Don't compare absolute numbers across different projects (languages): each project has a different audience size.

## Available metrics
- `interest_volume`: how big is the interest in a subject? Total, average and median daily views, banded niche / moderate / significant / mass by the median. Needs a period of at least 28 days; 12 months recommended.
