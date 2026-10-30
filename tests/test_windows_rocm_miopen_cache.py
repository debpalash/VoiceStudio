"""Keep the pinned Windows HIP disk-cache workaround narrowly scoped."""

import importlib
from types import SimpleNamespace

import pytest


@pytest.fixture
def runtime():
    return importlib.import_module("core.windows_rocm.runtime")


@pytest.fixture
def cache_environment(monkeypatch, runtime):
    monkeypatch.setattr(runtime, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(runtime.metadata, "version", lambda package: "2.9.1+rocm7.2.1")
    monkeypatch.setattr(runtime, "dll_directories", lambda: [])
    monkeypatch.setenv("MIOPEN_DISABLE_CACHE", "")
    monkeypatch.delenv("MIOPEN_DISABLE_CACHE")
    monkeypatch.setenv("MIOPEN_CUSTOM_CACHE_DIR", "")
    monkeypatch.delenv("MIOPEN_CUSTOM_CACHE_DIR")
    return monkeypatch


@pytest.mark.parametrize(
    "cache_path",
    ["D:/VoiceStudio/Za\u017c\u00f3\u0142\u0107 cache", "D:/VoiceStudio/\u65e5\u672c\u8a9e"],
)
def test_unicode_custom_cache_disables_only_disk_cache(cache_environment, cache_path, runtime):
    cache_environment.setenv("MIOPEN_CUSTOM_CACHE_DIR", cache_path)

    runtime.initialize_dll_search()
    runtime.initialize_dll_search()

    assert runtime.os.environ["MIOPEN_DISABLE_CACHE"] == "1"
    assert runtime.os.environ["MIOPEN_CUSTOM_CACHE_DIR"] == cache_path


@pytest.mark.parametrize("cache_path", [None, "", "D:/VoiceStudio/cache", "D:/Voice Studio/cache"])
def test_ascii_or_default_cache_keeps_disk_cache(cache_environment, cache_path, runtime):
    if cache_path is not None:
        cache_environment.setenv("MIOPEN_CUSTOM_CACHE_DIR", cache_path)

    runtime.initialize_dll_search()

    assert "MIOPEN_DISABLE_CACHE" not in runtime.os.environ


@pytest.mark.parametrize("override", ["0", "1", "", "false", "true"])
def test_explicit_cache_override_is_preserved(cache_environment, override, runtime):
    cache_environment.setenv("MIOPEN_CUSTOM_CACHE_DIR", "D:/Za\u017c\u00f3\u0142\u0107/cache")
    cache_environment.setenv("MIOPEN_DISABLE_CACHE", override)

    runtime.initialize_dll_search()

    assert runtime.os.environ["MIOPEN_DISABLE_CACHE"] == override


@pytest.mark.parametrize("platform", ["linux", "darwin"])
def test_other_platforms_keep_cache(cache_environment, platform, runtime):
    cache_environment.setattr(runtime, "sys", SimpleNamespace(platform=platform))
    cache_environment.setenv("MIOPEN_CUSTOM_CACHE_DIR", "D:/Za\u017c\u00f3\u0142\u0107/cache")

    runtime.initialize_dll_search()

    assert "MIOPEN_DISABLE_CACHE" not in runtime.os.environ


@pytest.mark.parametrize(
    "version",
    ["2.9.1", "2.9.1+cu128", "2.9.1+cpu", "2.10.0+rocm7.2.1", "2.9.1+rocm7.2.2"],
)
def test_other_torch_builds_keep_cache(cache_environment, version, runtime):
    cache_environment.setattr(runtime.metadata, "version", lambda package: version)
    cache_environment.setenv("MIOPEN_CUSTOM_CACHE_DIR", "D:/Za\u017c\u00f3\u0142\u0107/cache")

    runtime.initialize_dll_search()

    assert "MIOPEN_DISABLE_CACHE" not in runtime.os.environ


def test_missing_torch_keeps_cache(cache_environment, runtime):
    def missing_version(package):
        raise runtime.metadata.PackageNotFoundError(package)

    cache_environment.setattr(runtime.metadata, "version", missing_version)
    cache_environment.setenv("MIOPEN_CUSTOM_CACHE_DIR", "D:/Za\u017c\u00f3\u0142\u0107/cache")

    runtime.initialize_dll_search()

    assert "MIOPEN_DISABLE_CACHE" not in runtime.os.environ


def test_cache_is_prepared_before_native_directories(cache_environment, runtime):
    observed = []
    cache_environment.setenv("MIOPEN_CUSTOM_CACHE_DIR", "D:/Za\u017c\u00f3\u0142\u0107/cache")
    def directories():
        observed.append(runtime.os.environ.get("MIOPEN_DISABLE_CACHE"))
        return []

    cache_environment.setattr(runtime, "dll_directories", directories)

    runtime.initialize_dll_search()

    assert observed == ["1"]
