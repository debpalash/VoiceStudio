"""AI agent identities, shared by the CLA check and the commit-identity gate.

Lives in .github/scripts because .github/workflows/cla.yml checks out only this
directory from the base branch. scripts/check_commit_identities.py loads it by
path. Standard library only.

Only identities that name an agent are listed, never whole company domains, so
people who commit with a work address at an AI company are treated as people.
"""
from __future__ import annotations

import re

# GitHub logins of agent apps and bots. Their no-reply addresses are
# "[<id>+]<login>[[bot]]@users.noreply.github.com".
AGENT_LOGINS = (
    "copilot", "claude", "codex", "cursor", "devin-ai-integration", "google-labs-jules",
    "copilot-swe-agent", "coderabbitai", "openhands-agent", "sweep-ai",
)
_LOGINS = "|".join(re.escape(login) for login in AGENT_LOGINS)
_BOT_NAMES = rf"{_LOGINS}|devin|gemini|jules"

AGENT_EMAILS = re.compile(
    r"^(noreply@anthropic\.com|cursoragent@cursor\.com|codex@openai\.com|noreply@openai\.com"
    r"|noreply@coderabbit\.ai|codex@users\.noreply\.github\.com"
    rf"|(\d+\+)?({_LOGINS})(\[bot\])?@users\.noreply\.github\.com)$",
    re.IGNORECASE,
)

# Unambiguous tool names in trailers. Human first names need the same
# protection as author names; known agent email addresses are checked separately.
AGENT_TRAILER_NAMES = re.compile(
    r"^((claude (code|opus|sonnet|haiku|fable|\d).*|cursor( agent)?|cursoragent|(github )?copilot"
    r"|(openai )?codex|chatgpt|devin ai|gemini code assist|google jules|aider|cline|coderabbit(ai)?)"
    rf"(\[bot\])?|({_BOT_NAMES})\[bot\])$",
    re.IGNORECASE,
)

# Author and committer names that can only be an agent. People are called
# Claude or Jules, so a bare first name is not enough here.
AGENT_AUTHOR_NAMES = re.compile(
    r"^((claude code( .*)?|claude (opus|sonnet|haiku|fable|instant|\d)\S*( .*)?|cursor agent|cursoragent"
    r"|(github )?copilot|(openai )?codex|chatgpt|devin ai|google jules|openhands( agent)?)(\[bot\])?"
    rf"|({_BOT_NAMES})\[bot\])$",
    re.IGNORECASE,
)


def is_agent_email(email: str) -> bool:
    return bool(AGENT_EMAILS.match((email or "").strip()))


def _name(name: str) -> str:
    return " ".join((name or "").split())


def is_agent_trailer_name(name: str) -> bool:
    return bool(AGENT_TRAILER_NAMES.match(_name(name)))


def is_agent_author_name(name: str) -> bool:
    return bool(AGENT_AUTHOR_NAMES.match(_name(name)))
