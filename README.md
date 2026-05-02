# Windows Duplicate File Finder

A local Windows duplicate-file inventory tool with a browser UI, SQLite storage, selected-drive scans, machine-scoped duplicate detection, configurable directory blacklists, and a treemap-style directory size view.

The app is designed for large file inventories. It keeps the heavy work in Python and SQLite, while the browser UI uses server-side pagination so massive duplicate lists are not loaded all at once.

## Features

- Scan one selected drive, several selected drives, or all detected volumes.
- Store inventory in SQLite with the Windows machine name attached to every scan.
- Keep duplicate reports scoped to one machine, so files from a desktop and laptop are not mixed together.
- Find quick duplicate candidates by size and name.
- Hash only duplicate-size candidates for stronger SHA-256 confirmation.
- Browse duplicate groups and group members in a local web UI.
- View top-level directory size as a treemap.
- Reset stale inventory by latest scan or all scans for the selected machine.
- Configure blacklisted system directories and local path patterns.

## Requirements

- Windows
- Python 3.11 or newer
- PowerShell

Install dependencies:

```powershell
python -m pip install -r .\requirements.txt
```

## Start the Web App

From the project folder:

```powershell
python .\file_indexer.py --db .\file_index.sqlite web --host 127.0.0.1 --port 8000
```

Open:

```text
http://127.0.0.1:8000
```

Basic workflow:

1. Pick the machine name.
2. Pick a drive such as `C:\`, `D:\`, or `E:\`.
3. Click `Scan Drive`.
4. Review duplicate groups in `Size + name` mode.
5. Click `Hash Candidates` for SHA-256 confirmation.
6. Switch the mode to `SHA-256` after hashing.

## Command Line Usage

List detected drives:

```powershell
python .\file_indexer.py --db .\file_index.sqlite volumes
```

Scan one drive:

```powershell
python .\file_indexer.py --db .\file_index.sqlite scan --drive D:\
```

Scan several drives:

```powershell
python .\file_indexer.py --db .\file_index.sqlite scan --drive C:\ --drive D:\
```

Scan every detected volume:

```powershell
python .\file_indexer.py --db .\file_index.sqlite scan --all-drives
```

Hash duplicate-size candidates:

```powershell
python .\file_indexer.py --db .\file_index.sqlite hash
```

Report duplicates:

```powershell
python .\file_indexer.py --db .\file_index.sqlite report --mode size_name --limit 100
python .\file_indexer.py --db .\file_index.sqlite report --mode sha256 --limit 100
```

Reset stale inventory:

```powershell
python .\file_indexer.py --db .\file_index.sqlite reset --scope latest
python .\file_indexer.py --db .\file_index.sqlite reset --scope all --vacuum
```

## Machine-Scoped Data

Each scan stores `machine_name`, defaulting to the Windows computer name. Duplicate queries, hash jobs, reports, and resets are scoped to that machine by default. This prevents files from different machines from being reported as duplicates of each other.

Use `--machine` only when you deliberately need to override the detected computer name:

```powershell
python .\file_indexer.py --db .\file_index.sqlite --machine LAPTOP scan --drive C:\
```

Avoid running multiple active machines against the same live SQLite file through OneDrive or a network sync folder. SQLite creates `-wal` and `-shm` sidecar files, and sync tools can corrupt or conflict with active databases. Prefer separate database files per machine or export/import later.

## Blacklisted Directories

Default scan exclusions live in `config.default.toml`. Create `config.local.toml` for your own overrides; it is intentionally ignored by Git so local machine rules do not get published.

The default blacklist skips common Windows system, recovery, cache, and protected folders:

- `Windows`
- `Users\georg\AppData`
- `Program Files`
- `Program Files (x86)`
- `ProgramData`
- `Recovery`
- `PerfLogs`
- `Documents and Settings`
- `MSOCache`
- `Windows.old`
- `$RECYCLE.BIN`
- `System Volume Information`

These are excluded because they are usually noisy, protected, volatile, or not useful for personal duplicate cleanup. Scanning them can produce permission errors, slow scans, or duplicate groups that should not be touched.

Example local override:

```toml
[scan]
only_user = "georg"
follow_hidden_dirs = false

exclude_dirnames = [
  "Windows",
  "Program Files",
  "Program Files (x86)",
  "ProgramData",
  "$RECYCLE.BIN",
  "System Volume Information",
  "Users\\georg\\AppData",
  "Games\\SteamLibrary",
]

exclude_globs = [
  "D:\\Backups\\*",
]
```

Set `only_user = ""` to disable the `C:\Users\<name>` restriction and scan all user profiles.

## Development

Run tests:

```powershell
python -m pytest -q
```

The repository intentionally ignores SQLite inventory files, Python caches, virtual environments, local config, and logs.

## Safety

This is a finder, not a deleter. It inventories files and reports duplicate candidates. It does not delete, move, or modify the files it scans.
