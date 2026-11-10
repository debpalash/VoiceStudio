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


def test_new_contributors_are_told_to_sign_before_their_first_pull_request():
    """GitHub cannot block opening a pull request, so the docs must say that the
    merge is gated and where to sign first: the maintainer-labelled signing issue
    that ``sign_on_issue`` records for anyone."""
    signing_issue = "https://github.com/debpalash/VoiceStudio/issues/2586"
    contributing = (_REPO / ".github" / "CONTRIBUTING.md").read_text(encoding="utf-8")
    template = (_REPO / ".github" / "pull_request_template.md").read_text(encoding="utf-8")
    readme = (_REPO / "README.md").read_text(encoding="utf-8")
    assert "Before your first pull request" in contributing
    assert contributing.count(signing_issue) >= 2
    assert signing_issue in template
    assert "sign the Contributor License Agreement once" in " ".join(template.split())
    assert "contribution-licensing" in readme
    assert cla.SIGNING_LABEL == "cla"


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
    """Allow privileged checkouts to use only the trusted event base commit."""
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
                    assert "repository" not in options, f"{where} checks out another repository"
                    assert options.get("ref", "${{ github.sha }}") in {
                        "${{ github.sha }}",
                        "${{ github.event.pull_request.base.sha }}",
                    }, f"{where} checks out a non-base ref"
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


def test_cla_gate_job_holds_least_privilege_for_drafting_and_restarting_ci():
    job = _load(_CLA_WORKFLOW)["jobs"]["cla"]
    # pull-requests: draft/ready mutations; actions: re-run skipped CI; the rest predate the hold.
    assert job["permissions"] == {"contents": "write", "issues": "write", "pull-requests": "write",
                                  "statuses": "write", "actions": "write"}
    assert _load(_CLA_WORKFLOW)["permissions"] == {"contents": "read"}
    checkout, check = job["steps"]
    assert checkout["with"] == {"persist-credentials": False, "sparse-checkout": ".github/scripts"}
    assert check["run"] == "python3 .github/scripts/cla_check.py"
    assert set(check["env"]) == {"GITHUB_TOKEN"}


def test_ready_for_review_reapplies_the_hold_without_replacing_queued_checks():
    workflow = _load(_CLA_WORKFLOW)
    assert "ready_for_review" in workflow["on"]["pull_request_target"]["types"]
    group = workflow["jobs"]["cla"]["concurrency"]["group"]
    # A no-op ready_for_review run must not cancel a queued run that sets a new head's status.
    assert group == ("cla-${{ github.event.pull_request.number || github.event.issue.number }}"
                     "${{ github.event.action == 'ready_for_review' && '-ready' || '' }}")
    assert "cancel-in-progress" not in workflow["jobs"]["cla"]["concurrency"]


def test_every_held_workflow_is_restarted_after_signing():
    """A workflow that skips for an unsigned opener must be one the CLA check re-runs."""
    held = set()
    for path in sorted(_WORKFLOWS.glob("*.yml")):
        workflow = _load(path)
        if "pull_request" not in _triggers(workflow):
            continue
        for job in (workflow.get("jobs") or {}).values():
            if any("ci_scope.py" in str(step.get("run", "")) for step in job.get("steps", [])):
                held.add(path.name)
    assert held == cla.GATED_WORKFLOWS
    for name in ("cla.yml", "commit-identity.yml"):  # always run, on drafts and unsigned PRs too
        for job in _load(_WORKFLOWS / name)["jobs"].values():
            assert "ci_scope" not in str(job.get("steps")) and "draft" not in str(job.get("if", ""))
    secrets = _load(_WORKFLOWS / "security.yml")["jobs"]["secrets"]
    assert "needs" not in secrets and "if" not in secrets  # the secret scan always runs


def test_nix_build_steps_wait_for_a_signed_cla():
    steps = _load(_WORKFLOWS / "nix.yml")["jobs"]["flake"]["steps"]
    hold = next(step for step in steps if step.get("id") == "cla")
    assert hold["run"] == "python3 .github/scripts/ci_scope.py --cla"
    assert hold["if"] == "github.event_name == 'pull_request'"
    scope = next(step for step in steps if step.get("id") == "scope")
    assert scope["env"]["CLA_HOLD"] == "${{ steps.cla.outputs.cla }}"
    assert scope["run"].lstrip().startswith('if [ "$CLA_HOLD" = held ]; then')
    later = steps[steps.index(scope) + 1:]
    assert later and all(step.get("if") == "steps.scope.outputs.run == 'true'" for step in later)


def test_review_bots_skip_drafts():
    """The hold drafts unsigned pull requests; both review bots must skip drafts."""
    coderabbit = yaml.safe_load((_REPO / ".coderabbit.yaml").read_text(encoding="utf-8"))
    assert coderabbit["reviews"]["auto_review"]["drafts"] is False
    greptile = json.loads((_REPO / "greptile.json").read_text(encoding="utf-8"))
    assert greptile["triggerOnDrafts"] is False


def test_agent_guide_is_short_and_surfaced_with_the_same_prompt_everywhere():
    guide = _REPO / cla.AGENT_GUIDE_PATH
    text = guide.read_text(encoding="utf-8")
    assert len(text.splitlines()) <= 60
    assert cla.SIGN_PHRASE in text and "issues/2586" in text
    prompt = cla.agent_prompt(f"https://github.com/debpalash/VoiceStudio/blob/main/{cla.AGENT_GUIDE_PATH}")
    assert prompt in (_REPO / ".github" / "pull_request_template.md").read_text(encoding="utf-8")
    contributing = (_REPO / ".github" / "CONTRIBUTING.md").read_text(encoding="utf-8")
    top = contributing.split("## Adding a TTS or ASR engine")[0]
    assert "sign the CLA first" in top and "(AGENT_PR_GUIDE.md)" in top
    body = cla.with_gate("x", "drafted", "https://g")
    assert cla.agent_prompt("https://g") in body and "Signing takes a minute" in body


def test_backfill_is_manual_main_only_and_never_checks_out_pr_code():
    workflow = _load(_WORKFLOWS / "cla-backfill.yml")
    assert _triggers(workflow) == {"workflow_dispatch"}
    job = workflow["jobs"]["backfill"]
    assert job["if"] == "github.ref == 'refs/heads/main'"
    checkout = next(step for step in job["steps"] if "checkout" in step.get("uses", ""))
    assert checkout["with"]["persist-credentials"] is False
    assert "ref" not in checkout["with"]
