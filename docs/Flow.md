# Flow.md — *how execution actually travels*

> Companion to [Decision.md](Decision.md) (why the code looks like this) and the
> repo's [README.md](../README.md) (what it does).
>
> This file answers: **who calls what, in what order, through which files and modules**,
> and **where the code you are editing right now plugs into that travel.**

Read it as three maps:
**§1** the five ways in · **§2** the layers · **§3–§9** the traces · **§13** your change.

---

## 1. The five ways into Probagent

Everything the program does starts at one of these five points.

| # | Entry point | Trigger | Defined in |
|---|---|---|---|
| 1 | `probagent` console script | You type a command | `pyproject.toml:24` → `src/probagent/cli.py:30` |
| 2 | `probagent dq-serve` | Starts a web server | `cli.py::serve_command` → `uvicorn.run(create_app(...))` |
| 3 | `GET /` | You open the page | `dq/server.py:144` → serves `dq/static/index.html` |
| 4 | Your agent's tool call | LangGraph / `@guard.protect` | `guard/langgraph.py:30` or `guard/core.py:209` |
| 5 | `python` importing the library | Reuse `check_dataset` yourself | `dq/validator.py:35` |

Every one of them eventually passes through the **same checker**:
`dq/validator.py::check_dataset`. That is the design's central point (decision GUARD-1).

---

## 2. The layers — who is allowed to import whom

```
                         ┌────────────────────────────────────────┐
 entry points             │ cli.py     dq/server.py   guard/*      │
 ───────────────────────►│            (static/index.html)        │
                         └───────────────┬────────────────────────┘
                                         │
             ┌───────────────────────────┼───────────────────────────┐
             ▼                           ▼                           ▼
   ┌──────────────────┐        ┌──────────────────┐        ┌──────────────────┐
   │ get the data     │        │ read the rules   │        │ the run log      │
   │ loader.py        │        │ rules.py         │        │ runlog.py        │
   │ fetch.py         │        │ models.py        │        │ dq/log_schema.py │
   │                  │        │ infer.py         │        │ guard/log_schema │
   └────────┬─────────┘        └────────┬─────────┘        └──────────────────┘
            │  Dataset                  │  Ruleset
            └────────────┬──────────────┘
                         ▼
              ┌────────────────────┐        ┌──────────────────────────┐
              │ types.py           │◄───────│ validator.py             │
              │ text → typed value │        │ THE CHECK (the heart)    │
              └────────────────────┘        └───────────┬──────────────┘
                                                        │ Report
        ┌───────────────────────────┬───────────────────┴──────────┐
        ▼                           ▼                              ▼
  cli: print table          server: JSON → page          guard: allow/block/warn/filter
                                                        (+ run log + ToolMessage → model)
```

**The rules of the layering**

- `settings.py` is imported by everyone and imports nobody. It holds the 14 limits.
- `models.py` holds shapes only. No logic, no I/O.
- `validator.py` never does I/O. It takes a `Dataset` + `Ruleset` and returns a `Report`.
  That is why the same function serves the web page, the CLI **and** the agent guard.
- `guard/core.py` imports *from* `probagent.dq.*` (loader, validator, log_schema) — the
  guard is a thin policy layer **on top of** the checker, not a second checker.
- `guard/langgraph.py` is the only file that imports LangGraph. `guard/core.py` imports
  no framework at all (parent spec §0.8).

---

## 3. Trace A — `probagent lint-data file.csv --rules rules.json`

The shortest complete path. Use this one to learn the shapes.

```
 probagent (console script)
   │
   └─► cli.main()                                  cli.py:30
         argparse → dispatch → lint_command()      cli.py:85
         │
         └─► with RunLog().track("dq","check",origin="cli") as run:      runlog.py:143
               │  Run.__init__ checks the action was registered  ──────► dq/log_schema.py:94
               │      ("dq","check") → CheckInputs / CheckOutputs            register_action()
               │
               ├─► load_csv_bytes(bytes, name)          dq/loader.py:29
               │     _decode()          utf-8-sig, else cp1252 + warning
               │     _detect_delimiter() csv.Sniffer → , ; tab
               │     csv.reader(...)    every cell stays TEXT
               │     _check_header()    ≤200 cols, no blanks, no duplicates
               │     _check_row_count() ≤50,000 rows
               │     pad/cut short or long rows  →  warning
               │     ► Dataset(columns, rows, source, sha256)
               │
               ├─► parse_ruleset(json)                 dq/rules.py:29
               │     Ruleset.model_validate()   ──────► models.py:53  (Pydantic, extra="forbid")
               │     _friendly_message() per error       rules.py:135
               │     check_field_rule() per column       rules.py:50
               │        (min≤max, pattern compiles, min/max fit the type, …)
               │     ► Ruleset   — or raises RulesetError
               │
               ├─► run.set_inputs(**check_inputs(...))  dq/log_schema.py:118
               │
               ├─► check_dataset(dataset, ruleset, columns)      validator.py:35
               │     ├─ find_issues(...)                          validator.py:48
               │     │    ├─ check_columns()   missing/unexpected column
               │     │    └─ for each row:
               │     │         duplicate-row test → continue
               │     │         for each checked column:
               │     │            check_cell()  ──────────► types.py:parse_value()
               │     │            check_unique()  (only if the cell was otherwise fine)
               │     └─ build_report(...)                        validator.py:208
               │          sort → counts → cut to 500 (MAX_ISSUES_SHOWN)
               │     ► Report
               │
               └─► run.set_outputs(**check_outputs(report))       dq/log_schema.py:126
             │
       ╰──── finally: ────► RunLog.write(RunEntry)                runlog.py:173
                            _prune() once → append 1 line → .probagent/runs.jsonl
         │
         ├─► format_report(report) → stdout                      cli.py:159
         └─► return 0 (clean) | 1 (issues) | 3 (invalid)
```

---

## 4. Trace B — the web page

```
 Browser                          FastAPI (dq/server.py)              Engine
 ───────                          ────────────────────              ───────
 pick CSV
   └ POST /api/data/upload ────► upload()                          server.py:147
                                 ├ read_upload(limit+1)            server.py:116
                                 ├ with run_log.track("dq","upload")
                                 ├ load_csv_bytes() ──────────────► loader.py:29
                                 ├ store.add(dataset)  ──────────► DatasetStore (in memory)
                                 └ set_outputs(load_outputs(...))
        ◄──── {dataset_id, columns, row_count, preview, warnings}
 showLoadedData() → draws checkboxes, enables Run

 (400 ms after you stop typing)
   └ POST /api/rules/validate ─► validate() ────► parse_ruleset()   server.py:223
        ◄──── {ok, errors}      ← NOT logged (LOG-3)

 click "Generate rules"
   └ POST /api/rules/infer ────► infer()                          server.py:181
        (multipart: file OR dataset_id, columns repeated)         ← DQ-3
                                 ├ load_csv_bytes()  or store.get()
                                 └ infer_ruleset(dataset, columns) ─► infer.py:29
                                       infer_field() → guess_type() → add_min_max()
                                                          → add_text_rules()
                                 ► InferResult(ruleset, notes)
        ◄──── {ruleset, notes}   → editor + notes list

 click "Run check"
   └ POST /api/check ──────────► check()                          server.py:235
        {dataset_id, ruleset, columns}
                                 ├ store.get()  (404 if expired)
                                 ├ parse_ruleset()   or 400 invalid_rules
                                 ├ run.set_inputs(check_inputs(...))
                                 ├ check_dataset() ───────────────► validator.py:35
                                 └ run.set_outputs(check_outputs(...))
        ◄──── Report
 showReport() → summary cards, per-column chips, filterable table
 click Print → window.print()  (+ @media print CSS; browser offers Save as PDF)
```

**Order matters here:** the page will not enable **Run check** until there is a dataset
**and** the server has validated the rules **and** at least one column is ticked
(`updateRunButton()`, `index.html:585`).

---

## 5. Trace C — REST fetch with the JWT retry

```
 fetchFromApi(null)                       index.html:387
   └ POST /api/data/fetch {url, data_path, token:null}
      └ fetch() (sync def → worker thread)              server.py:164
         └ fetch_dataset(url, data_path, token)         fetch.py:45
              ├ check_url()          http/https only
              ├ get_url(url, token)                     fetch.py:86
              │     for hop in 0..MAX_REDIRECTS(3):                ← DQ-12, manual
              │        headers = {"Accept": "application/json"}
              │        if token and origin_of(current) == original_origin:
              │            headers["Authorization"] = "Bearer …"   ← same host only
              │            current_url = without_login(current_url) ← LOG-7
              │        if redirect → next hop; else return (status, read_limited(body))
              │                     read_limited stops mid-stream at MAX_FILE_BYTES
              ├ status in (401,403) and no token → FetchError("auth_required")
              ├ status in (401,403) with token  → FetchError("auth_failed")
              ├ not 2xx                          → FetchError("fetch_failed")
              ├ json.loads(body)                 → FetchError("fetch_failed") if not JSON
              ├ follow_data_path(data, "data.items")    fetch.py:160
              ├ records_to_dataset(records) ───────────────► loader.py:101
              │     _flatten()  nested dicts → "a.b", lists → JSON text, null → ""
              └ dataset.sha256 = sha256(raw body)

 401/403 ──► page reveals the token box (only then) ──► paste token ──► retry once
 $("api-token").value = ""            ← cleared immediately, never in localStorage
```

The token exists for exactly one `get_url` call. It is never stored, never logged
(`token_sent: true` is logged, the token is not), never returned.

---

## 6. Trace D — rule inference

```
infer_ruleset(dataset, columns)                      infer.py:29
 ├ sample_rows = rows[:INFER_SAMPLE_ROWS=1000]  + a note if the file is longer
 └ for each column in dataset.columns (skipping unticked ones):
      infer_field(values)                             infer.py:52
        ├ required = (no empty value in the sample)
        ├ guess_type(filled)                          infer.py:90
        │    boolean → integer → number (each needs ≥95% match)
        │    → guess_date_format()  tries all 5 formats, keeps the best,
        │      warns when DD/MM and MM/DD tie
        │    → datetime → fallback "string"
        ├ add_min_max()    only for integer/number   ← DQ-13: never for dates
        ├ add_text_rules() string only                infer.py:136
        │    named pattern? → allowed list (≤10 values, ≤50%) → fixed length if uniform
        └ unique = true   only for integer/string, ≥10 values, zero repeats   ← DQ-7
      every step that could over-restrict adds a note
 ► InferResult(ruleset, notes)  → the page shows the notes next to the editor
```

---

## 7. Trace E — the data guard inside LangGraph  ← *the interesting one*

### 7a. Startup, once

```
DataGuard.from_config("guard.json")                    guard/core.py:104
 └ load_config(path)                                    guard/config.py:76
    ├ read_text → json.loads
    ├ build_sources(data, base_dir = guard.json's folder)    config.py:89
    │    GuardConfig.model_validate()   ← SourcePolicy, extra="forbid"
    │    for every source:
    │       load_source()                                       config.py:114
    │         ├ read rules file (path is RELATIVE to guard.json)
    │         ├ parse_ruleset() ──────────────────────► dq/rules.py:29
    │         ├ cross-checks: data_path with format:"csv" → error
    │         │              empty columns list        → error
    │         │              columns with no rules     → error  ← GUARD-8 (a typo must
    │         │                                                          never skip a check)
    │         └ GuardedSource(name, policy, ruleset, ruleset_sha256)
    │    problems from ALL sources are collected → one GuardConfigError
    └ DataGuard(sources, run_log, config_path)

ToolNode([...], wrap_tool_call=guard_tool_calls(guard, {"get_orders": "orders"}))
 └ guard_tool_calls()                                  guard/langgraph.py:30
    ├ check_tool_sources()   ← raises NOW if a mapped source is not in guard.json
    └ returns the closure
```

### 7b. Every tool call, at runtime

```
LangGraph calls wrap_tool_call(request, execute)
 └ guard_message(guard, map, request, execute(request))     langgraph.py:58
    │                         ▲
    │                         └── ① THE TOOL RUNS FIRST, and may fail
    ├ source = map.get(tool_call["name"])
    │    unknown tool → return the result UNTOUCHED (normal, unguarded)
    ├ ToolMessage with status "error" → return as-is; there is no data to check
    └ else guard.check(result.content, source)              guard/core.py:121
         with run_log.track("guard","check", origin="agent") as run:
           _check(data, source, run)                         guard/core.py:139
             ├ GuardedSource lookup → "unknown_source" if absent
             ├ read_data(content, guarded)                   guard/core.py:261
             │    format "csv" → load_csv_bytes() ────────► dq/loader.py:29
             │    format "json"→ json.loads
             │                  walk data_path  ("data" → data["data"])
             │                  records_to_dataset() ──────► dq/loader.py:101
             │                  dataset.sha256 = fingerprint(data)
             │    any failure → _error() → verdict "error", action "block"  ← fails closed
             ├ no rows at all → pass/allow                  ← GUARD-7 ("no orders today")
             ├ find_issues(dataset, ruleset, policy.columns) ─► dq/validator.py:48
             ├ build_report(...)                              ─► dq/validator.py:208
             ├ DECIDE:
             │    no issues                      → allow  (data unchanged)
             │    on_fail == "warn"              → warn   (parsed JSON + note)
             │    on_fail == "filter" and no dataset-level issue
             │        and bad_pct ≤ 10%         → filter (keep_rows())
             │    otherwise                      → block  ← also covers
             │                                            a "filter" that is unsafe
             ├ run.set_outputs(... first 100 IssueRef = row/column/code only)
             ├ safe_summary()                     guard/core.py:315
             │    strips column names that are NOT in your rules  ← GUARD-4
             └ GuardResult(source, verdict, action, data, report, summary)
         result.run_id = run.run_id

    GuardResult.for_agent()                           guard/core.py:82
      allow  → the original data, untouched             ← GUARD-11
      warn   → {"data": …, "quality_warning": {…, "run_id": …}}
      filter → {"data": <clean rows only>, "quality_note": {…, "rows_removed": n}}
      block  → {"blocked": true, "issue_count": n, "by_code": {…}, "by_column": {…},
                "message": "Do not guess … give them the run_id", "run_id": …}

    returns ToolMessage(content=json.dumps(that), status="success"|"error")
    └────────────► back into the graph → the MODEL sees only this

    (contextmanager finally: one JSON line appended to .probagent/runs.jsonl)
```

**Note the asymmetry:** `report` (full, with values) lives on the `GuardResult` for humans.
`for_agent()` is the only thing the model receives, and it contains no cell values.

**Note `keep_rows()`** (`guard/core.py:247`): with `filter`, bad rows are removed from the
data *in its original shape* — CSV is rebuilt as CSV text, JSON is rebuilt by replacing
the list at `data_path` (via `replace_at_path`), so the model's tool output shape does not
change underneath it.

---

## 8. Trace F — `probagent explain`

```
probagent explain [RUN_ID]                            cli.py:143
 └ explain_command()
    ├ RunLog().read()   parse every line into RunEntry  runlog.py:186
    ├ find_entry(entries, run_id)                        guard/explain.py:32
    │    with an id   → that guard/check entry
    │    without one  → the LAST check whose status is error OR action != allow
    └ explain(entry)                                     guard/explain.py:41
         prints: source, rules + sha, on_fail, columns, row/col counts
         for each of the ≤100 IssueRefs:
             row + column + code + describe(code, column, rule)   explain.py:101
             e.g. pattern_mismatch → 'does not match "pattern": email.'
         and the closing note: values are never logged; run lint-data to see them
```

`explain` reads **only** the log. It re-runs nothing. That is the point: the row numbers
are the ones from the moment the agent was blocked.

---

## 9. Trace G — `probagent guard-validate guard.json`

```
cli.guard_validate_command(config)                     cli.py:121
 └ load_config(config)            ← EXACTLY the loader the guard uses
    on GuardConfigError → print every problem → exit 3
    on success → list each source: rules name, field count, format, on_fail, columns
    exit 0
```

---

## 10. The run log, from the caller's point of view

One JSON line per action, written by `RunLog.track()` in a `finally:` block — so a crash
still gets a line, with only the exception **type** (LOG-6).

| Who calls `track()` | feature/action | Where |
|---|---|---|
| upload endpoint | `dq` / `upload` | `server.py:151` |
| fetch endpoint | `dq` / `fetch` | `server.py:168` |
| infer endpoint | `dq` / `infer` | `server.py:193` |
| check endpoint | `dq` / `check` | `server.py:240` |
| `lint-data` | `dq` / `check` | `cli.py:90` |
| `DataGuard.check()` and `fail_closed()` | `guard` / `check` | `guard/core.py:123,133` |
| rule validation | — | **never logged** (LOG-3) |

**The import-time dependency that makes this work:** `Run.__init__` (`runlog.py:106`)
raises `ValueError` for an unregistered action. Registration happens when
`probagent.dq.log_schema` (four calls) and `probagent.guard.log_schema` (one call) are
**imported**. Every caller imports one of those modules at the top, which is why
`run.set_inputs(...)` with an unexpected field blows up immediately instead of writing
something secret to disk.

---

## 11. The heart, in pseudo-code — `validator.py::find_issues`

If you read one file, read this one.

```
column-level issues = for each rule column missing from the data → "missing_column"
                      (plus "unexpected_column" if allow_extra_columns is false)

for each row (numbered from 1):
    if the whole row was seen before:          ← compares the FULL row, DQ-15
        add "duplicate_row" and CONTINUE      ← its cells were already checked, DQ-6
    remember this row

    for each checked column:
        issue = first of:                       ← DQ-5: at most ONE issue per cell
            missing      → if required
            wrong_type   → parse_value() failed          (types.py)
            bad_date     → looks like the format but is impossible
            below_min / above_max
            too_short / too_long
            not_allowed
            pattern_mismatch
        if issue is None and rule.unique:
            issue = "duplicate_value" if this value appeared earlier in this column
        if issue is not None: add it

build_report(): sort by row → count by column and by code → keep the first 500
                (counts are over ALL issues; the list is capped)   ← MAX_ISSUES_SHOWN
```

---

## 12. Where values and secrets stop

One table, because it is the single most important invariant in the codebase.

| Stage | Values? | Notes |
|---|---|---|
| Loaded into `Dataset` | **Yes**, all as text | Never converted |
| In `Report.issues[].value` | Yes, cut to 100 chars | For humans, on screen and in `lint-data` |
| In the web page DOM | Yes | via `textContent`, never `innerHTML` |
| In `.probagent/runs.jsonl` | **No** | only sha256, row/column/code |
| In what the **model** sees | **No** | `safe_summary()` strips data-derived names (GUARD-4) |
| Tokens | **Nowhere** | used for one request, cleared, never logged |

---

## 13. What is being modified right now — and where it enters the flow

### 13.1 The actual state of the working tree

```
git log  →  803a46a  "Initial commit: Probagent data quality checks and agent guard"
git status →  ?? demo/          ← the only uncommitted change
```

So the work in flight is the **`demo/` folder**:

| File | What it is |
|---|---|
| `demo/make_demo_data.py` | Generator (seeded `random.Random(7)`, so output is identical every run) |
| `demo/customers_clean.csv` | 50 rows, 0 issues |
| `demo/customers_partial.csv` | 50 rows, 3 issues |
| `demo/customers_dirty.csv` | 51 rows, 17 issues (every issue kind, plus a duplicate row) |
| `demo/customers_rules.json` | Hand-written rules for all three files |
| `demo/README.md` | Regenerated **by the script**, including the exact expected issue tables |

**Where it enters execution: nowhere in `src/`.** Verified — nothing in `src/probagent/`
references `demo/`. It is **input data**, consumed at exactly two points in the flow:

| You edit… | What actually changes in the flow | Where |
|---|---|---|
| `customers_rules.json` | Step 5 of Trace A / §4 | `dq/rules.py::parse_ruleset` — a bad rule fails here, exit code 3 |
| a `.csv` | Step 4 of Trace A / §4 | `dq/loader.py::load_csv_bytes`, then `validator.py` |
| `make_demo_data.py` | **No running code changes at all.** It rewrites the CSVs *and* `demo/README.md` | — |
| `customers_dirty.csv` and the "Issues in …" table disagree | Nothing catches that automatically. The README is generated, so re-run the script: `python demo/make_demo_data.py` |

Then the data flows through the rest of Trace A untouched:
`check_dataset` → `find_issues` → `Report` → `format_report` → exit code.

**The 17 dirty issues deliberately cover every branch in §11**: `wrong_type`,
`missing_value`, `pattern_mismatch` (named *and* custom regex), `below_min`, `above_max`,
`not_allowed`, `bad_date`, `too_short`, `duplicate_value` (twice), and `duplicate_row`.
That is what makes the demo a smoke test of the validator itself.

### 13.2 Hot-spot map — "I am changing X, which files do I touch?"

| Change | Files, **in this order** | Tests that prove it |
|---|---|---|
| A new **rule** (e.g. a `not_empty_string` check) | `models.py` (field) → `rules.py` (validation) → `types.py` (helper) → `validator.py::check_cell` → `index.html` (editor UI) → `README.md` table → `docs/decisions.md` | `tests/dq/test_validator.py`, `test_rules.py`, `test_types.py`, `test_column_selection.py` |
| A new **issue code** | `validator.py` → `guard/explain.py::describe` → `README.md` | `tests/dq/test_validator.py`, `tests/guard/test_explain.py` |
| The **check order** (which rule fires first) | `validator.py::check_cell` only — but it invalidates `fixtures/dq/DQ_ISSUES.md` and `demo/README.md` | `tests/dq/test_validator.py`, `tests/dq/test_column_selection.py` |
| A new **guard policy** (`on_fail` value) | `guard/config.py::SourcePolicy` → `guard/core.py::_check` → `GuardResult.for_agent` → `guard/explain.py` → README | `tests/guard/test_core.py`, `test_explain.py` |
| A new **agent framework** | one new file in `guard/` + one line in `guard/__init__.py`. **Never** touch `core.py` | new `tests/guard/test_<framework>.py` |
| A new **run-log action** | `dq/log_schema.py` or `guard/log_schema.py` (`register_action`) → the caller → `docs/run_log.md` | `tests/dq/test_run_logging.py`, `tests/test_runlog.py` |
| A new **endpoint** | `dq/server.py` → `index.html` JS | `tests/dq/test_server.py` |
| A **limit** | `dq/settings.py` **only** | — |
| A **demo fixture** | `fixtures/dq/make_fixtures.py` → `fixtures/dq/DQ_ISSUES.md` | — |

### 13.3 Walk one change end to end

*"Add a rule that rejects values containing the substring `TEMP`."*

```
1. models.py       add the field to FieldRule
2. rules.py        make sure it only works on type "string"     (check_field_rule)
3. types.py        (maybe) the actual test, next to matches_pattern
4. validator.py    add the check in check_cell, in the right position
                   ← POSITION IS THE DECISION: DQ-5 allows one issue per cell,
                     so before or after pattern_mismatch changes which code you see
5. guard/explain.py::describe   so `probagent explain` can describe it
6. index.html      a row in the rules editor UI
7. README.md       the rules table
8. docs/decisions.md   a row explaining the position you chose
9. tests           test_validator.py (the new code), test_rules.py (the validation),
                   test_explain.py (the text), and regenerate any fixture whose
                   expected-issue list now differs
```

---

## 14. Quick file index

| File | Role in the flow |
|---|---|
| `cli.py` | Entry point 1/2/5; `lint-data`, `dq-serve`, `guard-validate`, `explain` |
| `dq/server.py` | Entry points 2/3; the 5 endpoints + the demo APIs |
| `dq/static/index.html` | The whole UI; every fetch call the page makes |
| `dq/loader.py` | bytes / JSON records → `Dataset` |
| `dq/fetch.py` | URL → bytes, with the manual redirect + token rules |
| `dq/types.py` | text → typed value, or `None`; named patterns; date formats |
| `dq/rules.py` | JSON → `Ruleset`, with beginner-friendly errors |
| `dq/infer.py` | `Dataset` → guessed `Ruleset` + notes |
| `dq/validator.py` | **`Dataset` + `Ruleset` → `Report`. The centre of everything.** |
| `dq/models.py` | `Dataset`, `Ruleset`, `FieldRule`, `Issue`, `Report` |
| `dq/settings.py` | The 14 limits, and nothing else |
| `runlog.py` | `track()` → one JSON line; `register_action` registry |
| `dq/log_schema.py` | What `dq` is allowed to log; the four `register_action` calls |
| `guard/config.py` | `guard.json` → validated `GuardedSource`s |
| `guard/core.py` | `DataGuard.check`, the policy decision, `for_agent()` |
| `guard/langgraph.py` | The `ToolNode(wrap_tool_call=…)` adapter |
| `guard/explain.py` | A log line → a human-readable reason |
| `guard/log_schema.py` | What `guard` is allowed to log |