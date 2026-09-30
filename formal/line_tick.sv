// Line unit (docs/isa.md, "Ticker"; docs/extension.md): the ticker fires on
// the schedule of the ISA formula, for arbitrary instruction streams.
//
// A reference ticker written from the formula runs beside the production
// engine with the unit (protocol_engine_line). Its clock is the count of
// cycles in which the engine is active (running, no fault, no START, STOP or
// reset): the ticker "runs while the engine runs, also during WAIT and
// blocked instructions", and once the engine stops only START (which clears
// the ticker) restarts it. After an issued LTIM (P, Q, D) in cycle c0 it
// predicts ticks in the cycles
//   T(0) = c0 + (D, or P when D = 0),
//   T(k+1) = T(k) + P + carry(k), carry(k) = (acc(k) + Q) >> 8,
//   acc(k+1) = (acc(k) + Q) mod 256, acc(0) = 0,
// until a classic XFER, START, reset or LTIM with P = 0 stops it (an LTIM
// restarts it). The instruction stream, the host commands and all other
// inputs are unconstrained.
//
// A tick is observed through the phase register (ls_line_phase), which every
// tick toggles; even ticks are bit boundaries (phase 0 before the tick), odd
// ones mid-bit. Claims (target of: line_tick_neg):
//   A1  outside START, reset and an issued LTIM (which set the phase), the
//       phase toggles in exactly the cycles in which the reference ticks;
//   A2  while the reference runs, the phase before tick k is k mod 2 (0 after
//       an LTIM);
//   A3  the running flag (ls_line_run, LSTAT bit 6) equals the reference's.
// Lemmas (L1-L3), which make the claims inductive: while the reference runs,
// the engine's tick counter (transfer_tick) equals the distance to the next
// tick plus 1, the period, fraction and accumulator registers equal P, Q
// and acc(k), and no classic XFER (which would use the shared tick counter)
// is in progress. They reach the tick counter and the period register through
// ports that the .sby script adds with `expose` (observation only). With
// -DNO_LEMMAS (the negative control, a bounded check) they are left out, so
// that a counterexample can only come from A1-A3.
//
// Other defines, from formal/variant_sby.py: ASYNC_RESET (clear is an
// asynchronous reset: the checks apply while it is low), PC_SAT (7-bit PC).
`ifdef PC_SAT
`define LINE_PCW 7
`else
`define LINE_PCW 24
`endif

module line_tick #(parameter PCW=`LINE_PCW) (input wire clk);
    (* anyseq *) reg clear, start, stop, clear_fault;
    (* anyseq *) reg [31:0] instruction, timestamp;
    (* anyseq *) reg [23:0] image_length;
    (* anyseq *) reg [7:0] ownership, pins;
    (* anyseq *) reg tx_valid, rx_ready, event_pending;
    (* anyseq *) reg [31:0] tx_data;
    wire [PCW-1:0] pc;
    wire running, issue;
    wire [7:0] fault;
    wire [23:0] wait_timer;
    wire [6:0] transfer_edges, transfer_mode;
    wire l_run, l_phase;
    wire [7:0] l_frac, l_acc;
`ifndef NO_LEMMAS
    wire [7:0] e_tick, e_period;
`endif
    protocol_engine_line dut(.clk(clk), .clear(clear), .start(start), .stop(stop),
        .clear_fault(clear_fault), .instruction(instruction), .image_length(image_length),
        .ownership(ownership), .pins(pins), .timestamp(timestamp), .tx_valid(tx_valid),
        .tx_data(tx_data), .rx_ready(rx_ready), .event_pending(event_pending), .pc(pc),
        .running(running), .fault(fault), .issue(issue), .wait_timer(wait_timer),
        .transfer_edges(transfer_edges), .transfer_mode(transfer_mode),
`ifndef NO_LEMMAS
        .transfer_tick(e_tick), .transfer_period(e_period),
`endif
        .ls_line_run(l_run), .ls_line_phase(l_phase), .ls_line_frac(l_frac), .ls_line_acc(l_acc));

    wire [7:0] op = instruction[31:24];
    wire [7:0] ltim_p = instruction[7:0], ltim_q = instruction[15:8], ltim_d = instruction[23:16];
    wire active = running && fault == 0 && !start && !stop && !clear;
    wire ltim = issue && op == 8'd30;                          // an LTIM that executes
    wire classic = issue && op == 8'd17 && !instruction[5];   // a classic XFER that starts

    // Reference ticker (docs/isa.md, "Ticker").
    reg r_run = 0;             // running
    reg [7:0] r_p = 0, r_q = 0;
    reg [7:0] r_acc = 0;       // acc(k) of the next tick k
    reg r_k = 0;               // k mod 2 of the next tick k
    reg [15:0] r_now = 0;      // active cycles so far
    reg [15:0] r_next = 0;     // T(k) of the next tick, on the same clock
    wire [8:0] r_sum = {1'b0, r_acc} + {1'b0, r_q};  // acc(k) + Q; bit 8 is carry(k)
    wire r_tick = r_run && active && r_now == r_next;
    always @(posedge clk) begin
        if (clear || (start && !stop)) begin
            r_run <= 0;                                        // START and reset clear it
        end else begin
            if (active) begin
                r_now <= r_now + 16'd1;
                if (r_tick) begin
                    r_next <= r_next + {8'd0, r_p} + {15'd0, r_sum[8]};
                    r_acc <= r_sum[7:0];
                    r_k <= !r_k;
                end
            end
            if (ltim) begin
                r_run <= ltim_p != 0;
                r_p <= ltim_p;
                r_q <= ltim_q;
                r_acc <= 0;
                r_k <= 0;
                r_next <= r_now + {8'd0, ltim_d != 0 ? ltim_d : ltim_p};
            end
            if (classic) r_run <= 0;                           // a classic XFER stops it
        end
    end

`ifdef ASYNC_RESET
    wire settled = !clear;
`else
    wire settled = 1'b1;
`endif
    reg past_valid = 0;
    always @(posedge clk) begin
        past_valid <= 1;
        if (!past_valid) assume(clear);
        if (past_valid && settled && !$past(clear)) begin
            if (!($past(start) && !$past(stop)) && !$past(ltim))
                assert((l_phase != $past(l_phase)) == $past(r_tick));  // A1, target of: line_tick_neg
            if (r_run) assert(l_phase == r_k);                         // A2, target of: line_tick_neg
            assert(l_run == r_run);                                    // A3, target of: line_tick_neg
`ifndef NO_LEMMAS
            if (r_run) begin
                assert(r_next - r_now < 16'd255);                      // L1
                assert({8'd0, e_tick} == r_next - r_now + 16'd1);      // L1
                assert(e_period == r_p && l_frac == r_q && l_acc == r_acc);  // L2
                assert(r_p != 0 && !(r_p == 8'd255 && r_q != 0));      // L2
                assert(transfer_edges == 0 || transfer_mode[5]);       // L3
            end
`endif
        end
    end

`ifdef TICK_COVER
    // Non-vacuity: ticks with a carry, a restart, a tick during WAIT.
    reg [3:0] carries = 0;
    always @(posedge clk) if (r_tick && r_sum[8] && carries != 4'hf) carries <= carries + 1;
    always @(posedge clk) if (past_valid && settled) begin
        cover(carries == 4'd3 && r_p == 8'd2 && r_q == 8'd171);
        cover(r_tick && r_p == 8'd1 && r_q != 0 && r_sum[8] && r_k);
        cover(r_tick && wait_timer != 0 && r_k);
    end
`endif
endmodule
