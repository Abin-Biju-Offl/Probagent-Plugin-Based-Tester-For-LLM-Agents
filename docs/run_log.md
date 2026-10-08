# Run Log

Every action Probagent runs is recorded as one line of JSON in:

```
.probagent/runs.jsonl
```

The folder is created inside the folder you start Probagent from. To use another folder, set the environment variable `PROBAGENT_LOG_DIR`. Entries are only ever added to the end, so the file is in time order, oldest at the top.

Code: [src/probagent/runlog.py](../src/probagent/runlog.py) (shared by all features) and [src/probagent/dq/log_schema.py](../src/probagent/dq/log_schema.py) (the data quality feature's entries).

---

## 1. The envelope (the same for every feature)

```json
{
  "schema_version": 1,
  "run_id": "20261007-143022-a1b2c3",
  "parent_run_id": "20261007-142950-f9e8d7",
  "feature": "dq",
  "action": "check",
  "origin": "web",
  "status": "ok",
  "started_at": "2026-10-07T14:30:22+05:30",
  "finished_at": "2026-10-07T14:30:22+05:30",
  "duration_ms": 41,
  "app_version": "0.1.0",
  "inputs": { "...": "depends on feature + action" },
  "outputs": { "...": "depends on feature + action" },
  "error": null
}
```

| Field | Meaning |
|---|---|
| `schema_version` | Version of this envelope. Raise it only when the envelope changes in a way that breaks readers. |
| `run_id` | Unique id: date, time, then 6 random characters. |
| `parent_run_id` | The run this one builds on, for example the upload whose data was checked. `null` if none. |
| `feature` | Which part of Probagent ran it, for example `dq`. |
| `action` | What it did within that feature, for example `check`. |
| `origin` | `web` (the page), `cli` (the terminal), or `agent` (a guarded tool call). |
| `status` | `ok` means the action finished, even if it found data issues. `error` means it could not finish. |
| `started_at` / `finished_at` | Local time with UTC offset. |
| `duration_ms` | How long it took, in milliseconds. |
| `app_version` | Probagent version that ran it. |
| `inputs` / `outputs` | What went in and what came out. Their shape is fixed per feature + action (see section 2). |
| `error` | `{"code": "...", "message": "..."}` when `status` is `error`, otherwise `null`. |

**`status` vs. findings:** a check that finds 13 issues has status `ok`, because it ran correctly. The findings are in `outputs`. `error` is only for "couldn't run", such as a bad file, invalid rules, or an API that refused the login. This keeps "the data is bad" separate from "the tool failed".

### What is never logged

- Tokens, passwords, and `Authorization` headers.
- Data values. Data is described by name, row and column counts, and a SHA-256 fingerprint of its bytes.
- URL query strings and `user:password@` parts, because API keys often hide there.
- Messages from unexpected crashes. Only the error type is logged, for example `KeyError`.

Every inputs/outputs model uses `extra="forbid"`, so a field not in the schema raises an error instead of being written.

---

## 2. Data quality (`feature: "dq"`)

| Action | Origin | Logged when | `parent_run_id` |
|---|---|---|---|
| `upload` | web | A CSV is uploaded | — |
| `fetch` | web | Data is fetched from a REST API | — |
| `infer` | web | Rules are generated from a sample | The upload/fetch, if it used loaded data |
| `check` | web, cli | Data is checked against rules (web page or `lint-data`) | The upload/fetch (web only) |

Rule validation is **not** logged. The page runs it automatically while you type.

### `dq/upload`
```json
"inputs":  { "file_name": "orders.csv", "size_bytes": 14820, "sha256": "9f2c…" },
"outputs": { "dataset_id": "…", "dataset": { "name": "orders.csv", "sha256": "9f2c…", "rows": 201, "columns": 8 }, "warnings": 0 }
```

### `dq/fetch`
```json
"inputs":  { "url": "https://api.example.com/orders", "data_path": "data", "token_sent": true },
"outputs": { "dataset_id": "…", "dataset": { "name": "https://api.example.com/orders", "sha256": "…", "rows": 201, "columns": 8 }, "warnings": 0 }
```
Error codes: `auth_required`, `auth_failed`, `fetch_failed`.

### `dq/infer`
```json
"inputs":  { "sample": { "name": "orders_clean.csv", "sha256": "…", "rows": 200, "columns": 8 }, "rows_used": 200,
             "columns": null },
"outputs": { "ruleset_sha256": "…", "fields": 8, "notes": 14 }
```

### `dq/check`
```json
"inputs":  { "dataset": { "name": "orders_dirty.csv", "sha256": "…", "rows": 201, "columns": 8 },
             "ruleset_name": "orders", "ruleset_sha256": "77ab…", "ruleset": { "...": "the full rules" },
             "columns": ["email", "quantity"] },
"outputs": { "issue_count": 13, "rows_with_issues": 13, "clean_rows_pct": 93.5,
             "by_code": { "missing_value": 2, "wrong_type": 2 }, "truncated": false }
```
Error codes: `dataset_not_found`, `invalid_rules`, `no_columns`, `load_failed`, `file_not_readable`, `rules_not_json`.

`columns` lists the ticked columns. `null` means all columns were used.

`ruleset_sha256` stays the same when only the JSON formatting or key order changes. If two checks have the same `dataset.sha256` and `ruleset_sha256`, they ran on exactly the same data with exactly the same rules.

---

## 3. Data guard (`feature: "guard"`)

| Action | Origin | Logged when | `parent_run_id` |
|---|---|---|---|
| `check` | agent | A guarded tool returned data, before the agent saw it | — |

### `guard/check`
```json
"inputs":  { "source": "orders",
             "dataset": { "name": "orders", "sha256": "d208…", "rows": 201, "columns": 8 },
             "ruleset_name": "orders", "ruleset_sha256": "6fad…", "ruleset": { "fields": { … } },
             "on_fail": "block", "columns": null },
"outputs": { "verdict": "fail", "action": "block", "issue_count": 13, "rows_with_issues": 13,
             "rows_removed": 0, "by_code": { "missing_value": 2, "wrong_type": 2 },
             "issues": [ { "row": 12, "column": "email", "code": "pattern_mismatch" }, … ],
             "issues_truncated": false }
```
`issues` holds the first 100 issues, by position only (row, column, code), never their values. `probagent explain RUN_ID` reads them back as a readable report.
`verdict` is `pass` or `fail`. `action` is what the agent got: `allow`, `block`, `warn` or `filter`.

Error codes: `unknown_source`, `unreadable_data`, `load_failed`, `data_path_not_found`, `unsupported_tool_result`. A check with status `error` has no `outputs`. Its data was **blocked**, because the guard fails closed.

`dataset.sha256` is the SHA-256 of the raw text when the tool returned text, or of a stable JSON form of the records when it returned Python objects.

---

## 4. Finding what ran

Each line is a complete JSON object, so ordinary tools work. Python ships with every install, so these examples use it.

```bash
# Every failed action
python -c "import json; [print(l, end='') for l in open('.probagent/runs.jsonl') if json.loads(l)['status']=='error']"

# Every check, with its issue count
python -c "import json; [print(e['run_id'], e['inputs'].get('dataset',{}).get('name'), e['outputs'].get('issue_count')) for e in map(json.loads, open('.probagent/runs.jsonl')) if e['action']=='check']"

# Everything that happened on one day (run ids start with the date)
grep '"run_id":"20261007-' .probagent/runs.jsonl

# Everything linked to one upload: the upload itself and the runs built on it
grep 20261007-142950-f9e8d7 .probagent/runs.jsonl
```

From Python code:

```python
from probagent.runlog import RunLog

for entry in RunLog().read():          # RunEntry objects, oldest first
    if entry.feature == "dq" and entry.status == "error":
        print(entry.run_id, entry.action, entry.error.code)
```

---

## 5. Retention

When Probagent starts writing, it first removes entries older than **90 days**, then keeps only the newest **10,000**. The file is only rewritten when something needs removing. Both limits are at the top of `runlog.py`.

---

## 6. Adding a new feature to the log

1. Create `<feature>/log_schema.py` with one Inputs and one Outputs model per action, using `extra="forbid"`.
2. Register each action once:
   ```python
   register_action("eval", "run", EvalRunInputs, EvalRunOutputs)
   ```
3. Wrap the action:
   ```python
   with run_log.track("eval", "run", origin="cli", parent_run_id=...) as run:
       run.set_inputs(...)
       ...                                  # do the work
       run.set_outputs(...)                 # or run.fail("code", "message")
   ```
   A crash inside the block is still logged (as `unexpected_error`) and then raised as normal. If the log can't be written, a warning is printed and the action carries on.
4. Add a section for the feature to this document, and a test that checks nothing secret reaches the log.

**Changing a schema later:** adding a new optional field is safe. Renaming or removing a field, or changing its meaning, breaks searches people already rely on. In that case, add a new field instead, or raise `schema_version`.
