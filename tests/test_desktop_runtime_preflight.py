"""Source desktop launchers must catch Linux native/runtime gaps up front."""

import json
import subprocess
from pathlib import Path


_ROOT = Path(__file__).resolve().parents[1]
_PREFLIGHT = (_ROOT / "scripts" / "desktop-runtime-preflight.mjs").as_uri()


def _probe(platform: str, os_release: str, *, xdo: bool, audio_sink: bool):
    """Run the JavaScript preflight against injected platform probe results."""
    script = f"""
      import {{ desktopRuntimeProblem }} from {json.dumps(_PREFLIGHT)};
      const calls = [];
      const problem = desktopRuntimeProblem({{
        platform: {json.dumps(platform)},
        osRelease: {json.dumps(os_release)},
        run: (command, args) => {{
          calls.push([command, args]);
          if (command === "cc") return {{ status: 0, stdout: {json.dumps('/usr/lib/libxdo.so' if xdo else 'libxdo.so')} }};
          return {{ status: {0 if audio_sink else 1} }};
        }},
      }});
      console.log(JSON.stringify({{ calls, problem }}));
    """
    completed = subprocess.run(
        ["node", "--input-type=module", "--eval", script],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def test_non_linux_skips_native_probes() -> None:
    assert _probe("darwin", "", xdo=False, audio_sink=False) == {
        "calls": [],
        "problem": None,
    }


def test_arch_diagnosis_combines_linker_and_webkit_runtime_packages() -> None:
    observed = _probe("linux", 'ID="cachyos"\nID_LIKE="arch"', xdo=False, audio_sink=False)
    assert observed["calls"] == [
        ["cc", ["-print-file-name=libxdo.so"]],
        ["gst-inspect-1.0", ["autoaudiosink"]],
    ]
    assert "libxdo (required by Enigo at link time)" in observed["problem"]
    assert "GStreamer autoaudiosink (required by WebKit audio)" in observed["problem"]
    assert "sudo pacman -S --needed xdotool gst-plugins-good" in observed["problem"]
    assert "blank window" in observed["problem"]


def test_debian_diagnosis_names_only_the_missing_audio_runtime() -> None:
    observed = _probe("linux", "ID=ubuntu\nID_LIKE=debian", xdo=True, audio_sink=False)
    assert "libxdo (required by Enigo at link time)" not in observed["problem"]
    assert "gstreamer1.0-plugins-good" in observed["problem"]


def test_ready_linux_host_has_no_problem() -> None:
    assert _probe("linux", "ID=fedora", xdo=True, audio_sink=True)["problem"] is None


def test_archived_tauri_preflight_is_not_an_active_root_command() -> None:
    for relative_path in ("scripts/desktop-dev.mjs", "scripts/desktop-prod.mjs"):
        launcher = (_ROOT / relative_path).read_text()
        assert 'from "./desktop-runtime-preflight.mjs"' in launcher
        assert "if (!desktopRuntimeReady()) process.exit(1);" in launcher

    scripts = json.loads((_ROOT / "package.json").read_text())["scripts"]
    assert not any("tauri" in name for name in scripts)
    assert scripts["dev"] == "bun run --cwd electron dev"


def test_linux_install_docs_name_the_required_runtime_packages() -> None:
    docs = (_ROOT / "docs/install/linux.md").read_text()
    for package in (
        "gstreamer1.0-plugins-good",
        "gstreamer1-plugins-good",
        "gst-plugins-good",
    ):
        assert package in docs
