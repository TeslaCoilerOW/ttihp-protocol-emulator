# Timing certificates: a worked example

This page follows one eight-instruction program through the flow of
[timing-certificates.md](timing-certificates.md): the analyzer's schedule, the
certificate generated from it, the proof, a negative control, and how the
pieces combine into a claim about the chip. That document has the full
method and the results; this page shows one small case of it.

Every output below comes from the commands shown, run on a `git archive`
snapshot of commit `56f4b20` plus the example files in
[`tools/timing/cert/examples/`](../tools/timing/cert/examples/). Sections 0
to 4 and the two lemma and equivalence runs of section 5 ran in Slurm job
24425442 (MIT Engaging cluster, 8 CPUs, 2 min 14 s wall time; OSS CAD Suite
2026-07-29, Yosys 0.67+111). The formal netlist `processor_fv.v` was
generated from the same snapshot (job 24425074); its sha256 (`2a039bae…`) is
that of the file every certificate campaign used. The two waveform rows were
read from the solvers' VCD (value change dump) traces with a short script
that is not part of the repository. Outputs are selected lines (`…` marks
omissions), paths are shortened (the work directory to `WORK`), and log
prefixes are trimmed. [`run.sh`](../tools/timing/cert/examples/run.sh)
repeats sections 0 to 4 and the `ci_prove.sh` run of section 3. Its
present version ran again on a snapshot of the same commit in job 24427706
and printed the same lines apart from run times (`ci_prove.sh`: 47 s).

## 0. The program

Engine 0 owns pin 0 (push-pull). Every word the host pushes into its TX
queue starts a burst of three high pulses
([`toy-pulses.source.json`](../tools/timing/cert/examples/toy-pulses.source.json)):

| pc | instruction | cycles | effect |
|---:|---|---:|---|
| 0 | `DIR 1` | 1 | enable pin 0; its value is 0, so the pad is driven low |
| 1 | `next: PULL` | 1, or blocks | wait for a word from the host |
| 2 | `COUNT 2` | 1 | three iterations of the loop |
| 3 | `pulse: SET 1` | 1 | pin 0 high |
| 4 | `WAIT 2` | 3 | `WAIT n` costs n + 1 |
| 5 | `SET 0` | 1 | pin 0 low |
| 6 | `LOOP pulse` | 1 | |
| 7 | `JMP next` | 1 | |

The OCaml assembler (`hardcaml/bin/assemble.ml`) produces the image, and
`run.sh` checks that it reproduces the committed one:

```
$ $ASSEMBLE --source tools/timing/cert/examples/toy-pulses.source.json --output WORK/toy-pulses.image.json
Assembled toy-pulses: 8 words; source_sha256=f7ee2a4a…; bytecode_sha256=5e3cb073…
run.sh: assembler output equals …/tools/timing/cert/examples/toy-pulses.image.json
```

The image is not in `firmware/`, so it is not part of the certified set, the
ledger or the `certs` workflow.

## 1. What the analyzer predicts

`pe_timing` ([timing-analysis.md](timing-analysis.md)) executes the image
abstractly and cuts it at every synchronization boundary (START, `PULL`,
`PUSH a=0`, `WAITPIN`, `WAITEVENT`). Offsets are counted in clock edges from
the *anchor*: the START edge, or the edge on which the boundary instruction
completed.

```
$ python3 tools/timing/pe_timing.py analyze tools/timing/cert/examples/toy-pulses.image.json
== toy-pulses  (engine 0, owned 0x01, open-drain 0x00, exact)
…
boundary graph: 3 nodes, 3 path variants; WCET between boundaries 21 cycles
…
  [PASS] exact-analysis: 3 boundary contexts, 3 path variants, no widening or budget cut
…
schedules:
  from START v0:
    +1      pc0   pin0 Z->0
    +2      pc1   arrive PULL
  from pc1 PULL v0 (+1 identical contexts):
    +2      pc3   pin0 0->1
    +6      pc5   pin0 1->0
    +8      pc3   pin0 0->1
    +12     pc5   pin0 1->0
    +14     pc3   pin0 0->1
    +18     pc5   pin0 1->0
    +21     pc1   arrive PULL
```

The three nodes are START (n0) and the `PULL` reached in two abstract
states: after START, where `tx` is 0 (n1), and after a burst, where `tx`
holds the previous word (n2). The `PULL` overwrites `tx`, so both segments
have the same schedule ("+1 identical contexts").

Read the `PULL` segment as follows. `COUNT` issues on edge +1 and `SET 1` on
+2, so the pad is high right after edge +2. `WAIT 2` issues on +3 and takes
three cycles, so `SET 0` issues on +6. `LOOP` issues on +7 and jumps back,
and the next `SET 1` issues on +8. After the third iteration `LOOP` falls
through on +19 and `JMP` issues on +20, so the `PULL` can issue again, at the
earliest, on +21 ("arrive"). Each pulse is 4 cycles high with 2 cycles low
between pulses: 80 ns and 40 ns at the 50 MHz operating clock.

The analyzer computes this from the cycle rules of [isa.md](isa.md). It was
validated against the Python reference model, not proved. The certificate is
what ties the schedule to the RTL.

## 2. From the schedule to a certificate

```
$ python3 tools/timing/cert/gen_cert.py emit tools/timing/cert/examples/toy-pulses.image.json \
    --out WORK/certs --rtl formal/build/rtl --models models
gen_cert: 3 certificate(s) in WORK/certs
```

This writes one SystemVerilog harness and one `.sby` file (a job for
SymbiYosys, `sby`) per node: `cert_toy_pulses_n0`, `_n1` and `_n2` (n1 and
n2 differ only in the module name). Before writing, the generator checks
that each segment's end state, advanced by the `PULL` completion, is exactly
the next segment's start state (`check_chain`); `emit` stops if it is not.

Here is `cert_toy_pulses_n1.sv`, in selected lines (`…` marks omissions).

**The chip's inputs and the image.**

```systemverilog
// pc1 PULL: 1 variant(s), BMC depth 22
module cert_toy_pulses_n1 (input wire clk);
    localparam K = 0;
    localparam [15:0] IMG_N = 16'd8;
    localparam [7:0] IMG_OWN = 8'h01, IMG_OD = 8'h00;
    wire rst_n = 1'b1, ena = 1'b1;
    wire [7:0] ui_in = 8'h00;
    (* anyseq *) reg [7:0] uio_in;
    function automatic [31:0] img(input [5:0] a);
        case (a)
            6'd0: img = 32'h03000001;
            …
            6'd7: img = 32'h05000001;
            …
```

There is no reset. `ui_in` is 0, so the host port accepts no command. The
pad inputs `uio_in` are free on every cycle. `img` holds the eight image
words.

**The design and the precondition.**

```systemverilog
`include "cert_dut.vh"
    wire k_pre = k_pc == 24'd2
        && k_running
        && k_fault == 8'd0
        && k_timer == 24'd0
        && k_xrem == 7'd0
        && k_blocked == 24'd0
        && k_rx == 32'd0
        …
        && k_repeat == 16'd0
        && k_limit == 24'd65535
        && k_xpins == 9'd0
        && (k_values & 8'hff) == 8'h00
        && (k_enables & 8'hff) == 8'h01;
`include "cert_env.vh"
```

[`cert_dut.vh`](../tools/timing/cert/cert_dut.vh) instantiates
`protocol_processor_fv`, the netlist that `formal/` proves, with the IHP
FUNCTIONAL SRAM models, and names engine K's registers `k_*`. `k_pre` is the
state right after the anchor edge (step 0): the `PULL` at pc 1 has just
completed, so the PC is 2, and every register the analyzer knows has its
value. `k_tx` does not appear: the pulled word is free, so the proof holds
for every word.

[`cert_env.vh`](../tools/timing/cert/cert_env.vh) assumes the rest of the
certificate's environment, run B. The image is committed with length 8 and
ownership 0x01. The SRAM output latch holds the word at the PC, and every
later read returns the image word. The other three engines are halted, every
FIFO is empty, and there are no routes, events or pin triggers. It also
asserts that B never controls engine 0 or writes its SRAM (`env_quiet`,
`env_no_sram_write`); these are proved, not assumed.

**The prediction, as tables indexed by step.** `off` counts steps from the
anchor.

```systemverilog
    wire [24:0] obs = {k_slot, k_slot ? k_pc : 24'd0};
    function automatic [24:0] att0(input [15:0] c);
        case (c)
            16'd0: att0 = {1'b1, 24'd2};
            16'd1: att0 = {1'b1, 24'd3};
            16'd2: att0 = {1'b1, 24'd4};
            16'd5: att0 = {1'b1, 24'd5};
            16'd6: att0 = {1'b1, 24'd6};
            …
            16'd19: att0 = {1'b1, 24'd7};
            16'd20: att0 = {1'b1, 24'd1};
            default: att0 = 25'd0;
        endcase
    endfunction
    function automatic [2:0] pad0_0(input [15:0] c);
        pad0_0 = 3'd1;
        if (c >= 16'd2) pad0_0 = 3'd2;
        if (c >= 16'd6) pad0_0 = 3'd1;
        …
        if (c >= 16'd18) pad0_0 = 3'd1;
    endfunction
    function automatic chg0_0(input [15:0] c);
        case (c)
            16'd2: chg0_0 = 1'b1;
            16'd6: chg0_0 = 1'b1;
            …
            default: chg0_0 = 1'b0;
            …
```

- `att0` lists the steps in which engine 0 is in an *issue slot* (running,
  not faulted, no WAIT or XFER count running) and the PC it then holds. An
  instruction the analyzer issues on edge +t is attempted in step t - 1:
  `COUNT` (+1) in step 0, `SET 1` (+2) in step 1, and the `PULL` (+21) in
  step 20. Steps 3 and 4 are missing: the WAIT timer runs there, and `obs`
  must be 0. The table therefore also fixes WAIT's duration.
- `pad0_0` is the set of states pin 0 may be in at each step, in the
  analyzer's encoding: 1 driven low, 2 driven high, 4 released. A pad changed
  by edge +t shows its new state in step t. A data-dependent level would be a
  union, such as 3 (low or high); this program has none.
- `chg0_0` lists the steps on which pin 0 may change.

**The claims.**

```systemverilog
    assign m[0] = alive[0] && off <= 16'd20 && obs == att0(off);
    …
    always @(posedge clk) if (!done) begin
        flow: assert(m != 0);
        if (alive[0] && off <= 16'd20) begin
            pad_v0_p0: assert((k_pads[0+:3] & pad0_0(off)) != 3'd0);
            if (past_valid && k_pads[0+:3] != prev_pads[0+:3])
                edge_v0_p0: assert(chg0_0(off));
        end
        if (m[0] && off == 16'd20) post_v0: assert(k_pc == 24'd1
        && k_running
        …
        && (k_enables & 8'hff) == 8'h01);
    end
`ifdef CERT_COVER
    …
        cover_v0: cover(m[0] && off == 16'd20);
    …
```

- `flow`: on every step up to 20, the issue slots and PCs are exactly the
  table's. `m` has one bit per path variant; this segment has one variant.
- `pad`: pin 0 is in the predicted set on every step.
- `edge`: pin 0 changes only on a listed step. With `pad`, a change between
  two known levels happens exactly on the predicted edge.
- `post`: on step 20 (the arrival), engine 0's state is the successor's
  precondition before the `PULL` completes: PC 1, repeat counter 0, LIMIT
  65535, pin 0 enabled with value 0.
- `cover` (run separately) shows that the end of the segment is reachable
  under the assumptions, so they are not contradictory.

The `.sby` file runs `bmc` (bounded model checking, BMC: every execution of
at most the given number of steps) to depth 22 on a bitwuzla/boolector/yices
portfolio, and `cover` on yices. The depth is the last step (20) plus 2: Yosys
samples clocked assertions at the edge, so sby's step k checks the values of
step k - 1, and depth D checks steps 0 to D - 2.

## 3. Running the proof

```
$ tools/timing/cert/run_one.sh WORK/certs cert_toy_pulses_n1 bmc
cert_toy_pulses_n1.bmc.portfolio: PASS (rc=0) in 5s
$ tools/timing/cert/run_one.sh WORK/certs cert_toy_pulses_n1 cover
cert_toy_pulses_n1.cover.portfolio: PASS (rc=0) in 19s
```

From the two sby logs (`bmc`, then `cover`):

```
…
engine_1: ##   0:00:02  Checking assertions in step 21..
…
engine_1: ##   0:00:02  Status: passed
…
summary: engine_1 (smtbmc boolector) returned pass
…
DONE (PASS, rc=0)

…
engine_0: ##   0:00:12  Reached cover statement in step 21 at cert_toy_pulses_n1: cover_v0
…
DONE (PASS, rc=0)
```

The cover's witness is one execution of the RTL. Its issue slots and pin 0,
read from the witness's VCD, match the tables above (`.` is a step outside an
issue slot):

```
step      0  1  2  3  4  5  6  7  8  9 10 11 12 13 14 15 16 17 18 19 20
slot PC   2  3  4  .  .  5  6  3  4  .  .  5  6  3  4  .  .  5  6  7  1
pin0      0  0  1  1  1  1  0  0  1  1  1  1  0  0  1  1  1  1  0  0  0
```

The proof covers every execution of B, for every pulled word and every pad
input, not only this one.

`ci_prove.sh` runs everything the `certs` workflow runs for an image: every
certificate, one cover per certificate, and the negative controls of the next
section.

```
$ tools/timing/cert/ci_prove.sh WORK/ci formal/build/rtl models tools/timing/cert/examples/toy-pulses.image.json
…
ci_prove: 14 runs, 0 deep negative control(s) skipped
…
ci_prove: runs finished in 45 s with 2 proof and 4 cover runs in parallel
ci_prove: 3/3 segments proved, 3/3 covers, 8/8 negative controls failed as required (0 deep ones skipped)
```

This program has no input samples, so it exercises `flow`, `pad`, `edge` and
`post` but not `sample`. The receivers (`uart-rx`, the I2C and SPI targets)
exercise that one.

## 4. A negative control: one edge late

A certificate that also passes for a wrong schedule would show nothing. The
generator can perturb one prediction:

```
$ python3 tools/timing/cert/gen_cert.py emit tools/timing/cert/examples/toy-pulses.image.json \
    --out WORK/neg --rtl formal/build/rtl --models models --schedule-mutant pad-late --pick-one
gen_cert: 1 certificate(s) in WORK/neg
```

It moves the first rise of pin 0 from step 2 to step 3. Apart from its name
and header comment, the harness differs from the real one only in these
lines, and its `.sby` file says `expect fail`:

```diff
-        if (c >= 16'd2) pad0_0 = 3'd2;
+        if (c >= 16'd3) pad0_0 = 3'd2;
-            16'd2: chg0_0 = 1'b1;
+            16'd3: chg0_0 = 1'b1;
```

```
$ tools/timing/cert/run_one.sh WORK/neg cert_toy_pulses_n1_neg_pad_late bmc
cert_toy_pulses_n1_neg_pad_late.bmc.portfolio: FAIL (rc=0) in 3s edge_v0_p0 pad_v0_p0
…
summary: engine_1 (smtbmc boolector) returned FAIL
…
summary:   failed assertion cert_toy_pulses_n1_neg_pad_late.edge_v0_p0 at cert_toy_pulses_n1_neg_pad_late.sv:101.17-101.48 step 3
summary:   failed assertion cert_toy_pulses_n1_neg_pad_late.pad_v0_p0 at cert_toy_pulses_n1_neg_pad_late.sv:99.13-99.68 step 3
…
DONE (FAIL, rc=0)
```

The counterexample, read from its VCD:

```
step      0  1  2  3
slot PC   2  3  4  .
pin0      0  0  1  1
```

sby's "step 3" is harness step 2, one sampling edge later. In step 2 the RTL
already drives pin 0 high. The perturbed prediction still says "driven low"
(`pad` fails) and lists no change there (`edge` fails). The real certificate,
which predicts the rise in step 2, passes.

`ci_prove.sh` ran all eight controls this image admits, and each failed:

| negative control | node | depth | outcome | failed assertions |
|---|---:|---:|---|---|
| count-n-iterations | 1 | 16 | FAIL (expected) | flow |
| issue-late | 1 | 23 | FAIL (expected) | flow |
| pad-early | 1 | 22 | FAIL (expected) | pad_v0_p0 |
| pad-late | 1 | 22 | FAIL (expected) | edge_v0_p0, pad_v0_p0 |
| pad-level | 0 | 3 | FAIL (expected) | pad_v0_p0 |
| post-limit | 0 | 3 | FAIL (expected) | post_v0 |
| set-two-cycles | 1 | 28 | FAIL (expected) | flow |
| wait-n-cycles | 1 | 19 | FAIL (expected) | flow |

Three rows come from a deliberately wrong copy of the analyzer:
`count-n-iterations` gives `COUNT n` n iterations, `set-two-cycles` makes
`SET` cost 2 cycles, and `wait-n-cycles` makes `WAIT n` cost n. The other
five perturb one prediction of the correct schedule: a pad change one edge
late or early, a flipped level, every issue after the first multi-cycle
instruction one edge late, or a successor LIMIT off by one. The wrong
analyzers and `issue-late` predict segments of other lengths, hence the
other depths. The other mutations of
[timing-certificates.md](timing-certificates.md) section 5 (`XFER`, loopback
latency, open-drain, `HALT`, soft branches) do not apply to this program.

## 5. From segments to the chip

For this image, the end-to-end claim of
[timing-certificates.md](timing-certificates.md) section 3 reads as follows.

**Premises.** (H1) At the START edge s, engine 0's image is committed with
length 8, its SRAM holds the eight words at addresses 0 to 7, and its
ownership and open-drain masks are 0x01 and 0x00. (H2) From s on, there is
no reset and no host command that starts, stops, clears, reloads or re-owns
engine 0. Nothing else is constrained: the host may push any words at any
time, the other engines may run any program, and the pad inputs are free.

**Claim.**

- Pin 0 is released after the START edge s and driven low from edge s + 1
  on (node n0). From edge s + 2 on, engine 0 attempts the `PULL` on every
  edge until it completes, and pin 0 does not change while it waits.
- If the `PULL` completes on edge a, pin 0 goes high on edges a + 2, a + 8
  and a + 14 and low on a + 6, a + 12 and a + 18, and it changes on no other
  edge until the next `PULL` completes. That `PULL` is attempted on every
  edge from a + 21 on. When it completes, engine 0's state satisfies the
  precondition of n2, whose schedule is the same, so this holds for every
  later word.

**How the pieces combine.** Each row is a separate result. Rows 1 to 4 give
the claim on the formal netlist `protocol_processor_fv`. Row 5 carries it to
the committed `src/` RTL and row 6 to the official gate-level netlist; row 7
is that netlist's timing sign-off.

| # | step | evidence | run for this page |
|---:|---|---|---|
| 1 | The pad schedule of each segment, in the quiet run B | the certificates of n0, n1, n2 and their covers | job 24425442: 3/3 segments proved, 3/3 covers, 8/8 negative controls failed as required |
| 2 | The segments hand over exactly: end state plus `PULL` completion equals the next precondition | `check_chain` in `gen_cert.py` | every `emit` above |
| 3 | START; the `PULL` completes exactly when a TX word is present, changes nothing and keeps the pads while waiting, and advances the PC when it completes; fetch | boundary lemmas (`lemma_start`, `lemma_pull_*`, `lemma_fetch`), proved for an arbitrary state in *any* environment, under the assumptions of [timing-certificates.md](timing-certificates.md) section 2, each a premise or an invariant proved elsewhere | `ci_lemmas.sh`, job 24425442: 12/12 lemma tasks met their expectation |
| 4 | From B to any host traffic and any other engine, until engine 0's next FIFO or event interaction (here: the `PULL`) | the timing-isolation theorem, [`formal/timing_isolation.sv`](../formal/timing_isolation.sv), proved per engine by k-induction (induction whose step assumes the property on k consecutive steps; unbounded) | GitHub `formal` run [36701333147](https://github.com/TeslaCoilerOW/ttihp-protocol-emulator/actions/runs/36701333147) on `56f4b20`: `timing_isolation_prove_k0` to `_k3` pass; its `processor_fv.v` has the same sha256 (`2a039bae…`) |
| 5 | `protocol_processor_fv` equals the committed `src/` top on the chip ports | `equiv_src.py`, ABC `dprove` | job 24425442: "Networks are equivalent", `EQUIVALENT` |
| 6 | The `src/` RTL equals the official gate-level netlist | the `equivalence` workflow ([equivalence.md](equivalence.md)) | run [36697580346](https://github.com/TeslaCoilerOW/ttihp-protocol-emulator/actions/runs/36697580346) checked `gds` run [36657674287](https://github.com/TeslaCoilerOW/ttihp-protocol-emulator/actions/runs/36657674287) (commit `3364ad9`, whose `src/` and `info.yaml` equal `56f4b20`'s): `equivalent` |
| 7 | The netlist meets its clock period | static timing analysis (STA) in the official `gds` flow at 15 ns | the same `gds` run's `metrics.csv`: 0 setup and 0 hold violations at the three corners; worst setup slack 2.24 ns (slow corner), worst hold slack 0.165 ns (fast corner) |

The induction of section 3 uses rows 1 to 4. `lemma_start`, `lemma_fetch`
and (H1) give n0's precondition. Then, segment by segment: build the quiet
run B from the real run A at the anchor, where the certificate holds.
Timing isolation makes A agree with B on engine 0's registers and pins up
to the arrival at the `PULL`. The `PULL` lemmas take A through its wait and
its completion to the next precondition.

**What this is not.** The composition is argued over separate proofs; it is
not one machine-checked proof, as
[timing-certificates.md](timing-certificates.md) section 3 and
[verification.md](verification.md) section 5 state. In addition:

- (H1) is a premise. That a host load establishes it is a separate argument
  over further proofs ([timing-certificates.md](timing-certificates.md)
  section 3, "The premises (H1)"). There, the bounded end-to-end check of
  the loaded words (`program_load.sv`, `spec_word_*`, BMC to depth 48;
  [formal-depth.md](formal-depth.md)) is replaced by an unbounded lemma
  about the SRAM macro model.
- For images with data pins, the certificates fix the edges and the level
  set, not the data bit: such a pin is certified as "0 or 1" ("0 or
  released" for open-drain), not as the bit it carries
  ([timing-certificates.md](timing-certificates.md) section 4). This image
  has no data pins.
- Rows 5 and 6 are sequential equivalence with a stated scope: from the
  all-zero state (row 5) or from reset with every flip-flop at 0 (row 6),
  with the vendor's FUNCTIONAL SRAM model (row 5) or the SRAM macros as cut
  points (row 6); ABC is trusted ([equivalence.md](equivalence.md) sections
  2 and 6).
- Row 7 is tool sign-off, not proof. Its input and output delays are
  constraints (20% of the period), not derived from the Tiny Tapeout
  multiplexer or the board. The official flow times the design at 15 ns;
  the chip is operated at 50 MHz. The netlist of that `gds` run has the same
  sha256 (`09841e31…`) as the p018 layout (same netlist as `d76f1cc`, R85)
  that [results.md](results.md) R86 re-analyses at 20 ns.
- The claims are about clock edges. Clock-to-pad delay, board delays, the
  phase of an asynchronous input within a cycle and metastability are
  outside every row ([timing-certificates.md](timing-certificates.md)
  section 4).

## 6. The full campaign

The same flow, run over every committed image, is the campaign of
[timing-certificates.md](timing-certificates.md) section 6. The ledger
[`summary.json`](../tools/timing/cert/results/summary.json) (tables in
[`summary.md`](../tools/timing/cert/results/summary.md)) binds each image's
sha256 to the campaign that certified it. On the same snapshot of `56f4b20`:

```
$ python3 tools/timing/cert/ledger.py check
Timing certificates: 26/26 firmware images certified for their current sha256 on the current RTL.
```

Summed over the ledger's 26 image records: 441 of 441 segments proved, 441
covers reached, and 185 of 221 negative controls failed as required; the
other 36 are deeper than 2,000 steps and were not run. The 19 images of
`24f31f0` were certified in that campaign ([results.md](results.md) R42b),
the 7 added in `e64cd6b` in the campaign on `6a3ea08` (R42c). The longest
segment, in `onewire-master`, has 80,004 steps; like every segment longer
than 96 steps it is proved as a chain of chunk certificates of at most 96
steps each ([timing-certificates.md](timing-certificates.md) section 1,
"Long segments are split").

## Reproducing

```sh
formal/run.sh --generate-only                      # formal/build/rtl/processor_fv.v
(cd hardcaml && dune build ./bin/assemble.exe)     # optional: re-assemble the image
ASSEMBLE=hardcaml/_build/default/bin/assemble.exe \
  tools/timing/cert/examples/run.sh $WORK formal/build/rtl        # sections 0 to 4, ci_prove.sh
tools/timing/cert/ci_lemmas.sh $WORK/lemmas formal/build/rtl models   # row 3
python3 tools/timing/cert/equiv_src.py --src src --rtl formal/build/rtl \
  --models models --out $WORK/equiv --run                          # row 5
```

The first two commands need the OCaml/Hardcaml toolchain of
`scripts/opam-deps.txt` (`dune`; [formal/README.md](../formal/README.md)).
Without it, use the `formal-rtl` artifact of the GitHub `formal` workflow
for the same commit (run 36701333147 for `56f4b20`) as
`formal/build/rtl`, and run `run.sh` without `ASSEMBLE`, which skips the
reassembly. The rest needs Python 3.10 or later and OSS CAD Suite on
`PATH`. `run.sh` exits 1 if the reassembled image differs from the
committed one, and otherwise with the status of `ci_prove.sh`. On one
cluster node with 8 CPUs, the last three commands took 2 min 14 s together
(job 24425442).
