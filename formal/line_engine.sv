// Line unit (docs/extension.md): engine-level properties over arbitrary
// instruction streams (like engine_safety.sv) on the production engine with
// the line unit (protocol_engine_line). One group per define:
//
//   P_PINS    pin locality: in a cycle without START or reset, the engine's
//             logical outputs change only on the two pins named by the PINS
//             register (clock/pair field and data field), unless the
//             instruction issued is SET or OUT. So the line unit, including
//             the Manchester second half and the arbitration monitor, drives
//             no other pin; the processor's ownership masking then confines
//             it to the engine's own pins (processor_invariants).
//   P_RESET   after reset, and after START, every line-unit register and
//             transfer_mode are zero.
//   P_DECODE  an issued invalid line-unit encoding (the rules of
//             docs/extension.md, "Invalid encodings", recomputed here from the
//             instruction bits, the ownership input and the observed LTIM/LCFG
//             state) faults with code 1, keeps the PC and changes no line-unit
//             configuration, flag or CRC; a valid LTIM, LCFG, CRC or LSTAT
//             completes (PC + 1, no fault) and a valid line XFER starts
//             (no fault, bit count loaded).
//
// Other defines, from formal/variant_sby.py: ASYNC_RESET (clear is an
// asynchronous reset: next-state checks apply while it is low, the reset
// state while it is high), PC_SAT (7-bit PC).
`ifdef PC_SAT
`define LINE_PCW 7
`else
`define LINE_PCW 24
`endif

module line_engine #(parameter PCW=`LINE_PCW) (input wire clk);
    (* anyseq *) reg clear, start, stop, clear_fault;
    (* anyseq *) reg [31:0] instruction, timestamp;
    (* anyseq *) reg [23:0] image_length;
    (* anyseq *) reg [7:0] ownership, pins;
    (* anyseq *) reg tx_valid, rx_ready, event_pending;
    (* anyseq *) reg [31:0] tx_data;
    wire [PCW-1:0] pc;
    wire running, stalled, tx_pop, rx_push, consume_event, issue;
    wire [7:0] fault, pin_values, pin_enables;
    wire [31:0] rx_data, completed;
    wire [3:0] signal_events;
    wire [23:0] wait_timer, wait_limit, blocked_cycles;
    wire [15:0] repeat_count;
    wire [6:0] transfer_edges, transfer_mode;
    wire [8:0] transfer_pins;
    wire l_run, l_phase, l_seen, l_level, l_rx_prev, l_cell, l_man, l_se0, l_trail, s_last, s_err, a_lost;
    wire [7:0] l_frac, l_acc;
    wire [9:0] l_cfg;
    wire [5:0] l_rem;
    wire [3:0] s_run;
    wire [15:0] c_state;
    wire [1:0] c_preset;
    protocol_engine_line dut(.clk(clk), .clear(clear), .start(start), .stop(stop),
        .clear_fault(clear_fault), .instruction(instruction), .image_length(image_length),
        .ownership(ownership), .pins(pins), .timestamp(timestamp), .tx_valid(tx_valid),
        .tx_data(tx_data), .rx_ready(rx_ready), .event_pending(event_pending), .pc(pc),
        .running(running), .fault(fault), .stalled(stalled), .tx_pop(tx_pop),
        .rx_push(rx_push), .rx_data(rx_data), .pin_values(pin_values),
        .pin_enables(pin_enables), .signal_events(signal_events),
        .consume_event(consume_event), .completed(completed), .issue(issue),
        .wait_timer(wait_timer), .wait_limit(wait_limit), .blocked_cycles(blocked_cycles),
        .repeat_count(repeat_count), .transfer_edges(transfer_edges),
        .transfer_pins(transfer_pins), .transfer_mode(transfer_mode),
        .ls_line_run(l_run), .ls_line_phase(l_phase), .ls_line_frac(l_frac), .ls_line_acc(l_acc),
        .ls_line_boundary_seen(l_seen), .ls_line_cfg(l_cfg), .ls_line_level(l_level),
        .ls_line_rx_prev(l_rx_prev), .ls_line_cell_bit(l_cell), .ls_line_man_pending(l_man),
        .ls_line_se0(l_se0), .ls_line_remaining(l_rem), .ls_line_trailing_stuff(l_trail),
        .ls_stuff_run(s_run), .ls_stuff_last(s_last), .ls_stuff_error(s_err),
        .ls_arbitration_lost(a_lost), .ls_crc_state(c_state), .ls_crc_preset(c_preset));

    wire [7:0] op = instruction[31:24], a = instruction[23:16], b = instruction[15:8], c = instruction[7:0];
    wire [2:0] ck = transfer_pins[2:0], out = transfer_pins[5:3];
    wire owned_ck = ownership[ck], owned_out = ownership[out];
    wire pair = l_cfg[7], manchester = l_cfg[1:0] == 2'd2;
    // docs/extension.md, "Invalid encodings"
    wire bad_ltim = op == 30 && instruction[7:0] == 8'd255 && instruction[15:8] != 0;
    wire bad_lcfg = op == 31 && (instruction[23:11] != 0 || instruction[1:0] == 2'd3
                                 || (instruction[2] && instruction[3] && instruction[6:4] == 0));
    wire bad_crc = op == 32 && !((c == 1 && a == 0 && b < 4) || (c == 2 && a < 4 && b == 0)
                                 || (c == 3 && a == 0 && b < 4));
    wire bad_lstat = op == 33 && (a >= 4 || b != 0 || c != 0);
    wire line_xfer = op == 17 && c[5];
    wire bad_line_xfer = line_xfer && (c[7] || a == 0 || a > 32 || b != 0 || c[1:0] != 0 || !l_run
                                       || (manchester && c[4]) || (c[3] && !owned_out)
                                       || (c[3] && pair && (!owned_ck || ck == out)));
    wire bad_classic = op == 17 && !c[5] && c[7];  // the other classic rules are the base ISA's
    wire bad_line = bad_ltim || bad_lcfg || bad_crc || bad_lstat || bad_line_xfer || bad_classic || op >= 34;
    wire good_simple = (op == 30 && !bad_ltim) || (op == 31 && !bad_lcfg) || (op == 32 && !bad_crc)
                       || (op == 33 && !bad_lstat);
    wire [PCW-1:0] image_limit = image_length[PCW-1:0];
    wire could_issue = running && fault == 0 && !start && !stop && !clear
        && wait_timer == 0 && transfer_edges == 0 && pc < image_limit
        && (image_length >> PCW) == 0;
    // line-unit configuration, flags and CRC (not the ticker phase/accumulator
    // or the line level, which the ticker and the Manchester half move)
    wire [54:0] held = {l_run, l_frac, l_seen, l_cfg, l_rx_prev, l_cell, l_se0, l_rem, l_trail, s_run, s_last,
                        s_err, a_lost, c_state, c_preset};
    wire zero_state = {l_run, l_phase, l_frac, l_acc, l_seen, l_cfg, l_level, l_rx_prev, l_cell, l_man, l_se0,
                       l_rem, l_trail, s_run, s_last, s_err, a_lost, c_state, c_preset, transfer_mode} == 0;
`ifdef ASYNC_RESET
    wire settled = !clear;
`else
    wire settled = 1'b1;
`endif
    reg past_valid = 0;
    always @(posedge clk) begin
        past_valid <= 1;
        if (!past_valid) assume(clear);
`ifdef P_RESET
`ifdef ASYNC_RESET
        if (past_valid && clear) assert(zero_state);
`else
        if (past_valid && $past(clear)) assert(zero_state);
`endif
        if (past_valid && settled && !$past(clear) && $past(start) && !$past(stop)) assert(zero_state);  // target of: line_reset_neg
`endif
        if (past_valid && settled && !$past(clear) && !$past(start)) begin
`ifdef P_PINS
            if (!($past(issue) && ($past(op) == 2 || $past(op) == 8)))
                assert(((pin_values ^ $past(pin_values))  // target of: line_pins_neg
                        & ~((8'd1 << $past(ck)) | (8'd1 << $past(out)))) == 0);
`endif
`ifdef P_DECODE
            if (!$past(stop) && $past(could_issue)) begin
                if ($past(bad_line)) begin
                    assert(fault == 1 && !running && pc == $past(pc));  // target of: line_decode_neg
                    assert(held == $past(held));  // target of: line_decode_neg
                end
                if ($past(good_simple)) assert(fault == 0 && running && pc == $past(pc) + 1);
                if ($past(line_xfer) && !$past(bad_line_xfer))
                    assert(fault == 0 && running && pc == $past(pc) && transfer_edges == $past(a[6:0]));
            end
`endif
        end
    end
endmodule
