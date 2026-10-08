# Probagent Data Quality Checker (Input Data Quality)

> **Document type:** Product + engineering specification
> **Audience:** A coding agent (and human reviewers) that will build this feature
> **Parent spec:** [Probagent_Spec.md](Probagent_Spec.md). This feature expands the `lint-data` step and addresses challenge **C-22 (data-ingestion quirks)**.
> **Scope:** Lightweight, demo-sized data. One FastAPI server plus one HTML page.

---

## 0. In Plain Words

The tool answers one question: **"Is this data clean?"**

1. **Get the data.** Upload a `.csv` file or fetch it from a REST API.
2. **Get the rules.** Write a JSON ruleset by hand, or drop in a sample CSV and let the tool guess the rules for you to review.
3. **Check.** Every row is tested against every rule.
4. **Show.** The issues are listed on screen, and a **Print** button gives a clean printout (or PDF through the browser's "Save as PDF").

```
 1. GET DATA            2. GET RULES               3. CHECK              4. SHOW
 ┌──────────────┐       ┌───────────────────┐      ┌──────────────┐      ┌──────────────┐
 │ Upload CSV   │       │ Write JSON        │      │ Every row ×  │      │ Results on   │
 │   or         │ ───►  │   or              │ ───► │ every rule   │ ───► │ screen       │
 │ REST API     │       │ Infer from sample │      │              │      │ + Print      │
 │ (+JWT only   │       │ CSV → user edits  │      │              │      │              │
 │  if needed)  │       │                   │      │              │      │              │
 └──────────────┘       └───────────────────┘      └──────────────┘      └──────────────┘
```

**Why it matters for agent evals:** if the data is dirty (duplicates, missing values), an agent's answer can be wrong without the agent being at fault. The data quality check runs **before** evals so that failures can be blamed on the right thing.

---

## 1. Instructions for the Coding Agent (READ FIRST)

1. Keywords **MUST**, **SHOULD**, **MAY** follow RFC 2119 meaning.
2. Build in the phase order in Section 13. Every feature **MUST** ship with tests.
3. **NEVER** use `eval()` / `exec()` on uploaded data, rulesets, or API responses.
4. **NEVER** store, log, or display a JWT token after the request that uses it.
5. Treat all uploaded and fetched data as **untrusted**. In the HTML page, insert values with `textContent`, never `innerHTML`.
6. Use only synthetic data in fixtures and examples.
7. When something is ambiguous, pick the simplest option, record it in `docs/decisions.md`, and continue.

---

## 2. Goals and Non-Goals

### 2.1 Goals

- G1. Load data from a **CSV upload** or a **REST API** (open, or protected by a JWT).
- G2. Describe "good data" in a simple **JSON ruleset**, one entry per field.
- G3. **Infer** a starting ruleset from a sample CSV, so beginners don't start from a blank page.
- G4. List **every issue** with row, column, bad value, and a plain-English reason.
- G5. Show the results in a **single web page** with a **print-friendly** layout.
- G6. Stay **lightweight**: no database, no frontend build step, few dependencies.

### 2.2 Non-Goals

- Not for large data. Demo-sized only (see limits in Section 10).
- Not a data-cleaning tool. It reports problems and does not fix them.
- No user accounts, no saved history, no multi-user support.
- No OAuth flows. JWT means "paste a Bearer token" only.

---

## 3. Core Concepts

| Term | Meaning |
|---|---|
| **Dataset** | A table of rows and columns, loaded from a CSV or an API |
| **Field** | One column in the dataset |
| **Ruleset** | A JSON document that says what valid values look like for each field |
| **Rule** | One condition on a field, for example `required`, `min`, or `pattern` |
| **Inference** | Guessing a ruleset by studying sample data |
| **Issue** | One broken rule in one cell (or one dataset-level problem) |
| **Report** | The summary plus the list of issues from one check |

---

## 4. Tech Stack

| Part | Choice | Why |
|---|---|---|
| Backend | **FastAPI** + `uvicorn` | Small, fast, auto-validates requests with Pydantic |
| Data handling | **pandas** | Easy CSV/JSON loading at demo sizes |
| HTTP client | **httpx** | Timeouts, size limits, redirect control |
| Models | **Pydantic v2** | Same as the parent spec |
| Frontend | **One HTML file** (plain HTML + CSS + JavaScript) | No build step; prints cleanly |

Python 3.11+. No frontend framework, no database.

---

## 5. Step 1: Getting the Data

All sources produce the same thing: a **Dataset** of rows and columns where **every value is kept as text**. The validator decides types later, so the loader never silently changes a value (for example, `"007"` stays `"007"`).

### 5.1 Option A: CSV Upload

Loading rules (the loader **MUST** do all of these):

| Situation | Behavior |
|---|---|
| UTF-8 file with BOM | Read with `utf-8-sig` so the first header is clean |
| Non-UTF-8 file | Fall back to `cp1252`; add a warning to the report |
| Delimiter | Detect comma, semicolon, or tab (`csv.Sniffer`); default to comma |
| Quoted cells with newlines | Supported |
| Types | Read everything as text: `dtype=str, keep_default_na=False` |
| Empty or whitespace-only cell | Treated as **missing** |
| Duplicate column names | Reject the file with a clear error |
| Empty file / header only | Reject the file with a clear error |
| Over the size or row limit | Reject with a message that names the limit (Section 10) |

### 5.2 Option B: REST API (JWT only if needed)

**Inputs from the user:**

| Field | Required | Example | Notes |
|---|---|---|---|
| URL | Yes | `https://api.example.com/orders` | `http` or `https` only |
| Data path | No | `data.items` | Where the list of records sits in the JSON. Empty = the response itself is the list |
| JWT token | No | `eyJhbGciOi...` | Only asked for after a 401/403 (see flow) |

**Fetch flow:**

```
User clicks "Fetch"
      │
      ▼
GET <url>  (no Authorization header)
      │
      ├── 200 + valid JSON ───────► load records ✅
      │
      ├── 401 or 403 ─────────────► return code "auth_required"
      │                              UI shows the JWT token box + "Retry with token"
      │                                     │
      │                                     ▼
      │                              GET <url> with header
      │                              Authorization: Bearer <token>
      │                                     │
      │                                     ├── 200 ──────► load records ✅
      │                                     └── 401/403 ──► "auth_failed":
      │                                                     "Token was rejected. Check it is valid and not expired."
      │
      └── 404 / 5xx / timeout / ──► return code "fetch_failed" with the status and a plain message.
          not JSON                   Do NOT ask for a token: a token will not fix these.
```

**Rules:**

- Only **401 Unauthorized** and **403 Forbidden** trigger the token prompt.
- Method is `GET` only.
- Timeout: **30 seconds**. Max response size: **10 MB** (stream the body and stop once it passes the limit).
- Redirects: follow at most 3. **Never** send the token to a different host than the original URL.
- The token is used for that single request, then discarded. The server does not keep it in memory between requests, write it to logs, or put it in any report.
- The tool does **not** decode or verify the JWT. If the value does not look like `xxx.yyy.zzz`, the UI shows a soft warning but still sends it.

**Turning JSON into a table:**

- After applying the data path, the value **MUST** be a list of objects (`[{...}, {...}]`). Anything else returns `fetch_failed` with reason `not_a_list_of_records`.
- Nested objects are flattened with dot names (`customer.email`) using `pandas.json_normalize`.
- Lists inside a record are kept as JSON text.
- All values are converted to text, as with CSV. `null` becomes missing.

---

## 6. Step 2: The Ruleset (JSON)

### 6.1 Format

```json
{
  "version": 1,
  "name": "orders",
  "allow_extra_columns": true,
  "check_duplicate_rows": true,
  "fields": {
    "order_id": { "type": "integer", "required": true, "unique": true },
    "email":    { "type": "string",  "required": true, "pattern": "email" },
    "quantity": { "type": "integer", "required": true, "min": 1, "max": 1000 },
    "price":    { "type": "number",  "min": 0 },
    "status":   { "type": "string",  "allowed": ["pending", "shipped", "cancelled"] },
    "country":  { "type": "string",  "min_length": 2, "max_length": 2 },
    "order_date": { "type": "date",  "format": "YYYY-MM-DD", "required": true }
  }
}
```

In plain English: *"Every order needs a unique whole-number ID, a valid email, a quantity from 1 to 1000, a non-negative price, one of three known statuses, a 2-letter country code, and a date written as YYYY-MM-DD."*

### 6.2 Field Types

| Type | Valid examples | Invalid examples |
|---|---|---|
| `string` | anything | none (any text is valid) |
| `integer` | `42`, `-3`, `007` | `4.5`, `five`, `1,000` |
| `number` | `42`, `4.5`, `-0.25`, `1e3` | `abc`, `4,5` |
| `boolean` | `true`, `false`, `yes`, `no`, `1`, `0` (case-insensitive) | `maybe` |
| `date` | Matches `format` (default `YYYY-MM-DD`) and is a real date | `2026-02-30`, `07/10/2026` when format is `YYYY-MM-DD` |
| `datetime` | ISO 8601, for example `2026-10-07T09:30:00` | `yesterday` |

Supported date formats: `YYYY-MM-DD`, `DD/MM/YYYY`, `MM/DD/YYYY`, `DD-MM-YYYY`, `YYYY/MM/DD`.

### 6.3 Rules per Field

| Rule | Applies to | Meaning |
|---|---|---|
| `type` | all | Value must parse as this type (default `string`) |
| `required` | all | Value must not be missing (default `false`) |
| `unique` | all | No two rows share this value (missing values are ignored) |
| `min` / `max` | integer, number, date | Inclusive bounds. For dates, use the same `format` |
| `min_length` / `max_length` | string | Text length bounds |
| `allowed` | all | Value must be one of this list (exact match after trimming spaces) |
| `pattern` | string | A named pattern: `email`, `phone`, `url`, `uuid`, or `{"regex": "^[A-Z]{3}-\\d+$"}` |
| `format` | date | One of the formats in 6.2 |

### 6.4 Dataset-Level Options

| Option | Default | Meaning |
|---|---|---|
| `allow_extra_columns` | `true` | If `false`, columns that are not in `fields` are reported |
| `check_duplicate_rows` | `true` | Report rows that are exact copies of an earlier row |

A field listed in the ruleset but missing from the data is **always** reported (`missing_column`).

### 6.5 Ruleset Validation

Before a check runs, the ruleset itself **MUST** be validated, and errors shown in the UI:

- Valid JSON, `version` is `1`, `fields` is a non-empty object.
- Unknown rule names are errors, so a typo like `"requird"` is caught instead of silently ignored.
- Rules must fit the type (for example, `min_length` on an `integer` is an error).
- `min` ≤ `max`, `min_length` ≤ `max_length`.
- Custom `regex`: at most 200 characters and **MUST** compile. Regexes are matched against values cut to 1,000 characters to limit slow-regex risk.

---

## 7. Rule Inference (Sample CSV → JSON)

The user drops a CSV of **real sample data**. The tool studies it and writes a draft ruleset. **The user MUST review and edit the draft before it is used.** The UI places the draft in the JSON editor, never applies it automatically.

### 7.1 How Each Rule Is Guessed

The tool uses the **first 1,000 rows** of the sample. "Non-empty" means the cell is not missing.

| Rule | Guessed when… |
|---|---|
| `type` | ≥ 95% of non-empty values parse as the type. Try in order: `boolean` → `integer` → `number` → `date` → `datetime` → `string` |
| `format` (date) | The format that matches the most values. If `DD/MM` vs `MM/DD` is ambiguous (no first part > 12), choose `DD/MM/YYYY` and add a note |
| `required` | The column has **no** empty cells |
| `unique` | All non-empty values are different **and** the sample has ≥ 10 rows |
| `min` / `max` | For number/integer columns: the smallest and largest values seen. **Not for dates:** a date column gets only `type` and `format`, because new data keeps adding later dates |
| `allowed` | String column with ≤ 10 distinct values **and** distinct count ≤ 50% of non-empty values |
| `pattern` | ≥ 95% of non-empty values match `email`, `phone`, `url`, or `uuid` |
| `min_length` / `max_length` | Only when all values have the **same** length (e.g. country codes) |

### 7.2 Notes to the User

Inference also returns **notes** (shown next to the editor, not stored in the ruleset), for example:

- `quantity: min/max set to 3–480 from the sample. Widen these if larger values are valid.`
- `status: 'allowed' list built from 3 values seen. Add any values the sample didn't contain.`
- `order_date: 4% of values didn't match YYYY-MM-DD.`
- `notes: mostly empty (82%). Left as optional text.`

> ⚠️ **Rules are only as good as the sample.** A small or dirty sample produces rules that are too strict or too loose. That's why the review step is mandatory.

---

## 8. Step 3: The Check

### 8.1 Order of Checks

1. **Dataset level:** `missing_column`, `unexpected_column` (if not allowed), `duplicate_row`.
2. **Per cell**, for each field:
   - If missing: report `missing_value` when `required`; then skip the other rules for that cell.
   - Check `type`. If the value fails, report `wrong_type` and **skip the remaining rules** for that cell (one clear issue instead of several confusing ones).
   - Check `min`/`max`, `min_length`/`max_length`, `allowed`, `pattern`.
3. **Per column:** `unique`. Every repeat is reported, and the message names the first row with that value.

### 8.2 Issue Codes

| Code | Example message |
|---|---|
| `missing_column` | Column `email` is in the rules but not in the data |
| `unexpected_column` | Column `notes` is not in the rules |
| `duplicate_row` | Row 45 is an exact copy of row 12 |
| `missing_value` | `email` is required but empty |
| `wrong_type` | `quantity` should be an integer, got `"five"` |
| `below_min` / `above_max` | `quantity` is -3, below the minimum of 1 |
| `too_short` / `too_long` | `country` has 3 characters, maximum is 2 |
| `not_allowed` | `status` is `"shiped"`, allowed: pending, shipped, cancelled |
| `pattern_mismatch` | `email` value `"john@"` is not a valid email |
| `bad_date` | `order_date` value `"2026-02-30"` is not a real date |
| `duplicate_value` | `order_id` 1045 already appears in row 67 |

Row numbers are **data rows starting at 1** (the header is not counted). The UI says this above the table.

### 8.3 Data Contracts (Pydantic v2) — `probagent/dq/models.py`

```python
from typing import Any, Literal, Optional
from pydantic import BaseModel, Field

FieldType = Literal["string", "integer", "number", "boolean", "date", "datetime"]

class FieldRule(BaseModel):
    type: FieldType = "string"
    required: bool = False
    unique: bool = False
    min: Optional[float | str] = None          # str for dates
    max: Optional[float | str] = None
    min_length: Optional[int] = None
    max_length: Optional[int] = None
    allowed: Optional[list[str]] = None
    pattern: Optional[str | dict[str, str]] = None   # "email" or {"regex": "..."}
    format: Optional[str] = None               # dates only
    model_config = {"extra": "forbid"}         # catches typos like "requird"

class Ruleset(BaseModel):
    version: Literal[1] = 1
    name: str = "ruleset"
    allow_extra_columns: bool = True
    check_duplicate_rows: bool = True
    fields: dict[str, FieldRule]
    model_config = {"extra": "forbid"}

class Issue(BaseModel):
    row: Optional[int] = None                  # None for dataset/column-level issues
    column: Optional[str] = None
    value: Optional[str] = None                # cut to 100 characters
    code: str
    message: str

class ReportSummary(BaseModel):
    rows_checked: int
    columns_checked: int
    issue_count: int
    rows_with_issues: int
    clean_rows_pct: float

class Report(BaseModel):
    source: str                                # file name or URL (never the token)
    ruleset_name: str
    checked_at: str
    summary: ReportSummary
    by_column: dict[str, int]                  # issue count per column
    by_code: dict[str, int]                    # issue count per issue code
    issues: list[Issue]                        # first 500 only
    truncated: bool                            # True if more than 500 issues exist
    warnings: list[str] = Field(default_factory=list)   # e.g. encoding fallback

class InferResult(BaseModel):
    ruleset: Ruleset
    notes: dict[str, list[str]]                # per field
```

---

## 9. Step 4: The Web Page and the API

### 9.1 FastAPI Endpoints

| Method | Path | Input | Output |
|---|---|---|---|
| `GET` | `/` | none | The HTML page |
| `POST` | `/api/data/upload` | multipart `.csv` file | `dataset_id`, `columns`, `row_count`, `preview` (first 10 rows), `warnings` |
| `POST` | `/api/data/fetch` | `{url, data_path?, token?}` | Same as upload, **or** `{error: "auth_required" \| "auth_failed" \| "fetch_failed", status?, message}` |
| `POST` | `/api/rules/infer` | multipart sample `.csv` **or** `{dataset_id}` | `InferResult` |
| `POST` | `/api/rules/validate` | ruleset JSON | `{ok: true}` or `{ok: false, errors: [...]}` |
| `POST` | `/api/check` | `{dataset_id, ruleset}` | `Report` |

**Dataset storage:** loaded datasets are held **in memory** under a random `dataset_id`. They expire after 30 minutes, and at most 5 are kept (oldest removed first). No database, nothing written to disk.

### 9.2 Page Layout (one HTML file: `probagent/dq/static/index.html`)

```
┌──────────────────────────────────────────────────────────────┐
│  Probagent · Data Quality Check                             │
├──────────────────────────────────────────────────────────────┤
│  ① DATA SOURCE        [ Upload CSV ] [ REST API ]            │
│     Upload: [Choose file]  orders.csv · 500 rows · 7 columns │
│     API:    URL [____________]  Data path [______] [Fetch]   │
│             🔒 This API needs a login. JWT [••••••] [Retry]  │  ← shown only after 401/403
│     Preview: first 10 rows                                   │
├──────────────────────────────────────────────────────────────┤
│  ② RULES              [ Write JSON ] [ Generate from sample ]│
│     ┌─ JSON editor (textarea) ───────┐  Notes:               │
│     │ { "fields": { ... } }          │  • quantity: widen... │
│     └────────────────────────────────┘                       │
│     [Validate rules]  [Download rules.json]  [Load rules.json]│
├──────────────────────────────────────────────────────────────┤
│  ③ [ Run check ]                                             │
├──────────────────────────────────────────────────────────────┤
│  ④ RESULTS                                     [🖨 Print]    │
│     500 rows · 7 issues · 494 clean rows (98.8%)             │
│     By column: email 2 · quantity 2 · status 1 · ...         │
│     ┌─────┬──────────┬─────────┬──────────────────────────┐  │
│     │ Row │ Column   │ Value   │ Problem                  │  │
│     ├─────┼──────────┼─────────┼──────────────────────────┤  │
│     │ 12  │ email    │ john@   │ Not a valid email        │  │
│     │ 45  │ quantity │ -3      │ Below minimum (1)        │  │
│     └─────┴──────────┴─────────┴──────────────────────────┘  │
│     Filter: [column ▾] [issue type ▾]                        │
└──────────────────────────────────────────────────────────────┘
```

**Page behavior:**

- "Generate from sample" accepts a separate sample CSV, or uses the loaded dataset if none is given. The draft goes into the editor, and the notes appear beside it.
- Once data is loaded, step ② shows a **checkbox for each column**, all ticked by default. Only ticked columns get generated rules and are checked. Rules for unticked columns are ignored, not reported as missing. Duplicate rows still compare the whole row. The report and printout list the ticked columns.
- "Run check" stays disabled until there is a dataset, a valid ruleset, and at least one ticked column.
- The JWT field is a password input. It is cleared after each attempt and on page reload, and never saved to `localStorage`.
- All data values go into the page with `textContent` (protection against malicious cell content).
- Long values are cut in the table, and hovering shows the full value (up to 100 characters).

### 9.3 Print

- The **Print** button calls `window.print()`. The browser's print dialog also offers **Save as PDF**, so PDF export needs no extra library.
- A `@media print` stylesheet **MUST**:
  - hide sections ①–③, buttons, filters, and the token field;
  - show a print header: title, data source (file name or URL), ruleset name, date and time of the check;
  - show the summary and per-column counts, then the full issues table;
  - repeat the table header on each page (`thead { display: table-header-group }`) and avoid splitting a row across pages (`tr { break-inside: avoid }`);
  - use black text on white, with no background colors required to read it.
- If the report is truncated, the printout says: *"Showing the first 500 of N issues."*

---

## 10. Limits (Demo-Sized Data)

This version targets **demo data**, so it keeps things simple: everything is loaded into memory, with clear limits instead of big-data machinery.

| Limit | Value | When exceeded |
|---|---|---|
| CSV file size | **10 MB** | Reject: "This file is larger than the 10 MB demo limit." |
| API response size | **10 MB** | Stop reading, `fetch_failed`, same style of message |
| Rows | **50,000** | Reject with a message naming the limit |
| Columns | **200** | Reject with a message naming the limit |
| Rows used for inference | **First 1,000** | Others ignored; note shown |
| Issues returned / shown | **First 500** | `truncated: true`; counts in `by_column` / `by_code` still cover **all** issues |
| API timeout | **30 s** | `fetch_failed: timeout` |
| Datasets in memory | **5**, 30-minute expiry | Oldest removed first |

All limits live in one `settings.py` so they are easy to change. **Future upgrade (not in scope):** chunked CSV reading for large files.

---

## 11. Security Checklist

| Risk | Rule |
|---|---|
| Token leak | Token used for one request only, never logged, stored, returned in responses, or printed |
| Token sent to the wrong server | Never forwarded on a redirect to a different host |
| Malicious cell content (XSS) | `textContent` only in the page; the report JSON contains raw text, never HTML |
| Code execution | No `eval`/`exec`; ruleset parsed by Pydantic with `extra = "forbid"` |
| Slow regex | Custom regex ≤ 200 characters, values cut to 1,000 characters before matching |
| Huge inputs | Size, row, and column limits from Section 10 |
| Unsafe URL schemes | Only `http` and `https` are accepted |
| Server exposure | The server binds to `127.0.0.1` by default. The README warns that the fetch feature can reach any URL the machine can reach, so the server shouldn't be exposed publicly |

---

## 12. Repository Layout (inside the Probagent repo)

```
src/probagent/dq/
├── models.py          # Section 8.3
├── settings.py        # limits (Section 10)
├── loader.py          # CSV + JSON → Dataset (Section 5)
├── fetch.py           # REST fetch + JWT retry flow (Section 5.2)
├── rules.py           # ruleset validation (Section 6.5)
├── types.py           # type parsing, date formats, named patterns
├── validator.py       # the check (Section 8)
├── infer.py           # rule inference (Section 7)
├── server.py          # FastAPI app (Section 9.1)
└── static/index.html  # the one page (Section 9.2–9.3)

fixtures/dq/
├── orders_clean.csv     # 200 rows, no issues (seed 42)
├── orders_dirty.csv     # same columns, seeded issues
├── DQ_ISSUES.md         # exact list of seeded issues (row, column, code)
└── orders_rules.json    # the ruleset that matches these files

tests/dq/
├── test_loader.py  test_fetch.py  test_rules.py
├── test_types.py   test_validator.py  test_infer.py
└── test_server.py
```

**Commands:**

```bash
probagent dq-serve                 # start the web page at http://127.0.0.1:8765
probagent dq-serve --demo          # also serve demo API endpoints (below)
probagent lint-data FILE --rules rules.json   # same engine, prints the report in the terminal
```

**Demo API endpoints (only with `--demo`)**, for trying the API flow without a real API:

| Path | Behavior |
|---|---|
| `GET /demo/api/orders` | Open; returns `orders_dirty.csv` as JSON under `data` |
| `GET /demo/api/secure-orders` | Returns 401 without a token; accepts the demo token printed in the terminal at startup |

---

## 13. Implementation Phases

### Phase 1: Engine
- `models.py`, `types.py`, `rules.py`, `loader.py` (CSV only), `validator.py`
- Fixtures generator with seeded issues and `DQ_ISSUES.md`

**Accept:** checking `orders_dirty.csv` with `orders_rules.json` reports **exactly** the issues in `DQ_ISSUES.md` (no more, no fewer); `orders_clean.csv` reports **zero** issues; a ruleset typo (`"requird"`) is rejected; CSV with BOM, semicolons, and quoted newlines loads correctly.

### Phase 2: Inference
- `infer.py` with the rules from Section 7 and the notes.

**Accept:** inferring from `orders_clean.csv` gives a ruleset under which `orders_clean.csv` has zero issues; inferred rules catch at least 80% of the issues seeded in `orders_dirty.csv`; the `DD/MM` vs `MM/DD` case produces a note.

### Phase 3: Server + Page (CSV path)
- `server.py` with the upload, infer, validate, and check endpoints; `index.html` with sections ①–④ and print CSS.

**Accept:** the whole flow (upload → generate rules → edit → check → print) works in a browser; print preview shows only the report, with the header repeated on each page; a cell containing `<script>alert(1)</script>` is displayed as text.

### Phase 4: REST API + JWT
- `fetch.py`, the `/api/data/fetch` endpoint, the token box in the page, and the `--demo` endpoints.

**Accept:** the open endpoint loads directly; the secure endpoint first returns `auth_required`, then loads with the demo token; a wrong token gives `auth_failed`; 404/500/timeout give `fetch_failed` **without** a token prompt; tests confirm the token never appears in logs, responses, or reports; the token is not sent after a cross-host redirect.

### Phase 5: Limits and CLI
- Enforce every limit in Section 10 with clear messages; `lint-data --rules` in the terminal.

**Accept:** a test for each limit; `lint-data` output matches the web report for the same input.

---

## 14. Decisions and Open Questions

| # | Question | Default decision |
|---|---|---|
| 1 | Ambiguous dates (`03/04/2026`) | Use `DD/MM/YYYY` and show an inference note |
| 2 | Should a failed `type` check also report range issues? | No. One issue per cell, the type issue |
| 3 | Is `"  "` (spaces only) missing? | Yes |
| 4 | Trim spaces before comparing `allowed` values? | Yes (leading and trailing only) |
| 5 | API pagination (page 1, 2, 3…) | Not supported in this version; only the first response is used |
| 6 | Save rulesets on the server? | No. Download/upload `rules.json` from the page |
| 7 | Link to agent evals | Later: the eval report shows "data had N quality issues" when its ground-truth file was checked |

**End of specification.**
