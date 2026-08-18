"""UP title blacklist matching (v0.2 Phase 3).

Matching is a case-insensitive contiguous-substring test (AGENT_PROMPT_v0.2
section 3.5), implemented with ``casefold()`` so ``TEST`` matches ``TESTDATAABC``
and ``testdataabc`` but not ``TESAAAB``.
"""
from __future__ import annotations

from typing import Iterable, Optional


def blacklist_match(title: str, keyword: str) -> bool:
    """True when ``keyword`` is a case-insensitive substring of ``title``."""
    return keyword.casefold() in title.casefold()


def blacklist_hit(title: str, keywords: Iterable[str]) -> Optional[str]:
    """Return the first matching keyword, or None when none match."""
    for keyword in keywords:
        if blacklist_match(title, keyword):
            return keyword
    return None
