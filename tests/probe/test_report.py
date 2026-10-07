"""Offline tests for the HTML report system itself (no browser is opened)."""

from __future__ import annotations

from . import report as R
from .spec import JudgeResult


def _sample_report() -> R.Report:
    outcome = R.SpecOutcome(
        name="tts-synthesis",
        feature="tts-synthesis",
        layer="media",
        duration_s=0.42,
        results=[
            JudgeResult("artifact_exists", True, "exists (1024 bytes)", measured=1024),
            JudgeResult("asr_wer_below", False, "WER=0.40 (max 0.15)", measured=0.40),
            JudgeResult("speaker_similarity_above", None, "skipped: no embedder"),
            JudgeResult("not_clipping", True, "peak=0.7", measured=0.7, advisory=True),
        ],
    )
    return R.Report(outcomes=[outcome])


def test_tallies_and_verdict():
    rep = _sample_report()
    assert (rep.total, rep.passed, rep.failed, rep.skipped, rep.advisory) == (4, 1, 1, 1, 1)
    assert rep.ok is False  # one blocking failure
    # advisory failures never flip the verdict
    rep2 = R.Report(outcomes=[R.SpecOutcome(name="x", results=[
        JudgeResult("a", True), JudgeResult("b", False, advisory=True)])])
    assert rep2.ok is True


def test_render_html_is_self_contained():
    html = R.render_html(_sample_report())
    assert html.startswith("<!doctype html>")
    assert "<style>" in html and "<script>" in html  # inline, no external assets
    assert "http://" not in html and "https://" not in html  # no remote deps
    assert "tts-synthesis" in html
    assert ">FAIL<" in html and ">PASS<" in html and ">SKIP<" in html
    assert "advisory" in html


def test_html_escapes_detail():
    rep = R.Report(outcomes=[R.SpecOutcome(name="x", results=[
        JudgeResult("inj", False, "heard '<script>alert(1)</script>'")])])
    html = R.render_html(rep)
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_write_creates_files_without_opening(tmp_path):
    path = R.save_and_open(_sample_report(), out_dir=tmp_path, open_browser=False)
    assert path.exists() and path.read_text(encoding="utf-8").startswith("<!doctype html>")
    assert (tmp_path / "report-latest.html").exists()


def test_should_open_respects_env(monkeypatch):
    monkeypatch.setenv("PROBE_NO_OPEN", "1")
    assert R._should_open(None) is False
    monkeypatch.delenv("PROBE_NO_OPEN", raising=False)
    monkeypatch.setenv("CI", "true")
    assert R._should_open(None) is False
    assert R._should_open(True) is True  # explicit override wins


def test_report_never_opens_by_default_even_with_a_display(monkeypatch):
    for name in ("PROBE_NO_OPEN", "PROBE_OPEN", "CI"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DISPLAY", ":0")
    assert R._should_open(None) is False
    monkeypatch.setenv("PROBE_OPEN", "1")
    assert R._should_open(None) is True
