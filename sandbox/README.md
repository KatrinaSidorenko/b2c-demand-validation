# Sandbox

A playground for calling the skill scripts (`wiki_client`, `data_resolver`, ...)
by hand and looking at what they return. It is for manual exploration, not
tests, and it is not shipped with the skill.

```
sandbox/
├── run.py         # CLI runner: lists and runs scenarios, pretty-prints results
└── scenarios.py   # the scenarios themselves, edit freely
```

## Run

From the repo root, with the virtual environment active
(`.venv\Scripts\Activate.ps1` on Windows, `source .venv/bin/activate` elsewhere)
and `pip install -r b2c-demand-validation/requirements.txt` done once:

```bash
python sandbox/run.py                                # list scenarios
python sandbox/run.py wiki-article                   # run one
python sandbox/run.py resolver-subject wiki-top      # run several
python sandbox/run.py --all                          # run everything
python sandbox/run.py wiki-top --max-items 50        # show more list items (default 10)
python sandbox/run.py resolver-subject --max-items 0 # no trimming
```

Output is JSON: dataclasses become objects, enums become their values, and a
client `(data, error)` result becomes a two-item list. Long lists are trimmed
to `--max-items` with a `"... N more"` marker.

Scenarios starting with `wiki-` and `resolver-` call the live Wikimedia API.
`offline-*` scenarios use a fake client and need no network.

## Available scenarios

| name | what it shows |
|------|---------------|
| `wiki-article` | daily views of one article |
| `wiki-aggregate` | daily views of the whole project, mobile web only |
| `wiki-top` | most viewed articles of a month |
| `wiki-top-countries` | countries that view the project most |
| `wiki-top-by-country` | most viewed articles in one country on one day |
| `wiki-not-found` | error path: a missing article returns `http_error` 404 |
| `resolver-subject` | synonyms summed into one daily series, with coverage |
| `resolver-subject-monthly` | monthly series over a year |
| `resolver-project` | total project views (relative interest denominator) |
| `resolver-invalid` | validation error, nothing is fetched |
| `offline-retries` | resolver retries a 503, then fills gaps with 0 |

## Change inputs

Edit `scenarios.py`. `PROJECT` and `PERIOD` at the top are shared by most
scenarios; change them to try another wiki or date range. Anything inside a
scenario (articles, granularity, access, ...) can be changed the same way.

## Add a scenario

Write a function without arguments in `scenarios.py`, decorate it with
`@scenario` and return what you want to see. The name is the function name
with `_` replaced by `-`, and the docstring is the description in the list.

```python
@scenario
def resolver_compare_access() -> Any:
    """Desktop vs mobile web for one subject."""
    subject = Subject(label="air fryer", articles=["Air fryer"])
    return {
        str(access): resolver.get_subject_series(
            SeriesRequest(project=PROJECT, subject=subject, period=PERIOD, access=access)
        ).series
        for access in (Access.DESKTOP, Access.MOBILE_WEB)
    }
```

```bash
python sandbox/run.py resolver-compare-access
```

## Test a new module

When a new script lands in `b2c-demand-validation/scripts/` (for example a
metric from step 03+), import it at the top of `scenarios.py` like the
existing ones — `run.py` already puts that folder on `sys.path` — and add
scenarios for it under a new section and a new prefix (`metric-...`).

To test logic without the network, pass a fake object in place of the real
client, as `FlakyClient` does. It only needs the methods the code under test
calls. When scenarios for one module grow large, move them to their own file
(e.g. `sandbox/metric_scenarios.py`) that imports `scenario` from
`scenarios.py`, and import that file at the bottom of `scenarios.py` so its
scenarios get registered.
