"""The web server: one HTML page plus a small JSON API (spec Section 9).

Run it with:   probagent dq-serve          (then open http://127.0.0.1:8765)

Endpoints:
    GET  /                    the page
    POST /api/data/upload     upload a CSV            -> dataset info
    POST /api/data/fetch      fetch from a REST API   -> dataset info
    POST /api/rules/infer     guess rules from sample -> ruleset + notes
    POST /api/rules/validate  check a ruleset         -> ok / errors
    POST /api/check           run the check           -> report

Errors come back as HTTP 400/404 with {"error": code, "message": text}.
Each action is also written to the run log (.probagent/runs.jsonl).
"""

import base64
import json
import secrets
import time
import uuid
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from probagent.dq import settings
from probagent.dq.fetch import FetchError, fetch_dataset, safe_url
from probagent.dq.infer import infer_ruleset
from probagent.dq.loader import LoadError, load_csv_bytes
from probagent.dq.log_schema import (
    FEATURE,
    check_inputs,
    check_outputs,
    describe_dataset,
    load_outputs,
    rules_error_message,
    ruleset_sha256,
    sha256_of,
)
from probagent.dq.models import Dataset
from probagent.dq.rules import RulesetError, parse_ruleset
from probagent.dq.validator import check_dataset
from probagent.runlog import RunLog

STATIC_DIR = Path(__file__).parent / "static"
FIXTURES_DIR = Path(__file__).resolve().parents[3] / "fixtures" / "dq"
PREVIEW_ROWS = 10


# ---------- Datasets kept in memory ----------

class DatasetStore:
    """Holds loaded datasets in memory: at most 5, each for 30 minutes.

    Nothing is written to disk. When the store is full, the oldest is removed.
    """

    def __init__(self):
        self._items: dict[str, tuple[float, Dataset]] = {}   # id -> (time added, dataset)

    def add(self, dataset: Dataset) -> str:
        self._remove_expired()
        while len(self._items) >= settings.MAX_DATASETS_IN_MEMORY:
            oldest_id = min(self._items, key=lambda key: self._items[key][0])
            del self._items[oldest_id]
        dataset_id = uuid.uuid4().hex
        self._items[dataset_id] = (time.time(), dataset)
        return dataset_id

    def get(self, dataset_id: str) -> Optional[Dataset]:
        self._remove_expired()
        item = self._items.get(dataset_id)
        return item[1] if item else None

    def _remove_expired(self) -> None:
        too_old = time.time() - settings.DATASET_EXPIRY_SECONDS
        for key in [k for k, (added, _) in self._items.items() if added < too_old]:
            del self._items[key]


# ---------- Request bodies ----------

class FetchRequest(BaseModel):
    url: str
    data_path: str = ""
    token: Optional[str] = None


class CheckRequest(BaseModel):
    dataset_id: str
    ruleset: dict
    columns: Optional[list[str]] = None    # the ticked columns; None = all


# ---------- Helpers ----------

def error_response(code: str, message: str, http_status: int = 400, **extra) -> JSONResponse:
    return JSONResponse({"error": code, "message": message, **extra}, status_code=http_status)


def dataset_info(dataset_id: str, dataset: Dataset) -> dict:
    """What the page needs to show after data is loaded."""
    return {
        "dataset_id": dataset_id,
        "source": dataset.source,
        "columns": dataset.columns,
        "row_count": len(dataset.rows),
        "preview": dataset.rows[:PREVIEW_ROWS],
        "warnings": dataset.warnings,
    }


async def read_upload(file: UploadFile) -> bytes:
    """Read at most limit + 1 bytes, so a huge file is never fully read into memory."""
    return await file.read(settings.MAX_FILE_BYTES + 1)


def make_demo_token() -> str:
    """A random JWT-shaped token for the demo API (header.payload.signature)."""
    def encode(part: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(part).encode()).decode().rstrip("=")
    return ".".join([encode({"alg": "HS256", "typ": "JWT"}),
                     encode({"sub": "demo-user"}),
                     secrets.token_urlsafe(24)])


# ---------- The app ----------

def create_app(demo: bool = False, demo_token: Optional[str] = None,
               run_log: Optional[RunLog] = None) -> FastAPI:
    """Build the FastAPI app. With demo=True, fake API endpoints are added too.

    Every user action (upload, fetch, infer, check) is written to the run log.
    Rule validation is not: the page runs it automatically while you type.
    """
    app = FastAPI(title="Probagent Data Quality")
    store = DatasetStore()
    run_log = run_log or RunLog()

    @app.get("/")
    def index():
        return FileResponse(STATIC_DIR / "index.html")

    @app.post("/api/data/upload")
    async def upload(file: UploadFile = File(...)):
        content = await read_upload(file)
        file_name = file.filename or "upload.csv"
        with run_log.track(FEATURE, "upload", origin="web") as run:
            run.set_inputs(file_name=file_name, size_bytes=len(content),
                           sha256=sha256_of(content))
            try:
                dataset = load_csv_bytes(content, source=file_name)
            except LoadError as error:
                run.fail("load_failed", str(error))
                return error_response("load_failed", str(error))
            dataset.run_id = run.run_id
            dataset_id = store.add(dataset)
            run.set_outputs(**load_outputs(dataset_id, dataset))
        return dataset_info(dataset_id, dataset)

    @app.post("/api/data/fetch")
    def fetch(body: FetchRequest):
        # A plain `def` (not async) runs in a worker thread, so the server can
        # still answer other requests, including its own demo API, meanwhile.
        with run_log.track(FEATURE, "fetch", origin="web") as run:
            run.set_inputs(url=safe_url(body.url), data_path=body.data_path,
                           token_sent=bool((body.token or "").strip()))
            try:
                dataset = fetch_dataset(body.url, body.data_path, body.token)
            except FetchError as error:
                run.fail(error.code, error.message)
                return error_response(error.code, error.message, status=error.status)
            dataset.run_id = run.run_id
            dataset_id = store.add(dataset)
            run.set_outputs(**load_outputs(dataset_id, dataset))
        return dataset_info(dataset_id, dataset)

    @app.post("/api/rules/infer")
    async def infer(file: Optional[UploadFile] = File(None),
                    dataset_id: Optional[str] = Form(None),
                    columns: Optional[list[str]] = Form(None)):
        """Guess rules from an uploaded sample CSV, or from an already loaded dataset.

        `columns` (sent once per ticked column) limits the rules to those columns.
        """
        content = await read_upload(file) if file is not None and file.filename else None
        loaded = store.get(dataset_id) if dataset_id and content is None else None
        parent = loaded.run_id if loaded else None

        with run_log.track(FEATURE, "infer", origin="web", parent_run_id=parent) as run:
            if content is not None:
                try:
                    dataset = load_csv_bytes(content, source=file.filename)
                except LoadError as error:
                    run.fail("load_failed", str(error))
                    return error_response("load_failed", str(error))
            elif loaded is not None:
                dataset = loaded
            elif dataset_id:
                run.fail("dataset_not_found", "The data has expired.")
                return error_response("dataset_not_found", "The data has expired. "
                                      "Load it again.", 404)
            else:
                run.fail("no_sample", "No sample CSV and no loaded data.")
                return error_response("no_sample", "Choose a sample CSV, or load data first.")

            run.set_inputs(sample=describe_dataset(dataset),
                           rows_used=min(len(dataset.rows), settings.INFER_SAMPLE_ROWS),
                           columns=columns)
            result = infer_ruleset(dataset, columns=columns)
            if not result.ruleset.fields:
                run.fail("no_columns", "None of the ticked columns are in the sample.")
                return error_response("no_columns", "None of the ticked columns are in "
                                      "the sample. Tick columns that the sample has.")
            run.set_outputs(ruleset_sha256=ruleset_sha256(result.ruleset),
                            fields=len(result.ruleset.fields),
                            notes=sum(len(notes) for notes in result.notes.values()))
        return result.model_dump(exclude_none=True)

    @app.post("/api/rules/validate")
    async def validate(request: Request):
        try:
            data = await request.json()
        except ValueError:
            return {"ok": False, "errors": ["The rules are not valid JSON."]}
        try:
            parse_ruleset(data)
        except RulesetError as error:
            return {"ok": False, "errors": error.errors}
        return {"ok": True, "errors": []}

    @app.post("/api/check")
    def check(body: CheckRequest):
        dataset = store.get(body.dataset_id)
        parent = dataset.run_id if dataset else None

        with run_log.track(FEATURE, "check", origin="web", parent_run_id=parent) as run:
            if dataset is None:
                run.fail("dataset_not_found", "The data has expired.")
                return error_response("dataset_not_found", "The data has expired. "
                                      "Load it again.", 404)
            if body.columns is not None and not body.columns:
                run.fail("no_columns", "No columns were ticked.")
                return error_response("no_columns", "Tick at least one column to check.")
            try:
                ruleset = parse_ruleset(body.ruleset)
            except RulesetError as error:
                run.fail("invalid_rules", rules_error_message(error.errors))
                return error_response("invalid_rules", "The rules have problems.",
                                      errors=error.errors)
            run.set_inputs(**check_inputs(dataset, ruleset, body.columns))
            report = check_dataset(dataset, ruleset, body.columns)
            run.set_outputs(**check_outputs(report))
        return report

    if demo:
        add_demo_endpoints(app, demo_token or make_demo_token())

    return app


def add_demo_endpoints(app: FastAPI, demo_token: str) -> None:
    """Fake APIs for trying the REST fetch without a real API (spec Section 12)."""

    def demo_records() -> list[dict]:
        dataset = load_csv_bytes((FIXTURES_DIR / "orders_dirty.csv").read_bytes(), "demo")
        return [dict(zip(dataset.columns, row)) for row in dataset.rows]

    @app.get("/demo/api/orders")
    def open_orders():
        return {"data": demo_records()}

    @app.get("/demo/api/secure-orders")
    def secure_orders(request: Request):
        sent = request.headers.get("authorization", "")
        if not secrets.compare_digest(sent, f"Bearer {demo_token}"):
            return JSONResponse({"detail": "Not authenticated"}, status_code=401)
        return {"data": demo_records()}
