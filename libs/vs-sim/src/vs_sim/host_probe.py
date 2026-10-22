"""A point-in-time reading of host contention, behind one role.

``PlatformHostProbe`` reads the operating system. ``FakeHostProbe`` replays
scripted readings. A field the platform cannot provide is ``None``. Neither
implementation raises for a missing tool or an unreadable file.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from vs_sim.probes import SubprocessProbe

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from vs_sim.probes import CommandProbe

_PROBE_TIMEOUT_SECONDS = 2.0
_LOADAVG_FIELD_COUNT = 3
_MEMINFO_FIELD_COUNT = 2
_GPU_ARGV: tuple[tuple[str, ...], ...] = (
    ("nvidia-smi", "--query-compute-apps=pid,process_name", "--format=csv,noheader"),
    ("rocm-smi", "--showpids"),
)


@dataclass(frozen=True, slots=True)
class HostSample:
    """One reading of the evaluation host. ``None`` means that fact was unavailable.

    ``gpu_processes`` is ``None`` when no unprivileged query ran, and empty when a
    query ran and listed nobody.
    """

    load_1: float | None = None
    load_5: float | None = None
    load_15: float | None = None
    cpu_count: int | None = None
    memory_free_bytes: int | None = None
    memory_pressure: str | None = None
    gpu_processes: tuple[str, ...] | None = None


class HostProbe(Protocol):
    """Read the host's contention once. Implementations do not raise."""

    def sample(self) -> HostSample:
        """The host's load, memory, and GPU users at this moment."""
        ...


class FakeHostProbe:
    """Replay scripted samples, then report every field unavailable."""

    def __init__(self, samples: Sequence[HostSample]) -> None:
        """Queue ``samples`` in the order ``sample`` will return them."""
        self._samples = list(samples)

    def sample(self) -> HostSample:
        """The next scripted sample, or an all-unavailable sample when the queue is empty."""
        if not self._samples:
            return HostSample()
        return self._samples.pop(0)


class PlatformHostProbe:
    """Read load, memory, and GPU users from the host this process runs on.

    ``commands`` runs ``vm_stat``, ``memory_pressure``, and the GPU queries.
    ``read_text`` reads Linux ``/proc`` files. ``loadavg`` and ``cpu_count``
    supply the two facts that are not command output. Defaults use the real
    host. A test passes substitutes and never touches the machine.
    """

    def __init__(
        self,
        commands: CommandProbe | None = None,
        *,
        read_text: Callable[[str], str | None] | None = None,
        loadavg: Callable[[], tuple[float, float, float] | None] | None = None,
        cpu_count: Callable[[], int | None] | None = None,
        platform: str | None = None,
    ) -> None:
        """Bind the command probe and the readers. Defaults are the real host.

        The real command probe is built on the first sample, not here, so constructing
        this class does not start a process.
        """
        self._commands = commands
        self._read_text = read_text if read_text is not None else _read_text
        self._platform = sys.platform if platform is None else platform
        self._loadavg = loadavg if loadavg is not None else self._real_loadavg
        self._cpu_count = cpu_count if cpu_count is not None else os.cpu_count

    def sample(self) -> HostSample:
        """One reading. Each fact that fails comes back unavailable."""
        load = _call(self._loadavg)
        free, pressure = self._memory()
        cpus = _call(self._cpu_count)
        return HostSample(
            load_1=None if load is None else load[0],
            load_5=None if load is None else load[1],
            load_15=None if load is None else load[2],
            cpu_count=cpus if isinstance(cpus, int) else None,
            memory_free_bytes=free,
            memory_pressure=pressure,
            gpu_processes=self._gpu_processes(),
        )

    def _real_loadavg(self) -> tuple[float, float, float] | None:
        if self._platform == "linux":
            return parse_loadavg(self._read_text("/proc/loadavg") or "")
        try:
            one, five, fifteen = os.getloadavg()
        except OSError:
            return None
        return (one, five, fifteen)

    def _memory(self) -> tuple[int | None, str | None]:
        if self._platform == "linux":
            return _call(lambda: parse_meminfo(self._read_text("/proc/meminfo") or "")), None
        free = _call(lambda: parse_vm_stat(self._command("vm_stat")))
        pressure = _call(lambda: parse_memory_pressure(self._command("memory_pressure")))
        return free, pressure

    def _gpu_processes(self) -> tuple[str, ...] | None:
        for argv in _GPU_ARGV:
            text = self._command(*argv)
            if text is None:
                continue
            return parse_gpu_processes(text)
        return None

    def _command(self, *argv: str) -> str | None:
        commands = self._commands
        if commands is None:
            commands = SubprocessProbe()
            self._commands = commands
        try:
            result = commands.run(argv, timeout_seconds=_PROBE_TIMEOUT_SECONDS)
        except Exception:  # noqa: BLE001  # lint-waiver: LW-241003 [BLE001]; a status tool's failures are open and mean that fact is unavailable.
            # > Catching only OSError misses a probe that raises RuntimeError, and the sample would fail.
            # > Letting the exception propagate fails the measurement because a tool was missing.
            return None
        if result is None or result.returncode != 0:
            return None
        return result.stdout


def parse_loadavg(text: str) -> tuple[float, float, float] | None:
    """The 1, 5, and 15 minute loads from a ``/proc/loadavg`` line."""
    parts = text.split()
    if len(parts) < _LOADAVG_FIELD_COUNT:
        return None
    try:
        return (float(parts[0]), float(parts[1]), float(parts[2]))
    except ValueError:
        return None


def parse_vm_stat(text: str | None) -> int | None:
    """Free bytes from ``vm_stat``: free pages times the page size in the header."""
    if text is None:
        return None
    page = _page_size(text)
    free = _pages(text, "Pages free:")
    if page is None or free is None:
        return None
    return page * free


def parse_memory_pressure(text: str | None) -> str | None:
    """The system-wide free percentage ``memory_pressure`` prints, such as ``37%``."""
    if text is None:
        return None
    marker = "System-wide memory free percentage:"
    for line in text.splitlines():
        if marker in line:
            value = line.split(marker, 1)[1].strip()
            return value or None
    return None


def parse_meminfo(text: str) -> int | None:
    """``MemAvailable`` from ``/proc/meminfo``, in bytes."""
    for line in text.splitlines():
        if not line.startswith("MemAvailable:"):
            continue
        parts = line.split()
        if len(parts) < _MEMINFO_FIELD_COUNT:
            return None
        try:
            kibibytes = int(parts[1])
        except ValueError:
            return None
        return kibibytes * 1024
    return None


def parse_gpu_processes(text: str) -> tuple[str, ...]:
    """Non-empty process lines from an ``nvidia-smi`` or ``rocm-smi`` listing."""
    rows: list[str] = []
    for line in text.splitlines():
        cleaned = " ".join(line.replace(",", " ").split())
        if cleaned and not cleaned.lower().startswith("pid"):
            rows.append(cleaned)
    return tuple(rows)


def _call[T](read: Callable[[], T | None]) -> T | None:
    try:
        return read()
    except Exception:  # noqa: BLE001  # lint-waiver: LW-241004 [BLE001]; each host fact fails on its own, and the failures are open.
        # > Catching only OSError misses a parser that raises ValueError after a partial read.
        # > Letting the exception propagate drops the whole sample, including the facts that were read.
        return None


def _read_text(path: str) -> str | None:
    try:
        return Path(path).read_text(encoding="utf-8")
    except OSError:
        return None


def _page_size(text: str) -> int | None:
    marker = "page size of "
    start = text.find(marker)
    if start < 0:
        return None
    number = text[start + len(marker) :].split(" ", 1)[0]
    try:
        return int(number)
    except ValueError:
        return None


def _pages(text: str, label: str) -> int | None:
    for line in text.splitlines():
        if not line.startswith(label):
            continue
        digits = "".join(character for character in line.split(":", 1)[1] if character.isdigit())
        if not digits:
            return None
        return int(digits)
    return None
