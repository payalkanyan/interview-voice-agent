"""Make the scripts directory importable, so the report and eval logic can be tested."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
