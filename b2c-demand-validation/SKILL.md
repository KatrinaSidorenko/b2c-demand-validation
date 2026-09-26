---
name: b2c-demand-validation
description: Measure public interest in products, topics, brands or ideas to validate demand, using public attention data (currently Wikipedia pageviews). Use when someone wants to know whether people care about something, how much, whether interest is changing, or how several options compare, for example before building, launching or prioritising a product.
---

# B2C demand validation

Code computes, you decide. You turn the user's question into an analysis spec: the goal, the subjects, the metrics and the period. The scripts fetch the data, do all the arithmetic, grade how reliable each number is and lay out the PDF report. Never compute metric values yourself, never build API requests and never write code to make the report.

The only data source today is Wikipedia pageviews: how often people read the articles about a subject. It is a proxy for attention, not for sales or purchase intent. Say so when it matters for the user's decision.

## Workflow
Follow these steps in order. Paths are relative to this skill's folder.

### 1. Understand the request
From the user's message, work out:
- **Goal**: the decision behind the question, e.g. "is this niche big enough to build for?", "which of these options should we focus on?".
- **Question type**: what the user needs measured. See the table in step 3.
- **Subjects**: the things to measure: products, categories, brands, options. Each one becomes a subject.
- **Audience**: which language the target customers read. This picks the project: `en.wikipedia` for English, `de.wikipedia` for German, and so on.
- **Time frame**: a period the user named, or none.

Only ask the user a question if you cannot tell what the subjects are. For everything else, pick a sensible default and state it in the report.

### 2. Pick the analysis folder
Each analysis keeps its four files (spec, results, report text, PDF) in its own folder under `analyses/` in the working directory, so a new question never overwrites an earlier one. Code names the folders and keeps an index; you only decide whether to reuse one.

1. Run `python scripts/workspace.py list`. It prints one line per past analysis, most recently updated first: `id | keywords | project | period | subjects | metrics | report yes/no`.
2. **Reuse** a folder when the request refines the same question: the same project, the same subjects and the same goal (fixing an article title, changing the period, adding a metric). Run `python scripts/workspace.py show --id <id>` and write over its files.
3. **Create a new folder** for anything else: new subjects, another project (language), or a different decision. Run `python scripts/workspace.py new --keywords "<keyword>" ...` with 1 to 5 short keywords (at most 30 characters each) that name the topic, e.g. `--keywords "meal kits" "hellofresh" "market size"`. If you are unsure, create a new folder: it costs nothing and never overwrites anything.

Both `new` and `show` print `files` with the absolute paths of `spec`, `results`, `text` and `pdf`. Use these exact paths in the steps below, wherever they mention `spec.json`, `results.json`, `report.json` or `report.pdf`.

### 3. Pick the metrics
Run `python scripts/metrics.py catalog --format short` to see the metrics that exist now. Run `python scripts/metrics.py catalog` for the full entries: when to use each metric, when not to, and how to read its value.

| question type | metric |
|---|---|
| How big is the interest? | `interest_volume` |
| Is interest growing or falling? | `growth_rate` |
| Is that growth or decline steady, or caused by one event? | `trend` |
| Is interest steady or news-driven? | `volatility` |
| Which option gets the most attention? | `share_of_voice` (not available yet) |
| When in the year is interest highest? | `seasonality` (not available yet) |

Only use metric ids that the catalog lists. If the question needs a metric that doesn't exist yet, run the ones that help and say in the report what could not be measured.

### 4. Fill the spec
Write the spec to the `spec` path (`spec.json`):
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
- **baseline**: `previous_period` (the same number of days right before the period) or `year_over_year` (the same dates one year earlier). Only metrics that compare two periods use it; it defaults to `previous_period`. Prefer `year_over_year` with a 12-month period: `previous_period` mixes seasonality into periods shorter than a year. Leave it out for other metrics.

### 5. Run it
`python scripts/metrics.py run --spec <spec> > <results>`
- **Exit code 2, `"status": "invalid_spec"`**: nothing was fetched. Fix every listed error (`field`, `code`, `detail`) and run again.
- **Exit code 0**: the report has one result per metric (and per subject for per-subject metrics), and a `summary` of results by reliability level.
- If `fetch_errors` shows an article with HTTP 404, the title is wrong. Correct it, or remove it, and run again once. Don't keep guessing titles.

### 6. Report in the chat
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

### 7. Build the PDF report
Turn the same findings into a short PDF the user can keep and share. You write only the text; the tool adds the results table, the setup line and the reliability summary from `results.json`.

Run `python scripts/report.py schema` to see the fields, their limits and an example. Write the report text to the `text` path (`report.json`) in plain language:
- **title**, **problem**: the user's question and the decision behind it.
- **answer**: the same answer as in the chat, with the reliability level.
- **analysis**: what the results show, built on each result's `interpretation`.
- **conclusions**: what that means for the user's goal.
- **trust**: reliability levels, the checks behind a `medium`, `low` or `invalid` level, and the data being a proxy.
- **recommendations**: next steps, each tied to the goal.
- **not_measured** (optional): parts of the question no available metric covers.

Follow the reliability rules below, and don't write numbers that `results.json` doesn't contain.

Run `python scripts/report.py build --results <results> --text <text> --out <pdf>`.
- **Exit code 2, `"status": "invalid_report"`**: nothing was written. Fix every listed error and run again.
- **Exit code 0**: give the user the PDF `path`.

## Reliability rules
Every result has `value`, `unit`, a code-generated `interpretation`, and `reliability` with a `level` (`high`, `medium`, `low`, `invalid`), `usable_for_claims`, and the `checks` behind it.

- Never make a claim from an `invalid` result. Say the metric could not be measured and why, using the failed checks.
- Call a `low` result a "weak signal".
- Always name the reliability level in findings and recommendations.
- Base the wording on the `interpretation`; use the check details to explain a `medium` or `low` level.
- Don't compare absolute numbers across different projects (languages): each project has a different audience size.

## Available metrics
- `interest_volume`: how big is the interest in a subject? Total, average and median daily views, banded niche / moderate / significant / mass by the median. Needs a period of at least 28 days; 12 months recommended.
- `growth_rate`: is interest growing or falling? The change in average daily views versus the baseline period, as a ratio (`0.34` = +34%), banded strong decline / decline / flat / growth / strong growth. Needs a period of at least 28 days; 12 months with `year_over_year` recommended. It includes changes in overall Wikipedia traffic. If `signal_vs_noise` warns, the change is within normal week-to-week fluctuation: don't call it growth or decline.
- `trend`: is the direction of interest steady over the period? A straight line fitted to weekly (Monday to Sunday) views: the slope in % of an average week per month (rising above +1%, falling below -1%, flat in between) and R² for how consistent it is (consistent from 0.6, moderate 0.3 .. 0.6, not clear below 0.3). Needs at least 12 full weeks; 12 months recommended. It does not capture seasonality or turning points. Pair it with `growth_rate` to check that a change is sustained.
- `volatility`: is interest stable, or driven by news and one-off events? The coefficient of variation of daily views (stable below 0.3, moderately volatile 0.3 .. 0.7, volatile above), the max / median ratio (above 5 means at least one strong spike), and up to 5 spike days (above median + 5 × MAD). Needs at least 28 days; 6-12 months recommended. Use it before trusting `growth_rate` or `interest_volume`: a volatile subject's numbers are driven by events. Low-volume subjects look volatile by nature.
