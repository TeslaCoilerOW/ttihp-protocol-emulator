// Strengthening invariants that make formal/engine_safety.sv k-inductive.
// gen/make_engine_inductive.py splices this file into a renamed copy of that
// harness (engine_safety_inductive); every original assertion is unchanged.
// Plain k-induction fails on the original at depths 8, 16 and 32 through an
// unreachable loop: a faulted engine whose `running` flag is still set while
// clear_fault is held high forever (clear_fault is ignored while running), so
// the fault-release assertion is never triggered inside the induction window.
// With those two, depth-2 induction next fails on a blocked-cycle counter at
// 0xffffff, whose 24-bit increment wraps and so never reaches the limit; the
// third invariant excludes it (the counter always stays below the limit).
    always @(posedge clk) if (past_valid) begin
        strengthen_fault_stops: assert(fault == 0 || !running);
        strengthen_idle_no_xfer: assert(running || transfer_edges == 0);
        strengthen_blocked_below_limit: assert(blocked_cycles < effective_limit);
    end
