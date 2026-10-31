"""Voice-design "describe your voice" API (issue #317).

Maps a free-text voice description onto the existing design parameter space
via the deterministic keyword mapper in ``core.describe_voice``. Pure CPU +
stdlib — no model, no network — so it imports and responds instantly in any
environment, including test/CI without model weights.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from api.dependencies import require_admin
from pydantic import BaseModel, Field

from core.describe_voice import parse_description

router = APIRouter(dependencies=[Depends(require_admin)])


class DescribeRequest(BaseModel):
    description: str = Field(default="", max_length=2000)


@router.post("/design/describe")
def describe_voice(req: DescribeRequest) -> dict:
    """Parse a free-text description into design attrs + a validator-safe instruct.

    Response shape::

        {
          "attrs":     {"Gender": "female", "Age": "elderly", ... or "Auto"},
          "instruct":  "female, elderly, low pitch, british accent",
          "matched":   [{"category": "Age", "token": "elderly", "phrase": "elderly"}, ...],
          "unmatched": ["slightly raspy"]
        }
    """
    return parse_description(req.description)
