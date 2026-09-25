// Pin ownership and open-drain safety, end to end (docs/isa.md OWN, "Outputs
// are masked by ownership, run, and fault", "For an open-drain pin, output is
// always zero and physical OE = logical OE AND NOT logical output value";
// docs/architecture.md "Ownership is disjoint and can change only while
// halted", "Open-drain pins drive only low or release").
//
// Whole processor (protocol_processor_fd), reset on the first edge, all later
// inputs free. With gate_k = running_k & fault_k == 0 & !reset:
//   spec_pin_model   uio_oe and uio_out are exactly the OR over engines of
//                    (logical enable & owned & (push-pull | logical low)) and
//                    (logical value & owned & push-pull), gated by gate_k;
//   spec_one_driver  no two engines ever drive the same pin's OE (pairwise
//                    disjoint contributions), and ownership is disjoint;
//   spec_open_drain  an open-drain pin never drives high (uio_out = 0), and it
//                    drives low exactly when its engine's logical value is 0
//                    and its logical enable is 1 (SET high releases it);
//   spec_own_change  ownership/open-drain masks change only through an
//                    accepted OWN for a halted engine (or reset);
//   spec_in_owned    while an engine runs, its logical enables and values lie
//                    inside its ownership, so the ownership mask in the pin
//                    mux is defence in depth, not the only barrier.
module pin_safety (input wire clk);
    (* anyseq *) reg rst_n, ena;
    (* anyseq *) reg [7:0] ui_in, uio_in;
`include "fd_dut.vh"

    wire clear = !rst_n || !ena;
    reg past_valid = 0;
    always @(posedge clk) begin
        past_valid <= 1;
`ifndef FD_FROM_ANY
        if (!past_valid) assume(!rst_n);
`endif
    end

    wire [31:0] oe_k, out_k;
    generate for (genvar k = 0; k < 4; k = k + 1) begin : eng
        wire gate = fv_running[k] && fv_fault_code[8*k+:8] == 0 && !clear;
        wire [7:0] own = dbg_ownership[8*k+:8], od = dbg_open_drain[8*k+:8];
        wire [7:0] en = fv_logical_enable[8*k+:8], val = fv_logical_output[8*k+:8];
        assign oe_k[8*k+:8] = en & own & (~od | ~val) & {8{gate}};
        assign out_k[8*k+:8] = val & own & ~od & {8{gate}};
        wire own_cmd = dbg_command_accepted && dbg_command_code == 3 && dbg_host_selected == k;
        always @(posedge clk) if (past_valid) begin
            assert((uio_out & od) == 0);  // spec_open_drain_high
            assert((uio_oe & od) == (en & od & ~val & {8{gate}}));  // spec_open_drain_low
            assert((od & ~own) == 0);  // spec_od_owned
            if (!$past(clear) && (own != $past(own) || od != $past(od)))
                assert($past(own_cmd && !fv_running[k]));  // spec_own_change
            if (fv_running[k]) assert(((en | val) & ~own) == 0);  // spec_in_owned
`ifndef FD_SPEC_ONLY
            // A transfer in flight drives only pins it was checked to own at issue.
            if (fv_transfer_edges[7*k+:7] != 0)
                assert(own[fv_transfer_pins[9*k+:3]]  // link_xfer_owned
                       && (!fv_transfer_mode[5*k+3] || own[fv_transfer_pins[9*k+3+:3]]));
`endif
        end
    end endgenerate

    always @(posedge clk) if (past_valid) begin
        spec_pin_model_oe: assert(uio_oe == (oe_k[7:0] | oe_k[15:8] | oe_k[23:16] | oe_k[31:24]));
        spec_pin_model_out: assert(uio_out == (out_k[7:0] | out_k[15:8] | out_k[23:16] | out_k[31:24]));
        spec_one_driver: assert((oe_k[7:0] & oe_k[15:8]) == 0 && (oe_k[7:0] & oe_k[23:16]) == 0
                                && (oe_k[7:0] & oe_k[31:24]) == 0 && (oe_k[15:8] & oe_k[23:16]) == 0
                                && (oe_k[15:8] & oe_k[31:24]) == 0 && (oe_k[23:16] & oe_k[31:24]) == 0);
        spec_own_disjoint: assert((dbg_ownership[7:0] & dbg_ownership[15:8]) == 0
                                  && (dbg_ownership[7:0] & dbg_ownership[23:16]) == 0
                                  && (dbg_ownership[7:0] & dbg_ownership[31:24]) == 0
                                  && (dbg_ownership[15:8] & dbg_ownership[23:16]) == 0
                                  && (dbg_ownership[15:8] & dbg_ownership[31:24]) == 0
                                  && (dbg_ownership[23:16] & dbg_ownership[31:24]) == 0);
        // Witnesses: two engines drive pins at once; an open-drain pin pulls low.
        cover_two_drivers: cover(oe_k[7:0] != 0 && oe_k[15:8] != 0);
        cover_od_low: cover((uio_oe & dbg_open_drain[7:0]) != 0);
        cover_od_release: cover($past((uio_oe & dbg_open_drain[7:0]) != 0)
                                && (uio_oe & dbg_open_drain[7:0]) == 0 && fv_running[0]);
    end
`include "fd_invariants.vh"
endmodule
