#!/usr/bin/env python3
"""Seeded defects for the negative controls of the instruction-level
specification (formal/isa/isa.sby, docs/isa-spec.md).

usage: mutate_isa.py INPUT.v OUTDIR        write OUTDIR/processor_fv_isa_<name>.v
       mutate_isa.py --list                name and description of each defect

Every defect is a fixed edit of engine 0's logic (or of a processor register
of engine 0) in formal/build/rtl/processor_fv.v. The edit is located
structurally, never by a generated net number:

  * engine 0's registers are the last member of each fv_<name> port
    concatenation (generate_fv.ml packs engine 0 in the least significant
    slice); processor registers of engine 0 are named <name>_0;
  * engine 0's opcode is bits 31:24 of the {hi, lo} concatenation of the
    A_DOUT pins of instruction_sram_e0_hi/lo, and "opcode X" is a compare of
    that net with an 8-bit constant X;
  * a register's next-state logic is the combinational cone of its D input;
    in it, the multiplexer selected by "opcode X" (or by a command, a start
    or a stop) is the one a defect changes.

A defect changes the value that multiplexer selects: `flip` inverts bit 0,
`keep` holds the register, `swap:Y` takes the value opcode Y would write.
The script fails if a structure is missing or ambiguous, or if an edit leaves
the text unchanged.
"""
import re
import sys

E = 0  # the engine every defect is seeded into


class Netlist:
    def __init__(self, text):
        self.text = text
        self.assigns = {}
        for m in re.finditer(r'\n\s*assign\s+(\w+)\s*=\s*(.*?);', text, re.S):
            self.assigns[m.group(1)] = ' '.join(m.group(2).split())
        self.regs = {}
        for m in re.finditer(r'always @\(posedge clk\) begin\s*if \((\w+)\)\s*(\w+) <= (\w+);'
                             r'\s*else\s*\2 <= (\w+);\s*end', text):
            self.regs[m.group(2)] = dict(reset=m.group(1), d=m.group(4))
        self.consts = {}
        for n, e in self.assigns.items():
            mm = re.fullmatch(r"(\d+)'b([01]+)", e)
            if mm:
                self.consts[n] = (int(mm.group(1)), int(mm.group(2), 2))

    def expr(self, n):
        return self.assigns[n]

    def members(self, n):
        mm = re.fullmatch(r'\{\s*(.*)\s*\}', self.assigns[n])
        if not mm:
            raise SystemExit(f"mutate_isa.py: {n} is not a concatenation")
        return [x.strip() for x in mm.group(1).split(',')]

    def port(self, name):
        m = re.search(r'\n\s*assign %s = (\w+);' % re.escape(name), self.text)
        if not m:
            raise SystemExit(f"mutate_isa.py: no port {name}")
        return m.group(1)

    def engine_member(self, port):
        """Engine E's slice of a packed per-engine port (engine 0 last)."""
        mem = self.members(self.port(port))
        return mem[len(mem) - 1 - E]

    def resolve(self, n):
        while n in self.assigns and re.fullmatch(r'\w+', self.assigns[n]):
            n = self.assigns[n]
        return n

    def cone(self, root, stop):
        seen, stack = set(), [root]
        while stack:
            n = stack.pop()
            if n in seen or n not in self.assigns or n in stop:
                continue
            seen.add(n)
            for t in re.findall(r'\b\w+\b', self.assigns[n]):
                if t in self.assigns and t not in seen:
                    stack.append(t)
        return seen

    def muxes(self, root):
        out = []
        for n in self.cone(root, set(self.regs)):
            m = re.fullmatch(r'(\w+) \? (.+) : (.+)', self.assigns[n])
            if m:
                out.append((n,) + m.groups())
        return out


def opcode_net(nl):
    outs = {}
    for half in ('lo', 'hi'):
        m = re.findall(r'instruction_sram_e%d_%s\s*\([^;]*?\.A_DOUT\((\w+)\[15:0\]\)' % (E, half),
                       nl.text, re.S)
        if len(m) != 1:
            raise SystemExit(f"mutate_isa.py: engine {E} {half} SRAM A_DOUT not found")
        outs[half] = m[0]
    words = set()
    for n, ex in nl.assigns.items():
        mm = re.fullmatch(r'\{\s*(\w+),\s*(\w+)\s*\}', ex)
        if mm and nl.resolve(mm.group(1)) == outs['hi'] and nl.resolve(mm.group(2)) == outs['lo']:
            words.add(n)
            words.update(m for m, e2 in nl.assigns.items() if e2 == n)
    ops = [n for n, ex in nl.assigns.items()
           if re.fullmatch(r'(\w+)\[31:24\]', ex) and ex.split('[')[0] in words]
    if len(ops) != 1:
        raise SystemExit(f"mutate_isa.py: expected one opcode net of engine {E}, found {ops}")
    return ops[0]


def compare_value(nl, sel, key):
    """X if net sel is (key == X) or (X == key), else None."""
    ex = nl.assigns.get(sel, '')
    m = re.fullmatch(r'(\w+) == (\w+)', ex)
    if not m:
        return None
    a, b = m.groups()
    if a == key and b in nl.consts:
        return nl.consts[b][1]
    if b == key and a in nl.consts:
        return nl.consts[a][1]
    return None


def reg_of(nl, field):
    r = nl.engine_member('fv_' + field)
    if r not in nl.regs:
        raise SystemExit(f"mutate_isa.py: {r} ({field}) is not a register")
    return r


def op_mux(nl, reg, opcode):
    op = opcode_net(nl)
    found = [(n, s, t, e) for (n, s, t, e) in nl.muxes(nl.regs[reg]['d'])
             if compare_value(nl, s, op) == opcode]
    if len(found) != 1:
        raise SystemExit(f"mutate_isa.py: {len(found)} opcode-{opcode} multiplexers for {reg}")
    return found[0]


def sel_mux(nl, reg, pred, what):
    found = [(n, s, t, e) for (n, s, t, e) in nl.muxes(nl.regs[reg]['d']) if pred(s)]
    if len(found) != 1:
        raise SystemExit(f"mutate_isa.py: {len(found)} {what} multiplexers for {reg}")
    return found[0]


def cmd_select(nl, code):
    """Selects whose cone compares the accepted command code with `code`."""
    cmd = nl.port('dbg_command_code')

    def pred(s):
        return any(compare_value(nl, n, cmd) == code for n in nl.cone(s, set(nl.regs)))
    return pred


def rewrite(text, net, new_rhs):
    pat = re.compile(r'(\n\s*assign %s = )([^;]*);' % re.escape(net))
    text, n = pat.subn(lambda m: m.group(1) + new_rhs + ';', text)
    if n != 1:
        raise SystemExit(f"mutate_isa.py: expected one driver of {net}, found {n}")
    return text


def value_edit(nl, reg, mux, kind):
    n, s, t, e = mux
    if kind == 'flip':
        new_t = f"{t} ^ 1'b1"
    elif kind == 'keep':
        new_t = reg
    elif kind.startswith('swap:'):
        new_t = op_mux(nl, reg, int(kind[5:]))[2]
    else:
        raise SystemExit(kind)
    return rewrite(nl.text, n, f'{s} ? {new_t} : {e}')


def at_opcode(field, opcode, kind):
    def f(nl):
        reg = reg_of(nl, field)
        return value_edit(nl, reg, op_mux(nl, reg, opcode), kind)
    return f


def processor_reg(name):
    return f'{name}_{E}'


def at_command(reg_name, code, kind, from_payload=False):
    """The multiplexer of a register selected by an accepted command `code`
    (with from_payload: the one whose value is taken from the payload)."""
    def f(nl):
        reg = processor_reg(reg_name) if not reg_name.startswith('fv_') else reg_of(nl, reg_name[3:])
        pay = nl.port('dbg_command_payload')
        sel = cmd_select(nl, code)

        def pred_mux(m):
            n, s, t, e = m
            if not sel(s):
                return False
            if not from_payload:
                return True
            return any(nl.assigns[x].startswith(pay + '[') for x in nl.cone(t, set(nl.regs)))
        found = [m for m in nl.muxes(nl.regs[reg]['d']) if pred_mux(m)]
        if len(found) != 1:
            raise SystemExit(f"mutate_isa.py: {len(found)} command-{code} multiplexers for {reg}")
        return value_edit(nl, reg, found[0], kind)
    return f


def at_port_select(field, port, kind):
    """The multiplexer of engine E's register selected by engine E's slice of a dbg port."""
    def f(nl):
        reg = reg_of(nl, field)
        sig = nl.resolve(nl.engine_member(port))
        return value_edit(nl, reg, sel_mux(nl, reg, lambda s: nl.resolve(s) == sig, port), kind)
    return f


def drop_and_term(port, term_pred, what):
    """Engine E's slice of a dbg handshake port is `a & b`; drop the term that
    matches term_pred (the queue level or mailbox guard)."""
    def f(nl):
        net = nl.resolve(nl.engine_member(port))
        m = re.fullmatch(r'(\w+) & (\w+)', nl.assigns[net])
        if not m:
            raise SystemExit(f"mutate_isa.py: {port} slice is not a two-term AND")
        a, b = m.groups()
        hits = [x for x in (a, b) if term_pred(nl, x)]
        if len(hits) != 1:
            raise SystemExit(f"mutate_isa.py: {what} term of {port} not found")
        keep = b if hits[0] == a else a
        return rewrite(nl.text, net, keep)
    return f


def is_not_level_compare(nl, x):
    m = re.fullmatch(r'~ (\w+)', nl.assigns.get(x, ''))
    return bool(m) and re.fullmatch(r'\w+ == \w+', nl.assigns.get(m.group(1), '')) is not None


def is_mailbox(nl, x):
    return x == f'mailbox_{E}'


def decode_case(opcode, drop_ownership=False, to_vdd=False):
    """Engine E's instruction-valid decode is `case (OP) <n>: V <= NET;`."""
    def f(nl):
        op = opcode_net(nl)
        blocks = re.findall(r'always @\* begin\s*case \(%s\)(.*?)endcase' % re.escape(op), nl.text, re.S)
        if len(blocks) != 1:
            raise SystemExit(f"mutate_isa.py: {len(blocks)} decode case blocks")
        block = blocks[0]
        m = re.search(r'\n(\s*)%d:\s*\n\s*(\w+) <= (\w+);' % opcode, block)
        if not m:
            raise SystemExit(f"mutate_isa.py: decode case {opcode} not found")
        var, net = m.group(2), m.group(3)
        if to_vdd:
            new = 'vdd'
        else:
            terms = re.fullmatch(r'(\w+) & (\w+)', nl.assigns[net])
            if not terms:
                raise SystemExit(f"mutate_isa.py: decode {opcode} is not a two-term AND")
            owned = [x for x in terms.groups()
                     if re.search(r'ownership_%d' % E, ' '.join(nl.assigns.get(y, '')
                                  for y in nl.cone(x, set(nl.regs))) + ' ' + nl.assigns.get(x, ''))]
            if len(owned) != 1:
                raise SystemExit(f"mutate_isa.py: ownership term of decode {opcode} not found")
            new = [x for x in terms.groups() if x != owned[0]][0]
        old = m.group(0)
        new_case = old.replace(f'{var} <= {net};', f'{var} <= {new};')
        text = nl.text.replace(block, block.replace(old, new_case, 1), 1)
        return text
    return f


def signal_self(nl):
    """Engine E's SIGNAL delivers every recipient except engine E itself."""
    op = opcode_net(nl)
    net = nl.resolve(nl.engine_member('dbg_event_set'))
    hits = []
    for (n, s, t, e) in nl.muxes(net):
        if any(compare_value(nl, c, op) == 14 for c in nl.cone(s, set(nl.regs))):
            hits.append((n, s, t, e))
    if len(hits) != 1:
        raise SystemExit(f"mutate_isa.py: {len(hits)} SIGNAL terms of engine {E}")
    n, s, t, e = hits[0]
    return rewrite(nl.text, n, f"{s} ? ({t} & ~(4'b0001 << {E})) : {e}")


def host_event(nl):
    """Host EVENT does not set engine E's mailbox: the term that ANDs an
    accepted EVENT (code 9) with payload bit E becomes 0."""
    net = nl.resolve(nl.engine_member('dbg_event_set'))
    pay = nl.port('dbg_command_payload')
    is_event = cmd_select(nl, 9)
    hits = []
    for n in nl.cone(net, set(nl.regs)):
        m = re.fullmatch(r'(\w+) & (\w+)', nl.assigns[n])
        if not m:
            continue
        a, b = m.groups()
        for x, y in ((a, b), (b, a)):
            if nl.assigns.get(nl.resolve(y), '') == f'{pay}[{E}:{E}]' and is_event(x):
                hits.append(n)
    if len(hits) != 1:
        raise SystemExit(f"mutate_isa.py: {len(hits)} EVENT terms for engine {E}")
    return rewrite(nl.text, hits[0], "1'b0")


def fetch_bound(nl):
    """PC equal to the image length still issues (< becomes <=)."""
    pc = reg_of(nl, 'pc')
    text, count = nl.text, 0
    for n, ex in nl.assigns.items():
        m = re.fullmatch(r'%s < (\w+)' % re.escape(pc), ex)
        if m:
            text = rewrite(text, n, f'{pc} <= {m.group(1)}')
            count += 1
    if count == 0:
        raise SystemExit("mutate_isa.py: no PC bound compare")
    return text


def hold_stuck(nl):
    """The WAIT hold does not count down."""
    reg = reg_of(nl, 'wait_timer')

    def pred(s):
        m = re.fullmatch(r'~ (\w+)', nl.assigns.get(s, ''))
        return bool(m) and re.fullmatch(r'%s == \w+' % reg, nl.assigns.get(m.group(1), '')) is not None
    return value_edit(nl, reg, sel_mux(nl, reg, pred, 'hold'), 'keep')


def idle_x(nl):
    """A halted engine's x register changes every cycle: the issue gate's
    hold branch writes x ^ 1."""
    reg = reg_of(nl, 'x')
    found = [(n, s, t, e) for (n, s, t, e) in nl.muxes(nl.regs[reg]['d']) if e == reg
             and nl.muxes(t) and any(compare_value(nl, c, opcode_net(nl)) is not None
                                     for c in nl.cone(t, set(nl.regs)))]
    if len(found) != 1:
        raise SystemExit(f"mutate_isa.py: {len(found)} issue gates for {reg}")
    n, s, t, e = found[0]
    return rewrite(nl.text, n, f"{s} ? {t} : {reg} ^ 1'b1")


def pinmap_open_drain(nl):
    """An open-drain pin of engine E is enabled whatever its value."""
    lout = reg_of(nl, 'logical_output')

    def is_not(x, pat):
        return re.fullmatch(r'~ ' + pat, nl.assigns.get(x, '')) is not None
    hits = []
    for n, ex in nl.assigns.items():
        m = re.fullmatch(r'(\w+) \| (\w+)', ex)
        if m and {is_not(m.group(1), r'open_drain_%d' % E), is_not(m.group(2), re.escape(lout))} == {True} \
                or m and {is_not(m.group(2), r'open_drain_%d' % E), is_not(m.group(1), re.escape(lout))} == {True}:
            hits.append(n)
    if len(hits) != 1:
        raise SystemExit(f"mutate_isa.py: {len(hits)} open-drain enables for {lout}")
    return rewrite(nl.text, hits[0], "1'b1")


def flush_tx(nl):
    """FLUSH does not clear engine E's TX queue: its clear is the chip reset only."""
    lvl = nl.resolve(nl.engine_member('dbg_tx_level'))
    if lvl not in nl.regs:
        raise SystemExit("mutate_isa.py: TX level is not a register")
    rst = nl.regs[lvl]['reset']
    m = re.fullmatch(r'(\w+) \| (\w+)', nl.assigns.get(rst, ''))
    if not m:
        raise SystemExit("mutate_isa.py: TX queue clear is not reset | flush")
    flush = nl.resolve(nl.engine_member('dbg_fifo_clear'))
    parts = [x for x in m.groups() if nl.resolve(x) != flush]
    if len(parts) != 1:
        raise SystemExit("mutate_isa.py: FLUSH term of the TX queue clear not found")
    return rewrite(nl.text, rst, parts[0])


def rx_store(nl):
    """Engine E's RX queue stores the pushed word with bit 0 inverted."""
    head = nl.resolve(nl.engine_member('dbg_rx_head'))
    m = re.fullmatch(r'(\w+)\[(\w+)\]', nl.assigns[head])
    if not m:
        raise SystemExit("mutate_isa.py: RX head is not a memory read")
    mem = m.group(1)
    pat = re.compile(r'(%s\[\w+\] <= )(\w+);' % re.escape(mem))
    text, n = pat.subn(lambda mm: mm.group(1) + mm.group(2) + " ^ 1'b1;", nl.text)
    if n != 1:
        raise SystemExit(f"mutate_isa.py: {n} writes of {mem}")
    return text


# name: (description, edit, job)
MUTANTS = {
    "nop_uncounted": ("NOP does not increment the completed-instruction count",
                      at_opcode('completed_instructions', 0, 'keep'), "isa_nop_neg"),
    "halt_runs": ("HALT does not stop the engine", at_opcode('running', 1, 'keep'), "isa_halt_neg"),
    "set_unowned": ("SET skips the ownership check", decode_case(2, drop_ownership=True), "isa_set_neg"),
    "dir_unowned": ("DIR skips the ownership check", decode_case(3, drop_ownership=True), "isa_dir_neg"),
    "wait_count": ("WAIT loads n with bit 0 inverted", at_opcode('wait_timer', 4, 'flip'), "isa_wait_neg"),
    "jmp_target": ("JMP lands on its target with bit 0 inverted", at_opcode('pc', 5, 'flip'), "isa_jmp_neg"),
    "pull_empty": ("PULL pops an empty TX FIFO",
                   drop_and_term('dbg_tx_pop', is_not_level_compare, 'not-empty'), "isa_pull_neg"),
    "push_full": ("PUSH enqueues into a full RX FIFO",
                  drop_and_term('dbg_rx_push', is_not_level_compare, 'not-full'), "isa_push_neg"),
    "out_value": ("OUT writes the logical outputs with bit 0 inverted",
                  at_opcode('logical_output', 8, 'flip'), "isa_out_neg"),
    "in_keep": ("IN does not shift the sample into rx", at_opcode('rx', 9, 'keep'), "isa_in_neg"),
    "count_value": ("COUNT loads the repeat counter with bit 0 inverted",
                    at_opcode('repeat_count', 10, 'flip'), "isa_count_neg"),
    "loop_keep": ("LOOP does not decrement the repeat counter",
                  at_opcode('repeat_count', 11, 'keep'), "isa_loop_neg"),
    "limit_value": ("LIMIT loads the limit with bit 0 inverted",
                    at_opcode('wait_limit', 12, 'flip'), "isa_limit_neg"),
    "waitpin_uncounted": ("WAITPIN does not count unsuccessful samples",
                          at_opcode('blocked_cycles', 13, 'keep'), "isa_waitpin_neg"),
    "signal_self": ("SIGNAL from engine 0 does not reach engine 0's own mailbox", signal_self,
                    "isa_signal_neg"),
    "waitevent_empty": ("WAITEVENT consumes an empty mailbox",
                        drop_and_term('dbg_event_clear', is_mailbox, 'mailbox'), "isa_waitevent_neg"),
    "pins_value": ("PINS loads the pin configuration with bit 0 inverted",
                   at_opcode('transfer_pins', 16, 'flip'), "isa_pins_neg"),
    "xfer_issue": ("XFER issue writes the logical outputs with bit 0 inverted",
                   at_opcode('logical_output', 17, 'flip'), "isa_xfer_neg"),
    "mov_literal": ("MOV into tx writes what LOAD would (the literal)",
                    at_opcode('tx', 18, 'swap:19'), "isa_mov_neg"),
    "load_value": ("LOAD into rx writes the literal with bit 0 inverted",
                   at_opcode('rx', 19, 'flip'), "isa_load_neg"),
    "add_xor": ("ADD into x computes XOR", at_opcode('x', 20, 'swap:21'), "isa_add_neg"),
    "xor_or": ("XOR into y computes OR", at_opcode('y', 21, 'swap:23'), "isa_xor_neg"),
    "and_or": ("AND into x computes OR", at_opcode('x', 22, 'swap:23'), "isa_and_neg"),
    "or_xor": ("OR into y computes XOR", at_opcode('y', 23, 'swap:21'), "isa_or_neg"),
    "shl_right": ("SHL of tx shifts right", at_opcode('tx', 24, 'swap:25'), "isa_shl_neg"),
    "shr_left": ("SHR of rx shifts left", at_opcode('rx', 25, 'swap:24'), "isa_shr_neg"),
    "jz_never": ("JZ never branches", at_opcode('pc', 26, 'swap:0'), "isa_jz_neg"),
    "not_copy": ("NOT into x copies register b instead", at_opcode('x', 27, 'swap:18'), "isa_not_neg"),
    "time_value": ("TIME into y writes the timestamp with bit 0 inverted",
                   at_opcode('y', 28, 'flip'), "isa_time_neg"),
    "fault_code": ("FAULT records its code with bit 0 inverted",
                   at_opcode('fault_code', 29, 'flip'), "isa_fault_neg"),
    "invalid_30": ("opcode 30 decodes as valid", decode_case(30, to_vdd=True), "isa_invalid_neg"),
    "fetch_bound": ("an instruction at PC = image length issues", fetch_bound, "isa_fetch_neg"),
    "hold_stuck": ("the WAIT hold does not count down", hold_stuck, "isa_hold_neg"),
    "idle_x": ("a halted engine's x register changes", idle_x, "isa_idle_neg"),
    "pinmap_open_drain": ("an open-drain pin is enabled whatever its value", pinmap_open_drain,
                          "isa_pinmap_neg"),
    "cmd_select": ("SELECT records the engine with bit 0 inverted",
                   lambda nl: value_edit(nl, 'host_selected_engine',
                                         sel_mux(nl, 'host_selected_engine', cmd_select(nl, 0), 'SELECT'),
                                         'flip'), "isa_cmd_select_neg"),
    "cmd_begin": ("BEGIN does not reset the program write address",
                  at_command('image_loaded', 1, 'keep'), "isa_cmd_begin_neg"),
    "cmd_commit": ("COMMIT records the length with bit 0 inverted",
                   at_command('image_length', 2, 'flip'), "isa_cmd_commit_neg"),
    "cmd_own": ("OWN records the ownership with bit 0 inverted",
                at_command('ownership', 3, 'flip'), "isa_cmd_own_neg"),
    "cmd_start": ("START does not clear x", at_port_select('x', 'dbg_start', 'keep'), "isa_cmd_start_neg"),
    "cmd_stop": ("STOP does not stop the engine", at_port_select('running', 'dbg_stop', 'keep'),
                 "isa_cmd_stop_neg"),
    "cmd_route": ("ROUTE records the word count with bit 0 inverted",
                  at_command('route_remaining', 6, 'flip', from_payload=True), "isa_cmd_route_neg"),
    "cmd_clear": ("CLEAR does not clear the fault", at_command('fv_fault_code', 7, 'keep'),
                  "isa_cmd_clear_neg"),
    "cmd_event": ("host EVENT does not set engine 0's mailbox", host_event, "isa_cmd_event_neg"),
    "cmd_flush": ("FLUSH does not clear engine 0's TX queue", flush_tx, "isa_cmd_flush_neg"),
    "cmd_trigger": ("TRIGGER records the configuration with bit 0 inverted",
                    at_command('pin_trigger', 11, 'flip'), "isa_cmd_trigger_neg"),
    "queue_tx_head": ("PULL loads the TX head with bit 0 inverted", at_opcode('tx', 6, 'flip'),
                      "isa_queue_tx_neg"),
    "queue_rx_store": ("the RX queue stores the pushed word with bit 0 inverted", rx_store,
                       "isa_queue_rx_neg"),
    "xfer_edges": ("XFER schedules 2a+1 transitions", at_opcode('transfer_edges', 17, 'flip'),
                   "isa_xfer_e2e_neg"),
}


def main() -> None:
    if sys.argv[1:] == ["--list"]:
        for name, (desc, _, job) in MUTANTS.items():
            print(f"{name}\t{job}\t{desc}")
        return
    if len(sys.argv) < 3:
        raise SystemExit(__doc__)
    source, outdir = sys.argv[1], sys.argv[2]
    text = open(source).read()
    nl = Netlist(text)
    only = set(sys.argv[3:])
    for name, (_, edit, _) in MUTANTS.items():
        if only and name not in only:
            continue
        mutated = edit(nl)
        if mutated == text:
            raise SystemExit(f"mutate_isa.py: {name} left the RTL unchanged")
        with open(f"{outdir}/processor_fv_isa_{name}.v", "w") as f:
            f.write(mutated)


if __name__ == "__main__":
    main()
