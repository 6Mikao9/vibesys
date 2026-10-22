"""Darwin process families identified by an inherited, unforgeable Seatbelt policy.

A fresh random Mach lookup rule labels this one command. The kernel inherits
it across forks and execs; neither setsid nor clearing the environment removes
it. A second lookup distinguishes this family from profiles that deny all Mach
lookups. No Mach service is registered and no filesystem marker is created.
"""

from __future__ import annotations

import ctypes
import errno
import os
import uuid
from pathlib import PurePath
from typing import TYPE_CHECKING

from vs_sim.process_family import ProcessIdentity

if TYPE_CHECKING:
    import signal
    from collections.abc import Sequence

# Darwin libproc ABI, bsd/sys/proc_info{,_private}.h. The kernel validates the
# audit token's PID version atomically when delivering the signal.
_PID_SHORT_INFO = 13
_PID_UNIQUE_INFO = 17
_UID_ONLY = 4
_ZOMBIE = 5
_GLOBAL_NAME = 2
_INLINE_PROFILE_PREFIX_LENGTH = 3


class _BsdShortInfo(ctypes.Structure):
    _fields_ = [
        ("pid", ctypes.c_uint32),
        ("ppid", ctypes.c_uint32),
        ("pgid", ctypes.c_uint32),
        ("status", ctypes.c_uint32),
        ("comm", ctypes.c_char * 16),
        ("flags", ctypes.c_uint32),
        ("uid", ctypes.c_uint32),
        ("gid", ctypes.c_uint32),
        ("ruid", ctypes.c_uint32),
        ("rgid", ctypes.c_uint32),
        ("svuid", ctypes.c_uint32),
        ("svgid", ctypes.c_uint32),
        ("reserved", ctypes.c_uint32),
    ]


class _UniqueInfo(ctypes.Structure):
    _fields_ = [
        ("uuid", ctypes.c_uint8 * 16),
        ("uniqueid", ctypes.c_uint64),
        ("parent_uniqueid", ctypes.c_uint64),
        ("version", ctypes.c_int32),
        ("parent_version", ctypes.c_int32),
        ("reserved", ctypes.c_uint64 * 2),
    ]


class DarwinProcessFamily:
    """Find and signal only this command's inherited kernel label."""

    def __init__(self) -> None:
        """Load the native APIs before any command can start; fail if unavailable."""
        self._tag = "vibesys.process." + uuid.uuid4().hex
        self._control = "vibesys.process." + uuid.uuid4().hex
        self._proc = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
        self._sandbox = ctypes.CDLL("/usr/lib/libsandbox.dylib", use_errno=True)
        self._check = self._sandbox.sandbox_check
        # Fixed arguments are essential for variadic calls on Apple Silicon.
        self._check.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int]
        self._check.restype = ctypes.c_int
        self._filter = (
            _GLOBAL_NAME | ctypes.c_int.in_dll(self._sandbox, "SANDBOX_CHECK_NO_REPORT").value
        )
        self._proc.proc_pidinfo.argtypes = [
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_uint64,
            ctypes.c_void_p,
            ctypes.c_int,
        ]
        self._proc.proc_listpids.argtypes = [
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.c_int,
        ]
        self._proc.proc_signal_with_audittoken.argtypes = [ctypes.c_void_p, ctypes.c_int]
        # Discover an unsupported ABI before starting a command we could not reap.
        self._matches(os.getpid())
        for flavor, info in ((_PID_UNIQUE_INFO, _UniqueInfo()), (_PID_SHORT_INFO, _BsdShortInfo())):
            if not self._info(os.getpid(), flavor, info):
                message = "cannot inspect the process-family supervisor"
                raise OSError(message)

    def prepare(self, argv: Sequence[str]) -> tuple[str, ...]:
        """Add the label to an existing inline profile, or use an unrestricted profile.

        Keep the actual confinement in the same profile: macOS refuses nested
        sandbox-exec, so applying a separate profile before it would break it.
        """
        rules = (
            f'\n(deny mach-lookup (global-name "{self._tag}"))'
            f'\n(allow mach-lookup (global-name "{self._control}"))'
        )
        if (
            len(argv) >= _INLINE_PROFILE_PREFIX_LENGTH
            and PurePath(argv[0]).name == "sandbox-exec"
            and argv[1] == "-p"
        ):
            return (*argv[:2], argv[2] + rules, *argv[3:])
        return ("/usr/bin/sandbox-exec", "-p", "(version 1)(allow default)" + rules, *argv)

    def _info(self, pid: int, flavor: int, info: ctypes.Structure) -> bool:
        size = ctypes.sizeof(info)
        count = self._proc.proc_pidinfo(pid, flavor, 0, ctypes.byref(info), size)
        if count == size:
            return True
        number = ctypes.get_errno()
        if number in (errno.ESRCH, errno.ENOENT):
            return False
        raise OSError(number, f"cannot inspect process {pid}, flavor {flavor}")

    def _matches(self, pid: int) -> bool:
        def check(name: str) -> int:
            result = self._check(pid, b"mach-lookup", self._filter, ctypes.c_char_p(name.encode()))
            if result < 0 and ctypes.get_errno() not in (errno.ESRCH, errno.ENOENT):
                raise OSError(ctypes.get_errno(), f"cannot inspect process policy for {pid}")
            return result

        if check(self._tag) != 1:
            return False
        return check(self._control) == 0

    def members(self) -> frozenset[ProcessIdentity]:
        """Inspect all same-uid processes, including children already reparented to init."""
        capacity = self._proc.proc_listpids(_UID_ONLY, os.getuid(), None, 0)
        if capacity <= 0:
            raise OSError(ctypes.get_errno(), "cannot enumerate process family")
        while True:
            pids = (ctypes.c_int * (capacity // ctypes.sizeof(ctypes.c_int) + 64))()
            size = ctypes.sizeof(pids)
            count = self._proc.proc_listpids(_UID_ONLY, os.getuid(), pids, size)
            if count <= 0:
                raise OSError(ctypes.get_errno(), "cannot enumerate process family")
            if count < size:
                break
            capacity = size * 2
        members: set[ProcessIdentity] = set()
        for pid in pids[: count // ctypes.sizeof(ctypes.c_int)]:
            if pid <= 0 or not self._matches(pid):
                continue
            unique = _UniqueInfo()
            info = _BsdShortInfo()
            if (
                self._info(pid, _PID_UNIQUE_INFO, unique)
                and self._info(pid, _PID_SHORT_INFO, info)
                and info.status != _ZOMBIE
                and self._matches(pid)
            ):
                members.add(ProcessIdentity(pid, unique.version))
        return frozenset(members)

    def send(self, member: ProcessIdentity, number: signal.Signals) -> None:
        """Deliver using a PID-version audit token, so PID reuse cannot hit a bystander."""
        token = (ctypes.c_uint32 * 8)(0, 0, 0, 0, 0, member.pid, 0, member.generation)
        result = self._proc.proc_signal_with_audittoken(ctypes.byref(token), number)
        if result < 0 and ctypes.get_errno() != errno.ESRCH:
            raise OSError(ctypes.get_errno(), f"cannot signal process {member.pid}")
