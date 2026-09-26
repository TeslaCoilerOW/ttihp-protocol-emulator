# Timing certificates: the static schedule proved on the RTL

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

**Results (commit `c118027`, all runs on the MIT Engaging cluster; section 6).**

- All 368 segments of the 19 committed images are certified: 368 of 368
  whole-segment certificates pass. This covers 382 path variants, 1,822 issue
  slots, 796 pad events (551 of them between known levels) and 229 input
  samples.
- The 21 segments longer than 96 steps were also proved as chains of 125
  chunks. All chunks pass.
- A cover per variant (or per chunk) is reached for every segment.
- The boundary lemmas pass for all four engines, and the SRAM macro lemma is
  proved by IC3/PDR.
- All 132 negative controls generated from wrong analyzers or perturbed
  predictions fail as required. The two deepest (`waveform`, steps 1444 and
  1541) were found by ABC `bmc3`.
- The certificates catch three of five RTL timing bugs. Of the other two, one
  does not change any pad, and one changes only data-dependent pad levels,
  which the certificates do not pin down (section 5).
- The formal netlist and the committed `src/` top are sequentially equivalent
  on the chip ports from the all-zero state (ABC `dprove`). A netlist with one
  extra input flip-flop is not equivalent; the check finds a counterexample in
  9 cycles.

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
they fail, which confirms the alignment. There are no loops inside a committed
image's segment: none of the 19 analyses has a boundary-free periodic loop or
a soft boundary, and the generator refuses those cases. Loops through
boundaries (the byte loop of every image, for example) are closed by the
graph: each node's precondition is an inductive invariant at that boundary,
and each certificate proves the step from one node to its successor.

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
`hardcaml/lib/engine.ml` cycle by cycle over the analyzer's abstract values.
It must reproduce the analyzer's issue slots and pad sets exactly, or the
generator stops. For the 382 variants of the 19 committed images (19,854
steps) it does. This is a self-check of the generator only: it reuses the
analyzer's ALU and pad helpers, so it is not an independent model. The
chunk's assertions are what the proof checks, and a wrong intermediate state
makes a chunk fail, not pass.

A split segment is proved when all its chunks are. The whole-segment
certificates of all long segments were run as well, and they pass too
(section 6).

**Covers.** One cover per variant shows that the end of the variant is
reachable in B. This shows that the assumptions admit every branch, including
data-dependent `FAULT` exits such as an I2C NACK.

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
  - It rejects soft boundaries and boundary-free periodic loops. Scalar-issue
    SPI/JTAG images and some random programs have them; no committed image
    does.
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
- **Perturbed predictions.** One prediction per image is changed in the
  cheapest node where it applies:
  - `pad-late`/`pad-early`: one known-level pad change moves one edge;
  - `pad-level`: a known level is flipped;
  - `issue-late`: in every variant, every issue after the first multi-cycle
    instruction moves one edge. All variants are changed because `flow` only
    needs one variant to match.
  - `post-limit`: the successor's LIMIT is off by one.

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

**Setup.**

- Every run used the committed tree at `c118027` (tag `v0.1-hardened`),
  exported with `git archive`, so concurrent edits to the working tree could
  not leak in.
- `formal/run.sh --generate-only` built `processor_fv.v` from that snapshot's
  `hardcaml/` (Slurm job 23974773).
- Tools: OSS CAD Suite 2026-07-29 (Yosys 0.67, SymbiYosys, yices, boolector,
  bitwuzla, ABC, rIC3).
- Compute: Slurm on `mit_normal`, `mit_preemptable` and `mit_quicktest`.
  Every job id is in the work area's `manifest.json`, and the per-certificate
  outcomes are in [`tools/timing/cert/results/`](../tools/timing/cert/results/).
- The certified images are the 19 committed at `c118027`. The I2C controller
  images in the working tree have changed since; their certificates are
  regenerated by re-running the campaign on the commit that contains them.

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
- The job ids are those of the passing proofs.

| image | engine | segments | variants | pad events | known-level edges | issue slots | input samples | longest segment | proved | whole-segment BMC | split into chunks | covers | solver time max / total (s) | solvers | Slurm jobs (proofs) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| `event-transmitter` | 0 | 2 | 2 | 3 | 3 | 8 | 0 | 34 | 2/2 | 2 | 0/0 | 2/2 | 9 / 13 | boolector, yices | 23975821 |
| `i2c-read` | 3 | 36 | 38 | 86 | 68 | 252 | 12 | 106 | 36/36 | 36 | 2/2 | 36/36 | 35 / 591 | boolector, yices | 23975821 |
| `i2c-repeated-start` | 3 | 58 | 62 | 150 | 114 | 424 | 16 | 77 | 58/58 | 58 | 0/0 | 58/58 | 48 / 1200 | boolector, yices | 23975821 |
| `i2c-target-read` | 3 | 67 | 70 | 20 | 7 | 159 | 17 | 5 | 67/67 | 67 | 0/0 | 67/67 | 6 / 246 | bitwuzla, boolector, yices | 23975821 |
| `i2c-target-write` | 3 | 48 | 49 | 8 | 8 | 104 | 16 | 6 | 48/48 | 48 | 0/0 | 48/48 | 5 / 161 | bitwuzla, boolector, yices | 23975821 |
| `i2c-write` | 3 | 25 | 27 | 66 | 48 | 175 | 4 | 106 | 25/25 | 25 | 1/1 | 25/25 | 32 / 416 | bitwuzla, boolector, yices | 23975821 |
| `jtag` | 0 | 4 | 4 | 75 | 59 | 65 | 16 | 694 | 4/4 | 4 | 3/3 | 4/4 | 756 / 1520 | bmc3, boolector, yices | 23975821, 23976555, 23983459, 23988090 |
| `spi-controller-fast` | 2 | 4 | 4 | 57 | 39 | 18 | 16 | 261 | 4/4 | 4 | 2/2 | 4/4 | 305 / 503 | boolector, yices | 23975821, 23983459 |
| `spi-controller-mode0` | 2 | 4 | 4 | 57 | 39 | 18 | 16 | 517 | 4/4 | 4 | 2/2 | 4/4 | 934 / 1836 | yices | 23975821, 23983459 |
| `spi-controller-mode1` | 2 | 4 | 4 | 57 | 39 | 18 | 16 | 517 | 4/4 | 4 | 2/2 | 4/4 | 2281 / 1.0 h | boolector, yices | 23975821, 23983459 |
| `spi-controller-mode2` | 2 | 4 | 4 | 57 | 39 | 18 | 16 | 517 | 4/4 | 4 | 2/2 | 4/4 | 2951 / 1.2 h | boolector, yices | 23975821, 23983459 |
| `spi-controller-mode3` | 2 | 4 | 4 | 57 | 39 | 18 | 16 | 517 | 4/4 | 4 | 2/2 | 4/4 | 876 / 1727 | boolector, yices | 23975821, 23983459 |
| `spi-target-mode0` | 2 | 24 | 24 | 12 | 3 | 63 | 8 | 3 | 24/24 | 24 | 0/0 | 24/24 | 4 / 76 | boolector, yices | 23975821 |
| `spi-target-mode1` | 2 | 25 | 25 | 12 | 3 | 64 | 8 | 3 | 25/25 | 25 | 0/0 | 25/25 | 5 / 88 | bitwuzla, boolector, yices | 23975821 |
| `spi-target-mode2` | 2 | 24 | 24 | 12 | 3 | 63 | 8 | 3 | 24/24 | 24 | 0/0 | 24/24 | 6 / 111 | boolector, yices | 23975821 |
| `spi-target-mode3` | 2 | 25 | 25 | 12 | 3 | 64 | 8 | 3 | 25/25 | 25 | 0/0 | 25/25 | 4 / 79 | boolector, yices | 23975821 |
| `uart-rx` | 1 | 5 | 7 | 0 | 0 | 140 | 36 | 617 | 5/5 | 5 | 2/2 | 5/5 | 1206 / 2412 | boolector, yices | 23975821, 23983459 |
| `uart-tx` | 0 | 3 | 3 | 21 | 3 | 65 | 0 | 643 | 3/3 | 3 | 2/2 | 3/3 | 1495 / 2991 | boolector, yices | 23975821, 23983459 |
| `waveform` | 0 | 2 | 2 | 34 | 34 | 86 | 0 | 1540 | 2/2 | 2 | 1/1 | 2/2 | 759 / 762 | bmc3, yices | 23975821, 23988090 |
| **total** | | 368 | 382 | 796 | 551 | 1822 | 229 | | 368/368 | 368 | 21/21 | 368/368 | | | |

Cover runs: Slurm jobs 23975822, 23983908, 23984832, 23984833, 23984834, 23988106.

The 350 segments of at most 200 steps ran as one Slurm array, `23975821`: 35
array tasks of 10 certificates each, with a bitwuzla/boolector/yices
portfolio per certificate. They took 2 to 48 s each. The 125 chunks ran in
jobs 23976167 and 23983528, 4 to 38 s each. The 18 longer segments were
also run whole, as listed next.

**Long segments (whole segment and chunks).**

| segment | last step | whole-segment BMC: solver, s, job | chunks proved | chunk time max / total (s) | chunk jobs |
|---|---:|---|---:|---:|---|
| `i2c-read` n3, n26 (pc5 `WAITPIN`) | 106 | yices, 35 / 31, 23975821 | 2/2, 2/2 | 33 / 37 | 23976167 |
| `i2c-write` n3 (pc5 `WAITPIN`) | 106 | yices, 32, 23975821 | 2/2 | 26 / 30 | 23976167 |
| `jtag` n0 (START) | 694 | ABC `bmc3`, 431, 23988090 | 8/8 | 30 / 189 | 23976167 |
| `jtag` n1, n3 (pc29 `PULL`) | 515 | boolector 329 (23976555), yices 756 (23983459) | 6/6, 6/6 | 31 / 157 | 23976167 |
| `spi-controller-fast` n1, n3 (pc3 `PULL`) | 261 | yices, 191 / 305, 23983459 | 3/3, 3/3 | 32 / 84 | 23976167 |
| `spi-controller-mode0..3` n1, n3 (pc3 `PULL`) | 517 | yices, 846 to 2951, 23983459 | 6/6 each | 37 / 173 | 23976167, 23983528 |
| `uart-rx` n2, n4 (pc4 `WAITPIN`) | 617 | yices, 1206 / 1197, 23983459 | 7/7, 7/7 | 35 / 197 | 23983528 |
| `uart-tx` n1, n2 (pc2 `PULL`) | 643 | yices, 1493 / 1495, 23983459 | 7/7, 7/7 | 31 / 189 | 23983528 |
| `waveform` n0 (START) | 1540 | ABC `bmc3`, 759, 23988090 | 17/17 | 38 / 471 | 23983528 |

**Solvers.**

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

**Lemmas.**

| harness, task | outcome | s | solver | Slurm job |
|---|---|---:|---|---|
| `boundary_lemmas` k0, k1, k2, k3 (depth 4) | PASS each | 6 each | bitwuzla | 23981752 |
| `boundary_lemmas` cover_k0..k3 (8 covers each) | PASS, 8/8 reached each | 9 to 10 | yices | 23981752 |
| `boundary_lemmas` neg | FAIL (expected), `neg_waitpin_timeout_keeps_pads` | 4 | yices | 23981752 |
| `sram_lemma` prove (IC3/PDR) | PASS | 3 | ABC `pdr` | 23981956 |
| `sram_lemma` cover | PASS | 1 | yices | 23981956 |
| `sram_lemma` neg | FAIL (expected), `neg_sram_stale` | 1 | yices | 23981956 |

**Negative controls.**

| mutation | certificates that failed / generated | assertions that fired |
|---|---:|---|
| `wait-n-cycles` (analyzer) | 8 / 8 | `flow` |
| `xfer-late-edge` (analyzer) | 6 / 6 | `pad`, `edge` on the SPI/JTAG clock pin |
| `count-n-iterations` (analyzer) | 13 / 13 | `post` (successor state), `flow` |
| `open-drain-as-push-pull` (analyzer) | 4 / 4 | `pad` on SDA |
| `halt-keeps-pins` (analyzer) | 1 / 1 | `pad`, `edge` |
| `set-two-cycles` (analyzer) | 18 / 18 | `flow`, `pad` |
| `pad-late` (prediction) | 18 / 18 | `pad`, `edge` |
| `pad-early` (prediction) | 14 / 14 | `pad` |
| `pad-level` (prediction) | 18 / 18 | `pad` |
| `issue-late` (prediction) | 13 / 13 | `flow` |
| `post-limit` (prediction) | 19 / 19 | `post` |

The main array was 23975823. The deep ones were repeated with a single
solver (23983952, 23989372, 23989005). The two `waveform` controls whose violation lies at steps 1444 and 1541
were found by ABC `bmc3` (see *Solvers*). The SMT solvers ran out of memory
before those steps.

**RTL mutants** (real certificates run on a wrong netlist; jobs 23986495 and
23986823):

| RTL mutant | image | certificates run | failed | assertions that fired |
|---|---|---:|---:|---|
| `rtl_wait_plus1` | `uart-tx` | 15 | 14 | `flow`; only the START segment has no `WAIT` and passes |
| `rtl_xfer_short_tick` | `spi-controller-mode0` | 14 | 12 | `pad`, `edge` on SCK/MOSI, `flow`; the two segments without `XFER` pass |
| `rtl_sync_3flop` | `uart-rx` | 17 | 14 | `sample`; the three segments without a sample pass |
| `rtl_od_oe_ungated` | `i2c-write` | 26 | 0 | none (not caught; section 5) |
| `rtl_od_drive_high` | `i2c-write` | 26 | 0 | none (not pad-visible; expected) |

**Equivalence of the formal netlist with the committed top.**

- `equiv_src.py` (ABC method) reports "Networks are equivalent" (job
  23987598, 43 s, 2.6 GB).
- The miter has 24,408 latches after Yosys merged structurally identical
  flip-flops of the two copies. Latch correspondence reduced them to 0.
- Earlier attempts are recorded in `manifest.json`:
  - Yosys `equiv_simple`/`equiv_induct` over the name-matched signals proved
    no pair (23975461, 23987454);
  - the first AIGER export left latches uninitialised, and ABC reported a
    spurious frame-0 difference (23984871). `setundef -init` fixed that.
- As a negative control, the same flow compares the committed top with the
  `rtl_sync_3flop` netlist and finds a counterexample in frame 9 (job
  23989727).
- The deeper `formal/` mutant netlist needs about 45 cycles to diverge
  (loading, committing and starting engine 0). A BMC run on it was stopped
  at frame 19 (job 23989344), so it gave no result either way.

**Timing isolation** (cited, not re-run). All `timing_isolation_*` jobs pass
in the GitHub `formal` workflow on `c118027`, runs 36144357811 and
36188299526:

- `prove_k0` to `prove_k3`, `bmc` and `cover`;
- the negative controls `neg_pull` and `neg_mutant` (expected failures).

These runs generate `processor_fv.v` from the same commit with the same
generator (`formal/run.sh`). The certificates' copy was generated separately
(job 23974773) and was not compared byte for byte with the CI artifact.

## 7. Reproducing

```sh
export OSS_CAD_SUITE=/path/to/oss-cad-suite OCAML_ENV=/path/to/ocaml-env.sh
tools/timing/cert/campaign.sh prepare $WORK c118027   # snapshot, RTL, certificates, task lists
tools/timing/cert/campaign.sh submit $WORK            # Slurm arrays
tools/timing/cert/campaign.sh summarize $WORK         # results.md, results.json
```

A single certificate runs without Slurm:

```sh
python3 tools/timing/cert/gen_cert.py emit firmware/uart-tx.image.json --out $WORK/certs \
    --rtl formal/build/rtl --models models
tools/timing/cert/run_one.sh $WORK/certs cert_uart_tx_n1 bmc
```

`formal/run.sh --generate-only` produces `formal/build/rtl/processor_fv.v`.

The equivalence check, the RTL mutants and the generator's self-checks:

```sh
python3 tools/timing/cert/equiv_src.py --src src --rtl formal/build/rtl --models models \
    --out $WORK/equiv --run                                  # expect EQUIVALENT
python3 tools/timing/cert/equiv_src.py --src src --rtl formal/build/rtl --models models \
    --out $WORK/equiv_neg --run --gate formal/build/rtl/processor_fv_mutant.v   # expect NOT PROVED
python3 tools/timing/cert/rtl_mutants.py $SNAPSHOT $WORK/rtlmut         # needs OCAML_ENV
python3 tools/timing/cert/gen_cert.py emit firmware/uart-tx.image.json --out $WORK/rtlneg \
    --rtl $WORK/rtlmut/rtl_wait_plus1/fbuild/rtl --models models --chunk 96 --rtl-mutant rtl_wait_plus1
python3 tools/timing/cert/gen_cert.py crosscheck firmware/*.image.json
python3 -m unittest discover -s tools/timing/cert -p 'test_*.py'
```
