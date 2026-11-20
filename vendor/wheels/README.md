# Vendored wheels

Pure-Python packages that PyPI publishes only as source tarballs. `uv sync`
would compile them during first-run setup, and that build crashes setuptools on
some Windows hosts (#4253), so `[tool.uv.sources]` in `pyproject.toml` points
them here and `uv.lock` pins each wheel's sha256.

| Package | Licence |
|---|---|
| docopt 0.6.2 | MIT (`LICENSE-MIT` kept in the wheel's dist-info) |
| dora-search 0.1.12 | MIT (`LICENSE` kept in dist-info) |
| antlr4-python3-runtime 4.9.3 | BSD-3-Clause; the upstream sdist ships no licence file, so the ANTLR project notice is kept in `LICENSE-antlr4-python3-runtime.txt` |

Each wheel is the unmodified sdist built with the pinned backend in
`build-constraints.txt`. `SOURCES.json` records every sdist URL and sha256
(the values `uv.lock` pinned before vendoring).

## Regenerate or verify

```
uv run python scripts/build_vendored_wheels.py --verify   # rebuild, compare every file's content
uv run python scripts/build_vendored_wheels.py            # rebuild the committed wheels
uv lock --refresh                                         # re-pin the new wheel hashes
```

The script downloads only the sdist URLs in `SOURCES.json` and checks their
sha256 first. `tests/test_vendored_wheels.py` runs offline in CI: wheel hash
equals `uv.lock`, RECORD is consistent, only `.py`/data/metadata files plus the
sdist's declared script, no native code or `.pth`.

To add a package: put its sdist URL and sha256 in `SOURCES.json`, declare it in
`pyproject.toml` with a `[tool.uv.sources]` path entry, build, then `uv lock`.
`tests/test_lock_wheel_only.py` fails when a default-install package has no wheel.
