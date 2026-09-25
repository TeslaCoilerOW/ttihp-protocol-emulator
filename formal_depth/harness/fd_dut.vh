// Generated from processor_fd.v's port list: declares one wire per DUT output
// and instantiates protocol_processor_fd as `dut`. The including module must
// declare clk, rst_n, ena, ui_in[7:0] and uio_in[7:0].
// Regenerate with: formal_depth/gen/make_dut_include.py
wire [7:0] uo_out;
wire [7:0] uio_out;
wire [7:0] uio_oe;
wire [31:0] dbg_ownership;
wire [31:0] dbg_open_drain;
wire [23:0] dbg_trigger_config;
wire [3:0] dbg_trigger_event;
wire [7:0] dbg_synced_pins;
wire [7:0] dbg_previous_pins;
wire [3:0] dbg_events;
wire [3:0] dbg_event_set;
wire [3:0] dbg_event_clear;
wire [3:0] dbg_running;
wire dbg_clear;
wire [3:0] dbg_start;
wire [3:0] dbg_stop;
wire [3:0] dbg_fifo_clear;
wire [3:0] dbg_dma_grant;
wire [3:0] dbg_dma_eligible;
wire [1:0] dbg_dma_destination;
wire [31:0] dbg_dma_data;
wire [1:0] dbg_round_robin;
wire [63:0] dbg_route_count;
wire [7:0] dbg_route_destination;
wire [3:0] dbg_tx_push;
wire [3:0] dbg_tx_pop;
wire [127:0] dbg_tx_data;
wire [3:0] dbg_rx_push;
wire [3:0] dbg_rx_pop;
wire [127:0] dbg_rx_data;
wire [3:0] dbg_tx_ready;
wire [3:0] dbg_rx_valid;
wire [15:0] dbg_tx_level;
wire [15:0] dbg_rx_level;
wire [127:0] dbg_rx_head;
wire dbg_host_tx;
wire dbg_host_rx;
wire dbg_host_rx_reserved;
wire [1:0] dbg_host_selected;
wire dbg_command_accepted;
wire [7:0] dbg_command_code;
wire [23:0] dbg_command_payload;
wire [3:0] dbg_image_valid;
wire [63:0] dbg_image_length;
wire [95:0] fv_pc;
wire [3:0] fv_running;
wire [31:0] fv_fault_code;
wire [127:0] fv_tx;
wire [127:0] fv_rx;
wire [127:0] fv_x;
wire [127:0] fv_y;
wire [63:0] fv_repeat_count;
wire [95:0] fv_wait_timer;
wire [95:0] fv_wait_limit;
wire [95:0] fv_blocked_cycles;
wire [31:0] fv_logical_output;
wire [31:0] fv_logical_enable;
wire [35:0] fv_transfer_pins;
wire [127:0] fv_completed_instructions;
wire [27:0] fv_transfer_edges;
wire [31:0] fv_transfer_tick;
wire [31:0] fv_transfer_period;
wire [19:0] fv_transfer_mode;
wire [3:0] fv_image_writing;
wire [63:0] fv_image_loaded;
wire [127:0] fv_sram_dout;
wire [3:0] fv_sram_lo_a_men;
wire [3:0] fv_sram_hi_a_men;
wire [3:0] fv_sram_lo_a_wen;
wire [3:0] fv_sram_hi_a_wen;
wire [3:0] fv_sram_lo_a_ren;
wire [3:0] fv_sram_hi_a_ren;
wire [23:0] fv_sram_lo_a_addr;
wire [23:0] fv_sram_hi_a_addr;
wire [63:0] fd_sram_lo_a_din;
wire [63:0] fd_sram_hi_a_din;
wire [31:0] fv_timestamp;
wire [7:0] fv_sync1;
wire [1:0] fd_host_prev_window;
wire [2:0] fd_host_write_index;
wire [31:0] fd_host_write_buffer;
wire [2:0] fd_host_read_index;
wire [31:0] fd_host_snapshot;
wire fd_host_presenting;
wire fd_host_fault;
wire [2:0] fd_host_read_select;
wire [127:0] fd_tx_head;
wire [11:0] fd_tx_rd;
wire [11:0] fd_tx_wr;
wire [11:0] fd_rx_rd;
wire [11:0] fd_rx_wr;
wire [255:0] fd_rx_mem_0_flat;
wire [255:0] fd_rx_mem_1_flat;
wire [255:0] fd_rx_mem_2_flat;
wire [255:0] fd_rx_mem_3_flat;
wire [255:0] fd_tx_mem_0_flat;
wire [255:0] fd_tx_mem_1_flat;
wire [255:0] fd_tx_mem_2_flat;
wire [255:0] fd_tx_mem_3_flat;
protocol_processor_fd dut(
    .clk(clk),
    .rst_n(rst_n),
    .ena(ena),
    .ui_in(ui_in),
    .uio_in(uio_in),
    .uo_out(uo_out),
    .uio_out(uio_out),
    .uio_oe(uio_oe),
    .dbg_ownership(dbg_ownership),
    .dbg_open_drain(dbg_open_drain),
    .dbg_trigger_config(dbg_trigger_config),
    .dbg_trigger_event(dbg_trigger_event),
    .dbg_synced_pins(dbg_synced_pins),
    .dbg_previous_pins(dbg_previous_pins),
    .dbg_events(dbg_events),
    .dbg_event_set(dbg_event_set),
    .dbg_event_clear(dbg_event_clear),
    .dbg_running(dbg_running),
    .dbg_clear(dbg_clear),
    .dbg_start(dbg_start),
    .dbg_stop(dbg_stop),
    .dbg_fifo_clear(dbg_fifo_clear),
    .dbg_dma_grant(dbg_dma_grant),
    .dbg_dma_eligible(dbg_dma_eligible),
    .dbg_dma_destination(dbg_dma_destination),
    .dbg_dma_data(dbg_dma_data),
    .dbg_round_robin(dbg_round_robin),
    .dbg_route_count(dbg_route_count),
    .dbg_route_destination(dbg_route_destination),
    .dbg_tx_push(dbg_tx_push),
    .dbg_tx_pop(dbg_tx_pop),
    .dbg_tx_data(dbg_tx_data),
    .dbg_rx_push(dbg_rx_push),
    .dbg_rx_pop(dbg_rx_pop),
    .dbg_rx_data(dbg_rx_data),
    .dbg_tx_ready(dbg_tx_ready),
    .dbg_rx_valid(dbg_rx_valid),
    .dbg_tx_level(dbg_tx_level),
    .dbg_rx_level(dbg_rx_level),
    .dbg_rx_head(dbg_rx_head),
    .dbg_host_tx(dbg_host_tx),
    .dbg_host_rx(dbg_host_rx),
    .dbg_host_rx_reserved(dbg_host_rx_reserved),
    .dbg_host_selected(dbg_host_selected),
    .dbg_command_accepted(dbg_command_accepted),
    .dbg_command_code(dbg_command_code),
    .dbg_command_payload(dbg_command_payload),
    .dbg_image_valid(dbg_image_valid),
    .dbg_image_length(dbg_image_length),
    .fv_pc(fv_pc),
    .fv_running(fv_running),
    .fv_fault_code(fv_fault_code),
    .fv_tx(fv_tx),
    .fv_rx(fv_rx),
    .fv_x(fv_x),
    .fv_y(fv_y),
    .fv_repeat_count(fv_repeat_count),
    .fv_wait_timer(fv_wait_timer),
    .fv_wait_limit(fv_wait_limit),
    .fv_blocked_cycles(fv_blocked_cycles),
    .fv_logical_output(fv_logical_output),
    .fv_logical_enable(fv_logical_enable),
    .fv_transfer_pins(fv_transfer_pins),
    .fv_completed_instructions(fv_completed_instructions),
    .fv_transfer_edges(fv_transfer_edges),
    .fv_transfer_tick(fv_transfer_tick),
    .fv_transfer_period(fv_transfer_period),
    .fv_transfer_mode(fv_transfer_mode),
    .fv_image_writing(fv_image_writing),
    .fv_image_loaded(fv_image_loaded),
    .fv_sram_dout(fv_sram_dout),
    .fv_sram_lo_a_men(fv_sram_lo_a_men),
    .fv_sram_hi_a_men(fv_sram_hi_a_men),
    .fv_sram_lo_a_wen(fv_sram_lo_a_wen),
    .fv_sram_hi_a_wen(fv_sram_hi_a_wen),
    .fv_sram_lo_a_ren(fv_sram_lo_a_ren),
    .fv_sram_hi_a_ren(fv_sram_hi_a_ren),
    .fv_sram_lo_a_addr(fv_sram_lo_a_addr),
    .fv_sram_hi_a_addr(fv_sram_hi_a_addr),
    .fd_sram_lo_a_din(fd_sram_lo_a_din),
    .fd_sram_hi_a_din(fd_sram_hi_a_din),
    .fv_timestamp(fv_timestamp),
    .fv_sync1(fv_sync1),
    .fd_host_prev_window(fd_host_prev_window),
    .fd_host_write_index(fd_host_write_index),
    .fd_host_write_buffer(fd_host_write_buffer),
    .fd_host_read_index(fd_host_read_index),
    .fd_host_snapshot(fd_host_snapshot),
    .fd_host_presenting(fd_host_presenting),
    .fd_host_fault(fd_host_fault),
    .fd_host_read_select(fd_host_read_select),
    .fd_tx_head(fd_tx_head),
    .fd_tx_rd(fd_tx_rd),
    .fd_tx_wr(fd_tx_wr),
    .fd_rx_rd(fd_rx_rd),
    .fd_rx_wr(fd_rx_wr),
    .fd_rx_mem_0_flat(fd_rx_mem_0_flat),
    .fd_rx_mem_1_flat(fd_rx_mem_1_flat),
    .fd_rx_mem_2_flat(fd_rx_mem_2_flat),
    .fd_rx_mem_3_flat(fd_rx_mem_3_flat),
    .fd_tx_mem_0_flat(fd_tx_mem_0_flat),
    .fd_tx_mem_1_flat(fd_tx_mem_1_flat),
    .fd_tx_mem_2_flat(fd_tx_mem_2_flat),
    .fd_tx_mem_3_flat(fd_tx_mem_3_flat)
);
