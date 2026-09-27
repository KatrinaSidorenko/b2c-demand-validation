# B2C demand validation

A Claude skill that checks whether people care about a product, topic or brand before a business builds, launches or prioritises it. It turns a business question ("Is there enough interest in meal kits to build for?") into a measured answer with a reliability level for every number, a short chat summary and a PDF report.

The data source is **Wikipedia pageviews** (Wikimedia Analytics API). Pageviews show how much attention a subject gets. They do not measure sales or purchase intent, and the skill says so in every report.

## Main idea: code computes, the model decides
A smaller model can make mistakes with numbers, API requests and file names, so the work is split:

| the model | the code |
|---|---|
| understands the question and the goal | validates every input before doing any work |
| picks subjects, articles, metrics, period | builds API requests, retries, fills missing days |
| decides whether to reuse an analysis folder | names every folder and file |
| writes the answer and the report text | does all arithmetic, grades reliability, lays out the PDF |

Every script returns JSON with structured errors (`field`, `code`, `detail`), so the model knows exactly what to fix. Nothing is fetched or written until the input is valid.

## Architecture
```
SKILL.md                     workflow and rules for the model
scripts/
  wiki_client.py             pure pageviews client: one request, (data, error), no retries
  mediawiki_client.py        pure Action API client: title lookup and search
  data_resolver.py           subjects + period -> daily series, retries, coverage
  metrics_registry.py        one MetricDefinition per metric
  metrics_calc.py            pure calculation functions
  reliability.py             reliability checks and levels
  metrics.py                 CLI: catalog, run
  articles.py                CLI: search, resolve (max 3 lookup rounds per project)
  workspace.py               CLI: analysis folders and index
  report.py                  CLI: schema, build (PDF)
  *_contracts.py             dataclasses only, no I/O
examples/                    one example spec per metric, report text example
```

The layers depend only on the layer below: client → resolver → metrics runner → report. The clients stay pure so that retries and error handling live in one place, and so that they can later become MCP tools with little change.

## How it works
1. **Understand**: the model finds the goal, subjects, audience language (which picks the Wikipedia project) and period.
2. **Folder**: `workspace.py` creates or reuses an analysis folder under `analyses/`, so a new question never overwrites an earlier one.
3. **Metrics**: `metrics.py catalog` lists the metrics with when to use each.
4. **Titles**: `articles.py` checks exact article titles and flags redirects, disambiguation pages and missing titles. The model gets 3 lookup rounds per project, then stops guessing.
5. **Spec**: the model writes `spec.json`: project, subjects (each a label plus articles whose views are summed), period, baseline, metrics.
6. **Run**: `metrics.py run` validates the spec, fetches the data, runs reliability checks, computes the values and writes an interpretation sentence for each result.
7. **Report**: the model answers in chat, then `report.py build` makes the PDF from the results and the model's text. No number in the PDF is typed by the model.

## How data is analysed
| metric | answers |
|---|---|
| `interest_volume` | How big is the interest? (niche / moderate / significant / mass) |
| `growth_rate` | Is interest growing or falling versus a baseline? |
| `trend` | Is the direction steady over the period? |
| `volatility` | Is interest stable or news-driven? |
| `share_of_voice` | Which option gets the most attention? |
| `relative_interest` | Is the change real, or overall Wikipedia traffic moving? |
| `seasonality` | Which months are highest and lowest? |
| `platform_mix` | Is the audience mobile or desktop? |

**Reliability.** Each result goes through checks such as data coverage, minimum volume, spike dominance, fetch errors, data freshness and signal versus noise. They combine into one level:

| level | meaning | how the model may use it |
|---|---|---|
| `high` | all checks pass | state it |
| `medium` | one warning | state it, explain the warning |
| `low` | two or more warnings | call it a "weak signal" |
| `invalid` | a check failed, no value | make no claim |

## How data is compared
- **Subjects within one project**: `share_of_voice` for attention, and every per-subject metric side by side.
- **Periods**: `growth_rate` and `relative_interest` compare against a baseline, either `previous_period` or `year_over_year`. Comparisons use average daily views, so periods of different length compare fairly. `relative_interest` divides by total project traffic, which separates real change from overall traffic drift.
- **Across languages (projects)**: one analysis can have up to 5 projects, e.g. `en.wikipedia` and `uk.wikipedia`, each run separately and joined in one PDF. Absolute numbers are never compared across projects because audiences differ in size. The comparison table shows only metrics marked comparable across projects, which is every metric except `interest_volume`.

## Development process
The skill was built in small steps. Each step was first written as a design note (goal, decisions, examples, edge cases, "done when"), then built on its own branch and merged through a PR.

| steps | what was built | why |
|---|---|---|
| 01 | pure pageviews client | keep API access simple and free of business logic |
| 02 | data resolver | turn business inputs into clean series; retries and partial failures in one place |
| 03 | metrics framework | contracts, spec validation, reliability, catalog, CLI; no metrics yet |
| 04–11 | one metric per step | each metric is reviewed and usable on its own, and each adds one new kind of data (baseline, weekly, cross-subject, project aggregate, monthly, per-platform) |
| 12 | PDF report | a portable result; code owns the numbers, the model owns the words |
| 13 | analysis folders | several questions in one session no longer overwrite each other |
| 14 | article title lookup | the model guessed titles and looped on 404s; now it checks them first, with a limited number of rounds |
| 15 | English-only PDF | the PDF font has no Cyrillic; the rule is enforced in code, and each metric got a plain-language explainer |
| 16 | multi-project comparison | compare one question across languages without mixing projects in one spec |

Each problem found in real runs, such as wrong titles, overwritten files or broken letters, was fixed with a rule in code, not only an instruction in SKILL.md.

## Install the skill
Requires Python 3.10+.

1. Copy the `b2c-demand-validation/` folder (the one with `SKILL.md`) into a skills folder:
   - for all your projects: `~/.claude/skills/b2c-demand-validation/`
   - for one project: `<project>/.claude/skills/b2c-demand-validation/`

   ```bash
   git clone https://github.com/KatrinaSidorenko/b2c-demand-validation.git
   cp -r b2c-demand-validation/b2c-demand-validation ~/.claude/skills/
   ```
   For claude.ai, zip the folder and upload it under **Settings → Capabilities → Skills**.
2. Install the dependencies:
   ```bash
   pip install -r ~/.claude/skills/b2c-demand-validation/requirements.txt
   ```
3. Ask Claude a demand question, for example: *"Is interest in meal kits growing among English readers?"* The skill loads on its own.

Analyses are saved to `analyses/` in the working directory.

## Limitations
- One data source: Wikipedia pageviews, a proxy for attention.
- A project is a language, not a country. Geography is not covered yet.
- The PDF is English only.
- No automated tests yet. Each step was checked by hand against the real API.
