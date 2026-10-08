"""Data Quality checker (see InnDataQuality_Spec.md).

The flow is:  load data  ->  load rules  ->  check  ->  report.

    loader.py / fetch.py   get the data (CSV upload or REST API)
    rules.py               read and validate the JSON ruleset
    infer.py               guess a ruleset from sample data
    validator.py           check every row against every rule
    server.py              the web page and its API
"""
