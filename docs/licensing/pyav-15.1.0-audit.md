# PyAV 15.1.0 binary licence audit

Checked 2026-10-03 against the repository's `uv.lock`. This records binary
evidence, not commercial clearance or a complete release notice/source package.

## Result

All seven locked CPython 3.11 wheels were downloaded from their locked PyPI URLs
and matched their SHA-256 hashes. Their seven embedded FFmpeg libraries were
inspected without executing foreign-platform binaries. The audit covers macOS
arm64/x86_64, Windows amd64, and Linux glibc/musl arm64/x86_64.

Every wheel also bundles x264 and x265 shared libraries. Every inspected FFmpeg library contains an LGPL version 3 or later licence string and
build options enabling `libx264`, `libx265`, shared libraries, and version 3.
The absence of `--enable-gpl` is **not evidence that the bundled codecs are
cleared for proprietary redistribution**.

The PyAV `v15.1.0` wheel workflow selects the vendor manifest
`scripts/ffmpeg-7.1.json`, which points to `pyav-ffmpeg` release `7.1.1-6`.
That vendor tree's FFmpeg patch removes `libx264` and `libx265` from
`EXTERNAL_LIBRARY_GPL_LIST` and places them in the general external-library
list. This explains why the configure flags and runtime licence labels do not
by themselves settle the linked codec terms. The vendor tag resolves to
`f665a9654da201e8d5c9ec2b7597ae90fbdedba5`.

The wheel notice inventory found one licence-named file per wheel: PyAV's own
`dist-info/licenses/LICENSE.txt`. That does not establish complete FFmpeg and
transitive-codec notices or corresponding-source availability. A filename scan
alone also cannot establish that no other attribution text exists in a binary.

## Sources and retained evidence

- [PyAV v15.1.0 wheel workflow](https://github.com/PyAV-Org/PyAV/blob/v15.1.0/.github/workflows/tests.yml)
- [PyAV v15.1.0 vendor manifest](https://github.com/PyAV-Org/PyAV/blob/v15.1.0/scripts/ffmpeg-7.1.json)
- [Matching vendor FFmpeg patch](https://github.com/PyAV-Org/pyav-ffmpeg/blob/f665a9654da201e8d5c9ec2b7597ae90fbdedba5/patches/ffmpeg.patch)
- [FFmpeg's component-licensing documentation](https://ffmpeg.org/doxygen/trunk/md_LICENSE.html)
- [PyAV maintainer discussion of source versus wheel terms](https://github.com/PyAV-Org/PyAV/issues/2270): this discussion concerns other wheel versions and is context, not licence clearance for these exact artifacts.

The [accompanying JSON](pyav-15.1.0-wheels.json) records each wheel URL/hash, each inspected library's
member path/hash, build configuration, reported licence string, and notice-file
inventory. The [inspection script](../../scripts/audit_pyav_wheels.py) can regenerate that evidence from `uv.lock`:

```sh
python3.11 scripts/audit_pyav_wheels.py --cache-dir /tmp/pyav-audit-wheels --output /tmp/pyav-audit.json
```

The command downloads the seven pinned wheels (roughly 230 MB) on an empty cache; it does not install or execute them.
A PyAV version or wheel-hash change requires another audit.

## Remaining work

Resolve the redistribution terms of the exact linked x264/x265 artifacts before
using these wheels in Pro. Available implementation paths to assess include
building audited wheels without those codecs, or documenting an applicable
licensing route and its complete terms. No commercial exception has been
obtained or assumed by this audit.

For any shipped build, retain the full resolved dependency/source/build inputs,
generate the required notices, and validate the produced installer. This audit
does not cover source-built PyAV, other Python ABI wheels, system FFmpeg,
`imageio-ffmpeg`, the separately downloaded `ffmpeg_bins` executables, or Docker
distribution packages. It does not verify binary reproducibility or substitute
for release-specific provenance.
