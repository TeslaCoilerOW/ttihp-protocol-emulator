# Standards-based reference of the line unit (`stdref`)

An oracle for the pads of the `diet8_rec16` line unit that is independent of
the RTL and of the lockstep model:

| File | Contents |
|---|---|
| `../model/line_std_ref.py` | The reference: CRC by polynomial division over GF(2) (Williams model, reveng catalogue parameters) and the contract's bit-serial rule for the four presets in both bit orders; NRZ, NRZI (USB 2.0 7.1.8), Manchester (IEEE 802.3 7.3.1.1: complement, then the bit); bit stuffing and destuffing by polarity and run length (USB 2.0 7.1.9, CAN 2.0 part A section 5); the ticker T(k) from the contract's recursion; the line unit of one engine at tick level (`LineUnitRef`); the pad-trace decoder `decode_pad_trace` (pin traces plus LTIM/LCFG in, line bits, data bits, stuff cells, stuff errors, SE0, CRC and timing violations out); USB, CAN 2.0A and Ethernet frame builders. |
| `test_*.py` | Standalone pytest tests of the reference against published vectors: reveng check and residue values, the USB-IF CRC paper's token and data examples, `binascii.crc_hqx`/`crc32`, USB SYNC/NRZI/stuffing rules, CAN stuffing and the Bosch CRC shift register, the ticker formula, the decoder's error reports, and four `firmware/ext` demos (all but `relay`) run on the reference and decoded into standard frames. |
| `ticker_closed_form.lean` | Proof that the ticker recursion equals T(k) = T(0) + k*P + floor(k*Q/256) (Lean 4 + Mathlib). |
| `program.py` | Cycle schedule of one engine's program from the contract (base-ISA instructions and the line unit). |
| `cases.py` | Seeded random line programs (scenarios `tx`, `rx`, `loop`, `mixed`) and the testbench line that plays standard-encoded stimuli on the tick schedule. |
| `demos.py` | Stimuli and expected frames for the demos (Ethernet/UDP frame, USB IN token, CAN 2.0A nodes on a wired-AND bus). |
| `chip.py` | A host driver for the Tiny Tapeout top without a reference model. |
| `../test_line_stdref.py` | The cocotb module: RTL or gate-level netlist against this reference only. |

## Provenance

The reference and its standalone tests were written from `docs/isa.md`
(section "Line-unit extension" and the base-ISA sections it refers to),
`docs/extension.md` sections 1 to 3.3, the `firmware/ext/` listings and public
standards (reveng CRC catalogue, USB 2.0 and the USB-IF CRC paper, Bosch CAN
2.0, IEEE 802.3). The Hardcaml RTL, `test/model/line_unit.py`,
`formal/line_ref.vh` and the other line-unit tests were not read until those
tests passed. The cocotb module then ran on the RTL; each point below that the
contract leaves open became a named field of `line_std_ref.Interpretation`,
measured on the RTL by `test_contract_open_points`. The field defaults are
the readings chosen before the RTL ran; `RTL_READING` in
`../test_line_stdref.py` holds the readings the RTL follows.

## Running

```sh
python3 -m pytest test/stdref -q                           # reference only, no simulator
cd test && make COCOTB_TEST_MODULES=test_line_stdref       # RTL, PE_VARIANT=diet8_rec16
cd test && make GATES=yes COCOTB_TEST_MODULES=test_line_stdref   # gate level
```

`PE_STDREF_CASES` (default 2000 at RTL, 4 at gate level), `PE_STDREF_FIRST`,
`PE_STDREF_SEED` and `PE_STDREF_MAX_FAILURES` select the random cases;
`PE_LINE_GL_FULL=1` runs the CAN demo and both USB data sets at gate level.
The module is skipped on variants other than `diet8_rec16`.

## What the module compares

For every engine of every case: `uio_out` and `uio_oe` of the engine's pins in
every cycle from START to the end of its program; every driven line XFER
decoded back from the pads with `decode_pad_trace` (cell grid from LTIM, line
code and pair from LCFG); the pushed words (CRC, LSTAT, rx; TIME values up to
one common offset); the TX queue level and the fault status. In the `rx`
scenario the testbench plays the line from the standards' encoders at the
contract's sample ticks; in about half of the `rx` engines (`strobe`) the pins carry
the complement in every cycle except the sample cycle, so a sample taken one
cycle early or late reads the wrong level. The demos are checked against
frames built by the standard encoders (UDP/IPv4 in an Ethernet frame with its
FCS, a USB DATA1 packet with CRC16 after an IN token with CRC5, CAN 2.0A frames
with CRC-15, ACK and arbitration).

## Points the contract leaves open

| Point | Contract text | Readings | RTL (and lockstep model) |
|---|---|---|---|
| LSTAT[5] after sampling | "[5] line level" | a sample sets it / only driving sets it | only driving and LCFG set it |
| NRZI previous sample after a drive-only XFER | "a 1 when it equals the previous sample" | the driven level counts / only samples count | only samples (and LCFG's initial level) |
| Run counter after a wrong stuff bit | "sets the stuff-error flag and is still dropped" | as if right / one bit of the received level / `reset` / continue the run | `reset`: either polarity, one bit of the received level; runs of 1s, no 1s |
| SE0 end with LCFG pair clear | "both pair pins low when SE0 end is set" | pair needed / not needed | pair needed |
| Pair-pin rule in a sample-only XFER | "the data pin not owned when driving, or with the pair set a pair pin that is not owned or equals the data pin" | applies / only when driving | only when driving |
| LCFG bit 3, run length 1, stuffing off | field table: "length 1 with [3] set is invalid"; invalid encodings: "stuffing on either polarity with run length 1" | faults / does not fault | does not fault |
| Classic XFER, drive and sample, c bit 6 | "feeds the bits it shifts out, or the bits it samples" | shifted-out bits / sampled bits | sampled bits |
| LCFG before a pending Manchester second half | LCFG "resets the line state" | keep / cancel | cancel |
| LTIM before a pending Manchester second half | "writes the bit itself at the next mid-bit tick" | keep the old tick / cancel / the restarted ticker's T(1) | T(1) of the restarted ticker |

Readings on which the RTL matched the default: a line XFER issued in the cycle
of a tick uses the next tick of that parity; SE0 is read on the RX field's pin
and the pair pin; a drive-and-sample XFER counts stuffing runs and feeds the
CRC with the received bits; the arbitration check also applies in stuff cells;
after a lost arbitration later XFERs also drive 1; LCFG's initial level is not
a previous bit of a stuffing run.

## Disagreement with the contract

After an SE0 in the cell of a due trailing stuff bit (all data bits received),
the contract ("[13:8] data bits remaining at SE0"; stuff bits are not
counted) gives 0; the RTL and the lockstep model report 1.
`test_se0_in_trailing_stuff_cell_leaves_no_data_bits` reproduces it; the
reading `se0_left_counts_trailing_stuff=True` describes the RTL.
