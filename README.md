# Probagent

Probagent tests LLM agents and the data they rely on. This first part is the **Data Quality Checker**: it answers *"Is this data clean?"* before an agent (or a person) trusts it.

Specs: [InnDataQuality_Spec.md](InnDataQuality_Spec.md) (this feature) and [Probagent_Spec.md](Probagent_Spec.md) (the full project).

## How it works

1. **Get the data:** upload a CSV, or fetch JSON from a REST API. If the API answers 401/403, the page asks for a JWT token and retries.
2. **Get the rules:** write a JSON ruleset, or generate one from a sample CSV and then review it. Tick the columns you want: only ticked columns get rules and are checked.
3. **Check:** every row is tested against every rule.
4. **Show:** issues are listed on screen. **Print** gives a clean printout, or a PDF via "Save as PDF".

## Quick start

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows   (macOS/Linux: source .venv/bin/activate)
pip install -e ".[dev]"

probagent dq-serve --demo        # then open http://127.0.0.1:8765
```

### Try it with the demo data

- **CSV:** upload `fixtures/dq/orders_dirty.csv`. Load `fixtures/dq/orders_rules.json` as the rules, or click **Generate rules** with `fixtures/dq/orders_clean.csv` as the sample. Click **Run check**: you should see the 13 problems listed in [fixtures/dq/DQ_ISSUES.md](fixtures/dq/DQ_ISSUES.md).
- **REST API:** with `--demo`, these fake APIs run on the same server (use data path `data`):
  - `http://127.0.0.1:8765/demo/api/orders`: open, no login needed.
  - `http://127.0.0.1:8765/demo/api/secure-orders`: needs a login. The first fetch shows the token box. Paste the demo JWT token printed in the terminal.

### Check a file in the terminal

```bash
probagent lint-data fixtures/dq/orders_dirty.csv --rules fixtures/dq/orders_rules.json

# Only some columns
probagent lint-data fixtures/dq/orders_dirty.csv --rules fixtures/dq/orders_rules.json --columns "email,quantity"
```

Exit code: `0` no issues, `1` issues found, `3` invalid file or rules.

## Writing rules

```json
{
  "version": 1,
  "name": "orders",
  "fields": {
    "order_id":   { "type": "integer", "required": true, "unique": true },
    "email":      { "type": "string",  "required": true, "pattern": "email" },
    "quantity":   { "type": "integer", "min": 1, "max": 1000 },
    "status":     { "type": "string",  "allowed": ["pending", "shipped", "cancelled"] },
    "country":    { "type": "string",  "min_length": 2, "max_length": 2 },
    "order_date": { "type": "date",    "format": "YYYY-MM-DD" }
  }
}
```

| Rule | Meaning |
|---|---|
| `type` | `string`, `integer`, `number`, `boolean`, `date`, `datetime` |
| `required` | The value can't be empty |
| `unique` | No two rows may share the value |
| `min` / `max` | Smallest and largest allowed number or date |
| `min_length` / `max_length` | Text length limits |
| `allowed` | The value must be one of this list |
| `pattern` | `email`, `phone`, `url`, `uuid`, or `{"regex": "..."}` |
| `format` | Date format: `YYYY-MM-DD`, `DD/MM/YYYY`, `MM/DD/YYYY`, `DD-MM-YYYY`, `YYYY/MM/DD` |

Dataset options: `allow_extra_columns` (default `true`) and `check_duplicate_rows` (default `true`).

## Data guard: the same check, automatically, inside your agent

The data guard runs the check above every time an agent's tool pulls data, **before the model sees it**. Bad data is blocked (or flagged, or cleaned), so the agent can't build a confident answer on it.

```
agent calls get_orders  ->  tool returns data  ->  guard checks it against rules/*.json
                                                     |-- clean: data goes to the model unchanged
                                                     '-- bad:   the model gets {"blocked": true, ...}
```

**1. Say which data is checked, with which rules:** `guard.json`

```json
{
  "version": 1,
  "sources": {
    "orders": { "rules": "rules/orders_rules.json", "data_path": "data", "on_fail": "block" }
  }
}
```

| Setting | Meaning |
|---|---|
| `rules` | A rules file in the format above, relative to `guard.json` |
| `format` | `json` (default): a list of records, or JSON text. `csv`: CSV text |
| `data_path` | Where the list of records is inside the JSON, e.g. `data.items` |
| `on_fail` | `block` (default), `warn` (pass the data on with a warning), or `filter` (remove the bad rows) |
| `max_bad_rows_pct` | With `filter`: if more rows than this are bad (default 10), block instead |
| `columns` | Only check these columns (default: all columns in the rules) |

Check the config and every rules file before you start the agent:

```bash
probagent guard-validate guard.json          # exit 0 = valid, 3 = problems listed
```

**2. Plug it into LangGraph:** one line on the `ToolNode`, no change to your tools.

```bash
pip install -e ".[langgraph]"
```

```python
from langgraph.prebuilt import ToolNode
from probagent.guard import DataGuard
from probagent.guard.langgraph import guard_tool_calls

guard = DataGuard.from_config("guard.json")
tool_node = ToolNode(
    tools,
    wrap_tool_call=guard_tool_calls(guard, {"get_orders": "orders"}),   # tool name -> source
)
```

Tools not in the map run as usual. For async graphs, also pass `awrap_tool_call=aguard_tool_calls(guard, {...})`. For any other agent, put `@guard.protect("orders")` on the function that fetches the data (under `@tool`, if you use one).

**Try it:** `python examples/langgraph_guard/agent.py` runs a small graph offline: the clean orders are answered, and the dirty orders are blocked with their 13 issues.

**What the model sees**

| Result | The model gets |
|---|---|
| Clean | The data, unchanged |
| Bad, `block` | `{"blocked": true, "issue_count": 13, "by_code": {...}, "by_column": {...}, "message": "...do not guess..."}` |
| Bad, `warn` | `{"data": ..., "quality_warning": {...}}` |
| Bad, `filter` | `{"data": <clean rows only>, "quality_note": {"rows_removed": 13, ...}}` |
| Couldn't check (unreadable data, unknown source) | Blocked. The guard fails closed. |

The model never sees cell values, only counts, issue codes and column names from your rules. A bad cell could contain a prompt injection. Every check is written to the run log (`feature: "guard"`).

**Why was it blocked? `probagent explain`**

A block message includes a `run_id`, and the model is told to give it to the user as a reference. Anyone with access to the run log can then look it up:

```bash
probagent explain 20261008-123024-4291b0     # one check, by the reference the user got
probagent explain                            # the latest check that was blocked, warned or filtered
```

It lists each issue by row and column, with the rule it broke (for example `row 12  email  pattern_mismatch: does not match "pattern": email`), and what to do next. Values are never logged, so to see them run `lint-data` on the source data.

## Limits (demo-sized data)

10 MB per file or API response, 50,000 rows, 200 columns. The first 1,000 rows are used to generate rules, and the first 500 issues are listed (the counts include all of them). Change the limits in [src/probagent/dq/settings.py](src/probagent/dq/settings.py).

## Safety notes

- The server listens on `127.0.0.1` (this computer only). **Don't expose it publicly:** the fetch feature can reach any URL this machine can reach.
- JWT tokens are used for one request and never saved, logged, or shown in reports. A token is never sent to a different server after a redirect.
- Data values are always shown as plain text, so a cell containing `<script>` can't run.

## Run log

Every action (upload, fetch, generate rules, check, `lint-data`) is recorded as one JSON line in `.probagent/runs.jsonl`: what ran, when, with which file and rules, and what it found. Tokens, passwords and data values are never logged. See [docs/run_log.md](docs/run_log.md) for the schema and how to search it.

## Code map

| File | What it does |
|---|---|
| [settings.py](src/probagent/dq/settings.py) | All limits |
| [models.py](src/probagent/dq/models.py) | Data shapes: Dataset, Ruleset, Issue, Report |
| [loader.py](src/probagent/dq/loader.py) | CSV or JSON records → Dataset |
| [fetch.py](src/probagent/dq/fetch.py) | REST API fetch with the JWT retry flow |
| [types.py](src/probagent/dq/types.py) | Text → integer/number/date…, named patterns |
| [rules.py](src/probagent/dq/rules.py) | Ruleset validation with friendly errors |
| [validator.py](src/probagent/dq/validator.py) | The check itself |
| [infer.py](src/probagent/dq/infer.py) | Guess rules from sample data |
| [server.py](src/probagent/dq/server.py) | FastAPI endpoints and demo APIs |
| [static/index.html](src/probagent/dq/static/index.html) | The one web page |
| [cli.py](src/probagent/cli.py) | `probagent dq-serve` and `probagent lint-data` |
| [runlog.py](src/probagent/runlog.py) | The run log, shared by all features |
| [log_schema.py](src/probagent/dq/log_schema.py) | What the data quality feature writes to the run log |
| [guard/config.py](src/probagent/guard/config.py) | `guard.json`: sources, rules files, policies |
| [guard/core.py](src/probagent/guard/core.py) | `DataGuard`: check, block/warn/filter, `@guard.protect` |
| [guard/langgraph.py](src/probagent/guard/langgraph.py) | LangGraph `ToolNode` adapter |
| [guard/explain.py](src/probagent/guard/explain.py) | `probagent explain`: a guard check from the run log, in plain English |
| [guard/log_schema.py](src/probagent/guard/log_schema.py) | What the guard writes to the run log |

## Tests

```bash
pytest
```

To regenerate the demo data: `python fixtures/dq/make_fixtures.py` (it uses a fixed seed, so the output is always the same).
