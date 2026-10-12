"""Identities of the issue-queue search: the shared core grammar under the `plain` namespace.

The queue is serial, so every decision is named by what it does and the job it belongs to
(`issue.3/claim.0`). The agent pipeline reads the part before the slash as the subject of a
conversation, so a job's roles keep one session each.
"""

from vs_core.api.search import DecisionId, IdScheme

# The role prefix matches the role ids the host registers (`vibesys.issue_queue_core`).
IDS = IdScheme("plain", role_prefix="plain-")


def job_key(kind: str, sequence: int) -> str:
    """The key of one job: its kind and its position among the jobs the run opened."""
    return f"{kind}.{sequence}"


def decision(key: str, name: str, *numbers: int) -> DecisionId:
    """The decision ``name`` of the job ``key``, numbered when it can be sent again."""
    suffix = "".join(f".{number}" for number in numbers)
    return DecisionId(root=f"{key}/{name}{suffix}")


def is_log(identity: DecisionId | None) -> bool:
    """Whether the decision is a progress entry, which nothing waits for."""
    return identity is not None and identity.root.rpartition("/")[2].startswith("log.")
