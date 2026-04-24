import sys
from pathlib import Path

from .app import TreesizeApp


def main() -> None:
    if len(sys.argv) != 2:
        print(f"Usage: {Path(sys.argv[0]).name} <find_output.txt>", file=sys.stderr)
        print("  Generate input with: find /some/path -ls > snapshot.txt", file=sys.stderr)
        sys.exit(1)

    input_file = sys.argv[1]
    if not Path(input_file).exists():
        print(f"Error: file not found: {input_file}", file=sys.stderr)
        sys.exit(1)

    TreesizeApp(input_file).run()


if __name__ == "__main__":
    main()
