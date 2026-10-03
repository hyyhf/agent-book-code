"""Own a command's descendants, including those outliving the shell.

Windows children are assigned to a kill-on-close Job before their primary thread
is resumed. No taskkill subprocess, console window, or PID-tree guessing is used.
POSIX commands run in a new session and are terminated by process group.
"""
from __future__ import annotations

import ctypes
import os
import signal
import subprocess
import time


class ProcessTree:
    def __init__(self):
        self.proc = None
        self.job = None
        if os.name == "nt":
            self.api = ctypes.WinDLL("kernel32", use_last_error=True)
            signatures = {
                "CreateJobObjectW": ([ctypes.c_void_p, ctypes.c_wchar_p], ctypes.c_void_p),
                "SetInformationJobObject": ([ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32], ctypes.c_int),
                "AssignProcessToJobObject": ([ctypes.c_void_p, ctypes.c_void_p], ctypes.c_int),
                "TerminateJobObject": ([ctypes.c_void_p, ctypes.c_uint32], ctypes.c_int),
                "QueryInformationJobObject": ([ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p], ctypes.c_int),
                "CloseHandle": ([ctypes.c_void_p], ctypes.c_int),
                "CreateToolhelp32Snapshot": ([ctypes.c_uint32, ctypes.c_uint32], ctypes.c_void_p),
                "Thread32First": ([ctypes.c_void_p, ctypes.c_void_p], ctypes.c_int),
                "Thread32Next": ([ctypes.c_void_p, ctypes.c_void_p], ctypes.c_int),
                "OpenThread": ([ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32], ctypes.c_void_p),
                "ResumeThread": ([ctypes.c_void_p], ctypes.c_uint32),
                "OpenProcess": ([ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32], ctypes.c_void_p),
                "WaitForSingleObject": ([ctypes.c_void_p, ctypes.c_uint32], ctypes.c_uint32),
            }
            for name, (args, result) in signatures.items():
                fn = getattr(self.api, name)
                fn.argtypes, fn.restype = args, result
            self.job = self.api.CreateJobObjectW(None, None)
            if not self.job:
                raise ctypes.WinError(ctypes.get_last_error())
            limits = _ExtendedLimits()
            limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not self.api.SetInformationJobObject(self.job, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
                error = ctypes.WinError(ctypes.get_last_error())
                self.close()
                raise error

    def start(self, command, kwargs) -> subprocess.Popen:
        kwargs = dict(kwargs)
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW | 0x4  # CREATE_SUSPENDED
        else:
            kwargs["start_new_session"] = True
        try:
            self.proc = subprocess.Popen(command, **kwargs)
            if os.name == "nt":
                if not self.api.AssignProcessToJobObject(self.job, int(self.proc._handle)):
                    raise ctypes.WinError(ctypes.get_last_error())
                self._resume_primary_thread(self.proc.pid)
            return self.proc
        except BaseException:
            self.close()
            raise

    def _resume_primary_thread(self, pid: int):
        class ThreadEntry(ctypes.Structure):
            _fields_ = [("size", ctypes.c_uint32), ("usage", ctypes.c_uint32),
                        ("tid", ctypes.c_uint32), ("pid", ctypes.c_uint32),
                        ("priority", ctypes.c_long), ("delta", ctypes.c_long), ("flags", ctypes.c_uint32)]

        snapshot = self.api.CreateToolhelp32Snapshot(4, 0)  # TH32CS_SNAPTHREAD
        if snapshot == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            entry = ThreadEntry()
            entry.size = ctypes.sizeof(entry)
            found = self.api.Thread32First(snapshot, ctypes.byref(entry))
            while found:
                if entry.pid == pid:
                    thread = self.api.OpenThread(2, False, entry.tid)  # THREAD_SUSPEND_RESUME
                    if not thread:
                        raise ctypes.WinError(ctypes.get_last_error())
                    try:
                        if self.api.ResumeThread(thread) == 0xFFFFFFFF:
                            raise ctypes.WinError(ctypes.get_last_error())
                    finally:
                        self.api.CloseHandle(thread)
                    return
                found = self.api.Thread32Next(snapshot, ctypes.byref(entry))
            raise OSError("Cannot locate suspended command thread")
        finally:
            self.api.CloseHandle(snapshot)

    def terminate(self):
        if self.job:
            if not self.api.TerminateJobObject(self.job, 1):
                raise ctypes.WinError(ctypes.get_last_error())
        elif self.proc is not None and os.name != "nt":
            try:
                # The group ID remains valid even after the shell exits.
                os.killpg(self.proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        if self.proc is not None and self.proc.poll() is None:
            self.proc.kill()

    def close(self):
        handles = []
        try:
            if self.job:
                # Job accounting can reach zero before process teardown releases
                # files/cwd. Keep handles to wait for actual process termination.
                pids = _ProcessIds()
                if self.api.QueryInformationJobObject(self.job, 3, ctypes.byref(pids), ctypes.sizeof(pids), None):
                    for pid in pids.ids[:pids.count]:
                        handle = self.api.OpenProcess(0x100000, False, pid)
                        if handle:
                            handles.append(handle)
            self.terminate()
            if self.job:
                deadline = time.monotonic() + 2
                accounting = _Accounting()
                while time.monotonic() < deadline:
                    if not self.api.QueryInformationJobObject(
                        self.job, 1, ctypes.byref(accounting), ctypes.sizeof(accounting), None
                    ):
                        raise ctypes.WinError(ctypes.get_last_error())
                    if not accounting.active:
                        break
                    time.sleep(0.01)
                for handle in handles:
                    self.api.WaitForSingleObject(handle, max(0, int((deadline - time.monotonic()) * 1000)))
        finally:
            for handle in handles:
                self.api.CloseHandle(handle)
            if self.job:
                self.api.CloseHandle(self.job)
                self.job = None
            if self.proc is not None:
                try:
                    self.proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    pass
                self.proc = None


class _BasicLimits(ctypes.Structure):
    _fields_ = [("process_time", ctypes.c_int64), ("job_time", ctypes.c_int64),
                ("flags", ctypes.c_uint32), ("min_ws", ctypes.c_size_t), ("max_ws", ctypes.c_size_t),
                ("active_limit", ctypes.c_uint32), ("affinity", ctypes.c_size_t),
                ("priority", ctypes.c_uint32), ("scheduling", ctypes.c_uint32)]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [("basic", _BasicLimits), ("io", ctypes.c_uint64 * 6),
                ("process_memory", ctypes.c_size_t), ("job_memory", ctypes.c_size_t),
                ("peak_process_memory", ctypes.c_size_t), ("peak_job_memory", ctypes.c_size_t)]


class _Accounting(ctypes.Structure):
    _fields_ = [("times", ctypes.c_int64 * 4), ("faults", ctypes.c_uint32),
                ("total", ctypes.c_uint32), ("active", ctypes.c_uint32), ("terminated", ctypes.c_uint32)]


class _ProcessIds(ctypes.Structure):
    _fields_ = [("assigned", ctypes.c_uint32), ("count", ctypes.c_uint32), ("ids", ctypes.c_size_t * 2048)]
