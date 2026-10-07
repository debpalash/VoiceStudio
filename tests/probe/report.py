"""probe HTML report — render a run's JudgeResults to a self-contained page.

Design goals, matching the harness's ethos:
  - **Self-contained**: one HTML file, inline CSS+JS, zero external assets. It
    renders on any machine, attaches to a GitHub issue, no build step to break.
  - **Honest by construction**: blocking PASS/FAIL drives the verdict; SKIP and
    the ADVISORY lane are visually separated and never counted as failures —
    a green run can never overstate confidence.
  - **Auto-open**: after writing, open it in the browser (suppressed in CI /
    headless / when ``PROBE_NO_OPEN`` is set).

No dependencies beyond the stdlib.
"""

from __future__ import annotations

import html
import os
import webbrowser
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .spec import JudgeResult, Spec


@dataclass
class SpecOutcome:
    """The results of running one spec (or one logical check group)."""

    name: str
    feature: str = ""
    layer: str = ""
    results: list[JudgeResult] = field(default_factory=list)
    duration_s: float = 0.0
    source: str | None = None

    @classmethod
    def from_spec(cls, spec: Spec, results: list[JudgeResult], duration_s: float = 0.0) -> "SpecOutcome":
        return cls(
            name=spec.feature,
            feature=spec.feature,
            layer=spec.layer,
            results=list(results),
            duration_s=duration_s,
            source=spec.source,
        )


@dataclass
class Report:
    outcomes: list[SpecOutcome] = field(default_factory=list)
    label: str = "OmniVoice · probe"
    generated_at: datetime | None = None
    issue_url: str | None = None  # set by the Triager when there are failures

    # ── tallies ───────────────────────────────────────────────────────────────
    def _flat(self) -> list[JudgeResult]:
        return [r for o in self.outcomes for r in o.results]

    @property
    def passed(self) -> int:
        return sum(1 for r in self._flat() if not r.advisory and r.passed is True)

    @property
    def failed(self) -> int:
        return sum(1 for r in self._flat() if not r.advisory and r.passed is False)

    @property
    def skipped(self) -> int:
        return sum(1 for r in self._flat() if not r.advisory and r.passed is None)

    @property
    def advisory(self) -> int:
        return sum(1 for r in self._flat() if r.advisory)

    @property
    def total(self) -> int:
        return len(self._flat())

    @property
    def ok(self) -> bool:
        """The verdict: only blocking failures matter."""
        return self.failed == 0


# ── rendering ──────────────────────────────────────────────────────────────────

_CSS = """
:root{--bg:#0f1115;--panel:#171a21;--panel2:#1d2129;--line:#272c36;--txt:#e6e9ef;
--muted:#9aa3b2;--pass:#37d399;--fail:#ff6b6b;--skip:#7c8699;--adv:#f0b449;--accent:#6aa6ff}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--txt);font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
.wrap{max-width:1100px;margin:0 auto;padding:32px 24px 80px}
header{display:flex;align-items:center;justify-content:space-between;gap:16px;flex-wrap:wrap;margin-bottom:24px}
h1{font-size:20px;margin:0;font-weight:650}
.sub{color:var(--muted);font-size:12px;margin-top:4px}
.verdict{font-weight:700;font-size:13px;padding:8px 16px;border-radius:999px;letter-spacing:.3px}
.verdict.ok{background:rgba(55,211,153,.14);color:var(--pass);border:1px solid rgba(55,211,153,.4)}
.verdict.bad{background:rgba(255,107,107,.14);color:var(--fail);border:1px solid rgba(255,107,107,.4)}
.issue-btn{display:inline-block;margin-left:10px;font-size:12px;font-weight:600;text-decoration:none;
padding:8px 14px;border-radius:999px;color:var(--accent);border:1px solid rgba(106,166,255,.4);background:rgba(106,166,255,.1)}
.issue-btn:hover{background:rgba(106,166,255,.2)}
.cards{display:grid;grid-template-columns:repeat(5,1fr);gap:12px;margin-bottom:28px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:14px 16px}
.card .n{font-size:26px;font-weight:700;line-height:1}
.card .l{color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.6px;margin-top:6px}
.card.pass .n{color:var(--pass)}.card.fail .n{color:var(--fail)}
.card.skip .n{color:var(--skip)}.card.adv .n{color:var(--adv)}
.filters{display:flex;gap:8px;margin-bottom:18px;flex-wrap:wrap}
.filters button{background:var(--panel2);color:var(--muted);border:1px solid var(--line);
border-radius:999px;padding:6px 14px;font-size:12px;cursor:pointer}
.filters button.active{color:var(--txt);border-color:var(--accent);background:rgba(106,166,255,.12)}
.spec{background:var(--panel);border:1px solid var(--line);border-radius:12px;margin-bottom:16px;overflow:hidden}
.spec>.head{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:14px 18px;background:var(--panel2);border-bottom:1px solid var(--line)}
.spec .feat{font-weight:650}.spec .meta{color:var(--muted);font-size:12px}
.layer{font-size:10px;letter-spacing:.5px;text-transform:uppercase;color:var(--accent);
border:1px solid rgba(106,166,255,.4);border-radius:6px;padding:2px 7px;margin-right:8px}
table{width:100%;border-collapse:collapse}
td{padding:10px 18px;border-top:1px solid var(--line);vertical-align:top}
tr.adv td{background:rgba(240,180,73,.05)}
.badge{display:inline-block;min-width:48px;text-align:center;font-size:11px;font-weight:700;
border-radius:6px;padding:3px 8px;letter-spacing:.4px}
.badge.pass{background:rgba(55,211,153,.16);color:var(--pass)}
.badge.fail{background:rgba(255,107,107,.16);color:var(--fail)}
.badge.skip{background:rgba(124,134,153,.18);color:var(--skip)}
.jname{font-weight:600}
.advtag{font-size:10px;color:var(--adv);border:1px solid rgba(240,180,73,.4);border-radius:5px;padding:1px 6px;margin-left:8px}
.detail{color:var(--muted)}
.measured{font-variant-numeric:tabular-nums;color:var(--txt);font-weight:600}
.note{margin-top:32px;padding:16px 18px;border:1px solid var(--line);border-left:3px solid var(--adv);
border-radius:8px;background:var(--panel);color:var(--muted);font-size:12.5px}
.note b{color:var(--txt)}
"""

_JS = """
function pf(f){document.querySelectorAll('.filters button').forEach(b=>b.classList.toggle('active',b.dataset.f===f));
document.querySelectorAll('tr.row').forEach(r=>{let s=r.dataset.status,a=r.dataset.adv==='1';
let show=(f==='all')||(f==='fail'&&s==='fail')||(f==='skip'&&s==='skip')||(f==='adv'&&a);
r.style.display=show?'':'none';});}
document.addEventListener('DOMContentLoaded',()=>pf('all'));
"""


def _badge(r: JudgeResult) -> str:
    if r.passed is True:
        return '<span class="badge pass">PASS</span>'
    if r.passed is False:
        return '<span class="badge fail">FAIL</span>'
    return '<span class="badge skip">SKIP</span>'


def _status_key(r: JudgeResult) -> str:
    return "pass" if r.passed is True else ("fail" if r.passed is False else "skip")


def _row(r: JudgeResult) -> str:
    measured = "" if r.measured is None else html.escape(str(r.measured))
    advtag = '<span class="advtag">advisory</span>' if r.advisory else ""
    return (
        f'<tr class="row{" adv" if r.advisory else ""}" data-status="{_status_key(r)}" '
        f'data-adv="{"1" if r.advisory else "0"}">'
        f"<td>{_badge(r)}</td>"
        f'<td><span class="jname">{html.escape(r.name)}</span>{advtag}</td>'
        f'<td class="measured">{measured}</td>'
        f'<td class="detail">{html.escape(r.detail)}</td>'
        f"</tr>"
    )


def _spec_block(o: SpecOutcome) -> str:
    rows = "".join(_row(r) for r in o.results)
    p = sum(1 for r in o.results if not r.advisory and r.passed is True)
    f = sum(1 for r in o.results if not r.advisory and r.passed is False)
    s = sum(1 for r in o.results if not r.advisory and r.passed is None)
    a = sum(1 for r in o.results if r.advisory)
    layer = f'<span class="layer">{html.escape(o.layer)}</span>' if o.layer else ""
    meta = f"{p} passed · {f} failed · {s} skipped · {a} advisory"
    if o.duration_s:
        meta += f" · {o.duration_s:.2f}s"
    return (
        '<section class="spec">'
        f'<div class="head"><div>{layer}<span class="feat">{html.escape(o.name)}</span></div>'
        f'<div class="meta">{meta}</div></div>'
        f"<table><tbody>{rows}</tbody></table></section>"
    )


def render_html(report: Report) -> str:
    when = (report.generated_at or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")
    verdict = ("ok", "PASS") if report.ok else ("bad", "FAIL")
    issue_btn = (
        f'<a class="issue-btn" href="{html.escape(report.issue_url)}" target="_blank" '
        f'rel="noopener">📋 Draft GitHub issue</a>'
        if (report.issue_url and not report.ok)
        else ""
    )
    cards = [
        ("", report.total, "checks"),
        ("pass", report.passed, "passed"),
        ("fail", report.failed, "failed"),
        ("skip", report.skipped, "skipped"),
        ("adv", report.advisory, "advisory"),
    ]
    card_html = "".join(
        f'<div class="card {c}"><div class="n">{n}</div><div class="l">{l}</div></div>'
        for c, n, l in cards
    )
    specs_html = "".join(_spec_block(o) for o in report.outcomes) or (
        '<section class="spec"><div class="head"><span class="feat">No outcomes recorded</span></div></section>'
    )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(report.label)} — report</title>
<style>{_CSS}</style></head>
<body><div class="wrap">
<header>
  <div><h1>{html.escape(report.label)}</h1><div class="sub">Generated {when}</div></div>
  <div><span class="verdict {verdict[0]}">{verdict[1]}</span>{issue_btn}</div>
</header>
<div class="cards">{card_html}</div>
<div class="filters">
  <button data-f="all" onclick="pf('all')">All</button>
  <button data-f="fail" onclick="pf('fail')">Failures</button>
  <button data-f="skip" onclick="pf('skip')">Skipped</button>
  <button data-f="adv" onclick="pf('adv')">Advisory</button>
</div>
{specs_html}
<div class="note"><b>How to read this:</b> the verdict reflects <b>blocking</b> checks only.
<b>SKIP</b> = an optional backend wasn't installed (e.g. speaker embeddings). The
<b>advisory</b> lane (naturalness / trends) is reported but never gates — those
metrics fail out-of-domain and would give false confidence. probe verifies output
is <b>correct and not broken</b>, not that it is <b>good</b> (naturalness, prosody,
accent stay human-judgment-only).</div>
</div><script>{_JS}</script></body></html>"""


# ── write + open ───────────────────────────────────────────────────────────────


def _default_dir() -> Path:
    env = os.environ.get("PROBE_REPORT_DIR")
    return Path(env) if env else (Path(__file__).resolve().parent / "reports")


def _should_open(explicit: bool | None) -> bool:
    """Open the report only when asked: `open_browser=True` or `PROBE_OPEN=1`.

    Test runs (local, agent or CI) must never pop a browser window by default.
    """
    if explicit is not None:
        return explicit
    if os.environ.get("PROBE_NO_OPEN") or os.environ.get("CI"):
        return False
    return os.environ.get("PROBE_OPEN", "").strip() == "1"


def open_in_browser(path: str | Path) -> bool:
    url = Path(path).resolve().as_uri()
    try:
        return bool(webbrowser.open(url))
    except Exception:  # noqa: BLE001 — never let opening a report break a run
        return False


def write_html(report: Report, out_dir: str | Path | None = None, filename: str | None = None) -> Path:
    directory = Path(out_dir) if out_dir else _default_dir()
    directory.mkdir(parents=True, exist_ok=True)
    stamp = (report.generated_at or datetime.now()).strftime("%Y%m%d-%H%M%S")
    name = filename or f"report-{stamp}.html"
    path = directory / name
    body = render_html(report)
    path.write_text(body, encoding="utf-8")
    # Stable pointer to the newest report for tooling/bookmarks.
    (directory / "report-latest.html").write_text(body, encoding="utf-8")
    return path


def save_and_open(
    report: Report,
    out_dir: str | Path | None = None,
    filename: str | None = None,
    open_browser: bool | None = None,
) -> Path:
    if report.generated_at is None:
        report.generated_at = datetime.now()
    path = write_html(report, out_dir=out_dir, filename=filename)
    opened = open_in_browser(path) if _should_open(open_browser) else False
    print(f"\nprobe report: {path}" + ("  (opened in browser)" if opened else ""))
    return path
