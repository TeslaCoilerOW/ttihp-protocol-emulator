# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""Exceptions raised by the host library (MicroPython compatible)."""


class HostError(Exception):
    """Base class of every host-library error."""


class HostTimeout(HostError):
    """A handshake did not complete within its cycle budget.

    MicroPython has no TimeoutError, so this derives from HostError only.
    The partial transfer has already been abandoned by a window change.
    """


class CommandRejected(HostError):
    """The FAULT pin rose after a command word: the chip set its sticky host fault.

    The chip does not report which command was rejected; the inference is from
    uo[7] before and after the word. A running engine that faults on the same
    cycles is indistinguishable, so call ProtocolEmulator.fault_report().
    """

    def __init__(self, message, op=None, payload=None):
        super().__init__(message)
        self.op = op
        self.payload = payload


class ImageError(HostError):
    """A firmware image failed verification (schema, SHA-256 or architecture binding)."""


class IsaMismatch(HostError):
    """READ_SELECT 7 returned an ISA version this library or image does not accept."""


class EngineFault(HostError):
    """An engine or the host interface reported a fault."""

    def __init__(self, message, report=None):
        super().__init__(message)
        self.report = report


class PadContention(HostError):
    """A simulated pad was driven high by one side and low by the other."""


class ReplayDivergence(HostError):
    """A replayed trace and the running code disagree (MicroPython differential check)."""
