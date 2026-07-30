"""Public, non-secret build metadata supplied by the deployment platform."""

from __future__ import annotations

import os
import re

_BUILD_SHA_RE = re.compile(r"[0-9a-f]{40}", re.IGNORECASE)
_BUILD_BRANCH_RE = re.compile(r"[A-Za-z0-9._/-]{1,128}")


def safe_build_sha(value: object) -> str:
    checked = str(value or "").strip()
    if _BUILD_SHA_RE.fullmatch(checked):
        return checked.lower()
    return "unknown"


def safe_build_branch(value: object) -> str:
    checked = str(value or "").strip()
    if _BUILD_BRANCH_RE.fullmatch(checked):
        return checked
    return "unknown"


BUILD_SHA = safe_build_sha(os.getenv("RENDER_GIT_COMMIT"))
BUILD_BRANCH = safe_build_branch(os.getenv("RENDER_GIT_BRANCH"))
