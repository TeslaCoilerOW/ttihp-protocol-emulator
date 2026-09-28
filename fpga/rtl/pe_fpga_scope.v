/*
 * Copyright (c) 2026 TeslaCoilerOW
 * SPDX-License-Identifier: Apache-2.0
 *
 * On-board pin-edge capture unit ("scope"), FPGA prototype only (not part of
 * the ASIC). It watches 24 channels in the core clock domain, timestamps
 * every change with a free-running cycle counter, stores the changes in
 * block RAM and returns them to the PC through the UART bridge
 * (pe_uart_host_bridge.v forwards the scope commands; docs/fpga.md,
 * "On-board capture unit").
 *
 * Channels (bit index in the 24-bit values/mask fields):
 *    0..7   uio[0..7]  protocol pins. Pad view (default): the pad input
 *                      through the unit's input register, what a probe on
 *                      the pin sees. Core view (ARM mode bit 1): uio_out
 *                      where uio_oe is set, the pad elsewhere.
 *    8..15  uo_out[0..7]
 *   16..23  ui_in[0..7] (as applied to the core: bridge or pin host)
 *
 * Timestamps. `stamp` counts clock edges since configuration, minus 2, so
 * that a record's stamp is the index p of the clock edge that launched the
 * change (the p-th rising edge since configuration; the bridge's 'C'
 * counter reads p at that point). Every channel passes the same two
 * registers (sampled at edge p+1, compared at edge p+2, written at edge
 * p+3), so all channels share one latency and the stamp needs no
 * correction. For the pad view this holds when the pad settles within one
 * clock period after the launching edge (pad delays are not timed by the
 * openXC7 flow; the core view does not depend on them).
 *
 * Record (72 bits, returned as 9 bytes, little endian):
 *   [71:48] values   all 24 channels after the change
 *   [47:24] mask     enabled channels that changed in this cycle
 *   [23:22] kind     0 change, 1 start, 2 marker, 3 end
 *   [21:0]  stamp    low 22 bits of the launching edge's stamp
 * Record 0 of a capture is the start record, written at the trigger cycle
 * (its mask holds the enabled channels that changed in that cycle; the value
 * of an enabled channel before the record is values ^ mask). A marker record
 * (mask 0) is written whenever stamp[20:0] == 0 and nothing else is written,
 * so consecutive records are less than 2^22 cycles apart and the full stamp
 * of every record follows from the start stamp (status 'Q') and the
 * differences of the 22-bit fields modulo 2^22. The last record of a
 * finished capture is the end record: written at the halt cycle ('H'), or
 * by the write that fills the buffer (or reaches the ARM record limit); it
 * also carries that cycle's changes.
 *
 * Overflow. When the end record fills the buffer the capture enters FULL:
 * nothing more is stored and every later cycle with an enabled change is
 * counted in `lost` (saturating) until 'H' or the next 'A'. Records are
 * therefore always a complete, gap-free account of the enabled channels from
 * the start stamp to the end stamp.
 *
 * Activity counters: per channel, the number of changes (enabled or not)
 * from the start record's cycle to the end record's cycle inclusive
 * (32-bit, saturating), e.g. host strobes or UART edges while the
 * capture ran.
 *
 * Commands (issued through the bridge; payloads little endian):
 *   'A' en[3] trig[3] mode limit[2] -> 'A'   arm: clears the buffer and the
 *        counters. mode bit0 = 1: wait for a change on a trig channel (or
 *        'F'); 0: start immediately. mode bit1 = 1: core view of uio.
 *        limit: records in this capture, 2..depth (0 or out of range =
 *        depth). Recording starts after a 3-cycle settling time.
 *   'F'                             -> 'F'   force the trigger (WAIT only)
 *   'H'                             -> 'H'   halt (after it takes effect)
 *   'Q'                             -> 'Q' + 38 bytes of status (below)
 *   'P'                             -> 'P' + 24 x 4 bytes activity counters
 *   'U' first[2] count[2]           -> 'U' n[2] records[n x 9] crc[2]
 *        n = min(count, records stored - first); crc = CRC-16/CCITT-FALSE
 *        (poly 0x1021, init 0xFFFF) over the n x 9 record bytes.
 * Status after 'Q': version, state (0 idle, 1 wait, 2 run, 3 full, 4 done,
 * 5 settling), flags (bit0 full, bit1 halted, bit2 forced, bit3 core view),
 * log2 depth, channels (24), records[2], lost[4], start stamp[6], end
 * stamp[6], current stamp[6], en[3], trig[3], mode, limit[2].
 *
 * SINGLE_PORT = 1 builds the record buffer with one address port (writes
 * take the port; a read-back read that coincides with a write is repeated).
 * The Urbana build uses it: the Spartan-7 part of the prjxray database in the
 * openXC7 release used here has no configuration bits for the RAMB36 port-B
 * widths that a simple dual-port buffer needs, and fasm2frames rejects them.
 */

`default_nettype none

module pe_fpga_scope #(
    parameter integer AW = 14,         // log2 of the record buffer depth
    parameter integer SINGLE_PORT = 0  // 1: one address port for the buffer
) (
    input  wire        clk,
    input  wire        rst,        // synchronous, active high
    input  wire [7:0]  uio_pad,
    input  wire [7:0]  uio_out,
    input  wire [7:0]  uio_oe,
    input  wire [7:0]  uo,
    input  wire [7:0]  ui,
    input  wire        cmd_valid,  // one-cycle pulse from the bridge
    input  wire [7:0]  cmd,
    input  wire [71:0] args,       // payload byte k in [8k+7:8k]
    output wire [7:0]  rsp_data,
    output wire        rsp_valid,
    output wire        rsp_last,
    input  wire        rsp_ready,
    output wire        busy        // armed or recording (LED)
);
  localparam integer DEPTH = 1 << AW;
  localparam [7:0]   VERSION = 8'd1;
  localparam integer TSB = 22;     // stamp bits per record
  localparam integer MB  = 21;     // marker interval 2^MB cycles
  localparam [AW:0]  DEPTH_W = DEPTH;
  localparam [7:0]   AW8 = AW;

  localparam [2:0] ST_IDLE = 3'd0, ST_WAIT = 3'd1, ST_RUN = 3'd2, ST_FULL = 3'd3,
                   ST_DONE = 3'd4, ST_SETTLE = 3'd5;
  localparam [1:0] K_CHANGE = 2'd0, K_START = 2'd1, K_MARK = 2'd2, K_END = 2'd3;

  // ---- sampling: value after launching edge p, registered at edge p+1 -----
  (* ASYNC_REG = "TRUE" *) reg [7:0] pad_s1 = 8'hff;
  reg [7:0]  out_s1 = 8'd0, oe_s1 = 8'd0, uo_s1 = 8'd0, ui_s1 = 8'd0;
  reg [23:0] cur = 24'd0, prev = 24'd0;
  reg        core_view = 1'b0;
  wire [7:0] uio_view = core_view ? ((oe_s1 & out_s1) | (~oe_s1 & pad_s1)) : pad_s1;
  always @(posedge clk) begin
    pad_s1 <= uio_pad;
    out_s1 <= uio_out;
    oe_s1  <= uio_oe;
    uo_s1  <= uo;
    ui_s1  <= ui;
    cur    <= {ui_s1, uo_s1, uio_view};   // edge p+2
    prev   <= cur;
  end
  wire [23:0] diff = cur ^ prev;          // changes launched at edge p, seen before edge p+3

  // ---- free-running stamp: clock edges since configuration, minus 2 ------
  reg [47:0] stamp = 48'hFFFF_FFFF_FFFE;
  always @(posedge clk) stamp <= stamp + 48'd1;

  // ---- configuration and recorder ----------------------------------------
  reg [2:0]  state = ST_IDLE;
  reg [1:0]  settle = 2'd0;
  reg [23:0] en_mask = 24'd0;
  reg [23:0] trig_mask = 24'd0;
  reg [7:0]  mode = 8'd0;
  reg [AW:0] limit = DEPTH_W;
  reg [AW:0] wptr = {(AW + 1){1'b0}};
  reg [AW:0] records = {(AW + 1){1'b0}};
  reg [31:0] lost = 32'd0;
  reg [47:0] start_stamp = 48'd0;
  reg [47:0] end_stamp = 48'd0;
  reg        f_full = 1'b0, f_halted = 1'b0, f_forced = 1'b0;
  reg        force_req = 1'b0, halt_req = 1'b0;

  reg          we = 1'b0;
  reg [AW-1:0] waddr = {AW{1'b0}};
  reg [71:0]   wdata = 72'd0;

  wire [23:0] chg       = diff & en_mask;
  wire        any_chg   = |chg;
  wire        trig_hit  = |(diff & trig_mask);
  wire        mark_due  = (stamp[MB-1:0] == {MB{1'b0}});
  wire        last_slot = (wptr + 1'b1 == limit);
  wire        starting  = (state == ST_WAIT) && !halt_req && (force_req || trig_hit);

  reg       do_write;
  reg [1:0] kind;
  always @* begin
    do_write = 1'b0;
    kind     = K_CHANGE;
    if (starting) begin
      do_write = 1'b1;
      kind     = K_START;
    end else if (state == ST_RUN) begin
      if (halt_req || (last_slot && (any_chg || mark_due))) begin
        do_write = 1'b1;
        kind     = K_END;
      end else if (any_chg) begin
        do_write = 1'b1;
        kind     = K_CHANGE;
      end else if (mark_due) begin
        do_write = 1'b1;
        kind     = K_MARK;
      end
    end
  end

  // command strobes from the responder (below)
  wire arm_cmd   = cmd_valid && cmd == "A";
  wire force_cmd = cmd_valid && cmd == "F";
  wire halt_cmd  = cmd_valid && cmd == "H";
  wire [15:0] arg_limit = args[71:56];

  always @(posedge clk) begin
    we <= 1'b0;
    if (we) records <= {1'b0, waddr} + 1'b1;
    if (rst) begin
      state     <= ST_IDLE;
      force_req <= 1'b0;
      halt_req  <= 1'b0;
      en_mask   <= 24'd0;
      trig_mask <= 24'd0;
      core_view <= 1'b0;
    end else if (arm_cmd) begin
      en_mask     <= args[23:0];
      trig_mask   <= args[47:24];
      mode        <= args[55:48];
      core_view   <= args[49];
      limit       <= (arg_limit < 16'd2 || {16'd0, arg_limit} > DEPTH) ? DEPTH_W : arg_limit[AW:0];
      wptr        <= {(AW + 1){1'b0}};
      records     <= {(AW + 1){1'b0}};
      lost        <= 32'd0;
      start_stamp <= 48'd0;
      end_stamp   <= 48'd0;
      f_full      <= 1'b0;
      f_halted    <= 1'b0;
      f_forced    <= 1'b0;
      force_req   <= !args[48];      // immediate start unless waiting for a trigger
      halt_req    <= 1'b0;
      settle      <= 2'd3;
      state       <= ST_SETTLE;
    end else begin
      if (force_cmd) force_req <= 1'b1;
      if (halt_cmd) halt_req <= 1'b1;
      if (do_write) begin
        we    <= 1'b1;
        waddr <= wptr[AW-1:0];
        wdata <= {cur, chg, kind, stamp[TSB-1:0]};
        wptr  <= wptr + 1'b1;
      end
      case (state)
        ST_SETTLE: begin
          if (settle == 2'd0) state <= ST_WAIT;
          else settle <= settle - 2'd1;
        end
        ST_WAIT: begin
          if (halt_req) begin
            state     <= ST_DONE;
            f_halted  <= 1'b1;
            halt_req  <= 1'b0;
            force_req <= 1'b0;
          end else if (starting) begin
            start_stamp <= stamp;
            f_forced    <= !trig_hit;
            force_req   <= 1'b0;
            state       <= ST_RUN;
          end
        end
        ST_RUN: begin
          if (do_write && kind == K_END) begin
            end_stamp <= stamp;
            if (halt_req) begin
              state    <= ST_DONE;
              f_halted <= 1'b1;
              halt_req <= 1'b0;
            end else begin
              state  <= ST_FULL;
              f_full <= 1'b1;
            end
          end
        end
        ST_FULL: begin
          if (any_chg && lost != 32'hFFFF_FFFF) lost <= lost + 32'd1;
          if (halt_req) begin
            state    <= ST_DONE;
            f_halted <= 1'b1;
            halt_req <= 1'b0;
          end
        end
        default: begin                      // IDLE, DONE
          halt_req  <= 1'b0;
          force_req <= 1'b0;
        end
      endcase
    end
  end

  assign busy = (state == ST_WAIT) || (state == ST_RUN) || (state == ST_SETTLE);

  // ---- record buffer (block RAM) -------------------------------------------
  reg [71:0]   mem [0:DEPTH-1];
  reg [AW-1:0] raddr = {AW{1'b0}};
  reg [71:0]   rdata = 72'd0;
  reg [71:0]   rdata_q = 72'd0;
  wire         rdata_ok;              // rdata_q holds mem[raddr] of the read issued 2 edges ago
  generate
    if (SINGLE_PORT != 0) begin : g_1port
      wire [AW-1:0] addr = we ? waddr : raddr;
      reg           rd_ok = 1'b0, rd_ok_q = 1'b0;
      always @(posedge clk) begin
        if (we) mem[addr] <= wdata;
        rdata   <= mem[addr];
        rdata_q <= rdata;
        rd_ok   <= !we;               // no write took the port in the read cycle
        rd_ok_q <= rd_ok;
      end
      assign rdata_ok = rd_ok_q;
    end else begin : g_2port
      always @(posedge clk) begin
        if (we) mem[waddr] <= wdata;
        rdata   <= mem[raddr];
        rdata_q <= rdata;
      end
      assign rdata_ok = 1'b1;
    end
  endgenerate

  // ---- activity counters -------------------------------------------------
  wire win = starting || (state == ST_RUN);
  reg [24*32-1:0] act = {(24 * 32){1'b0}};
  genvar gi;
  generate
    for (gi = 0; gi < 24; gi = gi + 1) begin : g_act
      always @(posedge clk) begin
        if (arm_cmd)
          act[32*gi +: 32] <= 32'd0;
        else if (win && diff[gi] && act[32*gi +: 32] != 32'hFFFF_FFFF)
          act[32*gi +: 32] <= act[32*gi +: 32] + 32'd1;
      end
    end
  endgenerate

  // ---- CRC-16/CCITT-FALSE --------------------------------------------------
  function [15:0] crc16_byte(input [15:0] c, input [7:0] d);
    integer b;
    reg [15:0] x;
    begin
      x = c ^ {d, 8'h00};
      for (b = 0; b < 8; b = b + 1)
        x = x[15] ? ({x[14:0], 1'b0} ^ 16'h1021) : {x[14:0], 1'b0};
      crc16_byte = x;
    end
  endfunction

  // ---- responder: reply bytes to the bridge ------------------------------
  localparam [2:0] R_IDLE = 3'd0, R_SEND = 3'd1, R_HALT = 3'd2, R_PCNT = 3'd3,
                   R_UREAD = 3'd4, R_UWAIT = 3'd5;
  localparam [1:0] C_DONE = 2'd0, C_PCNT = 2'd1, C_UREC = 2'd2, C_CRC = 2'd3;
  localparam integer SHB = 39;               // longest reply chunk ('Q'), bytes
  localparam [5:0]   SHB6 = SHB;

  reg [2:0]       rstate = R_IDLE;
  reg [1:0]       cont = C_DONE;
  reg [8*SHB-1:0] sh = {(8 * SHB){1'b0}};
  reg [5:0]       sh_n = 6'd0;
  reg             crc_en = 1'b0;
  reg [15:0]      crc = 16'hFFFF;
  reg [4:0]       pidx = 5'd0;
  reg [15:0]      uaddr = 16'd0;
  reg [15:0]      urem = 16'd0;
  reg [1:0]       uwait = 2'd0;

  assign rsp_valid = (rstate == R_SEND);
  assign rsp_data  = sh[7:0];
  assign rsp_last  = (sh_n == 6'd1) && (cont == C_DONE);
  wire   take      = rsp_valid && rsp_ready;
  wire [15:0] crc_next = crc_en ? crc16_byte(crc, sh[7:0]) : crc;

  wire [16:0] u_first = {1'b0, args[15:0]};
  wire [16:0] u_count = {1'b0, args[31:16]};
  wire [16:0] u_have  = records;           // zero-extended (AW <= 15)
  wire [16:0] u_avail = (u_have > u_first) ? (u_have - u_first) : 17'd0;
  wire [16:0] u_n     = (u_count < u_avail) ? u_count : u_avail;

  wire [2:0]  state_rep = state;
  wire [15:0] limit16   = limit;              // zero-extended (AW <= 15)
  wire [15:0] rec16     = records;
  wire [8*SHB-1:0] status_bytes = {
      limit16, mode, trig_mask, en_mask, stamp, end_stamp, start_stamp, lost, rec16,
      8'd24, AW8, {4'd0, core_view, f_forced, f_halted, f_full}, {5'd0, state_rep},
      VERSION, "Q"};
  wire [31:0] act_sel = act[32*pidx +: 32];

  always @(posedge clk) begin
    if (rst) begin
      rstate <= R_IDLE;
      crc_en <= 1'b0;
    end else begin
      case (rstate)
        R_IDLE: begin
          if (cmd_valid) begin
            crc_en <= 1'b0;
            cont   <= C_DONE;
            sh_n   <= 6'd1;
            rstate <= R_SEND;
            case (cmd)
              "A", "F": sh[7:0] <= cmd;
              "H": begin
                sh[7:0] <= "H";
                rstate  <= R_HALT;
              end
              "Q": begin
                sh   <= status_bytes;
                sh_n <= SHB6;
              end
              "P": begin
                sh[7:0] <= "P";
                cont    <= C_PCNT;
                pidx    <= 5'd0;
              end
              "U": begin
                sh[23:0] <= {u_n[15:0], "U"};
                sh_n     <= 6'd3;
                crc      <= 16'hFFFF;
                uaddr    <= args[15:0];
                urem     <= u_n[15:0];
                cont     <= (u_n == 17'd0) ? C_CRC : C_UREC;
              end
              default: sh[7:0] <= "?";
            endcase
          end
        end
        R_HALT: begin
          if (!halt_req && !halt_cmd) rstate <= R_SEND;   // the halt has taken effect
        end
        R_SEND: begin
          if (take) begin
            crc  <= crc_next;
            sh   <= sh >> 8;
            sh_n <= sh_n - 6'd1;
            if (sh_n == 6'd1) begin
              case (cont)
                C_DONE: rstate <= R_IDLE;
                C_PCNT: rstate <= R_PCNT;
                C_UREC: rstate <= R_UREAD;
                C_CRC: begin
                  sh[15:0] <= crc_next;
                  sh_n     <= 6'd2;
                  crc_en   <= 1'b0;
                  cont     <= C_DONE;
                end
              endcase
            end
          end
        end
        R_PCNT: begin
          sh[31:0] <= act_sel;
          sh_n     <= 6'd4;
          cont     <= (pidx == 5'd23) ? C_DONE : C_PCNT;
          pidx     <= pidx + 5'd1;
          rstate   <= R_SEND;
        end
        R_UREAD: begin
          raddr  <= uaddr[AW-1:0];
          uwait  <= 2'd2;
          rstate <= R_UWAIT;
        end
        R_UWAIT: begin                       // BRAM read + output register
          if (uwait == 2'd0 && !rdata_ok) begin
            rstate <= R_UREAD;                 // single port: a write took the read cycle
          end else if (uwait == 2'd0) begin
            sh[71:0] <= rdata_q;
            sh_n     <= 6'd9;
            crc_en   <= 1'b1;
            uaddr    <= uaddr + 16'd1;
            urem     <= urem - 16'd1;
            cont     <= (urem == 16'd1) ? C_CRC : C_UREC;
            rstate   <= R_SEND;
          end else begin
            uwait <= uwait - 2'd1;
          end
        end
        default: rstate <= R_IDLE;
      endcase
    end
  end

endmodule

`default_nettype wire
