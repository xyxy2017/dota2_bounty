from __future__ import annotations

from enum import StrEnum


class SessionState(StrEnum):
    IDLE = "idle"
    PRE_MATCH = "pre_match"
    IN_MATCH = "in_match"
    POST_MATCH_PENDING = "post_match_pending"
    RESOLVED = "resolved"


class DataStatus(StrEnum):
    PENDING = "pending"
    PARTIAL = "partial"
    COMPLETED = "completed"
    FAILED = "failed"
