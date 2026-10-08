"""Command line: `probagent <command>`.

    probagent dq-serve [--demo] [--port 8765]     start the web page
    probagent lint-data FILE --rules rules.json   check a CSV in the terminal
        [--columns "A,B"]                          only check these columns
    probagent guard-validate guard.json           check a data guard config and its rules
    probagent explain [RUN_ID]                    why the guard blocked or flagged a tool call

Exit codes for lint-data (same as the main Probagent spec):
    0 = no issues,  1 = issues found,  3 = invalid file or rules

Every lint-data run is written to the run log (.probagent/runs.jsonl).
"""

import argparse
import json
import sys
from pathlib import Path

from probagent.dq.loader import LoadError, load_csv_bytes
from probagent.dq.log_schema import FEATURE, check_inputs, check_outputs, rules_error_message
from probagent.dq.models import Report
from probagent.dq.rules import RulesetError, parse_ruleset
from probagent.dq.validator import check_dataset
from probagent.runlog import RunLog

EXIT_OK, EXIT_ISSUES, EXIT_INVALID = 0, 1, 3


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="probagent")
    commands = parser.add_subparsers(dest="command", required=True)

    serve = commands.add_parser("dq-serve", help="Start the data quality web page.")
    serve.add_argument("--host", default="127.0.0.1",
                       help="Default 127.0.0.1 (this computer only). Don't expose it publicly.")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--demo", action="store_true", help="Also serve fake demo APIs.")

    lint = commands.add_parser("lint-data", help="Check a CSV file against a ruleset.")
    lint.add_argument("file", type=Path)
    lint.add_argument("--rules", type=Path, required=True)
    lint.add_argument("--columns", help='Only check these columns, comma-separated: "A,B".')

    guard = commands.add_parser("guard-validate",
                                help="Check a data guard config and every rules file it uses.")
    guard.add_argument("config", type=Path)

    explain = commands.add_parser("explain",
                                  help="Explain a guard check from the run log.")
    explain.add_argument("run_id", nargs="?",
                         help="The run id from the agent's block message. Default: the "
                              "latest check that wasn't a clean pass.")

    args = parser.parse_args(argv)
    if args.command == "dq-serve":
        return serve_command(args.host, args.port, args.demo)
    if args.command == "guard-validate":
        return guard_validate_command(args.config)
    if args.command == "explain":
        return explain_command(args.run_id)
    columns = [name.strip() for name in args.columns.split(",")] if args.columns else None
    return lint_command(args.file, args.rules, columns)


def serve_command(host: str, port: int, demo: bool) -> int:
    import uvicorn

    from probagent.dq.server import create_app, make_demo_token

    demo_token = None
    if demo:
        demo_token = make_demo_token()
        base = f"http://{host}:{port}"
        print("Demo APIs are on:")
        print(f"  Open:   {base}/demo/api/orders          (data path: data)")
        print(f"  Login:  {base}/demo/api/secure-orders   (data path: data)")
        print(f"  Demo JWT token for the login API:\n  {demo_token}\n")

    print(f"Open http://{host}:{port} in your browser.")
    uvicorn.run(create_app(demo=demo, demo_token=demo_token), host=host, port=port)
    return EXIT_OK


def lint_command(file: Path, rules_file: Path, columns: list[str] | None = None) -> int:
    """Check a CSV file against a rules file, and write the run to the run log.

    columns: only check these columns. None means all columns.
    """
    with RunLog().track(FEATURE, "check", origin="cli") as run:
        try:
            dataset = load_csv_bytes(file.read_bytes(), source=file.name)
            ruleset = parse_ruleset(json.loads(rules_file.read_text(encoding="utf-8")))
        except OSError as error:
            run.fail("file_not_readable", f"Could not read {error.filename}.")
            print(f"Could not read a file: {error}", file=sys.stderr)
            return EXIT_INVALID
        except LoadError as error:
            run.fail("load_failed", str(error))
            print(f"Could not load {file.name}: {error}", file=sys.stderr)
            return EXIT_INVALID
        except json.JSONDecodeError as error:
            run.fail("rules_not_json", f"{rules_file.name} is not valid JSON.")
            print(f"{rules_file.name} is not valid JSON: {error}", file=sys.stderr)
            return EXIT_INVALID
        except RulesetError as error:
            run.fail("invalid_rules", rules_error_message(error.errors))
            print(f"{rules_file.name} has problems:", file=sys.stderr)
            for problem in error.errors:
                print(f"  - {problem}", file=sys.stderr)
            return EXIT_INVALID

        run.set_inputs(**check_inputs(dataset, ruleset, columns))
        report = check_dataset(dataset, ruleset, columns)
        run.set_outputs(**check_outputs(report))

    print(format_report(report))
    return EXIT_ISSUES if report.summary.issue_count else EXIT_OK


def guard_validate_command(config: Path) -> int:
    """Load the config the same way the guard does, and list each source."""
    from probagent.guard.config import GuardConfigError, load_config

    try:
        sources = load_config(config)
    except GuardConfigError as error:
        print(f"{config.name} has problems:", file=sys.stderr)
        for problem in error.errors:
            print(f"  - {problem}", file=sys.stderr)
        return EXIT_INVALID

    print(f"{config.name} is valid. Guarded sources:")
    for name, source in sources.items():
        policy = source.policy
        columns = ", ".join(policy.columns) if policy.columns else "all"
        print(f"  {name}: rules \"{source.ruleset.name}\" ({len(source.ruleset.fields)} "
              f"columns), format {policy.format}, on_fail {policy.on_fail}, "
              f"columns checked: {columns}")
    return EXIT_OK


def explain_command(run_id: str | None) -> int:
    """Print why a guard check passed, blocked, warned or filtered."""
    from probagent.guard.explain import explain, find_entry

    log = RunLog()
    entry = find_entry(log.read(), run_id)
    if entry is None:
        if run_id:
            print(f"No guard check with run id {run_id} in {log.path}.", file=sys.stderr)
        else:
            print(f"No blocked or flagged guard checks in {log.path}.", file=sys.stderr)
        return EXIT_INVALID
    print(explain(entry))
    return EXIT_OK


def format_report(report: Report) -> str:
    """The report as plain text for the terminal."""
    s = report.summary
    lines = [
        f"Data Quality Report: {report.source} (rules: {report.ruleset_name})",
        "Columns: " + (", ".join(report.columns) if report.columns else "all"),
        f"{s.rows_checked} rows checked, {s.issue_count} issues, "
        f"{s.rows_checked - s.rows_with_issues} clean rows ({s.clean_rows_pct}%)",
    ]
    lines += [f"Warning: {warning}" for warning in report.warnings]
    if report.issues:
        lines.append("")
        lines.append(f"{'Row':>5}  {'Column':<15} {'Code':<18} Problem")
        for issue in report.issues:
            row = str(issue.row) if issue.row is not None else "-"
            lines.append(f"{row:>5}  {issue.column or '-':<15} {issue.code:<18} "
                         f"{issue.message}")
    if report.truncated:
        lines.append(f"\nShowing the first {len(report.issues)} of {s.issue_count} issues.")
    return "\n".join(lines)
