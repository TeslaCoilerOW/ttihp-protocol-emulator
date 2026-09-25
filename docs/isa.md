# Protocol processor ISA v2 (implementation contract)

This file is the common contract for three independent implementations: the
Hardcaml RTL (`hardcaml/lib/`), the OCaml assembler (`hardcaml/lib/assembler.ml`)
and the Python reference model (`test/model/reference.py`). Any semantic change
must update all three. ISA2 adds strict receive enqueue, held-RX/version
inspection and autonomous input triggers; legacy `PUSH a=0` remains compatible.
The datasheet (`docs/info.md`) is the user-facing summary. Copied from the
author's asic-lab monorepo (`projects/protocol-emulator/docs/isa.md`, eb6b17c).

## Machine

Configuration: engines 2 or 4 (flagship 4), datapath 16 or 32 (flagship 32), program
words 32/64/128 (flagship 64), FIFO words 8/32 (flagship 8), issue scalar/fused,
prefetch false/true. Every instruction is 32 bits, op=[31:24], a=[23:16],
b=[15:8], c=[7:0], imm24=[23:0], imm16=[15:0]. Registers tx,rx,x,y have datapath
width; arithmetic wraps. Pin numbers are 0..7. PC is bounded by committed image
length; falling outside the image faults. Immediate masks must not address absent
engines. Unused operands are zero. Instructions commit on rising clk edges.

Inputs pass through two flip flops. Each engine observes the previous registered
synchronizer output on an edge. All engine state and output registers use one clk.
A nonblocking instruction costs one cycle. WAIT n advances PC on issue, then
holds execution for n additional cycles; WAIT 0 therefore costs one cycle.
Start is a higher-priority event than ordinary execution and starts execution
on the following edge. Reset/deselection releases all output enables, stops all
engines, invalidates program images, and clears queues/mailboxes/control. Program
storage bits need not reset. Outputs are masked by ownership, run, and fault.
For an open-drain pin, output is always zero and physical OE = logical OE AND
NOT logical output value. SET high therefore releases an enabled open-drain pin.

## Instructions

| op | mnemonic | operands / operation |
|---:|---|---|
|0|NOP|none|
|1|HALT|stop and release output enables; preserve queues and diagnostic registers|
|2|SET|imm24 low8 sets logical pin values; high16 zero|
|3|DIR|imm24 low8 sets logical output enables; high16 zero|
|4|WAIT|imm24 additional cycles|
|5|JMP|imm24 target PC|
|6|PULL|tx := TX FIFO head; block without changing state when empty|
|7|PUSH|a=0 block when full; a=1 fault4 when full; b=c=0. When space exists, append rx to RX FIFO and advance in one cycle|
|8|OUT|a pin, b=0, c direction 0=LSB/right shift, 1=MSB/left shift; update that output bit and shift tx|
|9|IN|a pin, b=0, c direction 0=right shift and insert input at MSB, 1=left shift and insert input at LSB|
|10|COUNT|imm16 repeat counter; a=0|
|11|LOOP|imm24 target: if repeat !=0 decrement and jump, otherwise next PC; COUNT n yields n+1 loop iterations|
|12|LIMIT|imm24 nonzero maximum blocked cycles for WAITPIN/WAITEVENT (reset default 65535)|
|13|WAITPIN|a pin, b expected bit 0/1, c=0; consume one issue edge if already true; otherwise block, fault after LIMIT consecutive unsuccessful samples|
|14|SIGNAL|imm24 low engine-count bits sets recipients' event mailboxes|
|15|WAITEVENT|none; consume own pending mailbox bit or wait bounded by LIMIT; simultaneous delivery wins over clear|
|16|PINS|imm24 bits2:0 clock pin,5:3 TX pin,8:6 RX pin; high15 zero|
|17|XFER|a bit count 1..datapath width, b half-period 1..255 cycles, c bits0 CPOL,1 CPHA,2 MSB-first,3 drive,4 sample; other bits zero; fused only|
|18|MOV|a dest register, b source register, c=0; registers 0=tx,1=rx,2=x,3=y|
|19|LOAD|a dest register, imm16 zero-extended literal|
|20|ADD|a dest, b source, c=0|
|21|XOR|a dest, b source, c=0|
|22|AND|a dest, b source, c=0|
|23|OR|a dest, b source, c=0|
|24|SHL|a register, b=0, c shift count < datapath width|
|25|SHR|a register, b=0, c shift count < datapath width|
|26|JZ|a register, imm16 target PC|
|27|NOT|a register, b=c=0|
|28|TIME|a register := common 32-bit timestamp truncated to datapath width; b=c=0|
|29|FAULT|imm24 low8 nonzero explicit fault code; high16 zero|

Other opcodes fault. Direction operations affect tx/rx only as specified. Pin
writes outside ownership and output enables outside ownership fault. Reading any
pin is legal. SET/DIR are whole-eight-bit logical values and may contain only
owned bits. The assembler emits owned masks.

XFER sets clock idle on issue, prepares CPHA0 first data bit on issue, then waits
b clocks to its first transition. CPHA0 samples on active edges and shifts output
for the next bit on idle edges. CPHA1 drives/shifts on active edges and samples on
idle edges. MSB-first samples by shifting rx left; LSB-first samples by shifting
rx right. Exactly 2*a transitions occur, each separated by b cycles. PC advances
at final idle edge. No FIFO operations occur inside XFER. Receiver synchronization
and physical return delay constrain usable b; no external maximum rate is implied.
When drive is enabled, the configured clock and TX pins must differ; a collision
faults with invalid-operand code 1 before any transfer transition. Observing the
clock or TX pin through the RX selection remains legal.

Fused-disabled firmware must use scalar instructions. Prefetch may change internal
implementation only: architectural cycle behavior must remain identical for an
identical program/configuration except the explicit scalar-vs-XFER expansion.

Faults stop execution and release output enables. PULL/PUSH expose stalled status
but do not drop data. Firmware is responsible for reaching a declared safe holding
point before a blocking FIFO operation. Host, DMA, and engine queue transfers must
use accepted handshake events, not intent. On a collision the host owns its selected
RX read until its word is consumed; other DMA service uses round-robin arbitration.
Built-in fault codes are 1 invalid opcode/operand/ownership, 2 PC outside the
committed image, 3 bounded-wait timeout, and 4 strict RX enqueue overflow.
Strict overflow preserves the rejected RX word, PC and completed-instruction
count, halts only that engine, and releases its output enables. It performs no
enqueue. Space freed on the same edge does not change pre-edge admission. Fault4
remains until explicit clear/reset; draining queues does not clear it. Read held
RX before restarting, because START resets datapath registers. FAULT uses its
explicit nonzero code.

## Host interface

ui[3:0] write nibble; ui4 write-valid; ui5 read-ready; ui[7:6] window.
uo[3:0] read nibble; uo4 write-ready; uo5 read-valid; uo6 event/IRQ; uo7 fault.
Host shares clk and respects synchronous input timing. uio[7:0] are protocol pins.
Each word is eight little-endian nibbles. Transfers occur only at ready AND valid.
Window change abandons partial reads/writes without a word-level side effect.
Read data remains stable while not accepted. Window 0 reads selected status;
window 3 reads selected engine RX FIFO (pop only at last accepted nibble).
Window 1 writes program words with auto-increment; window 2 writes TX FIFO words.
Window 0 writes a command (op high8, payload low24):

| command | operation |
|---:|---|
|0|SELECT low2 engine|
|1|BEGIN selected engine: stop, invalidate image, program write address=0|
|2|COMMIT low16 length: accept only contiguous fully written image of that length, nonzero and <= capacity|
|3|OWN low8 pin ownership, next8 open-drain mask: halted only; reject ownership overlap and open-drain bits outside ownership|
|4|START low engine-count-bit mask: all selected engines must have committed image and no fault; synchronous start|
|5|STOP low engine-count-bit mask|
|6|ROUTE bits1:0 source engine,3:2 destination engine,bit4 enable, bits20:5 word count; one descriptor per source; zero count disables; invalid endpoints reject|
|7|CLEAR low engine-count-bit mask: clear engine faults while halted|
|8|READ_SELECT low8: 0 status,1 timestamp,2 FIFO levels,3 PC,4 event pending,5 completed instruction count,6 held RX word,7 ISA version (2)|
|9|EVENT low engine-count-bit destination mask|
|10|FLUSH selected engine queues while halted; disable routes touching it|
|11|TRIGGER selected engine while halted: bits2:0 observed pin,4:3 mode (0 rising,1 falling,2 high,3 low),5 enable; higher bits zero|

Reads window0 status: bit0 running,bit1 committed,bit2 stalled,bit3 fault;
bits15:8 fault code. FIFO levels: TX low16, RX high16. PC and instruction count
are ordinary unsigned words. Read word snapshots at first valid presentation.
IRQ is asserted while any event mailbox is pending or any RX FIFO is nonempty.

The host implementation registers read snapshots. A window change immediately
deasserts both ready and valid; its first edge discards partial transfers. On the
following edge an available read word is captured, and valid then asserts. The
earliest nibble acceptance is the next edge. The eighth accepted read nibble
clears valid; a new word can be captured on the next edge. Window3 reserves the
selected RX source from the interval after the window-change edge, including
capture and arbitrary read stalls. Changing windows abandons this reservation.
Unused read-nibble values while read-valid is low are unspecified.

Write-ready is asserted in window0; in window1 only while halted, after BEGIN,
before COMMIT, and below program capacity; in window2 only while TX has space;
and never in window3. These conditions apply to every nibble and are also gated
by reset, deselection and the window-change bubble. BEGIN must precede program
words. A full FIFO does not accept a push on the same edge that first frees
space, and an empty FIFO does not allow a pop on the same edge as its first push.

Invalid host commands reject atomically and set sticky host fault, cleared by
reset or CLEAR with payload bit23 set. START resets PC, registers, local timers,
repeat state and local logical outputs; queues remain available for prefill.
START does not change ownership, open-drain configuration or image validity.
DMA decrements descriptor count only after an accepted RX-to-TX word transfer.
Self-route is legal. A destination may have multiple sources; no transfer is
accepted if destination cannot accept. Host TX writes have priority over DMA to
the same destination. No live instruction memory writes are allowed.
Updating a route inhibits that source's DMA grant on the command edge; flushing
an engine inhibits all routes touching it on that edge. At most one DMA word is
accepted per edge. The round-robin cursor advances to the accepted source plus
one, modulo the configured engine count.

Each engine has one independent input trigger. Detectors compare the current
second synchronizer stage with a shared previous sample. For an input transition
stable before an edge, two synchronizer updates precede mailbox delivery on the
third edge. Trigger delivery operates during waits, transfers, queue stalls and
halt/fault; it does not require instruction execution or pin ownership. Reset
and deselection clear detector configuration and previous samples. STOP/HALT do
not disable configured detectors.

An accepted TRIGGER inhibits that engine's old detector on the configuration
edge; its new configuration applies on the following edge. Other detectors
continue. Enabling an edge detector at a static level creates no artificial edge.
Level detectors deliver every cycle at the active synchronized level, so delivery
wins simultaneous mailbox consumption. Pending bits coalesce repeated events;
they are not edge counters. Host EVENT, engine SIGNAL and pin-trigger deliveries
are ORed. Invalid or running-engine TRIGGER commands reject atomically with the
ordinary sticky host fault. READ_SELECT6 zero-extends a 16-bit held RX register.

## Configuration variants

The sections above are the contract of the design of record
(`configs/instruction-sram-32.json`). The area and reset variants of
`docs/area-study.md` (sections 4, 5 and 8) are refinement configs with an
optional `"options"` object (`configs/variants/<name>.json`). A config without
`"options"` is the design of record. Each option changes the contract only as
stated here. The Hardcaml RTL (`hardcaml/lib/variant_options.ml`), the
assembler and the reference model (`test/model/variant.py`) implement this
section.

| option | values (default first) | contract change |
|---|---|---|
| `reset` | `sync`, `sync_registered`, `async`, `async_sync_release` | reset timing, below |
| `fifo_storage_reset` | `false`, `true` | none: queue storage words take the chip reset; stored words are never observable while a queue is empty |
| `narrow_image_regs` | `false`, `true` | none: the image length and loaded-word registers are log2(program words)+1 bits wide; both are at most the program capacity |
| `debug_counters` | `true`, `false` | `false`: no completed-instruction counters; READ_SELECT 5 reads 0 |
| `pc_bits` | `full`, `saturating_7` | `saturating_7`: a 7-bit PC. A JMP, LOOP or JZ target, or a next PC, of 128 or more becomes 127. 127 is outside every image (at most 64 words in this refinement), so the next issue faults with code 2 exactly as the unsaturated PC would; READ_SELECT 3 then reads 127 instead of the target |
| `shift` | `barrel`, `byte_lane` | `byte_lane`: SHL/SHR accept only c = 0, 8, 16 or 24 (and c < datapath width); any other count faults with invalid-operand code 1. The assembler rejects other counts |

The architecture field `fifo_words` also admits 2 and 4. Every queue rule above
applies unchanged at that depth; a host that needs more than `fifo_words` words
queued must top a queue up while its engine runs
(`firmware/flagship-scenario-topup.json` is the flagship scenario written that
way).

**ISA version.** READ_SELECT 7 reads 3 when `debug_counters` is `false`,
`pc_bits` is `saturating_7` or `shift` is `byte_lane`, and 2 otherwise. An ISA-2
image runs unchanged on a version-3 device when all its shift counts are byte
lanes and all its targets are below 128; every image in `firmware/` qualifies
(all shifting images use c = 24; the largest target is 54).

**Reset styles.** Let *raw* mean that `rst_n` is low or `ena` is low. Like any
input it is sampled before a rising edge. *Reset* means the reset of the Machine
section: release all output enables, stop all engines, invalidate program
images, and clear queues, mailboxes and control state. While the chip is in
reset its pin outputs and the host ready/valid bits are masked, as in the design
of record.

- `sync` (design of record): an edge sampled with raw resets.
- `sync_registered`: raw passes through two flip-flops first. An edge resets
  when raw was sampled two edges earlier, so both assertion and release take
  effect two edges late; after `rst_n` rises the chip stays in reset for two
  more edges (write-ready stays low). The two flip-flops have no reset: after
  power-up, hold `rst_n` low for at least three edges.
- `async`: raw resets every register asynchronously, so the state and the
  public outputs are already reset before the first edge that samples raw. The
  first edge sampled without raw operates normally. When the inputs change
  between edges, the edge-by-edge behaviour equals `sync`. FLUSH stays a
  synchronous clear.
- `async_sync_release`: assertion as `async`; release through a two-flop
  synchronizer. The first two edges sampled without raw still reset; the chip
  operates normally from the third edge.

With `sync_registered` and `async_sync_release` a host waits two cycles (or for
write-ready in window 0) after reset or deselection before its first command.
