"""Model-free checks by default; speech smoke requires explicit local assets."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.core.windows_rocm.smoke import main

if __name__ == "__main__":
    raise SystemExit(main())
