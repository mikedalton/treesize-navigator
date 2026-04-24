# treesize-navigator

A terminal UI for navigating directory sizes from a `find -ls` snapshot. Single-pane Midnight Commander-style interface — browse into directories, see sizes ranked largest-first, and navigate back up.

## Usage

Generate a snapshot, then open it:

```bash
find /some/path -ls > snapshot.txt
treesize-navigator snapshot.txt
```

## Keyboard shortcuts

| Key | Action |
|-----|--------|
| ↑ / ↓ | Move cursor |
| Enter / → | Open selected directory |
| Backspace / ← | Go up to parent |
| q | Quit |

## Installation

```bash
git clone <repo>
cd treesize-navigator
python3 -m venv .venv && .venv/bin/pip install -e .
ln -sf "$PWD/.venv/bin/treesize-navigator" ~/.local/bin/treesize-navigator
```
