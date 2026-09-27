# B2C demand validation

A Claude skill that checks whether people care about a product, topic or brand before a business builds, launches or prioritises it. It turns a business question into measured findings, grades how far each one can be trusted, and gives recommendations in the chat and in a PDF report.

The data comes from public attention data, which today means Wikipedia pageviews. Pageviews measure attention, not sales or purchase intent.

## Idea
The model running the skill may not be strong, so the skill doesn't rely on its judgement for anything that can be done in code:

- **Code computes, the model decides.** The model understands the question, picks what to measure and writes the conclusions. Code fetches the data, does all the arithmetic, grades reliability and lays out the report.
- **Reliability is part of every result.** Each number carries a trust level, and the model follows rules on how to word findings at each level, including making no claim at all.
- **Mistakes are caught before any work is done.** Inputs are validated first, and errors come back structured so the model knows exactly what to fix.
- **Problems are fixed in code, not only in instructions.** When the model went wrong in real runs, the fix was a rule the code enforces, such as a limit, a check or a fixed file name.

## Architecture
The layers are small, and each one depends only on the layer below it:

```
business question
      │  model: goal, subjects, audience language, period, metrics
      ▼
analysis spec ──► validation ──► data resolver ──► data source client
                                      │
                                      ▼
                        metrics + reliability checks
                                      │
                                      ▼
                         results (values, levels, interpretations)
                                      │  model: answer and recommendations
                                      ▼
                          chat answer + PDF report
```

- **Data source client**: pure. It sends one request and returns the data or an error, with no retries and no business logic. This makes it easy to swap for another source or wrap as an MCP tool.
- **Data resolver**: turns business terms into requests. A subject is a group of related articles whose views are summed, and a period is a date range. The resolver retries failed requests, keeps partial results when some articles fail, and records data gaps.
- **Metrics**: each metric is a self-contained definition. It lists the question it answers, when to use it, the data it needs, its calculation, its reliability checks and a sentence template. The model learns about metrics from a generated catalog.
- **Reliability**: shared checks such as coverage, volume, spikes, fetch errors, freshness and noise produce a trust level for each result.
- **Workspace**: every analysis gets its own folder, and code names all its files, so a new question never overwrites an earlier one.
- **Report**: code builds the tables and the reliability summary, and the model supplies only the text. No number in the PDF is typed by the model.

## Data flow
1. The model turns the question into a spec: which things to compare, in which language editions, over which period, with which metrics.
2. It looks up the exact article titles before writing the spec, with a limited number of attempts.
3. Code validates the spec, fetches the daily series, runs the reliability checks, computes the values and writes a plain-language interpretation of each one.
4. Things are compared in three ways:
   - **between options**, such as their share of attention;
   - **between periods**: against the previous period or the same period last year, adjusted for overall traffic change;
   - **between languages**, using only metrics that are comparable across audiences of different sizes.
5. The model writes the answer and the recommendations and names the reliability level of each finding. Code renders the PDF.

## Development process
Each step started as a short design note with the goal, the decisions, examples, edge cases and a "done when" line. The step was then built on its own branch and merged through a review.

1. **Foundation**: a pure API client, then a resolver that turns business inputs into clean series.
2. **Metrics framework**: contracts, spec validation, reliability checks, a catalog and a CLI, built before any metric existed.
3. **One metric per step**: volume, growth, trend, volatility, share of voice, relative interest, seasonality and platform mix. Each metric was usable on its own and added one new kind of data, such as a baseline period, weekly or monthly buckets, whole-project traffic or a per-platform split.
4. **Report**: a portable PDF in which the code owns the numbers and the model owns the words.
5. **Fixes from real runs**:
   - several questions in one session overwrote each other's files, so each analysis got its own folder;
   - the model guessed article titles and kept retrying after 404 errors, so it now looks titles up first, with a limited number of attempts;
   - Cyrillic showed up as broken characters in the PDF, so the PDF is now English-only and written in plain words;
   - comparing languages mixed projects in one spec, so each language is now a separate part of one analysis.

## Limitations
- **One data source**: Wikipedia pageviews measure attention, not demand, sales or intent.
- **Language, not country**: a Wikipedia edition is a language, so "English readers" is not the same as "the US market".
- **Absolute volume can't be compared across languages** because audiences differ in size.
- **Article choice drives results**: a broad article outweighs narrow ones, and titles in different languages are matched by hand.
- **English-only PDF**, with text and tables only and no charts.
- **No data caching**: every run fetches all data from the API again, even when an analysis is only refined. Large runs are slow and can hit API rate limits. These include many subjects, long periods, and platform mix, which makes three requests per article.
- **No automated tests**: each step was checked by hand against the real API.
- **Permission prompts**: running the scripts asks the user for approval often.

## Further development
- **MCP for data**: move the clients behind MCP tools, and add more sources such as search trends, app stores, marketplaces and social platforms.
- **Tests**: unit tests for the calculations and checks, recorded API responses for the resolver, and end-to-end runs of the example specs.
- **RAG for product context**: give the model product-specific knowledge such as segments, competitors, past analyses and business goals, so it picks better subjects and makes recommendations that fit the business.
- **Geography**: resolve countries through the top-by-country data, and map markets to the right languages and projects.
- **Cross-language matching**: link articles between languages automatically, and normalise volume by project size so it can be compared across languages.
- **Report**: a Unicode font for non-Latin scripts, and charts.
- **Smoother runs**: pre-approved tools so users see fewer permission prompts, caching of fetched data, and one command that runs every project of an analysis.

## Install the skill
Requires Python 3.10+.

1. Copy the `b2c-demand-validation/` folder (the one with `SKILL.md`) into `~/.claude/skills/` for all your projects, or into `<project>/.claude/skills/` for one project. For claude.ai, zip the folder and upload it under **Settings → Capabilities → Skills**.
   ```bash
   git clone https://github.com/KatrinaSidorenko/b2c-demand-validation.git
   cp -r b2c-demand-validation/b2c-demand-validation ~/.claude/skills/
   pip install -r ~/.claude/skills/b2c-demand-validation/requirements.txt
   ```
2. Ask a demand question, for example *"Is interest in meal kits growing among English readers?"* Claude loads the skill on its own.
