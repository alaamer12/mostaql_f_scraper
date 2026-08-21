"""CLI entry point for running the DuckDB Analytics Dashboard directly.

Usage examples:
    python dashboard -i collected/analysis.json
    python dashboard -i file1.json file2.json
    python dashboard -i file1.json -i file2.json
    python -m dashboard -i collected/*.json --port 8080
"""

import sys
from pathlib import Path

# When invoked directly via `python dashboard`, ensure parent directory is in sys.path
package_dir = Path(__file__).resolve().parent
parent_dir = package_dir.parent
if str(parent_dir) not in sys.path:
    sys.path.insert(0, str(parent_dir))

if __package__ is None or __package__ == "":
    from dashboard.dashboard import run_cli
else:
    from .dashboard import run_cli

if __name__ == "__main__":
    run_cli()
