"""A host probe reports the facts it can read and leaves the rest unavailable."""

from __future__ import annotations

from typing import TYPE_CHECKING

from vs_sim.api import HostSample, PlatformHostProbe, ProbeResult
from vs_sim.api.testing import FakeHostProbe

if TYPE_CHECKING:
    from collections.abc import Sequence

_VM_STAT = """Mach Virtual Memory Statistics: (page size of 16384 bytes)
Pages free: 100.
"""
_PRESSURE = "System-wide memory free percentage: 37%\n"
_NVIDIA = ("nvidia-smi", "--query-compute-apps=pid,process_name", "--format=csv,noheader")
_ROCM = ("rocm-smi", "--showpids")


class _Commands:
    """A command probe that answers from a script and records what it was asked."""

    def __init__(self, replies: dict[tuple[str, ...], str | None]) -> None:
        self._replies = replies

    def run(self, argv: Sequence[str], *, timeout_seconds: float) -> ProbeResult | None:
        del timeout_seconds
        text = self._replies.get(tuple(argv))
        if text is None:
            return None
        return ProbeResult(0, text)


class _Raising:
    def run(self, argv: Sequence[str], *, timeout_seconds: float) -> ProbeResult | None:
        del argv, timeout_seconds
        message = "the tool is not installed"
        raise OSError(message)


def test_a_scripted_probe_replays_then_reports_every_fact_unavailable() -> None:
    probe = FakeHostProbe((HostSample(load_1=1.0), HostSample(load_1=2.0)))

    assert probe.sample() == HostSample(load_1=1.0)
    assert probe.sample() == HostSample(load_1=2.0)
    assert probe.sample() == HostSample()


def test_darwin_reads_load_memory_and_gpu_processes_from_the_injected_readers() -> None:
    sample = PlatformHostProbe(
        _Commands(
            {
                ("vm_stat",): _VM_STAT,
                ("memory_pressure",): _PRESSURE,
                _NVIDIA: "4321, python3\n",
            }
        ),
        read_text=lambda _path: None,
        loadavg=lambda: (1.5, 2.5, 3.5),
        cpu_count=lambda: 12,
        platform="darwin",
    ).sample()

    assert sample == HostSample(
        load_1=1.5,
        load_5=2.5,
        load_15=3.5,
        cpu_count=12,
        memory_free_bytes=100 * 16384,
        memory_pressure="37%",
        gpu_processes=("4321 python3",),
    )


def test_linux_reads_proc_and_leaves_pressure_unavailable_when_no_gpu_tool_answers() -> None:
    def read_text(path: str) -> str | None:
        if path == "/proc/loadavg":
            return "0.1 0.2 0.3 1/2 4\n"
        if path == "/proc/meminfo":
            return "MemTotal: 8000 kB\nMemAvailable: 2048 kB\n"
        return None

    sample = PlatformHostProbe(
        _Commands({}), read_text=read_text, cpu_count=lambda: 4, platform="linux"
    ).sample()

    assert sample == HostSample(
        load_1=0.1,
        load_5=0.2,
        load_15=0.3,
        cpu_count=4,
        memory_free_bytes=2048 * 1024,
        memory_pressure=None,
        gpu_processes=None,
    )


def test_an_empty_gpu_listing_is_nobody_and_a_broken_tool_is_unavailable() -> None:
    nobody = PlatformHostProbe(
        _Commands({_NVIDIA: ""}),
        loadavg=lambda: (1.0, 1.0, 1.0),
        cpu_count=lambda: 1,
        platform="darwin",
    ).sample()
    unread = PlatformHostProbe(
        _Commands({_NVIDIA: None, _ROCM: None}),
        loadavg=lambda: None,
        cpu_count=lambda: None,
        platform="darwin",
    ).sample()

    assert nobody.gpu_processes == ()
    assert unread.gpu_processes is None
    assert unread.load_1 is None


def test_a_probe_that_cannot_read_the_host_returns_unavailable_fields() -> None:
    def unread_load() -> tuple[float, float, float] | None:
        message = "unread"
        raise OSError(message)

    def unread_count() -> int | None:
        message = "unread"
        raise OSError(message)

    def unread_text(path: str) -> str | None:
        del path
        message = "unread"
        raise OSError(message)

    sample = PlatformHostProbe(
        _Raising(),
        read_text=unread_text,
        loadavg=unread_load,
        cpu_count=unread_count,
        platform="linux",
    ).sample()

    assert sample == HostSample()
