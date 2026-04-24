# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

`treesize-navigator` is a Python project for navigating/visualizing directory sizes. The tool uses a CLI-based UI to navigate and report tree sizes of directory tree data loaded from a text file (passed as a parameter) containing the output of `find -ls`. It should have a single-pane "Midnight Commander" style interface.

The files can potentially be very large (up to 2GB or more), so consider what can be done to manage responsiveness.

## Tech Stack

- **Language:** Python 3.11+
- **UI framework:** Textual (TUI, installed in `.venv/`)
- **Linter:** Ruff (`.ruff_cache/` in `.gitignore`)
- **Package manager:** uv preferred; venv fallback if uv unavailable

## Commands

```bash
# Setup (first time, if uv not available)
python3 -m venv .venv && .venv/bin/pip install -e .

# Run
.venv/bin/python -m treesize_navigator <find_output.txt>

# Lint / format
.venv/bin/ruff check .
.venv/bin/ruff format .

# Tests
.venv/bin/pytest
.venv/bin/pytest tests/test_foo.py
```

Generate input with: `find /some/path -ls > snapshot.txt`

## Architecture

### `treesize_navigator/indexer.py` — `FileIndex`

Single streaming pass over the file. For each entry, its byte size is propagated up to **all ancestor directories**, so subtree totals for every possible navigation target are ready when indexing completes. Uses `{dir_path: int}` for subtree totals and `{dir_path: set[str]}` for parent→child relationships.

Key details:
- Columns used from `find -ls`: col 7 (size in bytes, portable across macOS/Linux) and col 11+ joined with spaces (path, handles filenames with spaces).
- Lines with fewer than 11 fields are skipped.
- The `cancelled` callback is checked every 50,000 entries (same cadence as progress updates) to allow clean abort without per-line overhead.

### `treesize_navigator/app.py` — `TreesizeApp`

Textual `App` subclass. The indexer runs in a background thread via `@work(thread=True, exit_on_error=False)`. `exit_on_error=False` is intentional: if `call_from_thread` fails because the event loop has already shut down, Textual's default behavior would invoke its panic handler — suppressing the exception is correct here.

`__main__.py` uses `if __name__ == "__main__": main()` (not a bare `main()` call) because the installed entry point script imports and calls `main()` directly; a bare call would run the app twice.

### Entry point

Installed as an editable package (`pip install -e .`) with a `treesize-navigator` console script, symlinked to `~/.local/bin/`. Source file changes take effect immediately without reinstalling.
