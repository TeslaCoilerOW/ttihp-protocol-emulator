# Bug ledger

This page lists the defects that the verification process found during
development. Each entry gives what was wrong, the method that found it, the
evidence, the fix (commit) and the current status. The scope is:

- this repository's history, from `180f98d` to `c118027` (tag
  `v0.1-hardened`);
- the earlier development record of the author's private asic-lab
  monorepo, for defects in the ISA and the firmware that this repository
  imported at `180f98d`. That record is not public, so its entries cite
  Slurm job ids only.

**Classes.**

- **Design:** the RTL or the ISA.
- **Firmware:** the shipped images in `firmware/`.
- **Flow:** hardening configuration and CI.
- **Test:** tests, harnesses and verification tools of this repository.
- **Peer:** third-party models used as test peers.
- **Tool:** external EDA tools.

**No defect in the generated RTL of the design of record was found in this
repository's history** by these methods:

- the random lockstep campaign (536,064 cases, 0 failures);
- the mutation campaign;
- the `formal/` and `formal_depth/` proofs;
- the independent-peer tests;
- the variant lockstep checks.

The sources say so explicitly:
[verification-campaign.md](verification-campaign.md) (finding 1),
[formal-depth.md](formal-depth.md) ("No design bug was found"),
[variants.md](variants.md) section 7.2 and
[independent-peers.md](independent-peers.md). The design defect below
(BL-1) predates this repository. BL-2 and BL-3 are firmware.

## Summary

| ID | Class | Defect | Found by | Fix | Status at `c118027` |
|---|---|---|---|---|---|
| BL-1 | Design (ISA1) | A UART byte was lost silently while the receiver's blocking PUSH waited at a full queue | Counterexample run in the monorepo's verification (job 22624426) | ISA2 strict PUSH with fault 4; imported at `180f98d` | Fixed; regression tests in `test/` |
| BL-2 | Firmware | `i2c-repeated-start` holds SCL low for only 7 cycles before the repeated START | Static timing analysis (`pe_timing`) | none | **Open** |
| BL-3 | Firmware | I2C controllers on NACK: 2-cycle SCL runt pulse and no STOP; the SPI target's undeclared 9-cycle CS-high minimum; a wrong image name | Static timing analysis (`pe_timing`) | none | **Open** (WARN/INFO) |
| BL-4 | Flow / Tool | LibreLane 3.1.0.dev3 passes `-threads None` to OpenROAD when `OPENROAD_THREADS` is unset, so detailed routing runs on one thread | Log inspection of a local mirror run (job 23715924) | `d16a327` | Fixed |
| BL-5 | Flow | Official `gl_test` failed to elaborate: `Unknown module type: ihp_mux2/ihp_mux4` | Official `gl_test`, run 36096045527 | `88f89a1` | Fixed for `test/`; **open** for `test_ext/` |
| BL-6 | Flow | Official precheck failed its Pin check: short VPWR/VGND Metal4 straps between paired SRAM macros were exported as power ports | Official precheck, run 36096045527 | `1e2cfb3` | Fixed; confirmed by run 36144357821 |
| BL-7 | Flow | The `docs` action failed: `docs/info.md` was still the template | Official `docs` action, run 36086335680 | `d16a327` | Fixed |
| BL-8 | Test | The random test's `deselect` operation ran in 0.26% of cases instead of about 10% | Random campaign accounting | `c118027` | Fixed |
| BL-9 | Test | The random generator never produced five host-command rejection paths | Random campaign coverage holes | `c118027` | Fixed |
| BL-10 | Test | The minimizer was disabled for replayed cases | Random campaign | `c118027` | Fixed |
| BL-11 | Test | A shared simulator image was rebuilt while campaign tasks were loading it | Campaign audit | `0cd4697` | Fixed; results shown unaffected |
| BL-12 | Test | Test-suite gaps: long counters, timeouts after arbitrary instructions, parity-sensitive timing, host corner cases, mover arbitration, unobserved data | Mutation testing (573 survivors analysed) | `c118027` | Fixed in part: score 80.2% → 89.4%; 244 survivors remain |
| BL-13 | Test | Formal harness: `past_valid` used before its declaration; a negative control counted as met on any failing assertion | Review during integration | `73536f0` | Fixed |
| BL-14 | Test | `formal_depth` harnesses: vacuous mover tag claims from reset; an out-of-range initial value of a read-mask register; a missing FIFO pointer invariant | From-reset cover; k-induction and BMC counterexamples | before `0fc6fe9` | Fixed |
| BL-15 | Test | `pe_timing`: two analyzer errors and one checker error | Validation against the reference model | before `0ec5138` | Fixed |
| BL-16 | Test | Host library: a MicroPython-incompatible exception constructor; SELECT/READ_SELECT acceptance tracking; fault handling in `run_flagship(peers="external")`; test Makefile paths | MicroPython replay; independent review | before `0ec5138` | Fixed |
| BL-17 | Test | Independent-peer UART test started a frame before the receiver could see an idle line | Per-cycle trace of a failing peer test | before `27ae5e1` | Fixed |
| BL-18 | Test | With one output delay, 3 of 12 wrong-SPI-mode substitutions went undetected | Wrong-mode matrix (job 23791858) | `27ae5e1` (skew corners and edge checks) | Fixed: 12 of 12 detected |
| BL-19 | Peer | Defects in third-party peer models: nandland `SPI_Slave`, verilog-i2c `i2c_slave`, `SPI_Master_With_Single_CS`, `hazard3_jtag_dtm` | Seeded peer campaigns; code reading | Tests assert each peer's predicted behaviour (`27ae5e1`) | Upstream not fixed |
| BL-20 | Test | Sweep harness: two job arrays submitted within one second shared a list file | Audit of Slurm logs against the manifest | `d510f85` | Fixed |
| BL-21 | Firmware sketch (study only) | CAN node sketch could not receive a frame started at the legal minimum spacing | Verification pass of the extension study (jobs 23781683, 23783254) | Revision 3 of `docs/extension-study.md` (`0fc6fe9`) | Fixed in the study; not part of the design |
| BL-22 | Simulation artefact | Gate-level X-pessimism: a synchronous-clear register of `rstreg` stays X in a plain-Yosys netlist | Gate-level simulation of the variants | none needed; LibreLane-replica netlists pass | Documented |
| BL-23 | Test (monorepo) | A negative canary in the earlier formal flow was vacuous: initialization flags made its assumptions inconsistent | Independent probe (unsatisfiable without the assertion) | Corrected run 22627095 | Fixed before this repository |
| BL-24 | Tool | Observed tool failures: LibreLane `Netgen.LVS` JSON parse error after a route with shorts; sby AIGER witness replay mismatch for rIC3 and abc; avy segfault | Sweep and formal-depth runs | Worked around | Documented |

## Details

### BL-1: ISA1 UART receive overrun lost bytes silently (design)

- **What.** In ISA1, `uart-rx` used a blocking `PUSH`. When engine 1's RX
  queue was full, the engine waited at the `PUSH`. An external UART byte
  arriving during the wait was missed, and no persistent fault recorded
  it. The queue tests of the time checked conservation of *accepted* words
  only, so they could not see the loss.
- **Found by.** A counterexample run in the monorepo's verification, Slurm
  job 22624426. It reproduced a missed external UART byte during a full-queue
  wait.
- **Fix.** ISA2 added a strict `PUSH`. On a full queue the engine faults
  with code 4 and keeps the rejected word in `rx`, readable with
  READ_SELECT 6. `uart-rx` and the SPI target RX use it. The monorepo's
  native regression 22625543 then passed the overloaded bridge case:
  - eight accepted words stay queued;
  - the ninth word can be inspected;
  - fault 4 is sticky;
  - the other engines' output periods are unchanged.
- **Where now.** This repository imported the ISA2 source at `180f98d`.
  The behaviour is in [isa.md](isa.md) and in the datasheet's
  "Limitations". `test_legacy.test_legacy_strict_push` and
  `test_legacy.test_legacy_firmware_uart_overflow` test it; both pass in
  the `test` action (run 36144357839).

### BL-2: `i2c-repeated-start` short SCL low phase (firmware, open)

- **What.**
  - After the register byte's ACK clock, `DIR 0x40` at pc29 pulls SCL low.
  - `start: DIR 0x00` at pc3 releases it 7 cycles later.
  - Every other SCL low phase is at least 35 cycles.
  - At 50 MHz this is 140 ns, below tLOW for every UM10204 mode above
    14 MHz (Fm+).
  - A target that is still holding its ACK low when SCL rises would see a
    STOP instead of a repeated START.
- **Found by.** Static timing analysis. `tools/timing/pe_timing.py report`
  reports it as the only FAIL among 241 checks: `i2c-repeated-start`
  `i2c-scl-low-phase`. See [timing-analysis.md](timing-analysis.md),
  finding 1; the analyzer was added in `0ec5138`.
- **Why simulation missed it.** The test peers release SDA immediately.
  The cocotb suite and the third-party I2C peers pass with this image.
- **Fix.** None at `c118027`. The analysis suggests one `WAIT 32` on the
  `JZ tx, start` path; the image has one free word. After a fix, re-run
  `pe_timing report`, the cocotb suite and `test_ext/`.

### BL-3: other firmware timing findings (open)

These come from [timing-analysis.md](timing-analysis.md), findings 2, 4
and 7:

- **NACK runt pulse (WARN).** On NACK (fault 65), the I2C controllers pull
  SCL low and release the bus 2 cycles later. That is a 40 ns SCL pulse at
  50 MHz, with no STOP. It happens at pc28 in `i2c-read`, pc29 in
  `i2c-repeated-start` and pc28/51 in `i2c-write`.
- **Undeclared SPI-target constraint.** The SPI target needs CS high for at
  least 9 cycles between bytes. The notes do not declare this; the margin
  sweep confirms 9 exactly.
- **Wrong image name (INFO).** `firmware/spi-controller-fast.image.json`
  carries `"name": "spi-controller-mode0"`.

**Related.** A third-party I2C target showed that `i2c-repeated-start`
leaves the bus inside a started transaction, SCL low, while it waits for
the next TX words ([independent-peers.md](independent-peers.md),
"`i2c-repeated-start` keeps the bus started between transactions").
This is recorded as a firmware property, not as a failing test.

### BL-4: `-threads None` in LibreLane 3.1.0.dev3 (flow / tool)

- **What.** LibreLane 3.1.0.dev3 builds OpenROAD's thread argument from
  `OPENROAD_THREADS`. When the key is unset, every OpenROAD step gets
  `-threads None` (ORD-0032), and detailed routing runs on one thread.
- **Found by.** Reading the log of the first local run that mirrors the gds
  action, `run1` (job 23715924). It was still in detailed routing on one
  thread after about 20 minutes ([hardening.md](hardening.md), section 7).
- **Consequence in CI.** The `gds` job of run
  [36086335671](https://github.com/TeslaCoilerOW/ttihp-protocol-emulator/actions/runs/36086335671)
  on `180f98d` did not set the key. It was cancelled when it reached
  GitHub's 6-hour job limit.
- **Fix.** `"OPENROAD_THREADS": 4` in `src/config.json`, commit `d16a327`.
- **After the fix.** The `gds` job completed in 4 h 34 min on `73536f0`
  (run 36096045527). With the Magic DRC step also removed (BL-6), it took
  1 h 53 min on `c118027` (run 36144357821).

### BL-5: missing IHP UDP primitives in gate-level simulation (flow)

- **What.** The PDK revision that the gds action installs,
  IHP-Open-PDK `2bbec755`, defines the `ihp_mux2`/`ihp_mux4` UDPs in
  `sg13cmos5l_udp.v`. Older revisions defined them in
  `sg13cmos5l_stdcell.v`. `test/Makefile` did not read the new file.
- **Found by.** The official `gl_test` job of run
  [36096045527](https://github.com/TeslaCoilerOW/ttihp-protocol-emulator/actions/runs/36096045527)
  on `73536f0` failed with `Unknown module type: ihp_mux4`.
- **Fix.** `88f89a1` adds the file when it is present.
- **Verified.**
  - Locally on the exact CI netlist with Icarus 13.0: 22 pass, 17 skip,
    0 fail, with the 39-test suite of the time.
  - Officially, by the `gl_test` job of run 36144357821 on `c118027`:
    66 tests, 36 pass, 30 skip, 0 fail.
- **Open.** `test_ext/Makefile` has the same gate-level source list without
  the UDP file. Slurm job 23975124 ran `make GATES=yes
  COCOTB_TEST_MODULES=test_ext_uart` in `test_ext/` with the action's PDK
  revision and the CI netlist of run 36144357821:
  - as committed, it failed to elaborate with `Unknown module type:
    ihp_mux4`;
  - with the `test/Makefile` line added to a copy of `test_ext/Makefile`,
    5 of 5 tests passed.

  The earlier 65/65 gate-level result used a PDK copy that defines the UDPs
  inline. The fix is that one line in `test_ext/Makefile`; it is not applied
  at `c118027`.

### BL-6: precheck Pin check, short power straps (flow)

- **What.**
  - Eight LEF errors: "Port VGND/VDPWR is too far from top/bottom edge of
    module: 627.26 > 10 um" (and 623.48).
  - OpenROAD's PDN generator added short VPWR/VGND Metal4 strap pairs in
    the 32.96 um channels between paired SRAM macros. Standard-cell rows
    outside the macro halos needed them.
  - Magic's LEF writer exported the straps as power ports. The precheck
    requires every Metal4 power port to reach within 10 um of both the top
    and the bottom edge.
  - The PDN wrapper's own check (check 3 in `src/sram_pdn_cfg.tcl`) looks
    only at stripes at least 100 um tall, so it let the straps through.
- **Found by.** The official `precheck` job of run 36096045527 on
  `73536f0`. The diagnosis, with the PDN step reproduced from scratch
  (job 23808070), is in [drc-triage.md](drc-triage.md), section 6.
- **Fix.** `1e2cfb3`:
  - `FP_MACRO_HORIZONTAL_HALO` 16.48, half the gap, so the halos meet and
    no rows or straps are generated there;
  - `RUN_MAGIC_DRC` false, which saves about 40 minutes of the 6-hour
    runner limit (drc-triage.md, section 7).
- **Verified.**
  - Locally: full run job 23850490, then the unmodified precheck passes
    9 of 9 checks (job 23856538).
  - Officially: the `precheck` job of run 36144357821 passed.
- **Open.** Extending the wrapper's check 3 to every stripe, as
  drc-triage.md recommends, is not done at `c118027`.

### BL-7: datasheet still the template (flow)

- **What.** The `docs` action on `180f98d` (run
  [36086335680](https://github.com/TeslaCoilerOW/ttihp-protocol-emulator/actions/runs/36086335680))
  failed with "Missing 'How it works' section" and "Missing 'How to test'
  section" in `docs/info.md`.
- **Fix.** The datasheet was written in `d16a327`.
- **Verified.** The `docs` action has passed on every later push.

### BL-8 to BL-11: random-campaign findings in the test infrastructure (test)

All four are from [verification-campaign.md](verification-campaign.md),
"Random differential campaign", findings 2, 3, 6 and 7.

- **BL-8, `deselect`.** `make_case` appended the `deselect` operation to
  10% of cases after the cycle budget, and `run_case` stopped at the
  budget. Only 669 of 262,144 `rtl-default` cases executed a deselect
  (0.26%), and the long campaigns executed none. Mid-run deselection, which
  must stop engines, invalidate images and clear queues, was therefore
  rarely tested.
  - Found by counting executed operations across the campaign.
  - Fixed in `c118027` (generator generation 2): the deselect moves to a
    random point in the first 70% of the traffic, drawn from a separate
    random stream. `PE_RANDOM_GEN=1` keeps the old cases.
  - The campaign's `deselect` variant (16,384 RTL and 1,024 gate-level
    cases) passed before the fix.
- **BL-9, rejection paths.** The generator emitted only well-formed
  commands. `BEGIN rejected`, `COMMIT rejected`, `STOP rejected`,
  `ROUTE rejected` and `EVENT rejected` were never hit in 393,216 default
  cases.
  - Found as holes in the merged coverage.
  - The `hostile` variant hit them 91,467 to 181,965 times with no
    mismatch.
  - Fixed in `c118027`: generation 2 inserts one to three such sequences in
    70% of cases.
- **BL-10, minimizer.** `test_random.report_failure` returned before
  minimizing whenever `PE_REPLAY` was set, so a saved case could not be
  shrunk.
  - Fixed in `c118027`: `PE_MINIMIZE` now shrinks replays too.
- **BL-11, shared simulator image.** A negative-control run rebuilt the
  shared RTL `sim.vvp` in place while campaign tasks were loading it.
  - A fresh build (job 23752228) was identical after normalizing paths and
    labels, and no seed recorded an infrastructure error, so the results
    are unaffected.
  - `upstream_run.sh` builds in a private copy since `0cd4697`.

### BL-12: gaps found by mutation testing (test)

- **What.** 573 of 2,420 mutants survived the 39-test suite. 116 of them
  were proven equivalent. The survivor analysis, with a formal equivalence
  check, a reach/infect/propagate simulation, deep random and directed
  tests, grouped the gaps as follows:
  - long-horizon counters (completed instructions, LIMIT/blocked cycles,
    WAIT timer, COUNT/LOOP, ROUTE counts, timestamp) never reached high
    bits or were never read back;
  - timeouts directly after arbitrary instructions;
  - parity-sensitive timing (odd XFER half-periods of 128 or more, LIMIT
    values that are not multiples of 32);
  - host command corner cases per engine and pin;
  - mover arbitration with competing routes;
  - computed data that never reaches a pin or the host.

  See [verification-campaign.md](verification-campaign.md), "Test gaps".
- **Fix.** `c118027` adds `test_directed.py` (the 12 directed tests plus
  2), `test_counters.py`, `test_timewarp.py`, `test_mover.py` and
  generator generation 2. The mutation score rose from 80.2%
  (1,847 / 2,304) to 89.4% (2,060 / 2,304); it is 85.8% without the
  white-box time warp.
- **Remaining.** 244 survivors are neither killed nor proven equivalent,
  71 of them in `engine_ctrl` (55 on `blocked_cycles`). Three mutants that
  old random cases killed survive the new random stimulus: 2341, 2391 and
  2394.

### BL-13: formal harness corrections at integration (test)

- **What.** Two corrections, both in `73536f0`:
  - `past_valid` was used before its declaration in a harness;
  - `formal/run.sh` counted a negative control as met when *any* assertion
    failed, including the observation-port sanity check.
- **Found by.** Review while integrating the formal suite into CI.
- **Fix.** `73536f0` declares `past_valid` first. `run.sh` now requires the
  failing assertion to be a pin-equality assertion (a line containing
  `& own`).

### BL-14: formal-depth harness defects (test)

See [formal-depth.md](formal-depth.md), "Notes on the table". No design bug
was found.

- **Vacuous mover claims.** The first mover harness dropped *unarmed* tags
  on reset. The first cycle is a reset, so every tag claim was vacuous from
  reset, and its BMC and prove tasks passed anyway. The from-reset cover
  (`mover_cover`, depth 64, job 23756748_24) found it, because no tag
  could ever be armed.
  - All mover results quoted come from the fixed harness (jobs 23760772,
    23760775, 23760892). The earlier results are set aside.
- **Two more harness bugs.** k-induction and BMC counterexamples found
  both:
  - an out-of-range initial value of a read-mask register in
    `host_protocol.sv`;
  - a missing FIFO pointer/level consistency invariant.
- **Fix.** All three were fixed before the harnesses were committed in
  `0fc6fe9`.

### BL-15: timing-analyzer defects (test)

See [timing-analysis.md](timing-analysis.md), "Bugs this found in the
analyzer". Validation against the reference model found three errors:

- **Look-back.** A 1-cycle SCL drive before a `WAITPIN` was assumed to be
  visible. The margin runs of `i2c-target-write` showed the wait completing
  earlier than predicted. The fix is a 3-edge look-back.
- **Loop exits.** Periodic loops with a data-dependent exit were missing
  their later-iteration exits. 27 random programs exposed it. The fix is
  soft boundaries.
- **LIMIT=1 timeouts.** A checker error rejected a LIMIT=1 wait that times
  out on its arrival edge.

All were fixed before `0ec5138`. The published validation (R41 in
[results.md](results.md)) uses the fixed code. Its 7 deliberately wrong
analyzers are all caught.

### BL-16: host-library defects (test)

The source is [host.md](host.md), "Verification of the library", and the
review rounds recorded in `<work dir>/host/manifest.json`. All were fixed
before `host/` was committed in `0ec5138`:

- **MicroPython replay.** An exception subclass called
  `HostError.__init__(self, ...)`, which raises AttributeError on
  MicroPython. It now calls `super().__init__`, and `tools/upy_check.py`
  flags the pattern.
- **Independent review, round 1.**
  - SELECT/READ_SELECT acceptance and selection tracking are now static
    and checked by an acceptance oracle. A mutant with the old rule is
    caught.
  - The `rtl_cocotb` Makefile now resolves paths from its own location,
    not from `$PWD`.
- **Independent review, round 2.** `run_flagship(peers="external")` now:
  - clears engine 1's expected fault 3 after STOP;
  - reads the host fault from `uo[7]`;
  - reports it as not checked on 6-bit `uo` ports.

### BL-17: independent-peer UART test stimulus (test)

- **What.** In the first runs, `uart-rx` lost the first byte whenever the
  Python `UartSource` began its start bit within a few clocks of START. The
  firmware waits for an idle line and then a falling edge. A line that is
  already low when it starts cannot be told from a frame in progress.
- **Found by.** A per-cycle trace of the engine PC and pin 1.
- **Fix.** The suite idles the line for one bit period after START. This
  was a test error, not a design or firmware defect
  ([independent-peers.md](independent-peers.md)).

### BL-18: SPI clock-phase blind spots in the peer bench (test)

- **What.** With one output delay for every DUT pin (the "old board"),
  3 of the 12 substitutions of one SPI mode's program for another were not
  detected, and 5 more were caught only by a peer's own defect.
- **Found by.** The wrong-mode matrix (job 23791858).
- **Fix.** `27ae5e1` adds two signal-skew corners and protocol edge
  checks. The third-party peers alone now detect all 12 substitutions
  ([independent-peers.md](independent-peers.md), "Clock phase").

### BL-19: defects in third-party peer models (peer)

These are defects in the vendored models, not in this design. The tests
assert each peer's predicted behaviour, so a change on either side shows up.
See [independent-peers.md](independent-peers.md).

- **nandland `SPI_Slave`.** Both its MOSI sampler and its MISO shifter
  are clocked on one edge that depends on CPHA only; `w_CPOL` is computed
  but never used (upstream issue #5). After the preload has put bit 7 on
  MISO, its shift counter still starts at bit 7 (upstream issue #11). The
  DUT controller therefore read other MISO bytes than the peer was given,
  exactly as predicted from the peer's source. An independent controller
  model (cocotbext-spi `SpiMaster`) reads the same bytes from it with the
  DUT idle.
- **verilog-i2c `i2c_slave`.**
  - While stretching, it holds SCL low with SDA at the ACK level. It then
    releases SCL and drives bit 7 in the same clock. With bit 7 = 1 this is
    a STOP condition, which the model detects itself; it then reads 0xFF.
  - Found by the first seeded campaign (arrays 23761868 and 23761869):
    113 of 256 RTL runs and 43 of 88 gate-level runs of two tests failed.
  - Confirmed without the DUT: the same author's `i2c_master` reads 0xFF
    for 0xC3.
- **`SPI_Master_With_Single_CS`.** It sizes a counter with
  `$clog2(CS_INACTIVE_CLKS)`, so a power-of-two value truncates to 0.
  Found by reading the code.
- **`hazard3_jtag_dtm`.** It captures the current IR in Capture-IR instead
  of the IEEE 1149.1 pattern ending in `01`. Found by reading the code.

### BL-20: sweep-harness list-file collision (test)

- **What.** Array list files were named `<group>.<time>.list`. Two arrays
  of one group submitted within the same second overwrote each other's
  list:
  - two runs were duplicated;
  - two tasks read empty lines and failed.
- **Found by.** An audit of every Slurm log against the manifest; 82 jobs
  checked.
- **Fix.** The list names now carry the process id and a content hash, and
  the file is created with `O_EXCL`. See [sweep.md](sweep.md), "Harness bug
  found and fixed"; committed in `d510f85`.

### BL-21: CAN minimum-spacing bug in the extension study (study firmware)

- **What.**
  - Revision 2 of the CAN node sketch ended its TX tail with a
    drive-and-sample `XFER 14`. That completes in the middle of the first
    bit after intermission, exactly where another node with a pending frame
    sends its SOF.
  - `WAITPIN` caught such an SOF half a bit late, and the node faulted 71.
  - Revision 2's tests had started the other node's frames later than the
    legal minimum spacing, which hid the bug.
- **Found by.** A verification pass of the study (jobs 23781683 and
  23783254).
- **Fix.** Revision 3 of [extension-study.md](extension-study.md) (committed
  in `0fc6fe9`). A behavioural CAN node now starts frames at the minimum
  spacing, and the revision-2 listing is kept as a negative control that
  fails.
- **Scope.** The prototype is not part of the chip or of `firmware/`.

### BL-22: gate-level X-pessimism in a plain-Yosys `rstreg` netlist (simulation artefact)

- **What.** With a synchronous clear, flip-flops have `RESET_B` tied high
  and power up X in simulation. Synthesis may implement a cleared
  register's next state as reconvergent logic that is 0 in hardware but X
  in simulation, so the register never leaves X. One such register in a
  plain-Yosys netlist of `rstreg` failed the gate-level read-back of
  READ_SELECT 5.
- **Found by.** Gate-level simulation of the variants
  ([test/README.md](../test/README.md), "Design variants").
- **Status.** This is not a hardware defect. The LibreLane-replica netlists
  of all six variants pass the gate-level subset (job 23761207), and so
  does the official hardened netlist of the design of record (R3 in
  [results.md](results.md)).
- **Earlier record.** The monorepo recorded the same class of four-state
  reset pessimism on a Yosys 0.67 mapped netlist (diagnostic 22628927).

### BL-23: vacuous negative canary in the earlier formal flow (test, monorepo)

- **What.** In the monorepo's mapped-netlist equivalence flow, initialization
  flags made the negative canary's assumptions inconsistent. The canary
  could not fail for the right reason.
- **Found by.** An independent probe was unsatisfiable even without the
  canary's assertion.
- **Fix.** Supported PDR with Bitwuzla witness replay passed the positive
  canary and rejected the inverted negative canary with a defined reset
  counterexample (job 22627095). This predates this repository. Its
  practice appears here as negative controls that must fail on a named
  assertion (BL-13, BL-14).

### BL-24: external tool failures observed (tool)

- **LibreLane 3.1.0.dev3 `Netgen.LVS`.** It raised a Python
  `JSONDecodeError` ("Invalid \escape") while parsing netgen's statistics
  after a route with residual shorts (`cn` full run, 23751800_0). LVS then
  gave no count at all ([sweep.md](sweep.md)).
- **sby AIGER witness replay.** It fails for rIC3 and abc on the
  timing-isolation miter ("witness signal mismatch for a.dut.ena"). sby then
  reports ERROR although the engine's verdict is FAIL. btormc gives a full
  trace (job 23770778; [formal-depth.md](formal-depth.md)).
- **avy.** It crashes with a segfault on these models.
