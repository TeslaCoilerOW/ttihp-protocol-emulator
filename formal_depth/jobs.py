"""Job table for formal_depth/portfolio.py.

Paths use placeholders expanded by path():
  {SNAP}   an export of the committed tree (git archive 73536f0)
  {RTL}    {SNAP}'s formal/run.sh --generate-only output (processor_fv.v, ...)
  {FDRTL}  generate_fd output (processor_fd.v and its mutants)
  {FD}     this directory (formal_depth/)
The formal/ harnesses are read from {SNAP}/formal, unchanged.
"""
import os

POOL = os.environ.get("FD_ROOT") or os.path.join(os.environ.get("PE_WORK", "."), "formal-depth")
ROOTS = {
    "SNAP": os.environ.get("FD_SNAP", POOL + "/snap"),
    "RTL": os.environ.get("FD_RTL", POOL + "/fbuild/rtl"),
    "FDRTL": os.environ.get("FD_FDRTL", POOL + "/rtl"),
    "FD": os.path.dirname(os.path.abspath(__file__)),
}
SBY_BIN = os.path.join(os.environ.get("OSS_CAD_SUITE", ""), "bin") if os.environ.get("OSS_CAD_SUITE") else ""
DEFAULT_TIMEOUT = 41400  # 11.5 h: fits mit_normal's 12 h limit


def path(p):
    return p.format(**ROOTS)


MODELS = ["{SNAP}/models/RM_IHPSG13_1P_core_behavioral.v", "{SNAP}/models/RM_IHPSG13_1P_64x16_c2.v"]
READ_MODELS = "read_verilog -DFUNCTIONAL -DSYNTHESIS RM_IHPSG13_1P_core_behavioral.v RM_IHPSG13_1P_64x16_c2.v"

BMC_ALL = ["smtbmc yices", "smtbmc boolector", "smtbmc bitwuzla", "smtbmc z3",
           "abc bmc3", "btor btormc", "btor pono", "aiger rIC3"]
PROVE_ALL = ["abc pdr", "aiger rIC3", "aiger avy", "aiger suprove"]
# avy segfaults (rc 139) on these models, so later jobs leave it out.

JOBS = []


def job(name, **kw):
    kw["name"] = name
    JOBS.append(kw)


# ---------------------------------------------------------------- deeper runs
# of the formal/ jobs (harnesses unchanged, read from the snapshot).

TI = dict(files=MODELS + ["{RTL}/processor_fv.v", "{SNAP}/formal/timing_isolation.sv"],
          script=[READ_MODELS, "read_verilog processor_fv.v",
                  "read -formal timing_isolation.sv",
                  "chparam -set WIDTH 32 -set ENGINES 4 -set DEPTH 8 timing_isolation",
                  "prep -top timing_isolation"])
job("deep_timing_isolation_bmc60", mode="bmc", depth=60, cls="heavy", engines=BMC_ALL, **TI)

ES = dict(files=["{RTL}/engine.v", "{SNAP}/formal/engine_safety.sv"],
          script=["read_verilog engine.v", "read -formal engine_safety.sv",
                  "chparam -set WIDTH 32 -set ENGINES 4 engine_safety",
                  "prep -top engine_safety"])
job("deep_engine_safety_bmc64", mode="bmc", depth=64, engines=BMC_ALL, **ES)
job("deep_engine_safety_prove", mode="prove", depth=16, engines=PROVE_ALL, **ES)
for k in (8, 16, 32):
    job(f"deep_engine_safety_kind{k}", mode="prove", depth=k,
        engines=["smtbmc yices", "smtbmc bitwuzla"], **ES)

PI = dict(files=MODELS + ["{RTL}/processor_debug.v", "{SNAP}/formal/processor_invariants.sv"],
          script=[READ_MODELS, "read_verilog processor_debug.v",
                  "read -formal processor_invariants.sv",
                  "chparam -set WIDTH 32 -set ENGINES 4 -set DEPTH 8 processor_invariants",
                  "prep -top processor_invariants"])
job("deep_processor_invariants_bmc64", mode="bmc", depth=64, cls="heavy", engines=BMC_ALL, **PI)

PD = dict(files=MODELS + ["{RTL}/processor_debug.v", "{SNAP}/formal/processor_inductive.sv"],
          script=[READ_MODELS, "read_verilog processor_debug.v",
                  "read -formal processor_inductive.sv",
                  "chparam -set WIDTH 32 -set ENGINES 4 -set DEPTH 8 processor_inductive",
                  "prep -top processor_inductive"])
job("deep_processor_inductive_bmc48", mode="bmc", depth=48, cls="heavy", engines=BMC_ALL, **PD)

FC = dict(files=["{RTL}/fifo.v", "{SNAP}/formal/fifo_conservation.sv"],
          script=["read_verilog fifo.v", "read -formal fifo_conservation.sv",
                  "chparam -set WIDTH 32 -set DEPTH 8 fifo_conservation",
                  "prep -top fifo_conservation"])
job("deep_fifo_conservation_prove", mode="prove", depth=8,
    engines=["abc pdr", "aiger rIC3", "aiger avy", "aiger suprove"], **FC)

# ---------------------------------------------------------------- new
# properties over protocol_processor_fd (formal_depth/harness/).

def fd(harness, rtl="processor_fd.v", defines=()):
    d = " ".join("-D" + x for x in defines)
    # Variable part-selects in the harnesses become $shiftx cells whose
    # out-of-range fill is 'x', which the AIGER flows reject. Every index used
    # is in range, so the fill is never observed; it is mapped to logic and
    # tied to 0 for all engines alike.
    return dict(files=MODELS + ["{FDRTL}/" + rtl, "{FD}/harness/fd_dut.vh",
                                "{FD}/harness/fd_invariants.vh",
                                "{FD}/harness/" + harness + ".sv"],
                script=[READ_MODELS, "read_verilog " + rtl,
                        f"read -formal {d} {harness}.sv".replace("  ", " "),
                        "prep -top " + harness,
                        "techmap -map +/techmap.v t:$shiftx", "setundef -zero", "opt_clean"])


def mutant(harness, name, depth, defines=("FD_SPEC_ONLY",), cls="light"):
    job(f"neg_{name}", mode="bmc", depth=depth, expect="FAIL", cls=cls,
        engines=["smtbmc yices", "smtbmc bitwuzla"],
        **fd(harness, rtl=f"processor_fd_{name}.v", defines=defines))


KIND = ["smtbmc yices", "smtbmc bitwuzla", "smtbmc boolector"]
IC3 = ["abc pdr", "aiger rIC3", "aiger suprove"]
BMC = ["smtbmc yices", "smtbmc bitwuzla", "smtbmc boolector", "abc bmc3", "btor btormc"]
COVER = ["smtbmc yices", "smtbmc bitwuzla"]

# Host port atomicity and read snapshots.
job("host_prove", mode="prove", depth=4, engines=KIND + IC3, **fd("host_protocol"))
job("host_bmc40", mode="bmc", depth=40, cls="heavy", engines=BMC, **fd("host_protocol"))
job("host_cover", mode="cover", depth=60, cls="heavy", engines=COVER, **fd("host_protocol"))
mutant("host_protocol", "host_early_commit", 24)
mutant("host_protocol", "host_keep_partial", 24)
mutant("host_protocol", "host_live_read", 24)

# Program-load safety. k-induction without the SRAM-data claim (it needs the
# macro array, which no port observes); IC3 and BMC with it.
job("load_prove", mode="prove", depth=4, engines=KIND, **fd("program_load", defines=["FD_NO_WORD"]))
job("load_prove_word", mode="prove", depth=4, engines=IC3, **fd("program_load"))
job("load_bmc48", mode="bmc", depth=48, cls="heavy", engines=BMC, **fd("program_load"))
job("load_cover", mode="cover", depth=56, cls="heavy", engines=COVER, **fd("program_load"))
mutant("program_load", "load_partial_commit", 32)
mutant("program_load", "load_start_uncommitted", 20)

# Mover conservation, quota and order.
job("mover_prove", mode="prove", depth=4, engines=KIND + IC3, **fd("mover"))
job("mover_bmc40", mode="bmc", depth=40, cls="heavy", engines=BMC, **fd("mover"))
job("mover_any_bmc24", mode="bmc", depth=24, cls="heavy", engines=BMC,
    **fd("mover", defines=["FD_FROM_ANY"]))
job("mover_any_cover", mode="cover", depth=16, engines=COVER, **fd("mover", defines=["FD_FROM_ANY"]))
job("mover_cover", mode="cover", depth=64, cls="heavy", engines=COVER, **fd("mover"))
for m in ("mover_wrong_data", "mover_quota_on_eligible", "mover_no_pop"):
    mutant("mover", m, 12, defines=("FD_SPEC_ONLY", "FD_FROM_ANY"))

# Round-robin grant bound (no reset assumed anywhere).
job("rr_prove", mode="prove", depth=4, engines=KIND + IC3, **fd("rr_bound"))
job("rr_bmc24", mode="bmc", depth=24, engines=["smtbmc yices", "abc bmc3"], **fd("rr_bound"))
job("rr_cover", mode="cover", depth=12, engines=COVER, **fd("rr_bound"))
mutant("rr_bound", "rr_no_advance", 12)

# Fault stickiness and output-enable release.
job("fault_prove", mode="prove", depth=4, engines=KIND + IC3, **fd("fault_release"))
job("fault_bmc40", mode="bmc", depth=40, cls="heavy", engines=BMC, **fd("fault_release"))
job("fault_cover", mode="cover", depth=60, cls="heavy", engines=COVER, **fd("fault_release"))
# Per-engine witnesses for engines 1-3 need more host traffic than depth 60
# allows from reset; these start from an arbitrary state that satisfies the
# proved invariants (fd_invariants.vh).
job("fault_any_cover", mode="cover", depth=16, engines=COVER,
    **fd("fault_release", defines=["FD_FROM_ANY"]))
mutant("fault_release", "fault_restart", 48, cls="heavy")
mutant("fault_release", "fault_keeps_oe", 56, cls="heavy")

# Pin ownership and open-drain safety.
job("pin_prove", mode="prove", depth=4, engines=KIND + IC3, **fd("pin_safety"))
job("pin_bmc40", mode="bmc", depth=40, cls="heavy", engines=BMC, **fd("pin_safety"))
job("pin_cover", mode="cover", depth=60, cls="heavy", engines=COVER, **fd("pin_safety"))
job("pin_any_cover", mode="cover", depth=16, engines=COVER, **fd("pin_safety", defines=["FD_FROM_ANY"]))
mutant("pin_safety", "pin_own_overlap", 32)
mutant("pin_safety", "pin_od_drive_high", 60, cls="heavy")

# engine_safety made unbounded: the original harness plus two strengthening
# invariants (harness/engine_safety_strengthen.vh), by k-induction; and the
# unmodified harness by IC3-style engines (see the deep_engine_safety_prove job).
ESI = dict(files=["{RTL}/engine.v", "{FDRTL}/engine_safety_inductive.sv",
                  "{FD}/harness/engine_safety_strengthen.vh"],
           script=["read_verilog engine.v", "read -formal engine_safety_inductive.sv",
                   "chparam -set WIDTH 32 -set ENGINES 4 engine_safety_inductive",
                   "prep -top engine_safety_inductive"])
for k in (2, 4, 8):
    job(f"engine_inductive_kind{k}", mode="prove", depth=k,
        engines=["smtbmc yices", "smtbmc bitwuzla", "smtbmc boolector"], **ESI)
job("engine_inductive_ic3", mode="prove", depth=8, engines=["abc pdr", "aiger rIC3", "aiger suprove"], **ESI)

# ---------------------------------------------------------------- second
# round: the bit-level engines (rIC3 BMC, abc bmc3, btormc) were the fastest
# in the first round, so they go further.
job("deep_timing_isolation_bmc100", mode="bmc", depth=100, cls="heavy",
    engines=["aiger rIC3", "abc bmc3", "btor btormc"], **TI)
job("deep_processor_invariants_bmc128", mode="bmc", depth=128, cls="heavy",
    engines=["aiger rIC3", "abc bmc3", "btor btormc"], **PI)
job("deep_processor_inductive_bmc96", mode="bmc", depth=96, cls="heavy",
    engines=["aiger rIC3", "abc bmc3", "btor btormc"], **PD)
job("deep_engine_safety_bmc128", mode="bmc", depth=128,
    engines=["aiger rIC3", "abc bmc3", "btor btormc", "smtbmc bitwuzla"], **ES)

# The SRAM data-integrity claim (spec_word_*) is the one new claim without a
# k-induction proof, so its BMC goes deeper with the fastest bit-level engines.
job("load_bmc96", mode="bmc", depth=96, cls="heavy", engines=["aiger rIC3", "abc bmc3", "btor btormc"],
    **fd("program_load"))

# Cross-check that the bit-level BMC engines used for the deepest runs do find
# the counterexamples of formal/'s two timing-isolation negative controls on
# the same large miter (their PASS results are only meaningful if they can FAIL).
TI_NEG_MUTANT = dict(files=MODELS + ["{RTL}/processor_fv_mutant.v", "{SNAP}/formal/timing_isolation.sv"],
                     script=[READ_MODELS, "read_verilog processor_fv_mutant.v",
                             "read -formal -DNI_PINS_ONLY timing_isolation.sv",
                             "chparam -set WIDTH 32 -set ENGINES 4 -set DEPTH 8 timing_isolation",
                             "prep -top timing_isolation"])
TI_NEG_PULL = dict(files=MODELS + ["{RTL}/processor_fv.v", "{SNAP}/formal/timing_isolation.sv"],
                   script=[READ_MODELS, "read_verilog processor_fv.v",
                           "read -formal -DNI_NO_EXEMPTION timing_isolation.sv",
                           "chparam -set WIDTH 32 -set ENGINES 4 -set DEPTH 8 timing_isolation",
                           "prep -top timing_isolation"])
job("xcheck_ti_neg_mutant", mode="bmc", depth=12, expect="FAIL", cls="heavy",
    engines=["aiger rIC3", "abc bmc3", "btor btormc"], **TI_NEG_MUTANT)
job("xcheck_ti_neg_pull", mode="bmc", depth=12, expect="FAIL", cls="heavy",
    engines=["aiger rIC3", "abc bmc3", "btor btormc"], **TI_NEG_PULL)
