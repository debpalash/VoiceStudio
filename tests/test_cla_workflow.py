"""Guards for the Contributor License Agreement check (.github/workflows/cla.yml).

A signature is evidence of agreement to one exact text, so published agreements
are pinned by hash: changing the terms means a new version file, sign phrase,
and signature store, never an edit in place. These tests also keep the checker,
workflow, and docs in step, and keep workflows that hold a write token on
untrusted events from running pull-request code.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import sys
from pathlib import Path

import yaml

_REPO = Path(__file__).resolve().parents[1]
_WORKFLOWS = _REPO / ".github" / "workflows"
_CLA_WORKFLOW = _WORKFLOWS / "cla.yml"
# Triggers that run with a write token and repository secrets, even on fork PRs.
_PRIVILEGED_TRIGGERS = {"pull_request_target", "issue_comment", "workflow_run"}
# SHA-256 of each published agreement with LF line endings. Never update a hash:
# publish a new version file instead, so existing signatures keep their text.
_PUBLISHED_AGREEMENTS = {
    ".github/CLA-1.0.md": "b21cab097b9ea9b7f9d938724910276bd1253083800f42d5e176c9d335086b84",
    ".github/CCLA-1.0.md": "4207f055243a9c4c87f89dcb1de49e61b46ffb1605ae020e848709c8705c9d5c",
}

_spec = importlib.util.spec_from_file_location("cla_check", _REPO / ".github" / "scripts" / "cla_check.py")
cla = importlib.util.module_from_spec(_spec)
sys.modules["cla_check"] = cla
_spec.loader.exec_module(cla)


def _load(path: Path) -> dict:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    # PyYAML reads the bare `on:` key as boolean True.
    data["on"] = data.pop(True, data.get("on")) or {}
    return data


def _triggers(workflow: dict) -> set[str]:
    on = workflow["on"]
    return set(on) if isinstance(on, (dict, list)) else {on}


def test_checker_document_store_and_phrase_share_one_version():
    version = cla.CLA_VERSION
    assert cla.DOCUMENT_PATH == f".github/CLA-{version}.md"
    assert cla.SIGNATURE_PATH == f"signatures/v{version}/cla.json"
    assert f"CLA {version} " in cla.SIGN_PHRASE
    assert (_REPO / cla.DOCUMENT_PATH).is_file()


def test_sign_phrase_matches_the_agreement_and_contributing():
    for path in (_REPO / cla.DOCUMENT_PATH, _REPO / ".github" / "CONTRIBUTING.md"):
        assert cla.SIGN_PHRASE in path.read_text(encoding="utf-8"), path


def test_workflow_comment_filter_admits_the_sign_phrase_and_recheck():
    job_filter = _load(_CLA_WORKFLOW)["jobs"]["cla"]["if"]
    needles = re.findall(r"contains\(github\.event\.comment\.body, '([^']+)'\)", job_filter)
    assert any(n in cla.SIGN_PHRASE for n in needles)
    assert "recheck" in needles


def test_pull_request_changes_that_affect_the_result_rerun_the_check():
    workflow = _load(_CLA_WORKFLOW)
    types = set(workflow["on"]["pull_request_target"]["types"])
    # New commits, (un)applying `cla-override`, and editing `Supersedes #N`
    # all change who must sign, so each must refresh the CLA status.
    assert {"opened", "synchronize", "reopened", "closed", "labeled", "unlabeled", "edited"} <= types
    assert workflow["jobs"]["cla"]["if"].startswith("github.event_name == 'pull_request_target' ||")


def test_published_agreements_are_never_edited():
    for path, expected in _PUBLISHED_AGREEMENTS.items():
        text = (_REPO / path).read_text(encoding="utf-8").replace("\r\n", "\n")
        assert hashlib.sha256(text.encode()).hexdigest() == expected, (
            f"{path} changed. Publish the new terms as a new version file instead."
        )


def test_cla_workflow_actions_are_pinned_to_commits():
    for job in _load(_CLA_WORKFLOW)["jobs"].values():
        for step in job.get("steps", []):
            if "uses" in step:
                ref = step["uses"].split("@", 1)[1]
                assert re.fullmatch(r"[0-9a-f]{40}", ref), step["uses"]


def test_signatures_never_go_to_main():
    assert cla.SIGNATURE_BRANCH not in {"main", "master"}


def test_privileged_workflows_never_run_pull_request_code():
    for path in sorted(_WORKFLOWS.glob("*.y*ml")):
        workflow = _load(path)
        if not _triggers(workflow) & _PRIVILEGED_TRIGGERS:
            continue
        text = path.read_text(encoding="utf-8")
        assert "pull_request.head" not in text and "head_sha" not in text, f"{path.name} references PR head code"
        for name, job in (workflow.get("jobs") or {}).items():
            where = f"{path.name}:{name}"
            assert "uses" not in job, f"{where} calls a reusable workflow"
            for step in job.get("steps", []):
                uses = step.get("uses", "")
                assert not uses.startswith("actions/download-artifact@"), f"{where} downloads artifacts"
                if uses.startswith("actions/checkout@"):
                    options = step.get("with") or {}
                    assert not {"ref", "repository"} & set(options), f"{where} checks out a non-base ref"
                    assert options.get("persist-credentials") is False, f"{where} keeps credentials"
                assert "${{ github.event" not in str(step.get("run", "")), f"{where} interpolates event data"


def _main_ruleset() -> dict:
    text = (_REPO / "docs" / "maintainers" / "repository-settings.md").read_text(encoding="utf-8")
    blocks = [json.loads(b) for b in re.findall(r"<<'JSON'\n(.*?)\nJSON\n", text, re.DOTALL)]
    return next(b for b in blocks if b.get("name") == "main")


def test_main_ruleset_requires_pull_requests_identities_and_the_cla_status():
    rules = {rule["type"]: rule.get("parameters", {}) for rule in _main_ruleset()["rules"]}
    # Required checks alone accept a direct push whose commit already passed.
    assert rules["pull_request"] == {
        "required_approving_review_count": 0, "dismiss_stale_reviews_on_push": False,
        "require_code_owner_review": False, "require_last_push_approval": False,
        "required_review_thread_resolution": False,
    }
    contexts = {c["context"] for c in rules["required_status_checks"]["required_status_checks"]}
    assert cla.STATUS_CONTEXT in contexts
    # Without the identity gate, agent identities that skip the CLA go unblocked.
    identity_job = next(iter(_load(_WORKFLOWS / "commit-identity.yml")["jobs"].values()))["name"]
    assert identity_job in contexts
    assert "Commit identity policy" in contexts  # fork jobs cannot forge this privileged status
    assert {"deletion", "non_fast_forward"} <= set(rules)


def test_contributing_states_the_open_source_commitment_limits():
    agreement = (_REPO / cla.DOCUMENT_PATH).read_text(encoding="utf-8")
    assert "Open Source\nInitiative" in agreement and "public source repository" in agreement
    section = (_REPO / ".github" / "CONTRIBUTING.md").read_text(encoding="utf-8").split("## Contribution licensing")[1]
    summary = " ".join(section.split("\n## ")[0].split())
    assert "while your contribution is in the public VoiceStudio repository" in summary
    assert "AGPL-3.0 or another OSI-approved licence" in summary


def test_cla_api_errors_block_merging_independently_of_the_commit_status():
    job = _load(_CLA_WORKFLOW)["jobs"]["cla"]
    checks = next(rule["parameters"]["required_status_checks"]
                  for rule in _main_ruleset()["rules"] if rule["type"] == "required_status_checks")
    assert {"context": job.get("name", "cla"), "integration_id": 15368} in checks
    assert not job.get("continue-on-error", False)
    assert all(not step.get("continue-on-error", False) for step in job["steps"])
