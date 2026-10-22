"""Convert one project's stopped pre-transport runs within the operation's cap.

Conversion is idempotent, so no admission receipt is kept: a repeat skips what
an earlier call converted. A call stops starting runs when less than
``_RESERVE_SECONDS`` of the cap remains and answers ``complete=false``; the
caller repeats until ``complete`` is true. Rows are bounded; already converted
runs are only counted, attempted runs beyond the row limit are counted as
unreported, and a host index entry for a run already attempted is omitted.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from vs_project.api import Project, ProjectError
from vs_proto.api import (
    FailureCategory,
    MutateRequest,
    MutateResponse,
    Oneof,
    OperationFailure,
    ValidationError,
    host_operations,
    validate,
)
from vs_proto.api import host_payloads as pb

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from vibesys.api.transport import RunConversion
    from vs_sim.api import Clock

__all__ = ["serve_convert_saved_runs"]

_POLICY = host_operations.MUTATIONS["convert_saved_runs"]
_MAX_BYTES = 1 << 20
_MAX_DIAGNOSTIC = 65536
_MAX_ROWS = 100
_MAX_REASON = 2048
# Helper start-up, the run in flight when the check passes, and the reply all fit here.
_RESERVE_SECONDS = 20.0
_STATUS = {
    "converted": pb.RunConversionStatus.CONVERTED,
    "skipped": pb.RunConversionStatus.SKIPPED,
    "failed": pb.RunConversionStatus.FAILED,
    "cannot-convert": pb.RunConversionStatus.CANNOT_CONVERT,
}


def serve_convert_saved_runs(
    payload: bytes,
    *,
    conversions: Callable[[Project, str], Iterator[RunConversion]],
    clock: Clock,
    home: Path,
) -> bytes:
    """Validate one ConvertSavedRuns envelope, convert until the deadline, and answer.

    ``conversions`` yields one outcome per run and attempts a run only when asked
    for it. A relative or ``~`` project path resolves against ``home``, as saved-run
    discovery does. An unopenable project is ``NOT_FOUND``; an unreadable project
    catalog is ``INVALID_INPUT``.
    """
    deadline = clock.now() + _POLICY.cap_ms / 1000 - _RESERVE_SECONDS
    decoded = _request(payload)
    if isinstance(decoded, bytes):
        return decoded
    accepted = _POLICY.accept(decoded)
    request = _POLICY.request_value(accepted)
    try:
        project = Project.open(_project_path(request.project, home))
    except ProjectError as exc:
        return _failure(FailureCategory.NOT_FOUND, str(exc))
    response = pb.ConvertSavedRunsResponse(
        request_id=request.request_id, project_root=str(project.root), complete=True
    )
    try:
        _convert(response, conversions(project, request.after_run), clock=clock, deadline=deadline)
    except (ProjectError, ValueError) as exc:
        return _failure(FailureCategory.INVALID_INPUT, str(exc))
    except PermissionError as exc:
        return _failure(FailureCategory.PERMISSION_DENIED, str(exc) or "permission denied")
    _POLICY.validate_response(accepted, response)
    return MutateResponse(result=Oneof("convert_saved_runs", response)).to_binary()


def _request(payload: bytes) -> pb.ConvertSavedRunsRequest | bytes:
    if len(payload) > _MAX_BYTES:
        return _failure(FailureCategory.INVALID_INPUT, "HostMutate request exceeds 1 MiB")
    try:
        envelope = MutateRequest.from_binary(payload)
        validate(envelope)
    except (ValidationError, ValueError) as exc:
        return _failure(FailureCategory.INVALID_INPUT, str(exc))
    method = envelope.method
    if method is None or method.field != "convert_saved_runs":
        return _failure(
            FailureCategory.INVALID_INPUT, "HostMutate.method must be convert_saved_runs"
        )
    return method.value


def _project_path(text: str, home: Path) -> Path:
    path = Path(text).expanduser() if text.startswith("~") else Path(text)
    return path if path.is_absolute() else home / path


def _convert(
    response: pb.ConvertSavedRunsResponse,
    rows: Iterator[RunConversion],
    *,
    clock: Clock,
    deadline: float,
) -> None:
    while True:
        if clock.now() >= deadline:
            response.complete = False
            return
        row = next(rows, None)
        if row is None:
            return
        response.next_run = row.cursor
        status = row.result.status.value
        if row.alias_of is not None:
            continue
        if status == "exists":
            response.already_converted += 1
        elif len(response.runs) < _MAX_ROWS:
            response.runs.append(
                pb.RunConversion(
                    run_id=row.run, status=_STATUS[status], reason=_bounded(row.result.reason)
                )
            )
        else:
            response.unreported += 1


def _bounded(text: str, limit: int = _MAX_REASON) -> str:
    encoded = text.encode()
    return text if len(encoded) <= limit else encoded[:limit].decode(errors="ignore")


def _failure(category: FailureCategory, message: str) -> bytes:
    text = _bounded(message.strip(), _MAX_DIAGNOSTIC) or "saved-run conversion failed"
    failure = OperationFailure(category=category, message=text)
    return MutateResponse(result=Oneof("failure", failure)).to_binary()
