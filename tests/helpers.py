"""Shared test helpers. Run as a script to regenerate examples/sample-output.json."""

import io
import json
import sys
from contextlib import redirect_stdout
from pathlib import Path

FIXED_TIMESTAMP = "2026-01-01 12:00:00"
SAMPLE_PATH = Path(__file__).resolve().parents[1] / "examples" / "sample-output.json"


def run_cli(argv):
    """Call dtemp.cli.main, returning (exit_code, stdout_text)."""
    from dtemp.cli import main

    buf = io.StringIO()
    with redirect_stdout(buf):
        try:
            code = main(argv)
        except SystemExit as exc:  # argparse errors and --version
            code = exc.code
    return code, buf.getvalue()


def synthetic_json():
    code, out = run_cli(["--simulate", "--json", "--no-color"])
    assert code == 0
    rows = json.loads(out)
    return [{**row, "timestamp": FIXED_TIMESTAMP} for row in rows]


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    SAMPLE_PATH.write_text(json.dumps(synthetic_json(), indent=2) + "\n", encoding="utf-8")
    print(f"wrote {SAMPLE_PATH}")
