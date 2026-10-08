"""All limits in one place (spec Section 10).

This tool is built for demo-sized data, so everything is loaded into memory.
These limits keep it fast and give clear messages instead of crashing.
Change a number here and the whole app uses the new value.
"""

# --- Input size ---
MAX_FILE_BYTES = 10 * 1024 * 1024      # 10 MB, for CSV uploads and API responses
MAX_ROWS = 50_000
MAX_COLUMNS = 200

# --- Rule inference ---
INFER_SAMPLE_ROWS = 1_000              # only the first rows are studied
INFER_MATCH_SHARE = 0.95               # 95% of values must fit a type or pattern
INFER_MAX_ALLOWED_VALUES = 10          # more distinct values than this = free text

# --- Report ---
MAX_ISSUES_SHOWN = 500
MAX_VALUE_CHARS_IN_REPORT = 100

# --- Custom regex safety ---
MAX_REGEX_CHARS = 200
MAX_VALUE_CHARS_FOR_REGEX = 1_000

# --- REST API fetch ---
FETCH_TIMEOUT_SECONDS = 30
MAX_REDIRECTS = 3

# --- Datasets kept in memory by the web server ---
MAX_DATASETS_IN_MEMORY = 5
DATASET_EXPIRY_SECONDS = 30 * 60
