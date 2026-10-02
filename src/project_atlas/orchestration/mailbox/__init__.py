"""Durable, provider-neutral agent message ingress.

Inbox receipt is transport state, not authority. Routing composes AS-ORCH-001A
and 001B; dispatch remains owned by the existing governor/001E/001D path.
"""

from project_atlas.orchestration.mailbox.governor_bridge import (
    MailboxGovernorBridge,
    SuccessorAdmissionError,
)
from project_atlas.orchestration.mailbox.models import (
    AgentInboxMessage,
    EnqueueReceipt,
    InboxMessageKind,
    InboxRoutingResult,
    MailboxError,
    MailboxRecord,
    MailboxStatus,
    MailboxSuccessorBindingV1,
    payload_sha256,
)
from project_atlas.orchestration.mailbox.router import InboxRouter
from project_atlas.orchestration.mailbox.store import AgentMailbox

__all__ = [
    "AgentInboxMessage",
    "AgentMailbox",
    "EnqueueReceipt",
    "InboxMessageKind",
    "InboxRouter",
    "InboxRoutingResult",
    "MailboxError",
    "MailboxGovernorBridge",
    "MailboxRecord",
    "MailboxStatus",
    "MailboxSuccessorBindingV1",
    "SuccessorAdmissionError",
    "payload_sha256",
]
