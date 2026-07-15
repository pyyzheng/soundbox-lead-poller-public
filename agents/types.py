from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable
import uuid
from datetime import datetime, timezone


@dataclass
class Message:
    type: str            # 'scan' | 'detect' | 'fix' | 'review' | 'learn'
    project: str
    payload: dict = field(default_factory=dict)
    source: str = ""
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class Anomaly:
    type: str            # 'spam_leaked' | 'format_anomaly' | 'unassigned' | ...
    severity: str        # 'low' | 'medium' | 'high' | 'critical'
    description: str
    evidence: dict = field(default_factory=dict)
    source: str = ""


@dataclass
class FixResult:
    success: bool
    summary: str
    changed_files: list = field(default_factory=list)
    confidence: float = 0.0
    needs_review: bool = True


@dataclass
class ReviewResult:
    verdict: str         # 'approve' | 'request-changes' | 'reject'
    issues: list = field(default_factory=list)
    summary: str = ""


DetectorFn = Callable[[str, Any], Awaitable[list[Anomaly]]]
FixStrategyFn = Callable[[list[Anomaly], Any], Awaitable[FixResult]]
MessageHandler = Callable[[Message], Awaitable[Message | None]]
