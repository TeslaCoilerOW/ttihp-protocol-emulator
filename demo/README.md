# Hardware demonstration kit

Scripts, firmware and analysis tools for running the protocol emulator's
demonstrations on the FPGA prototype (`fpga/`, [docs/fpga.md](../docs/fpga.md))
with a logic analyser. The procedure, wiring, expected results and limits are
in [docs/demo.md](../docs/demo.md). Nothing here has run on hardware yet; the
validation so far is in simulation (docs/demo.md, "Validation in simulation").

| Path | Contents |
|---|---|
| `pe_demo.py` | Scenarios (timing-isolation measurement, four protocols, host-free chain) over a small chip interface; MicroPython compatible |
| `pico_demo.py` | Raspberry Pi Pico entry point (host library `host/pe_host`, host-clock build) |
| `pc_demo.py` | PC entry point for the UART-bridge builds (`fpga/host/pe_host.py`) |
| `pe_capture.py` | sigrok capture parsing (srzip, VCD, CSV), cycle recovery, edge-interval histograms, jitter statistics, comparison, figures, analyser emulation |
| `capture.sh` | sigrok-cli command lines for the captures |
| `firmware/` | Demonstration images, all assembled by `hardcaml/bin/assemble.ml` from the sources here (`build.py` regenerates and checks them) |
| `sim/` | Simulation of every scenario on the FPGA top (cocotb + Icarus), mutants, Slurm scripts, result collection |
| `tests/` | Unit tests (`python3 -m unittest discover -s demo/tests`) |
| `results/` | Collected simulation-validation results |

Quick checks without hardware:

```sh
python3 -m unittest discover -s demo/tests -v          # scenarios on the reference model, capture tools
python3 demo/pe_capture.py predict --image demo/firmware/timing-probe.image.json --json predicted.json
```
