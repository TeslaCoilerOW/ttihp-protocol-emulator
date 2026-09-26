// Timing certificates: DUT instance and engine-K observation wires.
//
// Included inside a harness module body. The includer declares
//   localparam K            engine under certification (0..3)
//   rst_n, ena, ui_in, uio_in   the chip inputs (wires or anyseq regs)
// and gets one protocol_processor_fv instance (formal/gen/generate_fv.ml: the
// circuit Processor.create_refinement ~debug:true plus output ports that
// observe existing registers and SRAM pins; no logic is added) with the exact
// IHP FUNCTIONAL SRAM models, and the k_* wires below. Design of record only:
// 4 engines, 32-bit datapath, 64-word images, 8-word FIFOs, fused issue.
    wire [7:0] uo_out, uio_out, uio_oe;
    wire [31:0] owners, drains;
    wire [3:0] running_all, starts, stops, tx_pop, rx_push, event_clear, image_valid, mailbox;
    wire [15:0] tx_level, rx_level;
    wire [63:0] route_count, image_length_all;
    wire [23:0] trigger_config;
    wire [7:0] synced, previous, sync1, cmd_code;
    wire [23:0] cmd_payload;
    wire [1:0] host_selected;
    wire cmd_accepted, chip_clear;
    wire [95:0] fv_pc, fv_wait_timer, fv_wait_limit, fv_blocked;
    wire [31:0] fv_fault, fv_values, fv_enables, fv_tick, fv_period;
    wire [127:0] fv_tx, fv_rx, fv_x, fv_y, fv_completed, fv_dout;
    wire [63:0] fv_repeat, fv_loaded;
    wire [35:0] fv_pins;
    wire [27:0] fv_edges;
    wire [19:0] fv_mode;
    wire [3:0] fv_running, fv_writing;
    wire [3:0] men_lo, men_hi, wen_lo, wen_hi, ren_lo, ren_hi;
    wire [23:0] a_lo, a_hi;
    wire [31:0] timestamp;
    protocol_processor_fv dut(.clk(clk), .rst_n(rst_n), .ena(ena), .ui_in(ui_in),
        .uio_in(uio_in), .uo_out(uo_out), .uio_out(uio_out), .uio_oe(uio_oe),
        .dbg_ownership(owners), .dbg_open_drain(drains), .dbg_running(running_all),
        .dbg_clear(chip_clear), .dbg_start(starts), .dbg_stop(stops),
        .dbg_tx_pop(tx_pop), .dbg_rx_push(rx_push), .dbg_event_clear(event_clear),
        .dbg_tx_level(tx_level), .dbg_rx_level(rx_level), .dbg_route_count(route_count),
        .dbg_events(mailbox), .dbg_trigger_config(trigger_config),
        .dbg_synced_pins(synced), .dbg_previous_pins(previous),
        .dbg_host_selected(host_selected), .dbg_command_accepted(cmd_accepted),
        .dbg_command_code(cmd_code), .dbg_command_payload(cmd_payload),
        .dbg_image_valid(image_valid), .dbg_image_length(image_length_all),
        .fv_pc(fv_pc), .fv_running(fv_running), .fv_fault_code(fv_fault), .fv_tx(fv_tx),
        .fv_rx(fv_rx), .fv_x(fv_x), .fv_y(fv_y), .fv_repeat_count(fv_repeat),
        .fv_wait_timer(fv_wait_timer), .fv_wait_limit(fv_wait_limit),
        .fv_blocked_cycles(fv_blocked), .fv_logical_output(fv_values),
        .fv_logical_enable(fv_enables), .fv_transfer_pins(fv_pins),
        .fv_completed_instructions(fv_completed), .fv_transfer_edges(fv_edges),
        .fv_transfer_tick(fv_tick), .fv_transfer_period(fv_period), .fv_transfer_mode(fv_mode),
        .fv_image_writing(fv_writing), .fv_image_loaded(fv_loaded), .fv_sram_dout(fv_dout),
        .fv_sram_lo_a_men(men_lo), .fv_sram_hi_a_men(men_hi),
        .fv_sram_lo_a_wen(wen_lo), .fv_sram_hi_a_wen(wen_hi),
        .fv_sram_lo_a_ren(ren_lo), .fv_sram_hi_a_ren(ren_hi),
        .fv_sram_lo_a_addr(a_lo), .fv_sram_hi_a_addr(a_hi),
        .fv_timestamp(timestamp), .fv_sync1(sync1));

    // Engine K's execution registers (the 19 registers formal/ attributes).
    wire [23:0] k_pc = fv_pc[24*K+:24];
    wire k_running = fv_running[K];
    wire [7:0] k_fault = fv_fault[8*K+:8];
    wire [31:0] k_tx = fv_tx[32*K+:32], k_rx = fv_rx[32*K+:32];
    wire [31:0] k_x = fv_x[32*K+:32], k_y = fv_y[32*K+:32];
    wire [15:0] k_repeat = fv_repeat[16*K+:16];
    wire [23:0] k_timer = fv_wait_timer[24*K+:24], k_limit = fv_wait_limit[24*K+:24];
    wire [23:0] k_blocked = fv_blocked[24*K+:24];
    wire [7:0] k_values = fv_values[8*K+:8], k_enables = fv_enables[8*K+:8];
    wire [8:0] k_xpins = fv_pins[9*K+:9];
    wire [31:0] k_completed = fv_completed[32*K+:32];
    wire [6:0] k_xrem = fv_edges[7*K+:7];
    wire [7:0] k_xtick = fv_tick[8*K+:8], k_xperiod = fv_period[8*K+:8];
    wire [4:0] k_xmode = fv_mode[5*K+:5];
    // Configuration, SRAM port and FIFO/event handshakes of engine K.
    wire [7:0] k_own = owners[8*K+:8], k_od = drains[8*K+:8];
    wire [15:0] k_len = image_length_all[16*K+:16], k_loaded = fv_loaded[16*K+:16];
    wire k_valid = image_valid[K], k_writing = fv_writing[K];
    wire [31:0] k_dout = fv_dout[32*K+:32];
    wire k_rd_lo = men_lo[K] && ren_lo[K] && !wen_lo[K];
    wire k_rd_hi = men_hi[K] && ren_hi[K] && !wen_hi[K];
    wire k_wr = (men_lo[K] && wen_lo[K]) || (men_hi[K] && wen_hi[K]);
    wire [5:0] k_addr_lo = a_lo[6*K+:6], k_addr_hi = a_hi[6*K+:6];
    wire [3:0] k_rx_level = rx_level[4*K+:4], k_tx_level = tx_level[4*K+:4];
    wire k_tx_pop = tx_pop[K], k_rx_push = rx_push[K], k_event_clear = event_clear[K];
    wire k_start = starts[K], k_stop = stops[K];
    wire k_clear_fault = cmd_accepted && cmd_code == 8'd7 && cmd_payload[K];
    wire k_reconfigure = cmd_accepted && host_selected == K
        && (cmd_code == 8'd1 || cmd_code == 8'd2 || cmd_code == 8'd3);
    // An issue slot: the engine attempts the instruction at k_pc on the next edge.
    wire k_slot = k_running && k_fault == 0 && k_timer == 0 && k_xrem == 0;
    // Physical state of each pad as pe_timing encodes it: P0=1, P1=2, Z=4.
    function automatic [2:0] pad_state(input oe, input out);
        pad_state = !oe ? 3'b100 : (out ? 3'b010 : 3'b001);
    endfunction
    wire [23:0] k_pads = {pad_state(uio_oe[7], uio_out[7]), pad_state(uio_oe[6], uio_out[6]),
        pad_state(uio_oe[5], uio_out[5]), pad_state(uio_oe[4], uio_out[4]),
        pad_state(uio_oe[3], uio_out[3]), pad_state(uio_oe[2], uio_out[2]),
        pad_state(uio_oe[1], uio_out[1]), pad_state(uio_oe[0], uio_out[0])};

    function automatic valid_masks(input [31:0] o, input [31:0] d);
        integer i, j;
        begin
            valid_masks = 1;
            for (i = 0; i < 4; i = i + 1) begin
                if ((d[8*i+:8] & ~o[8*i+:8]) != 0) valid_masks = 0;
                for (j = i + 1; j < 4; j = j + 1)
                    if ((o[8*i+:8] & o[8*j+:8]) != 0) valid_masks = 0;
            end
        end
    endfunction
