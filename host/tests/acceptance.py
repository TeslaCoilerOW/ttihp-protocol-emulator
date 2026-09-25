# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""Acceptance oracle: command() return values and the library's selection
record checked against what the reference model really did (CPython only).

The spy wraps Reference.command, so every command word the model executes
records the model's own accept/reject result. After each operation of a
tests/upy/ops.py program the oracle checks:

- the command word was executed exactly once;
- True means the model accepted it;
- False means the model rejected it, or accepted it while an engine fault
  rose during the word (uo[7] cannot tell the two apart; no engine was
  faulted before, because FAULT was low when the word began);
- SELECT and READ_SELECT always return the model's exact answer (never None),
  on every port, whatever the FAULT pin shows;
- None for any other command only when the port cannot see uo[7], or the
  model's FAULT output (host fault or any engine fault) was already high when
  the word executed. FAULT is sticky within a word (only CLEAR and reset
  lower it), so that is a necessary condition for uo[7] being high at the
  word's first nibble, which is what the library's None reports;
- pe.selected == model.selected and pe.read_selected == model.read_select
  after every operation.
"""

from collections import Counter

from pe_host import protocol as P

STATIC = (P.SELECT, P.READ_SELECT)


class AcceptanceOracle:
    def __init__(self, pe, port):
        self.pe = pe
        self.model = port.model
        self.narrow = not port.uo_visible & P.UO_FAULT
        self.words = []
        self.problems = []
        self.stats = Counter()
        model = self.model
        real = model.command

        def spy(word):
            fault = model.host_fault or any(e.fault for e in model.engines)
            ok = real(word)
            self.words.append((ok, fault))
            return ok
        model.command = spy
        self._mark = 0

    def before(self, index, op):
        self._mark = len(self.words)

    def after(self, index, op, results):
        pe, model = self.pe, self.model
        if op[0] == "command":
            code, got = op[1], results[0]
            executed = self.words[self._mark:]
            if len(executed) != 1:
                self._problem(index, op, "command word executed %d times" % len(executed))
            else:
                ok, fault_high = executed[0]
                label = "SELECT/READ_SELECT" if code in STATIC else "other"
                self.stats["%s library %s / model %s" % (label, got, ok)] += 1
                if code in STATIC and got is not ok:
                    self._problem(index, op, "library %r, model %r" % (got, ok))
                elif got is True and not ok:
                    self._problem(index, op, "library True, model rejected")
                elif got is False and ok and not any(e.fault for e in model.engines):
                    self._problem(index, op, "library False, model accepted, no engine fault")
                elif got is None and not (self.narrow or fault_high):
                    self._problem(index, op, "None although FAULT was low throughout the word")
        if pe.selected != model.selected:
            self._problem(index, op, "selected %d, model %d" % (pe.selected, model.selected))
        if pe.read_selected != model.read_select:
            self._problem(index, op, "read_selected %d, model %d"
                          % (pe.read_selected, model.read_select))

    def _problem(self, index, op, text):
        if len(self.problems) < 20:
            self.problems.append("op %d %r: %s" % (index, list(op[:3]), text))
        self.stats["problems"] += 1
