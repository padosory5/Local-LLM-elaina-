from __future__ import annotations

import json
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from core.paths import RUNTIME_ROOT
from security.policy import PolicyEngine


@dataclass
class ApprovalProposal:
    proposal_id: str
    action: str
    title: str
    summary: str
    details: list[dict[str, str]]
    payload: dict[str, Any]
    risk: str
    status: str = "awaiting_approval"
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def public_payload(self) -> dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "action": self.action,
            "title": self.title,
            "summary": self.summary,
            "details": self.details,
            "risk": self.risk,
            "status": self.status,
        }


class ApprovalManager:
    """Hold exact action payloads until Electron returns a user decision."""

    def __init__(
        self,
        policy: PolicyEngine,
        audit_path: Path | None = None,
    ) -> None:
        self.policy = policy
        self.audit_path = (
            audit_path
            or RUNTIME_ROOT / "audit" / "approvals.jsonl"
        )
        self.audit_path.parent.mkdir(parents=True, exist_ok=True)
        self._proposals: dict[str, ApprovalProposal] = {}
        self._lock = threading.Lock()

    def create(
        self,
        *,
        action: str,
        title: str,
        summary: str,
        details: list[dict[str, str]],
        payload: dict[str, Any],
    ) -> ApprovalProposal:
        policy = self.policy.get(action)
        if not policy.approval_required:
            raise ValueError(
                f"Action '{action}' does not require an approval proposal."
            )

        proposal = ApprovalProposal(
            proposal_id=uuid4().hex,
            action=action,
            title=str(title),
            summary=str(summary),
            details=[
                {
                    "label": str(item.get("label", "")),
                    "value": str(item.get("value", "")),
                }
                for item in details
            ],
            payload=dict(payload),
            risk=policy.risk,
        )
        with self._lock:
            self._proposals[proposal.proposal_id] = proposal
            self._append(proposal)
        return proposal

    def resolve(
        self,
        proposal_id: str,
        approved: bool,
    ) -> ApprovalProposal:
        with self._lock:
            proposal = self._proposals.get(str(proposal_id))
            if proposal is None:
                raise KeyError("The approval proposal was not found.")
            if proposal.status != "awaiting_approval":
                raise ValueError("This proposal has already been resolved.")

            proposal.status = "approved" if approved else "rejected"
            self._append(proposal)
            return proposal

    def _append(self, proposal: ApprovalProposal) -> None:
        record = asdict(proposal)
        # Audit what was proposed without copying OAuth tokens or credentials.
        record["payload"] = {
            "keys": sorted(proposal.payload.keys()),
        }
        with self.audit_path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(record, ensure_ascii=False) + "\n")
