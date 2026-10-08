# Decisions

Choices made where the spec was open or where the code differs from it. Newest at the bottom.

## Data Quality Checker

| # | Decision | Why |
|---|---|---|
| DQ-1 | Python **3.10+** instead of 3.11+ | The development machine has 3.10, and nothing here needs 3.11. |
| DQ-2 | **No pandas.** CSV uses the standard `csv` module, and JSON is flattened by a small `_flatten` function. | Fewer dependencies and code that is easier for beginners to follow. Everything is kept as text anyway, so pandas' type handling isn't needed. |
| DQ-3 | `POST /api/rules/infer` takes **form data**: a `file` or a `dataset_id` field. | One endpoint can't easily accept both multipart and JSON bodies. Form data covers both cases. |
| DQ-4 | Errors are returned as HTTP 400/404 with `{"error", "message"}`. | The page can check `response.ok` the same way for every endpoint. |
| DQ-5 | A cell gets **at most one issue**. This includes `unique`: a cell that already failed another rule is not also reported as a duplicate. | Keeps reports readable (spec decision 2, extended). |
| DQ-6 | An exact **duplicate row** is reported once (`duplicate_row`), and its cells are not checked again. | Its cells were already checked on the first copy. Repeating those issues would double-count. |
| DQ-7 | Inference sets `unique` only for **integer and string** columns. | Prices and dates are often all different in a small sample without needing to be unique. Marking them unique would give false alarms. |
| DQ-8 | Inference sets `min_length`/`max_length` only when every value has the same length. | This matches the spec. It catches fixed-length codes such as countries without over-restricting free text. |
| DQ-9 | `"1e3"` is a valid `number` but `"1_000"`, `"nan"` and `"inf"` are not. | Python's `float()` accepts the last three. A regex keeps to plain written numbers. |
| DQ-10 | `bad_date` is used when the text has the date's shape but is impossible (`2026-02-30`). Otherwise the code is `wrong_type`. | Tells "typo in the date" apart from "not written as a date at all". |
| DQ-11 | The demo APIs read `fixtures/dq/orders_dirty.csv` from the repo. | Demo mode is meant for running from a repo checkout. It is not packaged. |
| DQ-12 | Redirects are followed manually, and the token is sent only to the same scheme + host + port as the original URL. | `httpx` gives no per-hop control of headers, and the spec requires that the token never goes to another server. |
| DQ-13 | Generated rules for date columns have only `type` and `format`, with no `min`/`max`. | Requested by the user. Data keeps getting new dates, so a `max` from the sample would flag every newer date. Hand-written rules can still set `min`/`max` on dates. |
| DQ-14 | Column checkboxes limit both rule generation and the check. Rules for unticked columns are ignored, not reported as `missing_column`. Ticked columns with no rules, or not in the data, are listed as report warnings. | Requested by the user. The warnings keep the selection from silently hiding mistakes. |
| DQ-15 | Duplicate rows compare the **whole row**, whichever columns are ticked. | "Exact copy of a row" is a property of the data. Comparing only ticked columns would flag rows that differ in columns the user isn't looking at. |
| DQ-16 | When every column is ticked, the page sends `columns: null` (all) instead of the full list. | Same result, and the run log stays easy to read: `null` means everything. |

## Run log

| # | Decision | Why |
|---|---|---|
| LOG-1 | One JSONL file at `.probagent/runs.jsonl` in the folder Probagent is started from. | Chosen by the user: readable in any editor and easy to search with standard tools. |
| LOG-2 | Fixed envelope, plus per-action `inputs`/`outputs` models registered by each feature. | New features can't drift from the format, and `extra="forbid"` stops unplanned fields (such as a token) from being written. |
| LOG-3 | Rule validation is not logged. | The page runs it automatically while you type, which would flood the log. |
| LOG-4 | Finding data issues is `status: ok`. `error` means the action could not finish. | Matches the parent spec: a FAIL (bad data) is not an ERROR (the tool failed). |
| LOG-5 | Retention: 90 days, then the newest 10,000 entries. Pruning runs once per process, before the first write. | Keeps the file small with no background job. If the server and the CLI prune at the same moment, an entry can rarely be lost. That is accepted for a local demo tool. |
| LOG-6 | Crashes log the error type only, not the message. | Exception messages can contain data values. |
| LOG-7 | A Bearer token now takes priority over `user:password@` in a URL. | httpx turns URL credentials into a Basic login that silently replaced the JWT. Found while testing the log. |

## Data guard

| # | Decision | Why |
|---|---|---|
| GUARD-1 | The guard checks data **after the tool returns it and before the model sees it**, using the Data Quality Checker unchanged. | One set of rules and one checker for both the manual check and the agent. The guard finds exactly the same issues as `lint-data` (tested). |
| GUARD-2 | The default `on_fail` is `block`. | A guard that lets bad data through by default protects nothing. `warn` and `filter` are opt-in per source. |
| GUARD-3 | The guard **fails closed**: unreadable data, an unknown source, or a tool result it can't read is blocked and logged with status `error`. | Matches the main spec: an ERROR is never counted as a PASS. |
| GUARD-4 | What the agent sees contains only codes, counts and column names from the rules, **never cell values or column names taken from the data**. | A bad cell or a made-up column name could carry a prompt injection (main spec C-09). The full report stays on `GuardResult.report`. |
| GUARD-5 | The block message tells the model not to guess and to tell the user the data couldn't be used. | A blocked tool call otherwise invites the model to make up an answer (main spec C-05). |
| GUARD-6 | `filter` blocks instead when a column is missing (it can't be fixed by removing rows) or when more than `max_bad_rows_pct` (default 10%) of rows are bad. | Removing most rows would leave answers that look complete but aren't. |
| GUARD-7 | An empty list of records is allowed. | "No orders today" is a normal answer with no bad rows. Blocking it would break ordinary use. |
| GUARD-8 | Every config and rules problem is found when the guard starts (`from_config`, `guard-validate`), and `columns` that have no rules are errors, not warnings. | A mistake should stop the agent from starting, not show up mid-conversation. A typo in `columns` would otherwise silently skip a check. |
| GUARD-9 | Rules are read at startup. `guard.reload()` reads them again; if the new files are invalid, the old rules stay in use. | Simple and predictable. Watching files for changes can be added later if needed. |
| GUARD-10 | LangGraph support uses `ToolNode(wrap_tool_call=...)` (langgraph-prebuilt 1.0+). LangGraph is an optional extra, and core guard code imports no framework. | One line in the graph, no change to the tools. Other frameworks can use `@guard.protect`. Follows main spec rule 8. |
| GUARD-11 | `warn` and `filter` hand on the **parsed** JSON, and `allow` hands on the tool output unchanged. | Wrapping JSON text inside `{"data": "..."}` would double-encode it. Leaving `allow` untouched means a passing check changes nothing. |
| GUARD-12 | `tests/dq/test_server.py` forwards fetch requests to the demo API by hand instead of reusing the TestClient transport. | LangGraph brings in `httpx2`, which Starlette's TestClient then uses. Its transport doesn't work with the `httpx` client that `fetch.py` uses. Only the test changed. |
| GUARD-13 | Guard checks log the **position** of each issue (row, column, code) and the rules used, but never values. Only the first 100 issues are logged. | `explain` needs to say which rows failed and which rule they broke after the fact. Positions and rules aren't data, and the cap keeps one blocked call to a few KB of log. |
| GUARD-14 | The block message asks the model to give the user the `run_id`. `probagent explain` with no run id shows the latest check that wasn't a clean pass. | The user only sees the agent's chat, so the reference is how a block reaches whoever can fix it. Running it with no id covers the common case: "what just happened?" |
