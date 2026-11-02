# Decision.md — *why this code looks like this*

> Companion to [decisions.md](decisions.md) (the log of *what* was decided) and
> [Flow.md](Flow.md) (how the code actually runs).
>
> This file explains **the thinking**: the rules the coding AI follows when it changes
> this repo, the pattern behind each library and each design choice, and — most
> importantly — **which trade-offs were refused, and why**.
>
> Written to be readable by someone who has never used FastAPI, Pydantic or LangGraph.

---

## 0. The short version

This project was built by an AI from two spec documents:
[Probagent_Spec.md](../Probagent_Spec.md) (the whole product) and
[InnDataQuality_Spec.md](../InnDataQuality_Spec.md) (the data quality feature).

Both specs contain this instruction, and it is the single most important line for
understanding the whole repo:

> **"When something is ambiguous, pick the simplest option, record it in
> `docs/decisions.md`, and continue."**
> — InnDataQuality_Spec §1.7, Probagent_Spec §0.9

So every unusual-looking choice in this codebase comes from that loop. There is no hidden
architecture. If you ever disagree with a choice, you change `docs/decisions.md` and
tell the AI — that is the intended workflow, not an act of sabotage.

---

## 1. The decision loop the AI follows

When asked to change something, the AI walks these six steps. It is worth memorising
because every "why did it do that?" question is answered by one of them.

| Step | What happens | Where you can see it |
|---|---|---|
| **1. Read the spec** | The spec is the boss. If it says MUST, the code must do it. | `Probagent_Spec.md`, `InnDataQuality_Spec.md` |
| **2. Check the decision log** | Maybe this exact question was already answered. Don't re-litigate. | `docs/decisions.md` (DQ-1…16, LOG-1…7, GUARD-1…14) |
| **3. Check the tests** | Tests are the real contract. Changing code that contradicts them means changing the behaviour on purpose. | `tests/` |
| **4. Pick the simplest option** | Smallest code that satisfies the spec, with no new machinery. | — |
| **5. Record the choice** | New row in `docs/decisions.md`. No silent decisions. | `docs/decisions.md` |
| **6. Prove it** | Tests pass, and a new test exists for new behaviour. | `pytest` |

**Worked example.** You ask: "support CSV files that use semicolons."

* Step 1: the spec says nothing about delimiters → ambiguous.
* Step 2: `docs/decisions.md` has nothing → ambiguous.
* Step 3: `tests/dq/test_loader.py` has nothing → new behaviour.
* Step 4: simplest option = ask the `csv` module to sniff the delimiter. No new
  dependency, no config setting. (`loader.py::_detect_delimiter`, which looks at the
  first 5,000 characters and accepts `,`, `;`, tab.)
* Step 5: the AI would add a row to `docs/decisions.md`.
* Step 6: a test with a semicolon file.

Notice what the AI did **not** do: it did not add a `delimiter` option to the upload form,
or a new library. Simple option.

---

## 2. The trade-off test — why *some* trade-offs are simply not acceptable

"Pick the simplest option" is the default, not the override. Three questions are asked in
this order, and the order is the whole point:

```
  1. Is it SAFE?      -> No  ==> STOP. This trade-off is not acceptable.
  2. Is it TRACEABLE? -> No  ==> add a log entry or a test first.
  3. Is it the SMALLEST thing? -> otherwise simplify.
```

### 2.1 Q1 — Safety. These override "simple", every time.

| Rule | Why it is not negotiable | Where |
|---|---|---|
| No `eval()` / `exec()` / `pickle` | User data and rules files are untrusted input. `eval` on them is remote code execution. | Spec §0.5, §0.6 (both specs) |
| Data **values** never go to the model | A dirty cell could contain `"ignore your instructions"`. This is *indirect prompt injection* (challenge C-09). The model gets counts, codes and column names from **your rules** — never text taken from the data. | `guard/core.py::safe_summary`, GUARD-4 |
| Data values never go to the run log | The log is a plain text file people paste into bug reports. | `dq/log_schema.py`, `guard/log_schema.py` |
| Tokens never stored, logged or echoed | One request only. Cleared from the page after every attempt. | `fetch.py`, `index.html:399` |
| A token is never sent to another server | A redirect to an attacker's host must not carry your credentials. | `fetch.py::get_url`, DQ-12 |
| Unreadable → block, never pass | An ERROR is never counted as a PASS (parent spec §0.7). A guard that silently allows what it cannot check protects nothing. | `guard/core.py::_error`, GUARD-3 |
| `extra="forbid"` on every model | A typo (`"requird"`) or an accidental field (`"token"`) raises instead of being quietly accepted. | every Pydantic model |
| Page inserts text with `textContent` | A cell containing `<script>` is displayed, never executed. | `index.html::el`, and `fixtures/dq/orders_dirty.csv` row 140 exists purely to prove it |
| Server binds to `127.0.0.1` | The fetch feature can reach any URL this machine can reach. Do not expose it. | `cli.py:35` |
| Custom regex ≤ 200 chars, values cut to 1,000 chars before matching | Stops a catastrophic-backtracking regex from freezing the app. | `settings.py`, `types.py::matches_pattern` |

### 2.2 Q2 — Traceability

Anything that runs gets a line in `.probagent/runs.jsonl`, including the failures. A
choice that cannot be logged or tested does not ship.

### 2.3 Q3 — Simplicity

Only now does "smallest wins" apply. Concretely, in this repo that means:
no ORM, no database, no frontend build step, no plugin discovery machinery, no async
where a sync call is clearer.

---

## 3. Dependencies: which library, and which one lost

The spec picked the stack. The interesting decisions are the *deviations*.

| Library | Used for | The alternative that was rejected, and why |
|---|---|---|
| **Python ≥ 3.10** | Everything | Spec said 3.11+. `DQ-1`: the dev machine has 3.10 and nothing in the code needs 3.11. |
| **`csv` (standard library)** | Reading CSV | **pandas** — the spec's choice. `DQ-2` rejects it for two reasons: fewer dependencies and code a beginner can follow end-to-end; and pandas does type inference, so it would turn `"007"` into `7` before the checker ever sees it. Since this tool keeps everything as text on purpose (see §4), pandas' main advantage buys nothing here. A small `_flatten` function does the same job for nested JSON. |
| **`json`, `re`, `hashlib`, `datetime`, `threading`** | Everything else | Standard library. No reason to add a dependency for these. |
| **Pydantic v2** | `Dataset`, `Ruleset`, `FieldRule`, `Issue`, `Report`, and every run-log input/output model | Manual `if` validation. Pydantic gives validation, defaults and `extra="forbid"` in three lines, and gives **one** error type to catch. This is the library that makes the whole safety story cheap. |
| **FastAPI + uvicorn** | The web page and the JSON API | Flask/Django. FastAPI parses and validates the request body with the same Pydantic models, so there is no second validation path. |
| **httpx** | Fetching a REST API | `requests`. httpx is async-capable, streams the body (so the 10 MB cap can stop a download **mid-transfer** instead of after it), and has a `transport` injection point, which is how the tests fake an API without a server. |
| **One `.html` file** | The whole UI | React/Vue + npm + a build step. The spec's goal G6 is "few dependencies". `DQ-3` extends the same logic to the API: the rules-infer endpoint takes **form data** because one endpoint cannot cleanly accept both multipart and JSON bodies. |
| **LangGraph** | *Optional*, only in `guard/langgraph.py` | Making it core. `GUARD-10` / parent spec §0.8: "Core code MUST NOT import any specific agent framework." LangGraph lives in an extra (`pip install -e ".[langgraph]"`) and imports from *one* file, so a different framework can use `@guard.protect` with nothing installed. |
| **`pytest`** | Tests | — |

> **Known gap, worth knowing:** the parent spec §13 asks for `ruff` and `mypy` to pass on
> every change, but `pyproject.toml` currently installs only `pytest` in the `dev` extra.
> So today there is no automated lint or type gate. If the AI adds clever or loosely-typed
> code, nothing mechanical will catch it — ask for the extra, or read the diff harder.

### 3.1 The one dependency that bit back

`GUARD-12`: installing LangGraph pulls in a package called `httpx2`, which Starlette's
`TestClient` then picks up instead of `httpx`. Its transport does not speak the protocol
`dq/fetch.py` uses, so `tests/dq/test_server.py` had to forward demo-API requests by hand.

The lesson: a dependency decision has a blast radius beyond the file that imports it.

---

## 4. The one architectural decision everything else follows from

> **Every value stays a string, from the moment it is read until the moment it is reported.**

`Dataset` is `columns: list[str]` + `rows: list[list[str]]` (`dq/models.py`).
`loader.py::_flatten` converts JSON to text. Nothing coerces types on load.

**Why.** Because the tool's job is to *judge* data, not to interpret it. If the loader
converted types, `"2026-13-01"` would either crash or silently become something else
before the rule `format: "YYYY-MM-DD"` ever got a chance to report `bad_date`. Keeping
text means the checker is the only thing that decides what a value means, so every report
line can be traced to one rule.

**What it costs.** You cannot compare values numerically without parsing them, which is
why `dq/types.py` exists — a small module whose whole job is *text → typed value, or
`None`*. It is deliberately not a general-purpose parser.

---

## 5. Validation decisions

| # | Decision | Reasoning |
|---|---|---|
| **DQ-9** | `"1e3"` is a valid `number`; `"1_000"`, `"nan"`, `"inf"` are not. | Python's `float()` happily accepts all three. A regex (`NUMBER_RE`) restricts us to plain written numbers. The rule is *what a human would write in a CSV*, not what Python is willing to coerce. |
| **DQ-5** | A cell gets **at most one** issue. | Order is fixed: missing → type → min/max → length → allowed → pattern → unique. Once a cell fails, deeper checks would be misleading ("quantity" is also "not an integer"?). `validator.py::check_cell` `return`s the first problem. `unique` is applied by the caller only when `check_cell` returned `None`. **This is why the report stays readable.** |
| **DQ-6** | An exact duplicate row is reported **once**, and its cells are not re-checked. | The same cells were already checked on the first copy. Reporting them again would double-count the same problem. `validator.py::find_issues` does `continue`. |
| **DQ-15** | Duplicate-row detection compares the **whole row**, even when you only ticked some columns. | "This row is a copy of that row" is a property of the *data*, not of your selection. Comparing only ticked columns would flag rows that are perfectly fine in the columns you did not select. |
| **DQ-10** | `bad_date` vs `wrong_type` for dates. | `"2026-02-30"` has the shape of a date but is not one → `bad_date` ("you typo'd"). `"March 3rd"` is not written as a date at all → `wrong_type` ("wrong format"). Different problems need different fixes, so different codes. `types.py::looks_like_date_format` does the shape check. |
| **DQ-14** | Ticking a column limits both rule generation *and* the check. Unticked columns' rules are **ignored**, not reported as `missing_column`. Ticked columns that have no rules, or are not in the data, become **warnings**. | Requested by the user. The point: ticking is a *deliberate narrowing*, so it must not be reported as an error — but a silent narrowing would hide mistakes. Warnings keep it honest without failing the check. |
| **DQ-16** | When every column is ticked, the page sends `columns: null`, not the full list. | Same behaviour, and `"columns": null` is instantly readable in the log as "everything". |
| — | Regex is compiled and length-checked at ruleset validation time (`rules.py::_check_pattern`). | A bad regex is a mistake in the *rules*, found before the check runs, with a sentence a beginner can act on. |
| — | `_friendly_message` in both `rules.py` and `guard/config.py` turns Pydantic errors into sentences. | Pydantic's raw error is `fields.email.requird: extra_forbidden`. The user sees `Column "email": unknown rule "requird". Known rules: type, required, unique, min, max, …`. |

---

## 6. Inference decisions — always biased toward *too few* rules

Rule inference guesses rules from a sample CSV. The guiding bias is: **a wrong rule that
is too strict produces false alarms, and a user who gets false alarms stops trusting the
tool.** So every guess that could over-restrict is either omitted or attached to a note.

| # | Decision | Reasoning |
|---|---|---|
| **DQ-7** | `unique` is only set for **integer** and **string** columns. | In a 200-row sample, prices and dates are usually all different — without anyone intending them to be unique. Setting `unique` there would generate noise. `infer.py::infer_field` also requires ≥ 10 values and *zero* repeats before it dares. |
| **DQ-8** | `min_length`/`max_length` only when **every** value has the same length. | Catches 2-letter country codes, does not over-restrict free text like names. |
| **DQ-13** | Date columns get only `type` and `format`. No `min`/`max`. | Requested by the user. A `max` taken from today's sample rejects every record created tomorrow. Hand-written rules may still set date bounds (the `demo/customers_rules.json` file does exactly that). |
| — | 95% of values must match before a type or pattern is accepted (`INFER_MATCH_SHARE`). | One weird row should not turn a whole column into "string". The leftovers get a note: *"5% of values didn't match."* |
| — | `allowed` lists only appear when there are ≤ 10 distinct values *and* they are ≤ 50% of the sample. | Otherwise it is free text, and a 10-item allow-list of an 11th value is a trap. |
| — | Ambiguous `03/04/2026` picks `DD/MM/YYYY` and adds a note. | It cannot be known. Picking one silently would be a lie; picking one with a visible note is a question. (`infer.py::guess_date_format`.) |
| — | Every guess produces **notes** next to the editor. | The spec calls the output "a draft the user MUST review". The notes are what makes review possible. |

---

## 7. Security and privacy decisions

The parent spec lists 25 challenges (C-01…C-25). This feature answers **C-22** (bad data
ingestion) and touches **C-09** (indirect prompt injection) and **C-13** (over-refusal).
Those three explain most of the unusual strictness.

| # | Decision | Reasoning |
|---|---|---|
| **GUARD-4** | What the agent sees contains **codes, counts, and column names from your rules** — never cell values, never column names taken from the data. | The core idea. A bad cell or a fabricated column name could carry `"ignore previous instructions"`. `safe_summary` filters `by_column` through `guarded.ruleset.fields` precisely so data-invented names cannot leak. The full report with values stays on `GuardResult.report` for humans. |
| **GUARD-2** | Default `on_fail` is `block`. | A guard that passes bad data through by default protects nothing. `warn` and `filter` are opt-in per source. |
| **GUARD-3** | The guard **fails closed**. | Unreadable data, unknown source, unsupported tool result → blocked, logged as `error`. Parent spec §0.7: an ERROR is never counted as a PASS. |
| **GUARD-5** | The block message tells the model *"Do not guess or make up the missing data"*. | A blocked tool call otherwise invites the model to invent a plausible answer — which is the exact failure mode of challenge C-05. |
| **GUARD-6** | `filter` **blocks instead** when a column is missing, or when more than `max_bad_rows_pct` (10%) of rows are bad. | Removing 60% of rows leaves answers that look complete but are not. A missing column cannot be fixed by deleting rows. |
| **GUARD-7** | An **empty** list of records is a pass. | "No orders today" is a normal answer, not dirty data. Blocking it would be over-refusal (C-13). |
| **GUARD-8** | Config and rules problems are found at **startup**, and a `columns` entry with no matching rule is an **error**, not a warning. | A typo would otherwise silently skip a check — the worst possible outcome for a guard. A mistake should stop the agent from starting, not appear mid-conversation. |
| **GUARD-9** | Rules are read once at startup; `reload()` re-reads them and keeps the old ones if the new files are broken. | Simple and predictable. File-watching can be added later if it is ever needed. |
| **GUARD-11** | `warn`/`filter` hand on the **parsed** JSON; `allow` hands on the tool output **untouched**. | Wrapping JSON *text* inside `{"data": "..."}` double-encodes it and the model sees escaped quotes. And a passing check must change nothing at all. |
| **DQ-12** | Redirects are followed **manually** (max 3), and the token is sent only if scheme + host + port match the original. | `httpx` gives no per-hop control of headers. The spec's requirement is absolute: never forward the token to another server. So the loop is written by hand. |
| **LOG-7** | A Bearer token wins over `user:password@` in a URL. | Found while testing the log: `httpx` turns URL credentials into a Basic-auth header that *silently replaced* the JWT. `fetch.py::without_login` strips the login when a token is present. |
| **DQ-11** | Demo APIs read fixture CSVs from the repo. | Demo mode assumes a repo checkout. It is not packaged, and that is intentional. |

---

## 8. Run-log decisions

| # | Decision | Reasoning |
|---|---|---|
| **LOG-1** | One JSONL file at `.probagent/runs.jsonl`. | Chosen by the user: readable in any editor, greppable with standard tools, no database. |
| **LOG-2** | A fixed envelope, plus per-action input/output models **registered by the feature that owns the action**. | New features cannot drift from the format, and `extra="forbid"` makes an undeclared field (e.g. a token) an error instead of a leak. Registration happens at import time, which is why `Run.__init__` can cheaply reject an unknown action. |
| **LOG-4** | Finding problems is `status: ok`. `error` means "could not run". | A dirty CSV is not a broken tool. Keeping the two apart is what makes "13 issues" and "the API refused the login" different kinds of events. |
| **LOG-3** | Rule validation is not logged. | The page validates while you type, 400 ms after you stop. It would flood the log with noise. |
| **LOG-5** | Retention: 90 days, then newest 10,000, pruned once per process before the first write. | Keeps the file small with no background job or cron. The accepted flaw is stated openly in the decision log: if the server and the CLI prune at the same instant, an entry can rarely be lost — accepted for a local demo tool. |
| **LOG-6** | Crashes log the error **type** only, not the message. | `KeyError('john@secret.com')` would leak the data value straight into a file people paste into tickets. |
| **GUARD-13** | Guard checks log the **position** of each issue (row, column, code) and the rules used — never values — capped at the first 100. | `probagent explain` needs to say *which rows* failed *which rule* after the fact, and positions are not data. The cap keeps one blocked call to a few KB. |
| **DQ-4** | Errors are HTTP 400/404 with `{"error", "message"}`. | The page can check `response.ok` the same way for every endpoint instead of parsing five different error shapes. |
| — | `sha256` fingerprints, not data. | Two checks with the same `dataset.sha256` + `ruleset_sha256` provably ran on identical data with identical rules — and no data is stored. `ruleset_sha256` is computed from canonical JSON, so reformatting the rules file does not change it. |
| — | `dataset_id` is a random `uuid4`, and the run log stores `parent_run_id`. | The page's temporary id is not meaningful outside the process; `parent_run_id` links a check back to the upload/fetch it used. |

---

## 9. Web-layer decisions

| Decision | Reasoning |
|---|---|
| Datasets live **in memory**, max 5, 30-minute expiry (`DatasetStore`). | No database, nothing on disk. Oldest is evicted first. A local demo tool does not need durability. |
| `read_upload` reads `MAX_FILE_BYTES + 1` bytes, not the whole file. | A 4 GB upload must not land in memory just to be rejected. Reading one byte past the limit is enough to know. |
| `fetch` is a plain `def`, not `async def` (`server.py:165`). | FastAPI runs sync endpoints in a worker thread, so the server can still answer **its own demo API** while a fetch is in flight. An `async def` blocking on `httpx.get` would deadlock the demo. There is a comment saying so. |
| The run log wraps the endpoint body, not the whole app. | `with run_log.track(...) as run:` gives `run.set_inputs(...)`, `run.fail(code, message)` and a guaranteed write in `finally`, even on a crash. |
| `guard-validate` reuses `load_config` exactly as the guard does. | The command must fail for the same reasons the agent would, or it is useless as a pre-flight check. |

---

## 10. Trade-offs that were consciously left unresolved

Honest list. These are real weaknesses, kept on purpose:

| Trade-off | Why it was accepted |
|---|---|
| Everything is loaded into memory; 50,000-row ceiling | The spec scopes this to demo data. Streaming would add a whole subsystem for no benefit here. The limit is stated clearly instead of failing mysteriously. |
| `float`-free but regex-based parsing is slower than native | Correctness over speed at this scale. A 50k-row check takes milliseconds. |
| Manual redirect following reimplements `httpx` behaviour | Needed for per-hop header control (DQ-12). Only ~10 lines, and it is commented. |
| Regex `re.search` on user regexes can still be slow | Mitigated (200-char regex, 1,000-char input), not eliminated. |
| Race window during log pruning | `LOG-5`. Accepted for a local single-user tool. |
| `MUST`/`SHOULD` from RFC 2119 are followed by eye, not by tooling | `ruff`/`mypy` are not installed yet (§3 gap). |
| Two nearly identical `_friendly_message` functions (`rules.py`, `guard/config.py`) | Deliberately duplicated. They produce different sentences for different audiences, and a shared abstraction would have to be parameterised to no benefit. |

---

## 11. How to review or override a decision

You do not need to read Python to check the AI's work. Ask these five questions:

1. **Is it in the spec?** If the spec says MUST and the code does not, that is a bug.
2. **Is it in `docs/decisions.md`?** If a real choice was made and it is *not* logged, the AI skipped step 5. Ask for the entry.
3. **Does it break a safety rule in §2.1?** Especially: did anything start sending values to the model, or logging values?
4. **Is there a test?** `pytest` is the contract. New behaviour with no test is unfinished.
5. **Is it the smallest thing?** Did a whole mechanism appear for a problem that had a one-line answer?

To **change** a decision: edit `docs/decisions.md` first, state the new rule, and ask for the code change.
That ordering is deliberate — it forces the reason to survive the edit.