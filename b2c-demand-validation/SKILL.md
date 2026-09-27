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
- **Audience**: which languages the target customers read. Each language picks a project: `en.wikipedia` for English, `de.wikipedia` for German, and so on. A question about several languages or markets ("English vs Ukrainian readers") is one analysis with several projects.
- **Time frame**: a period the user named, or none.

Only ask the user a question if you cannot tell what the subjects are. For everything else, pick a sensible default and state it in the report.

### 2. Pick the analysis folder
Each analysis has its own folder under `analyses/` in the working directory, so a new question never overwrites an earlier one. Inside it, every project is a **part** with its own spec and results; the report text and the PDF are shared by all parts. Code names the folders and every file; you only decide whether to reuse a folder. Keep the folder's `id`: steps 4 and 8 need it.

1. Run `python scripts/workspace.py list`. It prints one line per past analysis, most recently updated first: `id | keywords | projects | period | subjects | metrics | report yes/no`.
2. **Reuse** a folder when the request refines the same question: the same subjects and the same goal (fixing an article title, changing the period, adding a metric). Run `python scripts/workspace.py show --id <id>` and write over its files. To compare the same question in one more language, add a project to it: `python scripts/workspace.py part --id <id> --project <project>`.
3. **Create a new folder** for anything else: new subjects or a different decision. Run `python scripts/workspace.py new --keywords "<keyword>" ... --projects <project> ...` with 1 to 5 short keywords (at most 30 characters each) that name the topic, and every project of the analysis (1 to 5), e.g. `--keywords "kvass" "market size" --projects en.wikipedia uk.wikipedia`. If you are unsure, create a new folder: it costs nothing and never overwrites anything.

`new`, `part` and `show` print `files`: `parts` maps each project to the absolute paths of its `spec` and `results`, and `text` and `pdf` are shared. Use these exact paths in the steps below, wherever they mention `spec`, `results`, `report.json` or `report.pdf`. Never invent file names and never write two projects into one spec.

### 3. Pick the metrics
Run `python scripts/metrics.py catalog --format short` to see the metrics that exist now. Run `python scripts/metrics.py catalog` for the full entries: when to use each metric, when not to, and how to read its value.

| question type | metric |
|---|---|
| How big is the interest? | `interest_volume` |
| Is interest growing or falling? | `growth_rate` |
| Is that growth or decline steady, or caused by one event? | `trend` |
| Is the change real, or just overall Wikipedia traffic moving? | `relative_interest` |
| Is interest steady or news-driven? | `volatility` |
| Which option gets the most attention? | `share_of_voice` |
| When in the year is interest highest? | `seasonality` (not available yet) |

Only use metric ids that the catalog lists. If the question needs a metric that doesn't exist yet, run the ones that help and say in the report what could not be measured.

### 4. Find the exact article titles
Never guess article titles: look them up. A wrong title returns no data, and a redirect counts only a fraction of the views.

1. `python scripts/articles.py search --id <id> --project <project> --query "<subject>" "<brand>" ...` lists the matching articles for each query, best first, with a short description. Put every query of the analysis into one call (up to 10).
2. Pick the articles that match each subject. `python scripts/articles.py resolve --id <id> --project <project> --titles "<title>" ...` checks them (up to 20 in one call). Use each result's `title`, never the `input`:
   - `ok`, `normalized`, `redirect`: usable. For a redirect, `title` is the target article.
   - `disambiguation`, `missing`, `invalid`: not usable. Pick one of the `suggestions` (they are real articles, no need to resolve them again) or drop the title.
3. Look titles up separately for each project, with that project's `--project`: article titles differ by language. Each call uses one of the project's **3 lookup rounds**. When a call returns `"status": "attempts_exhausted"` (exit code 2), stop looking up: keep the titles you have, drop the rest and name them as not found under **Caveats**. A `"status": "lookup_failed"` (exit code 1) did not use a round; run the same call once more.

### 5. Fill the specs
Write one spec per project, each to its own `files.parts["<project>"].spec` path, with that project in `project`:
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
- **subjects**: give each subject a readable `label` and one or more `articles`. The views of all its articles are summed, so add synonyms and the main brands of the category. Use only the `title` values from step 4.
- **label**: always in English, whatever the project, e.g. `"Kvass"` for the uk.wikipedia article `Квас`. The PDF prints labels, and its font has no Cyrillic, Greek or CJK letters. Article titles stay in the project's language.
- **Several projects**: use the same label for the same subject in every project (`"Kvass"` in both the en and the uk spec), and the same period, baseline and metrics. The report matches subjects across projects by label and refuses projects with different periods or baselines.
- **period**: by default, the last 12 full months, ending before today. It must be at least as long as each metric's `min_period`.
- **baseline**: `previous_period` (the same number of days right before the period) or `year_over_year` (the same dates one year earlier). Only metrics that compare two periods use it; it defaults to `previous_period`. Prefer `year_over_year` with a 12-month period: `previous_period` mixes seasonality into periods shorter than a year. Leave it out for other metrics.

### 6. Run it
Run each project's spec into its own results path:
`python scripts/metrics.py run --spec <parts[project].spec> > <parts[project].results>`
- **Exit code 2, `"status": "invalid_spec"`**: nothing was fetched. Fix every listed error (`field`, `code`, `detail`) and run again. `project_mismatch`: the spec's `project` is not the project of the path it was written to; move it to the right path.
- **Exit code 0**: the report has one result per metric (and per subject for per-subject metrics), and a `summary` of results by reliability level.
- If `fetch_errors` shows an article with HTTP 404 although `resolve` returned it, the article had no views in the period (for example, it was created later). Don't look it up again; name it under **Caveats**.

### 7. Report in the chat
Keep the report short and in plain language. With several projects, add a `project` column to the table (one row per project and subject) and the **Across projects** line:

```
**Answer:** <one or two sentences that answer the user's question, with the reliability level>

| subject | result | reliability |
|---|---|---|
| <label> | <the key number, e.g. ~283 views/day, moderate interest> | high |

**Across projects:** <only with several projects: how they differ, using metrics comparable across projects>
**What this means:** <how the result bears on the user's goal>
**Caveats:** <medium/low/invalid results and their reason, articles not found, the data being a proxy>
**Setup:** <project, period, articles per subject, and the defaults you chose>
**Not measured yet:** <parts of the question that need metrics that don't exist yet, if any>
```

### 8. Build the PDF report
Turn the same findings into a short PDF the user can keep and share. You write only the text; the tool adds the results table, the setup line, a plain-language explanation of each metric used and the reliability summary from the results of every project.

Write the PDF for a business reader who is not an analyst:
- **English only**, whatever language the user or the project uses. Translate or transliterate non-English names (`Квас` → Kvass). The PDF font cannot show Cyrillic, Greek or CJK letters.
- Name metrics by their title (Interest volume, Growth rate), never by their id.
- Say in plain words what each number means for the decision. Build on the metric's `explainer` (from `python scripts/metrics.py catalog`) and the result's `interpretation`.
- Leave out jargon such as R², CV, MAD or z-scores; say "consistent", "steady", "bursty" instead.

Run `python scripts/report.py schema` to see the fields, their limits and an example. Write the report text to the `text` path (`report.json`):
- **title**, **problem**: the user's question and the decision behind it.
- **answer**: the same answer as in the chat, with the reliability level.
- **analysis**: what the results show, built on each result's `interpretation`.
- **conclusions**: what that means for the user's goal.
- **trust**: reliability levels, the checks behind a `medium`, `low` or `invalid` level, and the data being a proxy.
- **recommendations**: next steps, each tied to the goal.
- **comparison**: required when the analysis has several projects, left out otherwise. How the projects differ, using only metrics the catalog marks comparable across projects.
- **not_measured** (optional): parts of the question no available metric covers.

Follow the reliability rules below, and don't write numbers that the results don't contain.

Run `python scripts/report.py build --id <id>`. It collects the results of every project of the analysis, reads the text from the `text` path and writes the PDF to the `pdf` path. With several projects, the PDF adds a Project column and a comparison table limited to the metrics comparable across projects.
- **Exit code 2, `"status": "invalid_report"`**: nothing was written. Fix every listed error and run again. `non_latin_text` on a text field: rewrite it in English. On `results.<project>.spec.subjects[i]`: relabel the subject in English in that project's spec and run step 6 again. `part_not_run`: a project has no results yet; write its spec and run it. `period_mismatch` / `baseline_mismatch`: align the specs and run step 6 again.
- **Exit code 0**: give the user the PDF `path`.

## Reliability rules
Every result has `value`, `unit`, a code-generated `interpretation`, and `reliability` with a `level` (`high`, `medium`, `low`, `invalid`), `usable_for_claims`, and the `checks` behind it.

- Never make a claim from an `invalid` result. Say the metric could not be measured and why, using the failed checks.
- Call a `low` result a "weak signal".
- Always name the reliability level in findings and recommendations.
- Base the wording on the `interpretation`; use the check details to explain a `medium` or `low` level.
- Don't compare absolute numbers across different projects (languages): each project has a different audience size. Compare projects only with metrics that `metrics.py catalog` marks `comparable across projects` (`cross_project_comparable: true`); compare the others only within one project.

## Available metrics
- `interest_volume`: how big is the interest in a subject? Total, average and median daily views, banded niche / moderate / significant / mass by the median. Needs a period of at least 28 days; 12 months recommended.
- `growth_rate`: is interest growing or falling? The change in average daily views versus the baseline period, as a ratio (`0.34` = +34%), banded strong decline / decline / flat / growth / strong growth. Needs a period of at least 28 days; 12 months with `year_over_year` recommended. It includes changes in overall Wikipedia traffic. If `signal_vs_noise` warns, the change is within normal week-to-week fluctuation: don't call it growth or decline.
- `trend`: is the direction of interest steady over the period? A straight line fitted to weekly (Monday to Sunday) views: the slope in % of an average week per month (rising above +1%, falling below -1%, flat in between) and R² for how consistent it is (consistent from 0.6, moderate 0.3 .. 0.6, not clear below 0.3). Needs at least 12 full weeks; 12 months recommended. It does not capture seasonality or turning points. Pair it with `growth_rate` to check that a change is sustained.
- `volatility`: is interest stable, or driven by news and one-off events? The coefficient of variation of daily views (stable below 0.3, moderately volatile 0.3 .. 0.7, volatile above), the max / median ratio (above 5 means at least one strong spike), and up to 5 spike days (above median + 5 × MAD). Needs at least 28 days; 6-12 months recommended. Use it before trusting `growth_rate` or `interest_volume`: a volatile subject's numbers are driven by events. Low-volume subjects look volatile by nature.
- `share_of_voice`: which of the compared options gets the most attention? Each subject's share of the summed views (`0.62` = 62%), highest first; one result for all subjects together (`subject` is `null`). The top two within 5 points are a tie. Needs at least 2 subjects in the same project and a period of at least 28 days; 3-12 months recommended. An article can't be in two subjects. Any subject with failing data makes the whole result `invalid`; a low-volume or spiky subject only warns. Shares depend on the articles chosen: a broad article dominates narrow ones, so give each subject comparable articles.
- `relative_interest`: is the change in interest real, or just overall Wikipedia traffic going up or down? The change of the subject's share of all project views versus the baseline period (`relative_change`, banded like `growth_rate`), and the change of the project's own daily views (`project_change`). Needs a period of at least 28 days; 12 months with `year_over_year` recommended. Run it next to `growth_rate`: when they differ, `project_change` explains the gap, and `relative_interest` is the one to trust for "is demand really changing". If `signal_vs_noise` warns, don't call it growth or decline.
