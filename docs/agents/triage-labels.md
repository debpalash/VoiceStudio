# Triage Labels

The skills speak in terms of five canonical triage roles. This file maps those roles to the actual label strings used in this repo's issue tracker.

| Label in mattpocock/skills | Label in our tracker | Meaning                                  |
| -------------------------- | -------------------- | ---------------------------------------- |
| `needs-triage`             | `needs-triage`       | Maintainer needs to evaluate this issue  |
| `needs-info`               | `needs-info`         | Waiting on reporter for more information |
| `ready-for-agent`          | `ready-for-agent`    | Fully specified, ready for an AFK agent  |
| `ready-for-human`          | `ready-for-human`    | Requires human implementation            |
| `wontfix`                  | `wontfix`            | Will not be actioned                     |

When a skill mentions a role (e.g. "apply the AFK-ready triage label"), use the corresponding label string from this table.

Edit the right-hand column to match whatever vocabulary you actually use.

## Repo notes

`wontfix` and `needs-info` **pre-date this file** — they were already in use on
`debpalash/VoiceStudio` with exactly these names, which is why the mapping is an
identity mapping rather than an alias table. Reuse them; do not create variants
(`won't-fix`, `needs-information`) alongside them.

`needs-triage`, `ready-for-agent` and `ready-for-human` were created for this
vocabulary on 2026-08-07.

This repo also carries labels outside the triage vocabulary — `bug`,
`enhancement`, `documentation`, `question`, `duplicate`, `invalid`,
`good first issue`, `help wanted`, `roadmap`, `from-discord`,
`v0.3.0-investigate`, plus the area labels `install` (installer, setup and
first-run problems) and `sponsor` (sponsorship inquiries). They classify *what*
an issue is; the five above track *where it is in the queue*. The two sets are
orthogonal — applying a triage label never means removing a type label.

Issue templates apply only labels from this file: the bug and install-problem
forms add `needs-triage` next to their type/area label; the feature and
sponsor forms add only their type/area label. Do not reintroduce a bare
`triage` label.
