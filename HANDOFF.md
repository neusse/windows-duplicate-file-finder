# Project Handoff

Last Updated Local: 2026-05-02 19:13 America/Los_Angeles
Last Updated UTC: 2026-05-03T02:13:40Z
Stale After Hours: 24
Staleness: FRESH

## Project

- Path: `C:\Users\georg\Codex_Projects\windows-duplicate-file-finder`
- Git: initialized on branch `main`; `origin` points to `https://github.com/neusse/windows-duplicate-file-finder.git`.
- GitHub: public repository at `https://github.com/neusse/windows-duplicate-file-finder`.
- Original state: no `HANDOFF.md` existed.

## Current State

- The project is a Windows duplicate file finder that inventories files into SQLite.
- The implementation supports machine-scoped scans, selected-drive scans, configurable excludes, reset/reinventory, duplicate browsing, and a local FastAPI web UI.
- GitHub-facing documentation now explains setup, usage, machine scoping, SQLite sync caveats, reset commands, and blacklisted directories.
- Existing March 2026 SQLite inventory files were deleted at user request so the next scan starts fresh.
- Local web server is currently responding at `http://127.0.0.1:8000`.
- Validation at dropoff: `python -m pytest -q` passed with `3 passed`.
- GitHub repository verification at dropoff: public, default branch `main`, description set.

## Important Files

- `file_indexer.py`: CLI, SQLite schema/migration, scanner, hashing, reset, FastAPI backend.
- `config.default.toml`: default blacklist and scan behavior.
- `web/index.html`, `web/styles.css`, `web/app.js`: local browser UI.
- `tests/test_file_indexer.py`: focused tests for machine scoping, reset, and blacklist behavior.
- `README.md`, `.gitignore`, `.gitattributes`, `LICENSE`, `CONTRIBUTING.md`: GitHub publication files.

## Resume Steps

1. Install dependencies: `python -m pip install -r .\requirements.txt`
2. Check repo state: `git status --short --branch --ignored`
3. Run tests: `python -m pytest -q`
4. If the web app is not already running, start it: `python .\file_indexer.py --db .\file_index.sqlite web --host 127.0.0.1 --port 8000`
5. Open `http://127.0.0.1:8000`.
6. Pick a drive and run a fresh scan.
7. Push future source changes with `git push`.

## Known Risks

- New databases are created on demand. Back up important future DBs before heavy experimentation.
- The directory treemap summarizes top-level folders for the selected scan; deeper drilldown can be added later.
- Background scan/hash status is process-local. If the web server stops, the job record in memory is lost, though completed scan data remains in SQLite.
- Runtime SQLite files are ignored by Git. Verify `git status --ignored` if a future DB appears unexpectedly.

## Change Log

- 2026-05-02: Dropoff refresh performed; tests pass, local web server responds, GitHub repo remains public and synced.
- 2026-05-01: Published public GitHub repository `neusse/windows-duplicate-file-finder` and set the repo description.
- 2026-05-01: Initialized Git on `main` and verified SQLite inventory files are ignored.
- 2026-05-01: Added GitHub-ready README, ignore rules, attributes, MIT license, and contribution notes.
- 2026-05-01: Deleted stale March SQLite inventory files at user request.
- 2026-05-01: Created initial handoff and implemented the planned local web app, selected-drive scanning, machine identity, configurable blacklist, reset controls, and tests.
