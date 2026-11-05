"""Native console entry point built from the same package as the Python wheel."""
from voicestudio_cli.__main__ import main

if __name__ == '__main__':
    raise SystemExit(main())
