# `dirsize` — Design & Approach Document

A reference for porting or extending the directory-size analyser to another
language (e.g. Python). Covers the problem framing, input format, every
algorithmic decision, and the traps that were deliberately avoided.

---

## 1. Problem framing

The script answers the question: **"How much disk space does this subtree
consume?"** — but without access to the live filesystem. The input is a
plain-text snapshot produced by:

```sh
find /some/path -ls > snapshot.txt
```

The key constraints that shaped every decision:

- The file can be arbitrarily large (millions of inodes), so streaming/single-pass
  is preferred over loading everything into memory.
- The search path is user-supplied at runtime, not baked in.
- The output must be immediately actionable: sizes ranked largest-first so the
  biggest consumers are obvious at a glance.

---

## 2. Input format: `find -ls`

`find -ls` prints one line per filesystem entry. Each line has **at least 11
whitespace-separated columns**:

```
inode   blocks  perms       links  owner   group   SIZE   month  day  time/year  PATH
12349   64      -rw-r--r--  1      alice   staff   32768  Apr    1    10:04      /mypath/directory/subdir/data.bin
```

| Column | Index (1-based) | Used? | Notes |
|--------|-----------------|-------|-------|
| inode  | 1 | No | |
| blocks | 2 | No | Unit varies: 512 B on macOS/BSD, 1 KB on GNU — deliberately ignored |
| perms  | 3 | No | |
| links  | 4 | No | |
| owner  | 5 | No | |
| group  | 6 | No | |
| **size** | **7** | **Yes** | Always in **bytes**, consistent across platforms |
| month  | 8 | No | |
| day    | 9 | No | |
| time/year | 10 | No | |
| **path** | **11** | **Yes** | Full absolute path |

### Why column 7 (bytes) and not column 2 (blocks)?

Block counts are the most natural "disk usage" metric, but their unit differs
between macOS/BSD (`find` reports 512-byte blocks) and GNU/Linux (1 KB blocks).
Column 7 is always bytes on every platform, making the script portable without
any platform-detection logic.

### Why `NF >= 11`?

The format guarantees 11 columns only for normal entries. Blank lines,
comment lines injected by piping through other tools, or truncated lines from
interrupted `find` runs could have fewer fields. Skipping lines with fewer
than 11 fields is a cheap guard that prevents column mis-reads.

---

## 3. Path matching

### Normalise the search path first

Strip any trailing slash from the user-supplied path before doing any
comparisons. This lets `/foo/` and `/foo` behave identically throughout the
rest of the logic, with no special cases needed downstream.

### The prefix-collision problem

A naïve prefix match — `path.startswith(search_path)` — would make
`/mypath/directory` accidentally match `/mypath/directoryX`. The fix is to
always append a `/` to the search path before doing the prefix check:

```
filepath == search_path              → the root directory entry itself
filepath.startswith(search_path + "/") → a descendant
```

These two conditions together correctly match the whole subtree and nothing
outside it.

---

## 4. Single-pass aggregation

The core loop does everything in one pass over the file:

1. **Classify each line** — is it the root, a descendant, or irrelevant?
   Irrelevant lines are skipped immediately.
2. **Accumulate the total** — add the byte size of every matching line
   (root + all descendants) to a running total.
3. **Attribute to a first-level child** — for descendants, determine which
   immediate child of the search path they belong to, and add their size to
   that child's bucket.

### Determining the first-level child

Strip the search path prefix (plus the `/` separator) from the filepath to
get the relative remainder. The first-level child name is everything up to
the first `/` in that remainder — or the whole remainder if there is no `/`
(meaning the entry is a direct child file, not nested deeper).

```
search_path  = /mypath/directory
filepath     = /mypath/directory/subdir/data.bin

remainder    = subdir/data.bin          (strip prefix + "/")
child_name   = subdir                   (take up to first "/")
child_key    = /mypath/directory/subdir (reassemble full path for the bucket key)
```

Using the **full path** as the bucket key (rather than just the bare name)
avoids collisions if two different subdirectories share the same leaf name.

### The root directory entry

The root directory entry (`filepath == search_path`) contributes to the total
but is **not** attributed to any first-level child bucket. It represents the
directory's own inode metadata, not the content inside it. Including it in the
total keeps the reported number consistent with what `du` would report.

---

## 5. Sorting

Children are sorted by size **descending** at output time, not during
accumulation. This keeps the aggregation logic simple (just a dictionary/map
of `{child_path: total_bytes}`) and defers ordering to a separate, trivial
step. The largest consumers appear first, which is the most useful ordering
for a tool whose purpose is to identify what is taking up space.

---

## 6. Human-readable formatting

Sizes are displayed in the largest unit that keeps the integer part non-zero,
with two decimal places:

| Range | Unit |
|-------|------|
| < 1,024 | bytes |
| 1,024 – 1,048,575 | KB |
| 1,048,576 – 1,073,741,823 | MB |
| ≥ 1,073,741,824 | GB |

The raw byte total is always shown alongside the human-readable figure in the
summary line, so the number can be used in calculations or scripts without
losing precision to rounding.

---

## 7. Error handling

Three distinct failure modes, each with its own exit code:

| Condition | Exit code | Output |
|-----------|-----------|--------|
| Wrong number of arguments | 1 | Usage message to stderr |
| Input file missing or unreadable | 1 | Specific error to stderr |
| Search path not found in file | 2 | "No entries found" to stderr |

Exit code 2 for "path not found" is intentionally distinct from exit code 1
(invocation errors) so that callers can tell the difference
programmatically.

---

## 8. Output layout

```
Path: /mypath/directory
────────────────────────────────────────────────────────────
  768.50 KB             images
  33.12 KB              subdir
  16.00 KB              file2.log
  4.00 KB               file1.txt
────────────────────────────────────────────────────────────
  821.88 KB             TOTAL (841600 bytes)
```

Design choices:

- Child names are shown **relative** to the search path (not as full absolute
  paths) to reduce noise, since the search path is already shown on the header
  line.
- The human-readable size column has a **fixed width**, so names align into a
  clean second column regardless of size magnitude.
- The total line repeats the raw byte count in parentheses so that the precise
  figure is always available without requiring a separate invocation.

---

## 9. Porting to Python

The algorithm maps cleanly to Python. Rough sketch:

```python
import sys
from collections import defaultdict
from pathlib import PurePosixPath

def human_readable(n: int) -> str:
    for unit, threshold in [("GB", 1 << 30), ("MB", 1 << 20), ("KB", 1 << 10)]:
        if n >= threshold:
            return f"{n / threshold:.2f} {unit}"
    return f"{n} bytes"

def analyse(input_file: str, search_path: str) -> None:
    search_path = search_path.rstrip("/")
    prefix = search_path + "/"

    total = 0
    children: dict[str, int] = defaultdict(int)
    found = False

    with open(input_file) as fh:
        for line in fh:
            parts = line.split()
            if len(parts) < 11:
                continue

            size     = int(parts[6])   # column 7, 0-indexed → 6
            filepath = parts[10]       # column 11, 0-indexed → 10

            is_root  = (filepath == search_path)
            is_child = filepath.startswith(prefix)

            if not is_root and not is_child:
                continue

            found = True
            total += size

            if is_root:
                continue

            # First-level child: take the path segment immediately after the prefix
            remainder  = filepath[len(prefix):]
            slash      = remainder.find("/")
            child_name = remainder[:slash] if slash != -1 else remainder
            child_key  = prefix + child_name
            children[child_key] += size

    if not found:
        print(f"No entries found matching path: {search_path}", file=sys.stderr)
        sys.exit(2)

    # Sort children largest-first
    ranked = sorted(children.items(), key=lambda kv: kv[1], reverse=True)

    print(f"Path: {search_path}")
    print("─" * 60)
    for child_path, child_bytes in ranked:
        child_name = child_path[len(prefix):]
        print(f"  {human_readable(child_bytes):<20}  {child_name}")
    print("─" * 60)
    print(f"  {human_readable(total):<20}  TOTAL ({total} bytes)")

if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(f"Usage: {sys.argv[0]} <find_output.txt> <search_path>", file=sys.stderr)
        sys.exit(1)
    analyse(sys.argv[1], sys.argv[2])
```

Notable translation notes:

- `parts[6]` and `parts[10]` replace awk's `$7` and `$11` (0-indexed vs 1-indexed).
- `str.split()` with no argument splits on any whitespace and strips leading/trailing
  whitespace, which is equivalent to awk's default field splitting.
- `collections.defaultdict(int)` is the natural equivalent of awk's associative
  array with implicit zero initialisation.
- Sorting is a one-liner with `sorted(..., key=..., reverse=True)` rather than a
  separate shell `sort -rn` call.
- `str.startswith(prefix)` is the direct equivalent of awk's
  `substr(filepath, 1, prefix_len) == prefix`.
- No external processes (`bc`, `awk`, `sort`, `grep`) are needed — everything is
  pure Python.
