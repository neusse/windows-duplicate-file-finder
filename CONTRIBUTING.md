# Contributing

This project is early-stage and optimized for local Windows duplicate-file inventory work.

## Development Setup

```powershell
python -m pip install -r .\requirements.txt
python -m pytest -q
```

## Guidelines

- Keep duplicate detection scoped by `machine_name` unless a feature is explicitly about cross-machine comparison.
- Do not commit SQLite inventory files or local config.
- Keep scan behavior conservative. The app should find duplicates, not delete files.
- Add tests for database schema changes, reset behavior, and duplicate queries.
