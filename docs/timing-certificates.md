# Timing certificates: the static schedule proved on the RTL

A worked example, one small program end to end: [timing-certificates-tutorial.md](timing-certificates-tutorial.md).

[`timing-analysis.md`](timing-analysis.md) describes `pe_timing`, a static
analyzer that predicts, for every firmware image, the clock edge on which each
owned pad of the engine changes, as an offset from the last synchronization
boundary. Its predictions were validated against the executable ISA model
(the Python reference model), and the RTL was tied to that model by lockstep
simulation. Its section "What is not guaranteed" lists the missing link:
"The link between the ISA-level schedule and the RTL is shown by lockstep
simulation, not by proof."

This page describes the tools in [`tools/timing/cert/`](../tools/timing/cert/README.md)
that close that link with proofs. For every node of an image's boundary graph,
a generator emits a SymbiYosys certificate. The certificate proves, on the
design of record's RTL, that the pads change exactly on the edges the analyzer
predicts for that segment, for all data values and all pad inputs. Image-independent
one-step lemmas connect the segments. The existing timing-isolation proof in
[`formal/`](../formal/README.md) extends the result from the certificate's
quiet environment to every behaviour of the host and of the other engines.
An equivalence check ties the formal netlist to the committed `src/` files.

**Results (commit `24f31f0`, all runs on the MIT Engaging cluster; section 6).**

- All 350 segments of the 19 firmware images committed at `24f31f0` are
  certified: 350 of 350 whole-segment certificates pass. This covers 363 path
  variants, 1,686 issue slots, 733 pad events (506 of them between known
  levels) and 224 input samples. The three I2C controller images changed by
  `fb79f31` are included.
- The 22 segments longer than 96 steps were also proved as chains of 127
  chunks. All chunks pass.
- A cover per variant (or per chunk) is reached for every segment.
- The boundary lemmas pass for all four engines, and the SRAM macro lemma is
  proved by IC3/PDR.
- All 132 negative controls generated from wrong analyzers or perturbed
  predictions fail as required. The two deepest (`waveform`, steps 1,444 and
  1,541) were found by ABC `bmc3`, and their counterexamples were replayed
  with Yosys `sim`. The 52 chunk-level controls added afterwards (perturbed
  chunk certificates of the long segments) also all fail.
- The certificates catch three of five RTL timing bugs. Of the other two, one
  does not change any pad, and one changes only data-dependent pad levels,
  which the certificates do not pin down (section 5).
- The formal netlist and the committed `src/` top are sequentially equivalent
  on the chip ports from the all-zero state (ABC `dprove`). A netlist with one
  extra input flip-flop is not equivalent; the check finds a counterexample in
  9 cycles. The formal netlist is byte-identical to the one the GitHub
  `formal` workflow built and proved timing isolation on for the same commit.
- The certificates are bound to what they certify: the ledger
  [`tools/timing/cert/results/summary.json`](../tools/timing/cert/results/summary.json)
  records the sha256 of every certified image, the commit and the hashes of
  the RTL and harnesses, and the CI workflow `certs` fails when a committed
  image lacks a certificate for its current sha256 (section 8).

## 1. What one certificate states

The analyzer builds, per image, a *boundary graph*. A node is a
synchronization boundary (START, `PULL`, `PUSH a=0`, `WAITPIN`, `WAITEVENT`)
together with a *context*, the abstract engine state on arrival. A node has
one or more *path variants*, each with an exact schedule up to the next
boundary. For each node the generator (`gen_cert.py emit`) writes one harness
around `protocol_processor_fv`. That is the netlist `formal/` proves:
`Processor.create_refinement ~debug:true` plus output ports that observe
existing registers, with the exact IHP `RM_IHPSG13_1P_64x16_c2` FUNCTIONAL
SRAM models. The configuration is the design of record (4 engines, 32-bit
datapath, 64-word images, 8-word FIFOs, fused issue).

**Step 0 is the anchor.** It is the state right after the edge on which the
segment starts: the START edge, or the edge on which the previous boundary
completed. Step `n` is the analyzer's offset `+n`.

**Precondition (assumed at step 0).** Engine K's execution registers equal
the node's abstract start state:

- the PC is the first instruction of the segment;
- the engine is running and not faulted;
- the WAIT timer, the XFER edge counter and the blocked-cycle counter are
  zero;
- every register the analyzer knows (`tx`, `rx`, `x`, `y`, repeat counter,
  LIMIT, `PINS` selection, and each known bit of the logical output value
  and output enable) has its known value.

Everything the analyzer does not know is free: for example `tx` after a
`PULL`, `rx` after a sample, the completed-instruction counter and the XFER
tick, period and mode registers.

**Program image (assumed).** Engine K's image is committed with the image's
length, and its ownership and open-drain masks are the image's. The SRAM
output latch at step 0 holds the image word at the PC. Every later read of an
address below the image length returns that image word on the next cycle,
which is the macro's registered read.

**Environment of the certificate run.** Call this run B.

- The pad inputs `uio_in` are free on every cycle.
- The rest of the chip is quiet. `ui_in` is held at 0, so the host port
  accepts no command. There is no reset. The other three engines are halted.
  Every FIFO is empty. There are no routes, no pending events and no pin
  triggers.
- Everything not listed is free at step 0: the timestamp, the pin
  synchronizers, the host-port registers, the FIFO storage, the other
  engines' registers and SRAM contents.

The harness proves (it does not assume) that B accepts no command and never
writes engine K's SRAM (`env_quiet`, `env_no_sram_write`).

**Claims (asserted on every step up to the end of the segment).** A variant
is *alive* while every issue slot so far matched it.

| label | claim |
|---|---|
| `flow` | Some variant is alive. On every step, engine K is in an issue slot (running, not faulted, WAIT timer and XFER edge counter zero) exactly when that variant issues an instruction on the next edge, and then its PC is that instruction's. This also fixes WAIT and XFER durations and the arrival edge. |
| `pad_v<i>_p<pin>` | Every owned pad is in the variant's predicted set: driven 0, driven 1, released, or a union where the value depends on data. |
| `edge_v<i>_p<pin>` | An owned pad changes only on a step where the variant has a pad event. Together with the sets, a change between two disjoint known states happens exactly on the predicted edge. |
| `sample_v<i>_<n>` | At every predicted input sample (`IN`, or an `XFER` sampling transition) `rx` shifts in the sampled pin in the instruction's direction. From step 3 on, the bit is compared with the raw pad input `uio_in` three cycles earlier. This certifies the sampling instant including the two synchronizer flops. |
| `post_v<i>` | On the arrival step (the step in which the next boundary instruction is first attempted), engine K's state equals the successor node's abstract state: known registers, LIMIT, `PINS`, known pin bits, timer, edge counter and blocked count zero. For a variant that ends in `HALT` or a fault, the engine has stopped with that code and its enables are zero. |
| `env_push_v<i>_<n>` | A strict `PUSH` inside the segment succeeds in B. The composition in section 3 relies on this. |

The certificates form a chain. The generator checks, for every variant that
ends at a boundary (`check_chain` in `gen_cert.py`), that its `post` state,
advanced as the boundary lemmas of section 2 say a completion advances it,
is exactly the precondition of the successor node's certificate:

- the PC moves to the next instruction;
- the blocked count is zero;
- every other register is unchanged, except `tx` after a `PULL`, which
  becomes free.

**Method.** The BMC depth is the segment length plus two. Yosys 0.67 samples
clocked checks at the clock edge, so the check in sby step `k` sees the
values of step `k-1`, and a depth of `D` checks steps `0..D-2`. The depth
therefore covers every step up to the end of the segment, and each
certificate is a complete proof for its segment. The negative controls
(section 5) include predictions moved by one edge in either direction, and
they fail, which confirms the alignment. There are no loops inside a
segment: the generator refuses boundary-free periodic loops, and cuts a loop
without a blocking instruction at a soft boundary (below); none of the 19
images of section 6 has either. Loops through boundaries (the byte loop of
every image, for example) are closed by the graph: each node's precondition
is an inductive invariant at that boundary, and each certificate proves the
step from one node to its successor.

**Long segments are split.** A segment longer than 96 steps (UART frames,
SPI bytes, JTAG shifts, the waveform) is also certified as a chain of
*chunks* of at most 96 steps (`gen_cert.py emit --chunk 96`).

- Chunk 0 has the node's precondition.
- Every later chunk starts from engine K's complete abstract register state
  at its first step. This includes the WAIT timer and the XFER edge counter,
  tick, period and mode.
- Each chunk asserts, at its last step, the state the next chunk assumes.
- Variants that reach the same state at a cut share a chunk. Variants that
  differ there get separate chunks.

The intermediate states come from `rtl_trace` in `gen_cert.py`. It simulates
the engine RTL (`hardcaml/lib/engine.ml`; after `16cfc20` also
`engine_transfer.ml`, `engine_datapath.ml` and `engine_decode.ml`) cycle by
cycle over the analyzer's abstract values.
It must reproduce the analyzer's issue slots and pad sets exactly, or the
generator stops. For the 363 variants of the 19 images committed at
`24f31f0` (18,790 steps) it does. This is a self-check of the generator only: it reuses the
analyzer's ALU and pad helpers, so it is not an independent model. The
chunk's assertions are what the proof checks, and a wrong intermediate state
makes a chunk fail, not pass.

A split segment is proved when all its chunks are. For the images of
section 6, the whole-segment certificates of all long segments were run as
well, and they pass too. Segments longer than 2,000 steps are proved by their
chunks only (`CERT_WHOLE_MAX` in `campaign.sh`).

**Covers.** One cover per variant shows that the end of the variant is
reachable in B. This shows that the assumptions admit every branch, including
data-dependent `FAULT` exits such as an I2C NACK.

**Soft branch nodes.** A loop without a blocking instruction, such as a poll
loop (`IN`, then `JZ` back to the `IN`), has no boundary at which to cut it.
The analyzer anchors a *soft boundary* at its data-dependent `JZ` or `LOOP`
instead: a node without stall whose two start states are the branch taken and
not taken. Its certificate starts one step earlier than a boundary node's, in
the arrival state:

- step 0 is the state in which the branch is attempted (the PC is the branch,
  engine K is in an issue slot), and the branch issues on the next edge;
- every later step number is one larger than the analyzer's offset;
- the certificate proves the branch step as well, and its precondition is
  exactly the predecessor's `post` state (`check_chain` requires equality;
  there is no completion step in between).

Generator 1.1 supports these nodes. For images without them it emits the same
files as generator 1.0, byte for byte. None of the 19 images of section 6 has
one; section 6 reports a trial on an image that has two.

## 2. Boundary lemmas

A certificate stops on the arrival step. What happens between arrival and the
next anchor depends on the environment: how long a `PULL` waits for a word,
when a pin reaches its level, whether an RX FIFO is full. `boundary_lemmas.sv`
covers this part for any engine K, in *any* environment:

- the host port and the pad inputs are free;
- every register outside engine K is arbitrary;
- the only assumptions are the ones listed next.

The assumptions are: engine K is not stopped, cleared or reconfigured; there is
no reset; ownership is disjoint and open-drain masks lie inside ownership; a
running engine has a committed image of 1 to 64 words that is not being
loaded; FIFO levels are at most 8. Each of these is either the premise of the
claim or an invariant proved elsewhere (`formal/processor_inductive.sv`,
`formal_depth/harness/program_load.sv`).

Each lemma relates an arbitrary step-0 state to its successor. BMC from an
unconstrained initial state therefore proves it for every state, and it can be
applied on every cycle of an unbounded stall.

| lemma | statement |
|---|---|
| `lemma_start` | An accepted START gives exactly the analyzer's START state (PC 0, registers 0, LIMIT 65535, enables 0) with all owned pads released. |
| `lemma_{pull,push0,push1,waitpin,waitevent}_when` | The instruction completes exactly on its condition: a TX word is present, the RX FIFO has space, the synchronized pin has the level, or the engine's own mailbox is set. |
| `lemma_*_done` | A completing boundary instruction advances the PC and clears the blocked count. The completed count goes up by one. Nothing else in engine K changes, except `tx` for `PULL`. |
| `lemma_*_hold` | An instruction that does not complete changes nothing in engine K, except the blocked count of `WAITPIN`/`WAITEVENT`, which goes up by one. |
| `lemma_*_timeout` | `WAITPIN`/`WAITEVENT` fault with code 3 on the LIMIT-th unsuccessful attempt. A strict `PUSH` faults with code 4 on a full RX FIFO. |
| `lemma_*_pads`, `lemma_*_release` | The owned pads do not change in a done or hold step, and all are released in a fault step. |
| `lemma_fetch` | While engine K runs, its SRAM latch holds the word at its PC. It was read on the previous edge at the new PC. |
| `lemma_stopped` | A halted or faulted engine K stays stopped with its owned pads released until the host starts it. |
| `lemma_sync` | The engines see each pad input exactly two edges after it was present. |
| `sram_read_last_write` (`sram_lemma.sv`) | The SRAM macro model returns the last word written to the address read (used for (H1), section 3). |

## 3. Composition: the end-to-end claim

**Premises.** Take any execution of the chip and a START edge `s` of engine K,
such that:

- (H1) at `s`, engine K's image is committed with length `n` and its SRAM holds
  the image at addresses `0..n-1`. Ownership and open-drain masks are the
  image's.
- (H2) from `s` on, there is no reset, and the host issues no command that
  starts, stops, clears, reloads or re-owns engine K.

Nothing else is constrained. The host may push and pop any FIFO, including
engine K's, at any time. Other engines may run any program. The mover may move
words. Events and pin triggers may arrive. The pad inputs may do anything.

**Claim.** Engine K's execution splits into segments. The anchors `a_0 = s <
a_1 < ...` are the edges on which a boundary completed. For each segment there
is a node `N_i` of the boundary graph. `N_0` is START and `N_{i+1}` is the
successor of the variant taken in segment `i`. Then:

1. On every edge from `a_i` up to the arrival of segment `i`, the claims of
   `N_i`'s certificate hold. Issue edges, owned-pad changes, pad levels and
   input samples follow the variant exactly.
2. While engine K waits at the boundary, it re-attempts the instruction on
   every edge and its owned pads do not change. `WAITPIN`/`WAITEVENT` fault
   with code 3 on the LIMIT-th attempt, which releases the pads.
3. The next anchor `a_{i+1}` is the completion edge, and engine K's state there
   satisfies the precondition of `N_{i+1}`.
4. A strict `PUSH` inside a segment either completes, and the segment goes on
   as certified, or faults with code 4 on that edge and releases the pads.
   This is the conditional fault the analyzer reports.
5. A variant that ends in `HALT` or a fault releases the pads on the predicted
   edge, and they stay released.

**Proof.** The proof is by induction over segments.

*Base.* `lemma_start` gives the START state after edge `s`. `lemma_fetch` and
(H1) give the latch contents. Together these are the START certificate's
precondition.

*Step.* Suppose engine K's state after `a_i` (in the real run, A) satisfies the
precondition of `N_i`. Build a run B:

- B copies from A at `a_i` engine K's 19 execution registers, its
  configuration, its SRAM contents and output latch, the timestamp and the pin
  synchronizers.
- Everything else in B is the quiet choice of section 1.
- B's pad inputs are A's; its host port is idle.

B satisfies every assumption of `N_i`'s certificate, so the certificate's claims
hold along B.

Next, apply the timing-isolation theorem of `formal/timing_isolation.sv` to
the pair (A, B). Its premises hold:

- the engine-K slices, configuration, latch, timestamp and synchronizers are
  equal;
- both runs satisfy ownership disjointness;
- engine K is not being loaded;
- neither run issues a command that controls engine K;
- both SRAMs hold the same contents and are never written.

The theorem, proved for K = 0..3 with k-induction, gives: until engine K
completes a FIFO or event interaction in A or in B, its execution registers
are equal in A and B (the miter's auxiliary invariant) and so are its owned
pads.

In B, the only interactions before the arrival step are strict `PUSH`es, and
each succeeds (`env_push`). At a strict `PUSH` step, both runs are in the same
state. By `lemma_push1`, which holds in A's environment, A either faults with
code 4 (claim 4) or completes. When it completes, its successor state is the
same function of the pre-state as in B. After that edge, the engine-K slices
are therefore again equal. The timestamp and synchronizers are equal because
they depend only on the shared pad inputs. The latches are equal because both
read the same address (`lemma_fetch`). So the theorem applies again from that
edge. Hence A equals B in engine K's registers and pads up to and including
the arrival step, and claim 1 holds in A. The same holds for the arrival
state, which is the certificate's `post`.

From the arrival step on, the boundary lemmas apply to A directly, one edge at
a time, in A's own environment. This gives claims 2 and 3. The `post`
assertion fixes the blocked count at zero on arrival, so the LIMIT count in
claim 2 starts from the analyzer's arrival edge. For `HALT` and fault ends,
`post` and `lemma_stopped` give claim 5.

The step uses the same quiet construction of B for every environment of A.
Timing isolation is used as proved, not re-proved: the certificates never
reason about other engines or host traffic.

*Soft boundaries.* When `N_{i+1}` is a soft branch node, nothing waits: the
next certificate starts in the arrival state of segment `i` (its step 0 is
that arrival step), and `post` of segment `i` is its precondition as it
stands. Claims 2 and 3 hold trivially (there is no stall), no boundary lemma is
used, and the step above applies unchanged from that state.

**The premises (H1).** A host load produces (H1). The chain is:

- `formal_depth/harness/host_protocol.sv`: a window-1 word is written exactly
  as assembled from its eight nibbles.
- `formal_depth/harness/program_load.sv`, unbounded:
  - the SRAM is written only between BEGIN and COMMIT;
  - a running engine's image is committed, and every address below its length
    was written since the last BEGIN;
  - the engine decodes the word read at its PC on the previous edge.
- `sram_lemma.sv` (this directory), unbounded: one
  `RM_IHPSG13_1P_64x16_c2` FUNCTIONAL model with all inputs free returns, on
  every read of a symbolic address, the last word written there. IC3/PDR
  (ABC `pdr`) proves it on the bit-level model and finds the invariant over the
  array itself. A negative control (a read returns something else) fails, and
  a cover shows the read path.
- The write address of the k-th program word after BEGIN is the loaded-word
  counter (`processor.ml`, `write_address = image_loaded`).

`program_load.sv`'s own end-to-end data-integrity claim (`spec_word_*`) is
checked only by BMC to depth 48 from reset; see
[`formal-depth.md`](formal-depth.md), "What is still unproven". The macro
lemma above replaces the bounded step with an unbounded one, but the chain is
an argument over separate proofs, not one proof.

## 4. What is certified and what is assumed

**Certified by proof, per committed image:**

- the issue edge of every instruction and the arrival edge of every boundary,
  relative to the anchor;
- every owned-pad change: the edge on which it can happen, and for known
  levels, that it happens there and nowhere else;
- the level set of every owned pad on every edge;
- the edge and pin of every input sample, including the synchronizer
  latency;
- the engine state handed from one segment to the next.

**Certified independently of the image:** START, the behaviour of the four
boundary instructions and of strict `PUSH`, the fetch path, the stopped
engine and the synchronizer (the lemmas).

**Assumed or not covered:**

- **When boundaries complete.** The certificates prove what happens between
  boundaries and what the engine does while it waits. How long it waits is the
  environment's choice, as in `timing-analysis.md`:
  - `PULL`/`PUSH` wait for the host or the mover without bound;
  - `WAITEVENT` waits for an event and `WAITPIN` for a pin, each bounded by
    LIMIT.

  External peer behaviour enters only there, and through the values of
  sampled bits (free in every certificate).
- **Input timing at the pad.** The certificates use the RTL's synchronous
  model of the inputs: a pad value is present for a whole cycle and is
  captured on an edge. The phase of an asynchronous edge within the clock
  period (one cycle of uncertainty on a pin-derived anchor) and
  metastability are outside the RTL model. Their handling is as described in
  `timing-analysis.md`.
- **Physical delays.** Clock-to-pad, pad skew, board delays and gate-level
  timing are not covered; see [`hardening.md`](hardening.md).
- **The netlist.** The certificates and lemmas are proved on
  `protocol_processor_fv`. `equiv_src.py` proves it sequentially equivalent to
  the committed `src/project.v` + `src/protocol_emulator_core.v` on the chip
  ports (section 6).
  - ABC's statement is sequential equivalence from the all-zero state of
    both designs, with every SRAM and FIFO bit counted as state, for every
    input sequence. From that state the host can write any program word and
    FIFO entry, so the states reached after a reset and a program load are
    covered.
  - States that hold power-up contents in memory locations never written are
    outside the statement. It is not a proof for arbitrary initial states.
  - The SRAM is the vendor's FUNCTIONAL model, not the physical macro.
- **The premises.** (H1) and (H2) are premises of the claim. (H1) follows from
  a host load by the chain of separate proofs in section 3.
- **Scope of the generator.**
  - It needs an exact analysis (no widening, no budget cut).
  - It rejects boundary-free periodic loops (a loop with neither a blocking
    instruction nor a data-dependent exit, which never reaches a boundary).
    Scalar-issue SPI/JTAG images and some random programs have them; no
    committed image does. Soft branch boundaries are supported since
    generator 1.1 (section 1).
  - Values the analyzer leaves unknown are unknown in the certificates too. A
    data pin's level is therefore certified only as a set ("0 or 1", or
    "0 or released" for open-drain), not as the data bit. The RTL mutant
    `rtl_od_oe_ungated` (section 5) is a bug of exactly this kind that the
    certificates do not catch. Tying data pins to the bits of the pulled word
    would need symbolic data tracking in the generator; it is not
    implemented.
- **Timing isolation** is cited from `formal/`, from the GitHub `formal`
  workflow on the certified commit (section 6). It was not re-proved here.

## 5. Negative controls

A certificate that passes for a wrong schedule would be worthless. There are
three kinds of negative control: certificates from a deliberately wrong
analyzer, certificates with one perturbed prediction, and real certificates
run on a netlist with a deliberate timing bug.

- **Wrong analyzers.** These are the seven mis-modellings of `pe_validate.py`,
  applied to a copy of `pe_timing.py`:
  - `WAIT n` costs n;
  - one `XFER` edge is late;
  - `COUNT n` gives n iterations;
  - loopback latency 4;
  - open-drain modelled as push-pull;
  - `HALT` keeps the pins;
  - `SET` costs 2 cycles.

  For each image the mutant changes, the generator picks the cheapest node
  whose *precondition is unchanged but whose segment claim differs*. That
  certificate must fail on its own. A node whose precondition also changed can
  be self-consistent; there, the mutant is caught by the predecessor's `post`.
  Loopback latency 4 changes only the analyzer's minimum stalls, not any
  segment, so it yields no certificate.

  A wrong analyzer can also predict a schedule that contradicts itself, for
  example a pad event whose "before" state is not the state it predicted a
  step earlier. No certificate can be written for such a node. The generator
  skips it, lists it in the manifest under `not_generated` (with the reason),
  and takes the control from another node the mutation changes. It happens
  once among the images of section 8: `open-drain-as-push-pull` on
  `ps2-device`, nodes n3 and n7; the control comes from node n17.
- **Perturbed predictions.** One prediction per image is changed in the
  cheapest node where it applies:
  - `pad-late`/`pad-early`: one known-level pad change moves one edge;
  - `pad-level`: a known level is flipped;
  - `issue-late`: in every variant, every issue after the first multi-cycle
    instruction moves one edge. All variants are changed because `flow` only
    needs one variant to match.
  - `post-limit`: the successor's LIMIT is off by one.
  - `branch-late` (soft branch nodes only, section 1): the data-dependent
    branch at step 0 resolves one edge later, so every later issue of every
    variant moves one edge. Images without soft branch nodes (all 19 of
    section 6) get no `branch-late` control.

  Since the `24f31f0` campaign, each perturbation is also applied to the
  cheapest *chunk* certificate of a segment longer than 96 steps (a chunk's
  `post-limit` perturbs the state it hands to the next chunk), because the
  chunk chains are what prove those segments. These chunk-level controls
  have a BMC depth of at most 99, that is at most 97 steps: a chunk has at
  most 96 steps, and `issue-late` adds one (a certificate of n steps is BMC
  to depth n + 2). The results are at the end of section 6.
- **Deep controls.** A control of more than 2,000 steps is not run
  (`CERT_NEG_MAX`): BMC to that depth does not finish in useful time. It is
  recorded as not run, which counts as undecided, not as failed. The
  longest control of the `24f31f0` campaign is 1,573 steps, so this rule
  left out none of its controls. Several images of section 8 have only such
  deep whole-segment controls; for them the chunk-level controls are the
  ones that run.

The lemma file has its own control (`neg`): it claims that a `WAITPIN` timeout
keeps the pads, and it must fail.

- **Wrong RTL.** The real certificates are also run against netlists built
  from a private copy of `hardcaml/lib` with one timing bug each
  (`rtl_mutants.py`, then `formal/run.sh --generate-only`):
  - `rtl_wait_plus1`: `WAIT n` holds one cycle too long;
  - `rtl_xfer_short_tick`: after the first `XFER` transition every half period
    is one cycle short;
  - `rtl_sync_3flop`: the pad inputs pass through three flip-flops instead of
    two;
  - `rtl_od_oe_ungated`: an enabled open-drain pin at logical 1 keeps its
    output enable and pulls low instead of releasing;
  - `rtl_od_drive_high`: open-drain pins are not masked in `uio_out`. This
    changes `uio_out` only while `uio_oe` is 0, so no pad changes. The
    certificates are about pads and must *not* flag it; `formal_depth`'s
    `pin_safety.sv` covers `uio_out` itself.

  For each bug, the certificates of one image that uses the affected feature
  are run: `uart-tx` (WAIT), `spi-controller-mode0` (XFER), `uart-rx` (input
  samples) and `i2c-write` (open-drain). The certificates of segments without
  that feature must still pass; the others should fail.

  The outcome for `rtl_od_oe_ungated` shows a limit of the certificates. All
  26 `i2c-write` certificates pass on that netlist. The I2C images drive
  their open-drain pins through the output *enable*, with logical value 0, so
  the bug shows only where an enabled pin carries a data bit (`OUT` of SDA).
  There the certificate predicts the set {driven 0, released}, because the
  analyzer does not know the data. A pin that pulls low instead of releasing
  stays inside that set. The certificates check timing and known levels, not
  data values (section 4).


## 6. Results

The current campaign for the 19 images of `24f31f0` ran on that commit. The
seven images added in `e64cd6b` were certified by a later campaign on
`6a3ea08` (section 8). The earlier campaign on `c118027` is kept at the end
of this section; the ledger lists all three.

**Setup.**

- Every run used the committed tree at `24f31f0`, exported with `git archive`,
  so concurrent edits to the working tree could not leak in. It holds 19
  firmware images. `fb79f31` changed the three I2C controller images
  (`i2c-read`, `i2c-write`, `i2c-repeated-start`); the other 16 are unchanged
  since `c118027`.
- `formal/run.sh --generate-only` built `processor_fv.v` from that snapshot
  (Slurm job 24168359). Its sha256 (`2a039bae…`) is that of the `c118027`
  campaign's copy, and that of the `formal-rtl` artifact of the GitHub
  `formal` run 36391218338 on `24f31f0`.
- The harnesses of the 16 unchanged images are byte-identical to those proved
  at `c118027`. They were proved again, so every result below is for one
  commit.
- Tools: OSS CAD Suite 2026-07-29 (Yosys 0.67, SymbiYosys, yices, boolector,
  bitwuzla, ABC); generator 1.0 (1.1 emits the same files for these images).
- Compute: Slurm (`mit_preemptable`, `mit_normal`, `mit_quicktest`), at most
  128 CPUs at a time, 05:11 to 06:23 on 2026-09-28; the arrays were allocated
  43 CPU-hours.
  The job ids are in the ledger's campaign record, and every certificate's
  outcome is in
  [`results/campaigns/24f31f0.md`](../tools/timing/cert/results/campaigns/24f31f0.md).

**Segment certificates, per image.** Column definitions:

- *segments* are boundary-graph nodes, each with one certificate.
- *pad events* are the analyzer's pad events in all variants. *Known-level
  edges* are those between disjoint states, whose edge the certificate fixes
  exactly.
- *issue slots* are the certified instruction issues, including arrivals.
- *longest segment* is the last step checked.
- *proved* counts segments with a passing whole-segment or chunked proof.
- *split into chunks* is proved chunk chains out of segments that were split.
- *solvers* are those that returned first.

| image | engine | segments | variants | pad events | known-level edges | issue slots | input samples | longest segment | proved | whole-segment BMC | split into chunks | covers | solver time max / total (s) | solvers |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| `event-transmitter` | 0 | 2 | 2 | 3 | 3 | 8 | 0 | 34 | 2/2 | 2 | 0/0 | 2/2 | 8 / 12 | boolector |
| `i2c-read` | 3 | 26 | 28 | 55 | 46 | 174 | 10 | 105 | 26/26 | 26 | 1/1 | 26/26 | 32 / 409 | boolector, yices |
| `i2c-repeated-start` | 3 | 50 | 53 | 120 | 93 | 368 | 14 | 105 | 50/50 | 50 | 2/2 | 50/50 | 47 / 880 | boolector, yices |
| `i2c-target-read` | 3 | 67 | 70 | 20 | 7 | 159 | 17 | 5 | 67/67 | 67 | 0/0 | 67/67 | 5 / 237 | boolector, yices |
| `i2c-target-write` | 3 | 48 | 49 | 8 | 8 | 104 | 16 | 6 | 48/48 | 48 | 0/0 | 48/48 | 6 / 158 | bitwuzla, boolector, yices |
| `i2c-write` | 3 | 25 | 27 | 64 | 46 | 173 | 3 | 105 | 25/25 | 25 | 1/1 | 25/25 | 42 / 518 | boolector, yices |
| `jtag` | 0 | 4 | 4 | 75 | 59 | 65 | 16 | 694 | 4/4 | 4 | 3/3 | 4/4 | 527 / 1170 | bmc3, boolector |
| `spi-controller-fast` | 2 | 4 | 4 | 57 | 39 | 18 | 16 | 261 | 4/4 | 4 | 2/2 | 4/4 | 220 / 370 | bmc3, boolector, yices |
| `spi-controller-mode0` | 2 | 4 | 4 | 57 | 39 | 18 | 16 | 517 | 4/4 | 4 | 2/2 | 4/4 | 574 / 1132 | bmc3, boolector |
| `spi-controller-mode1` | 2 | 4 | 4 | 57 | 39 | 18 | 16 | 517 | 4/4 | 4 | 2/2 | 4/4 | 450 / 861 | bmc3, boolector |
| `spi-controller-mode2` | 2 | 4 | 4 | 57 | 39 | 18 | 16 | 517 | 4/4 | 4 | 2/2 | 4/4 | 732 / 1144 | bmc3, boolector, yices |
| `spi-controller-mode3` | 2 | 4 | 4 | 57 | 39 | 18 | 16 | 517 | 4/4 | 4 | 2/2 | 4/4 | 425 / 849 | bmc3, boolector, yices |
| `spi-target-mode0` | 2 | 24 | 24 | 12 | 3 | 63 | 8 | 3 | 24/24 | 24 | 0/0 | 24/24 | 4 / 73 | boolector, yices |
| `spi-target-mode1` | 2 | 25 | 25 | 12 | 3 | 64 | 8 | 3 | 25/25 | 25 | 0/0 | 25/25 | 4 / 84 | boolector, yices |
| `spi-target-mode2` | 2 | 24 | 24 | 12 | 3 | 63 | 8 | 3 | 24/24 | 24 | 0/0 | 24/24 | 5 / 95 | boolector, yices |
| `spi-target-mode3` | 2 | 25 | 25 | 12 | 3 | 64 | 8 | 3 | 25/25 | 25 | 0/0 | 25/25 | 5 / 88 | boolector, yices |
| `uart-rx` | 1 | 5 | 7 | 0 | 0 | 140 | 36 | 617 | 5/5 | 5 | 2/2 | 5/5 | 212 / 329 | bmc3, boolector |
| `uart-tx` | 0 | 3 | 3 | 21 | 3 | 65 | 0 | 643 | 3/3 | 3 | 2/2 | 3/3 | 357 / 712 | bmc3, yices |
| `waveform` | 0 | 2 | 2 | 34 | 34 | 86 | 0 | 1540 | 2/2 | 2 | 1/1 | 2/2 | 653 / 657 | bitwuzla, bmc3 |
| **total** | | 350 | 363 | 733 | 506 | 1686 | 224 | | 350/350 | 350 | 22/22 | 350/350 | | |

The three I2C controller images have fewer segments than at `c118027`
(`i2c-read` 26 instead of 36, `i2c-repeated-start` 50 instead of 58), because
`fb79f31` shortened their programs.

**How the segments ran.**

- The 332 segments of at most 200 steps ran as one Slurm array (24168398),
  10 certificates per task, with a bitwuzla/boolector/yices portfolio per
  certificate: 2 to 47 s each. Boolector returned first 213 times, yices 116
  times and bitwuzla 3 times.
- The 18 segments longer than 200 steps ran whole on ABC `bmc3` alone
  (24168403), 107 to 732 s each (table below).
- The 127 chunks of the 22 segments longer than 96 steps ran in array
  24168399, 5 to 48 s each.
- Covers (24168400): 328 whole-segment covers (median 8 s, slowest 1,248 s)
  and 127 chunk covers (median 475 s, slowest 717 s), with yices alone. All
  455 reach the end of their variant.

**Long segments (whole segment and chunks).**

| segment | last step | whole-segment BMC: solver, s | chunks proved | chunk time max / total (s) |
|---|---:|---|---:|---:|
| `i2c-read` n3, `i2c-write` n3 (pc5 `WAITPIN`) | 105 | boolector, 25 / 42 | 2/2, 2/2 | 44 / 104 |
| `i2c-repeated-start` n3, n27 (pc12 `WAITPIN`) | 105 | boolector, 34 / 44 | 2/2, 2/2 | 43 / 97 |
| `jtag` n0 (START) | 694 | ABC `bmc3`, 273 | 8/8 | 48 / 236 |
| `jtag` n1, n3 (pc29 `PULL`) | 515 | ABC `bmc3`, 365 / 527 | 6/6, 6/6 | 27 / 263 |
| `spi-controller-fast` n1, n3 (pc3 `PULL`) | 261 | ABC `bmc3`, 220 / 141 | 3/3, 3/3 | 26 / 132 |
| `spi-controller-mode0..3` n1, n3 (pc3 `PULL`) | 517 | ABC `bmc3`, 404 to 732 | 6/6 each | 40 / 1509 |
| `uart-rx` n2, n4 (pc4 `WAITPIN`) | 617 | ABC `bmc3`, 107 / 212 | 7/7, 7/7 | 24 / 283 |
| `uart-tx` n1, n2 (pc2 `PULL`) | 643 | ABC `bmc3`, 357 / 351 | 7/7, 7/7 | 47 / 462 |
| `waveform` n0 (START) | 1540 | ABC `bmc3`, 653 | 17/17 | 45 / 613 |

**Lemmas** (job 24168402).

| harness, task | outcome | s | solver |
|---|---|---:|---|
| `boundary_lemmas` k0, k1, k2, k3 (depth 4) | PASS each | 13 to 14 | bitwuzla |
| `boundary_lemmas` cover_k0..k3 (8 covers each) | PASS, 8/8 reached each | 18 each | yices |
| `boundary_lemmas` neg | FAIL (expected), `neg_waitpin_timeout_keeps_pads` | 7 | yices |
| `sram_lemma` prove (IC3/PDR) | PASS | 5 | ABC `pdr` |
| `sram_lemma` cover | PASS | 1 | yices |
| `sram_lemma` neg | FAIL (expected), `neg_sram_stale` | 0 | yices |

**Negative controls** (job 24168401).

| mutation | certificates that failed / generated | assertions that fired |
|---|---:|---|
| `wait-n-cycles` (analyzer) | 8 / 8 | `flow` |
| `xfer-late-edge` (analyzer) | 6 / 6 | `pad`, `edge` |
| `count-n-iterations` (analyzer) | 13 / 13 | `post` (successor state), `flow` |
| `open-drain-as-push-pull` (analyzer) | 4 / 4 | `pad` on SDA |
| `halt-keeps-pins` (analyzer) | 1 / 1 | `pad`, `edge` |
| `set-two-cycles` (analyzer) | 18 / 18 | `flow` |
| `pad-late` (prediction) | 18 / 18 | `pad`, `edge` |
| `pad-early` (prediction) | 14 / 14 | `pad` |
| `pad-level` (prediction) | 18 / 18 | `pad` |
| `issue-late` (prediction) | 13 / 13 | `flow` |
| `post-limit` (prediction) | 19 / 19 | `post` |

The 32 controls longer than 200 steps ran on ABC `bmc3` alone, with sby's
option `vcd_sim` so that the counterexample is replayed with Yosys `sim`
rather than smtbmc (which ran out of memory on such traces at `c118027`).
The deepest were `waveform`'s `count-n-iterations` (violation at step 1,444,
1,308 s) and `post-limit` (step 1,541, 946 s).

**RTL mutants** (real certificates run on a wrong netlist; netlists built
from the `24f31f0` snapshot by job 24168782, certificates in job 24168801):

| RTL mutant | image | certificates run | failed | assertions that fired |
|---|---|---:|---:|---|
| `rtl_wait_plus1` | `uart-tx` | 15 | 14 | `flow`; only the START segment has no `WAIT` and passes |
| `rtl_xfer_short_tick` | `spi-controller-mode0` | 14 | 12 | `pad`, `edge` on SCK/MOSI, `flow`; the two segments without `XFER` pass |
| `rtl_sync_3flop` | `uart-rx` | 17 | 14 | `sample`; the three segments without a sample pass |
| `rtl_od_oe_ungated` | `i2c-write` | 26 | 0 | none (not caught; section 5) |
| `rtl_od_drive_high` | `i2c-write` | 26 | 0 | none (not pad-visible; expected) |

From `76a81f5` until `16cfc20`, `engine.ml` held a second copy of the
classic `XFER` tick update (the one for engines with the line unit), and
`rtl_xfer_short_tick` mutated the design of record's copy (the one without a
line unit). After `16cfc20` the
classic transfer body is built once, in `Engine_transfer.transfer`
(`hardcaml/lib/engine_transfer.ml`), for engines with and without the line
unit; `rtl_mutants.py` mutates it there and keeps the `engine.ml` sites as
alternatives for older snapshots. For the design of record the mutated
netlist is the same either way (it has no line unit).

**Equivalence of the formal netlist with the committed top.**

- `equiv_src.py` (ABC method) reports "Networks are equivalent" (job
  24168404, 36 s). The miter has 24,408 latches; latch correspondence reduced
  them to 0.
- Negative control: the same flow compares the committed top with the
  `rtl_sync_3flop` netlist and finds a counterexample in frame 9 (ABC `bmc3`,
  30 frames; job 24168802, 42 s; the job exits 1 because the netlists differ,
  as required).
- The `formal/` mutant netlist is not used as the control: it needs about 45
  cycles to diverge, which `bmc3` does not reach on this miter in useful time
  (the attempt 24168405 was cancelled, as were 23988154 and 23989344 at
  `c118027`).

**Timing isolation** (cited, not re-run). All 16 proof jobs of the GitHub
`formal` workflow pass on `24f31f0` (run 36391218338), among them
`timing_isolation_prove_k0` to `_k3`, `_bmc`, `_cover` and the negative
controls `_neg_pull` and `_neg_mutant` (expected failures). That run's
`formal-rtl` artifact contains the same `processor_fv.v` as the certificates
(sha256 `2a039bae…`).

**Chunk-level controls** (added after the campaign; job 24198443, on the
same snapshot and netlist; not part of the ledger record). The five
schedule perturbations of section 5, applied to the cheapest chunk
certificate of each image with segments longer than 96 steps, give 52
controls: 11 each of `pad-late`, `pad-early` and `pad-level`, 7 of
`issue-late` and 12 of `post-limit`. All 52 fail as required, in at most
58 s each. A `pad` assertion fired in 33 of them, `post` in 12, `edge` in 11
and `flow` in 7 (some fire both `pad` and `edge`). Six of the `issue-late`
ones have BMC depth 99, one more than CI runs (section 8), so only the
cluster campaign runs them.

**Trial: soft branch nodes** (not a certification). The idle-tolerant UART
receiver `uart-rx-idle` (in the working tree at the time, not committed at
`24f31f0`, committed since in `e64cd6b` with the same image;
image sha256 `edf05fc9…`) has no blocking instruction, so its three
data-dependent `JZ`s are soft boundaries: four soft branch nodes (one `JZ`
in two contexts) of its five. Its certificates were run with generator 1.1
on the `24f31f0` netlist (job 24168524): 5 of 5 whole-segment certificates
and 14 of 14 chunks pass, 17 of 17 covers are reached, and 8 of 8 negative
controls fail. Four of the controls target the soft nodes n1 and n3
directly: the branch taking one cycle more (`branch-late`, fires `flow`) and
a wrong successor LIMIT (`post-limit`, fires `post`). In the trial, these
four were made for the trial (the two `branch-late` ones by hand); the generator now has `branch-late`
as a schedule mutant, and its harnesses for n1 and n3 are byte-identical to
the hand-made ones. A campaign emits one `branch-late` control per image
(the cheapest soft node). `post-limit` picks the cheapest node overall, which
need not be a soft one.
The image is certified only once it is committed and a campaign has recorded
it (section 8).

### Earlier campaign: `c118027` (superseded)

The first campaign ran on `c118027` (tag `v0.1-hardened`): all 368 segments
of the 19 images committed there were proved, with 21 long segments also as
chunk chains, 368 covers, the lemmas, 132 of 132 negative controls failing,
3 of 5 RTL mutants caught, and the same equivalence result (jobs 23975821 to
23989727). Its tables are in
[`results/campaigns/c118027.md`](../tools/timing/cert/results/campaigns/c118027.md),
and [`bmc3_neg_evidence.json`](../tools/timing/cert/results/bmc3_neg_evidence.json)
has its four ABC-only negative-control results. The ledger lists its 19
records as superseded: 16 re-certified with the same image bytes, and the
three I2C controller images because `fb79f31` changed them. The observations
below come from that campaign.

*Solvers.*

- *Engine comparison* on `uart-tx` n1 (644 steps, trial job 23975346, same
  certificate):
  - rIC3 BMC: 240 s;
  - ABC `bmc3`: 399 s;
  - boolector: 492 s;
  - yices: 2094 s;
  - bitwuzla: at step 113 after 40 minutes (cancelled).
- *Memory limits.*
  - yices stopped with "Out of memory" at step 686 of the 697-step `jtag`
    START segment (12 GB resident, job 23983459).
  - A three-solver portfolio in a 32 GB allocation ran out of memory on the
    deepest segments (23976555, and 6 negative controls in 23975823).
  - Those runs were repeated with a single solver.
- *The ABC flow is not vacuous.* For the two segments proved only by ABC
  `bmc3` as whole segments, `bmc3` also runs negative controls on the same
  harness. It finds the `waveform` `pad-late` violation at step 3 (job
  23989005). It also finds the deliberately wrong successor LIMIT (`post-limit`) at the
  end of `jtag` n0 (frame 695, 418 s), `waveform` n0 (frame 1541, 892 s) and
  `uart-tx` n1 (frame 644, 362 s). It finds the `count-n-iterations` error of
  `waveform` at frame 1444 (820 s). These are jobs 23989005 and 23989372;
  the ABC logs are summarized in
  [`bmc3_neg_evidence.json`](../tools/timing/cert/results/bmc3_neg_evidence.json). Sby's replay of
  these long counterexamples through smtbmc was stopped, because yices runs
  out of memory near step 680. Both segments are proved by chunks with the SMT
  solvers as well.
- *Covers.* Covers are slower than proofs, because they need a satisfying
  trace to the end of the segment. With a yices/boolector portfolio, boolector
  returned first in 164 of 197 cover runs. The median cover took 6 s, the
  slowest 504 s (jobs 23983908, 23984832 to 23984834, 23988106).
- *Equivalence attempts.* Yosys `equiv_simple`/`equiv_induct` over the
  name-matched signals proved no pair (23975461, 23987454). The first AIGER
  export left latches uninitialised, and ABC reported a spurious frame-0
  difference (23984871); `setundef -init` fixed that.

## 7. Reproducing

```sh
export OSS_CAD_SUITE=/path/to/oss-cad-suite OCAML_ENV=/path/to/ocaml-env.sh
python3 tools/timing/cert/ledger.py check                     # is the ledger current?
python3 tools/timing/cert/gen_cert.py preflight firmware/*.image.json   # certifiable? CI runs?
CERT_DRY_RUN=1 tools/timing/cert/campaign.sh certify $WORK    # prepare only, print task counts
tools/timing/cert/campaign.sh certify $WORK                   # certify what check flags (Slurm)

# the campaign of section 6, by hand
CERT_RTL_MUTANTS=1 tools/timing/cert/campaign.sh prepare $WORK 24f31f0
tools/timing/cert/campaign.sh submit $WORK short chunks cover neg lemma whole_long rtlneg equiv
tools/timing/cert/campaign.sh retry $WORK      # after the arrays: rerun ERROR runs (yices alone)
tools/timing/cert/campaign.sh record $WORK     # after the retry array: summarize + ledger.py record
```

Without Slurm, the certificates of one image (as CI runs them), or a single
certificate:

```sh
formal/run.sh --generate-only                                 # formal/build/rtl/processor_fv.v
tools/timing/cert/ci_prove.sh $WORK/ci formal/build/rtl models firmware/uart-tx.image.json
tools/timing/cert/ci_lemmas.sh $WORK/ci formal/build/rtl models   # the 12 lemma tasks
python3 tools/timing/cert/gen_cert.py emit firmware/uart-tx.image.json --out $WORK/certs \
    --rtl formal/build/rtl --models models
tools/timing/cert/run_one.sh $WORK/certs cert_uart_tx_n1 bmc
```

The equivalence check and its control, the RTL mutants and the self-checks:

```sh
python3 tools/timing/cert/equiv_src.py --src src --rtl formal/build/rtl --models models \
    --out $WORK/equiv --run                                   # expect EQUIVALENT
python3 tools/timing/cert/rtl_mutants.py $SNAPSHOT $WORK/rtlmut          # needs OCAML_ENV
python3 tools/timing/cert/equiv_src.py --src src --rtl formal/build/rtl --models models \
    --out $WORK/equiv_neg --run --bmc-only 30 \
    --gate $WORK/rtlmut/rtl_sync_3flop/fbuild/rtl/processor_fv.v    # expect COUNTEREXAMPLE
python3 tools/timing/cert/gen_cert.py emit firmware/uart-tx.image.json --out $WORK/rtlneg \
    --rtl $WORK/rtlmut/rtl_wait_plus1/fbuild/rtl --models models --chunk 96 --rtl-mutant rtl_wait_plus1
python3 tools/timing/cert/gen_cert.py crosscheck firmware/*.image.json
python3 -m unittest discover -s tools/timing/cert -p 'test_*.py'
```

## 8. Keeping the certificates current

A certificate is about one image on one RTL. The ledger
[`tools/timing/cert/results/summary.json`](../tools/timing/cert/results/summary.json)
(tables in [`summary.md`](../tools/timing/cert/results/summary.md)) makes
that binding machine-checkable.

- **Per image:** the sha256 of the image file and its `bytecode_sha256`, the
  campaign and commit that certified it, the counts of section 6, whether
  every segment was proved and every cover reached, how many negative
  controls failed (an image with a negative control that *passes* is not
  certified; one that does not finish, or is too deep to run, is listed as
  undecided; nodes a wrong analyzer's schedule could not be written for are
  listed as not generated, section 5), and two
  *obligation digests*: sha256 over every whole-segment and
  every chunk certificate (module name, harness, and the sby file without its
  solver choice and with its file list reduced to base names), together with
  the harness includes `cert_dut.vh` and `cert_env.vh`.
- **Per campaign:** the commit; the sha256 of `processor_fv.v`,
  `src/project.v`, `src/protocol_emulator_core.v`, the two SRAM model files
  and the harness files; the lemma, equivalence and equivalence-control
  results; the Slurm jobs.
- **History:** a record replaced by a later campaign moves to `superseded`
  with the reason. Every campaign's full results stay in
  [`results/campaigns/`](../tools/timing/cert/results/campaigns/).

**The check.** `python3 tools/timing/cert/ledger.py check` fails unless every
`firmware/*.image.json` (the images for the design of record; `firmware/ext/`
holds images for the extension variant's ISA) has a certified record whose
sha256 is the file's current sha256, and the current `src/` files, SRAM
models, harness includes and lemma files (`boundary_lemmas.sv`,
`sram_lemma.sv`: the composition of section 3 rests on them) are the ones
its campaign ran on. It also re-emits every certificate with the current
`pe_timing.py` and `gen_cert.py` and compares the obligation digests. So a
change to the analyzer or generator that changes any certificate makes the
image stale, while a change that emits the same files does not. With
`--rtl DIR` it also compares a regenerated `processor_fv.v`. For each image
it flags, it runs `gen_cert.py preflight` (below) and says whether `certify`
can handle it. It needs only the Python standard library. It takes under a
second when every image is certified, and about 20 s while the six deep
images below are flagged, because of their preflight.

**Certifying new or changed images** is one command on the cluster, for a
commit (it need not be pushed yet; see "Order of commits" below), with
`OSS_CAD_SUITE` and `OCAML_ENV` set as for `formal/run.sh`:

```sh
tools/timing/cert/campaign.sh certify $WORK        # REV defaults to HEAD
```

It submits a short Slurm job that snapshots the commit, lists the images
`ledger.py check` flags there, generates the formal RTL, emits their
certificates, chunks, covers and negative controls, the lemmas and the
equivalence check with its control, and submits the arrays. When the arrays
have finished, a retry job (`campaign.sh retry $WORK record`) reruns what did
not run to the end (below), and then submits the job that records the
campaign in `tools/timing/cert/results/`. After the record job,
`ledger.py check` must pass; then commit `tools/timing/cert/results/`.

- Before anything is emitted, each flagged image goes through
  `gen_cert.py preflight`. It runs, in memory, every emission the campaign
  makes for that image: the whole-segment certificates, the chunk chains
  with the cycle-level re-derivation of every variant, and every negative
  control. An image it refuses is reported and left out, so one image cannot
  stop the others. A negative control that cannot be generated for some
  nodes (section 5) does not refuse the image.
- `CERT_ONLY="name ..."` restricts the run to some of the flagged images.
- `CERT_DRY_RUN=1` stops after the emission and prints the size of every
  task list, without submitting anything.
- Negative controls of more than 2,000 steps are not run (`CERT_NEG_MAX`,
  section 5).
- Runs that pass keep only their sby log and result file
  (`CERT_PRUNE_PASSED=1`, the default in `certify`). A campaign of the
  images below has 10,781 runs, and each kept sby work directory is about
  17 files.
- **Retry step.** `certify` runs covers with two solvers, boolector and
  yices, because that is much faster: most chunk covers of `uart-rx-idle`
  took 71 to 86 s with both and 507 to 840 s with yices alone (jobs
  24201555 and 24202592). But boolector can run out of memory on the cover
  of the first chunk of a long segment, and sby then stops the whole run
  with ERROR. A run that ends in ERROR is not a result, and `summarize`
  needs every chunk cover to pass, so without a rerun the image would be
  recorded as not certified. `campaign.sh retry` (`retry_list.py`) lists
  every run of the short, chunks, cover and negative-control lists whose
  results are all ERROR and reruns it with yices alone; a run with no result
  at all (its Slurm task was lost, usually killed for running out of
  memory) is rerun as it was, except that a solver portfolio becomes yices
  alone. Runs on ABC `bmc3`
  are not repeated. `summarize` takes the best result of each run.
  `ci_prove.sh` uses the same list.
- **By hand**, if the record job still reports a run that did not finish:
  `tools/timing/cert/run_one.sh $WORK/long NAME cover yices` for one run (or
  `campaign.sh retry $WORK` for every such run, as another array), then
  `campaign.sh record $WORK` once it has finished.

**CI.** The `certs` workflow ([`.github/workflows/certs.yaml`](../.github/workflows/certs.yaml)):

- `staleness`, on every push and pull request: the generator's and the
  ledger's unit tests, then `ledger.py check`. It fails while any committed
  image lacks a certificate for its current sha256.
- `plan` and `proofs`, only when a push or pull request changes
  `firmware/`, `hardcaml/`, `tools/timing/`, `formal/gen/`, `formal/run.sh`,
  `models/` or `configs/`. `plan` regenerates `processor_fv.v` as the
  `formal` workflow does, and `ledger.py plan` picks the images to prove: the
  image files that changed and, when the RTL inputs or `tools/timing/`
  changed, every image that `ledger.py check --rtl` flags against the
  regenerated netlist (a new image, changed obligations, or a different
  `processor_fv.v`, which flags every image). An image whose proofs need
  more than 150 runs (counted by `gen_cert.py preflight`) gets no proof job:
  `plan` names it in a warning annotation and in its summary, and it is
  certified by the cluster campaign only. Each other image is proved on its
  own runner (at most four at a time) by `ci_prove.sh`: whole-segment
  certificates of up to 96 steps, chunk chains for longer segments, a cover
  per run (with yices alone, `CI_COVER_ENGINES`), and the negative controls of BMC depth up to 98 (96 steps). A run
  that ends in ERROR is rerun with yices alone, as in the cluster campaign.
  Deeper negative controls (among them the six `issue-late` chunk-level
  controls of depth 99, section 6) and whole-segment proofs of the long
  segments run only in the cluster campaign.
- `lemmas`, when the lemma files, `cert_dut.vh`, the generator or the RTL
  inputs change: the 12 lemma tasks of section 6 (`ci_lemmas.sh`).
- On branches and pull requests a newer push cancels the older run. On
  `main` every push has its own concurrency group, so `staleness` never
  waits behind the proofs of an earlier push, and every push's proofs
  complete.

The CI proofs re-check a change; they do not write the ledger. A changed
image therefore keeps `staleness` red until a cluster campaign has certified
it and the result is committed.

**Order of commits.** `certify` exports its commit with `git archive`, so it
can run on a local commit that is not pushed yet. Committing a new or
changed image, certifying that commit, and then pushing it together with the
commit of `tools/timing/cert/results/` keeps `staleness` green on `main`.
Pushing the image first leaves `staleness` red until the campaign is
recorded. The ledger names the certified commit, so that commit must not be
rewritten afterwards.

**First run on GitHub.** In run 36624435421 (the push of `bab697b`), the
`prove uart-rx-idle` job was killed about two minutes after its proofs
started (exit code 143; the job's later steps did not run), which is what a
runner out of memory looks like: the covers then ran on the boolector+yices
portfolio, whose boolector used up 16 GB on this image's first-chunk covers
on the cluster. Since then `ci_prove.sh` runs the covers with yices alone
(`CI_COVER_ENGINES`, default `yices`), as the cluster campaign of
`24f31f0` did; the proof runs keep the portfolio.

**CI runtime.** Measured on cluster nodes with a GitHub runner's resources
(4 CPUs, 16 GB, two certificate runs at a time), not on GitHub itself, and
with the covers on the portfolio (except `uart-rx`), so covers with yices
alone take longer. The
first four rows were measured before the `branch-late` and chunk-level
controls were added (section 5), which add one to four runs of BMC depth at
most 98 to each of these images; the run counts now are in the last column.

| image | runs when measured | wall time | Slurm job | runs now |
|---|---:|---:|---|---:|
| `i2c-repeated-start` | 112 | 22 min | 24170303 to 24170305 | 116 |
| `jtag` | 43 | 29 min | 24170303 to 24170305 | 47 |
| `waveform` | 37 | 24 min | 24170303 to 24170305 | 41 |
| `uart-rx` (covers with yices alone) | 36 | 15 min | 24169177 | 37 |
| `uart-rx-idle` (not committed at `24f31f0`) | 38 | 21 min | 24198439 | 38 |
| `uart-rx-idle` again, current `ci_prove.sh` | 38 | 31 min | 24206921 | 38 |
| the 12 lemma tasks (`ci_lemmas.sh`) | 12 | 34 s | 24197821 | 12 |

In `jtag` and `waveform`, the cover of the first chunk of the START segment
ran out of memory with two solvers; `ci_prove.sh` reran it with yices alone
and it passed. In `uart-rx-idle` the same happened, in both runs, to the
first chunk covers of n2 and n4 (boolector was killed; the yices reruns
passed in 482 s and 654 s, then in 653 s and 893 s): 5 of 5 segments
proved, 5 of 5 covers reached, 4 of 4 negative controls of BMC depth at most
98 failed as required. The second run's summary reports 2 of 2 split
segments (the first one's said 5 of 5, because `ci_prove.sh` then kept the
whole-segment entries in its chunk manifest; it now drops them, as
`campaign.sh prepare` does). With the RTL generation (about a minute with
the opam cache, as in the `formal` workflow) and the OSS CAD Suite setup,
one image takes half an hour or more on these nodes. The slowest rate
measured there is the second `uart-rx-idle` run's, 1,887 s for 38 runs.

**Measured on GitHub** (dispatched `certs` runs on the branch
`ci/certs-yices-covers`, which carried this `ci_prove.sh`; the covers with
yices alone):

| image | runs | proof time | run | covers in parallel |
|---|---:|---:|---|---:|
| `uart-rx-idle` | 38 | 5,949 s | 36625986442 | 2 |
| `uart-rx-idle` | 38 | 3,189 s | 36637581754 | 4 |
| `jtag` | 47 | 3,953 s | 36637581754 | 4 |
| `i2c-repeated-start` | 116 | 4,500 s | 36637581754 | 4 |

Every one passed (all segments proved, all covers reached, every negative
control that ran failed as required). GitHub's runners took about 84 s per
run on `uart-rx-idle` and `jtag`, against about 50 s on the cluster nodes, so
the budget is 150 runs: at 85 s per run that is about 12,750 s, 3 h 33 min,
under the proof job's limit of 300 minutes. The largest image at `24f31f0`
within the budget, `i2c-repeated-start`, needs 116 runs. When a change alters
`processor_fv.v`, all 19 images run, four at a time: five rounds (the slowest of the
three images measured took 4,500 s, 75 min, of proofs).

**The images added in `e64cd6b`** (in the working tree when this was
written, not committed at `24f31f0`). The `certify` campaign on commit
`6a3ea08`, which contains them, certified all seven (results.md R42c;
[`campaigns/6a3ea08.md`](../tools/timing/cert/results/campaigns/6a3ea08.md)):
91 of 91 segments, 91 covers, 53 of 53 negative controls that were run,
36 deeper ones not run. The table below is the plan it was run from.
`gen_cert.py preflight` accepts all seven. The longest segments of the
other six are 4,000 to 80,004 steps long, so they exceed the CI budget and
only the cluster campaign certifies them:

| image | segments | chunk certificates | negative controls (run / generated) | CI runs |
|---|---:|---:|---:|---:|
| `uart-rx-idle` | 5 | 14 | 8 / 8 | 38 (proved in CI) |
| `swd-read` | 6 | 172 | 11 / 13 | 362 |
| `ps2-host` | 50 | 132 | 7 / 13 | 367 |
| `ps2-device` | 18 | 532 | 10 / 16 | 1,082 |
| `ws2812` | 3 | 503 | 5 / 13 | 1,010 |
| `ws2812b-v5` | 3 | 503 | 5 / 13 | 1,010 |
| `onewire-master` | 6 | 3,438 | 7 / 13 | 6,889 |

"Run" counts the controls of at most 2,000 steps (section 5). A dry run of
`certify` for all seven (on the `24f31f0` snapshot plus these files, job
24198238) emits 63 whole-segment certificates of at most 200 steps, 2 of 201
to 2,000 steps (longer segments are proved by their chunk chains only),
5,294 chunk certificates, 5,357 covers, 53 negative controls to run (36 more
are too deep) and the 12 lemma tasks. Those 53 controls were run as a check of the generator on these
images (job 24198443, not a certification): all 53 fail as required, in at
most 46 s each. They include the `open-drain-as-push-pull` control of
`ps2-device` from node n17 (fires `pad` on pin 5) and three `branch-late`
controls on soft branch nodes.

**End-to-end test of `certify` with the retry step** (not a certification:
the `24f31f0` snapshot plus the `uart-rx-idle` image, run from a frozen copy
of these tools whose ledger is not the committed one; jobs 24206366 to
24208915). Before the retry step existed, a run of the same cover list
(job 24201555) left the cover of n2's first chunk in ERROR, so the record
job would have found the image not certified. This time the same cover
ended in ERROR again (boolector killed; the cover task reached its 32 GB
limit). The retry job found exactly that run, reran it with yices alone
(PASS in 498 s) and submitted the record job, which certified the image: 5
of 5 segments proved (all whole, the two long ones also as 2 of 2 chunk
chains), 5 of 5 covers reached, 8 of 8 negative controls failed as
required. `ledger.py check` on that copy passes, 20 of 20. The test took
about 37 minutes and about 3 CPU-hours.

**Cost of the six deep images** (a measured sample, not a certification;
jobs 24206817, 24206818 and 24208906). From the six images' chunk
certificates, 32 per image were run as the campaign runs them (`campaign.sh
submit`: the same bundles, CPUs, memory and runs in parallel per Slurm
task): per image, the first eight chunk certificates of two
long segments (so that each first chunk runs next to its neighbours, as in
the campaign) and two windows of eight at seeded random places.

- Chunk proofs: 192 of 192 pass (median 24.5 s, at most 90 s), 16,218
  allocated CPU-seconds, about 84.5 per run.
- Covers: 188 of 192 pass at the first attempt (median 148 s). The other 4
  are first-chunk covers (4 of the 13 first chunks in the sample, none of
  the 179 other chunks) whose boolector was killed; the retry step reran
  them with yices alone and all 4 pass, in 416 to 843 s. The covers took
  31,376 allocated CPU-seconds (about 163.4 per cover) and the reruns 3,380
  (845 per rerun).

The six images have 5,280 chunk certificates, 5,340 covers and 26 first
chunks. Scaled to them, with every first chunk rerun, and about 3,200
CPU-seconds for the short, negative-control, lemma and equivalence jobs
(from the `24f31f0` campaign and the test above): 1.34 million CPU-seconds,
about 373 CPU-hours. The sample holds more first chunks than the campaign
(13 of 192 against 26 of 5,280), and they are the slowest covers, so the
estimate is on the high side. With the limits below (128 CPUs: 6 for the
short list, 36 for the chunks, 68 for the covers, 4, 8 and 2 for the
negative controls, lemmas and long whole segments, 4 for the two
equivalence jobs), the chunk proofs take about 3.4 hours, the covers about
3.6 hours and the reruns under half an hour: about 4 hours if the queue
grants the CPUs.

```sh
CERT_ONLY="onewire-master ps2-device ps2-host swd-read ws2812 ws2812b-v5" \
CERT_MAX_short=1 CERT_MAX_chunks=6 CERT_MAX_cover=17 CERT_MAX_neg=1 \
CERT_MAX_lemma=1 CERT_MAX_whole_long=1 tools/timing/cert/campaign.sh certify $WORK
```
