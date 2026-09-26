let names = ["uart-tx";"uart-rx";"spi-controller";"spi-target";
  "i2c-write";"i2c-read";"i2c-repeated-start";"i2c-target-write";
  "i2c-target-read";"jtag";"waveform";"event-transmitter"]
(* NXP UM10204 Rev. 7.0 (1 October 2021), Table 11, in ns: the minimum SCL
   period (1/fSCL max), tLOW, tHIGH, tHD;STA, tSU;STA, tSU;STO, tBUF and
   tSU;DAT, then the tVD;DAT maximum. *)
let i2c_modes = [
  "Fast-mode Plus",[1_000;500;260;260;260;260;500;50],450;
  "Fast-mode",[2_500;1_300;600;600;600;600;1_300;100],900;
  "Standard-mode",[10_000;4_700;4_000;4_000;4_700;4_000;4_700;250],3_450]
(* Lower bounds, in clocks, of the same bus parameters for the three I2C
   controller examples at half-period p, and the largest delay from SCL
   falling to an SDA change on fixed-latency paths (the tVD;DAT that
   tools/timing/pe_timing.py checks).  tHIGH, tSU;STA and tSU;STO count the
   two-flop synchronizer between SCL rising at the pad and WAITPIN observing
   it.  An SDA change that follows a PULL comes later, and SCL then stays low
   longer; every SDA change still precedes the SCL release by at least p+2
   clocks, which is what UM10204 Table 11 note [3] requires of a device that
   stretches the SCL LOW period.  pe_timing measures the same quantities on
   the images. *)
let i2c_cycles p = [2*p+7;p+3;p+4;p+2;p+5;p+5;2*p+8;p+2],4
let i2c_meets ~clock_hz p (_,minima,vd_max) =
  let cycles,vd=i2c_cycles p in
  List.for_all2 (fun c t -> c*1_000_000_000>=t*clock_hz) cycles minima
  && vd*1_000_000_000<=vd_max*clock_hz
let i2c_speed_note ~clock_hz p =
  let met,unmet=List.partition (i2c_meets ~clock_hz p) i2c_modes in
  let mode_name (n,_,_)=n in
  let needs mode=
    match List.find_opt (fun q -> i2c_meets ~clock_hz q mode) (List.init 248 (fun i -> i+8)) with
    | Some q -> Printf.sprintf "%s needs half-period >= %d" (mode_name mode) q
    | None -> Printf.sprintf "%s is not reachable at this clock" (mode_name mode) in
  Printf.sprintf "Declared I2C bus speed at the %d Hz annotation: %s (UM10204 Table 11; SCL low >= %d and high >= %d clocks, START/repeated-START/STOP set-up and hold >= %d clocks, bus free >= %d clocks, SDA held >= 2 clocks after SCL falls)%s."
    clock_hz
    (match List.rev_map mode_name met with
     | [] -> "no UM10204 speed mode"
     | [m] -> m
     | ms -> String.concat ", " (List.filteri (fun i _ -> i<List.length ms-1) ms)^" and "^List.nth ms (List.length ms-1))
    (p+3) (p+4) (p+2) (2*p+8)
    (match unmet with [] -> "" | _ -> "; "^String.concat "; " (List.map needs unmet))
(* [byte_lane_shifts]: emit only SHL/SHR counts 0/8/16/24 (targets built with
   shift=byte_lane).  Fused-issue built-ins already comply and are unchanged;
   scalar SPI replaces its 1-bit shifts by ADD tx,tx and builds its MSB mask
   from 0x80, with identical timing; scalar JTAG needs a 1-bit SHR and is
   rejected. *)
let make ?(architecture=Isa.flagship) ?(half_period=32) ?(mode=0)
    ?(clock_hz=50_000_000) ?(byte_lane_shifts=false) name =
  if not (List.mem name names) then invalid_arg ("unknown firmware "^name);
  if half_period<8 || half_period>255 then invalid_arg "half-period must be 8..255";
  if mode<0 || mode>3 then invalid_arg "SPI mode must be 0..3";
  let p=half_period and width=architecture.Isa.data_width in
  let code=ref [] in
  let e ?label ?target ?a ?b ?c ?imm mnemonic =
    code:= !code@[Assembler.node ?label ?target (Isa.instruction ?a ?b ?c ?imm mnemonic)] in
  let wait n = e "WAIT" ~imm:n in
  let jmp target=e "JMP" ~target in
  let pin n=1 lsl n in
  let align ()=if width>8 then e "SHL" ~a:0 ~c:(width-8) in
  (* I2C controller building blocks, pins SCL6/SDA7, open drain.  DIR selects
     the lines pulled low; SDA's logical value is 0 except while OUT shifts a
     data bit, so DIR alone makes START and STOP.  SCL and SDA never change on
     the same edge, SDA changes only while SCL is low (except START/STOP), and
     every SCL release is followed by WAITPIN SCL==1 (clock stretching). *)
  let scl=6 and sda=7 in
  let scl_low ()=e "DIR" ~imm:(pin scl) in
  let both_low ?label ()=e ?label "DIR" ~imm:(pin scl lor pin sda) in
  let release_scl ()=e "DIR" ~imm:(pin sda);e "WAITPIN" ~a:scl ~b:1 in
  (* From SCL low with SDA released, or from an idle bus: release SCL, wait
     until both lines are high, then START (SDA low) and hold it. *)
  let i2c_start ?label ()=e ?label "DIR";e "WAITPIN" ~a:scl ~b:1;e "WAITPIN" ~a:sda ~b:1;
    wait p;e "DIR" ~imm:(pin sda);wait p in
  (* Eight bits MSB first from tx's low byte, entered with both lines low. *)
  let i2c_bits label=align ();e "COUNT" ~imm:7;
    e "OUT" ~a:sda ~c:1 ~label;wait p;release_scl ();wait p;both_low ();e "LOOP" ~target:label in
  (* Ninth clock with SDA released: rx := SDA (0 = ACK).  Ends with SCL low,
     SDA released and SDA's logical value 0. *)
  let i2c_ack_clock ()=scl_low ();wait p;e "DIR";e "WAITPIN" ~a:scl ~b:1;wait p;
    e "LOAD" ~a:1;e "IN" ~a:sda ~c:1;scl_low ();e "SET" in
  (* One byte from the target into rx (zero on entry) with SCL low, then the
     controller NACK clock (SDA stays released), then PUSH with SCL low. *)
  let i2c_receive ~label ~bit_label=e "COUNT" ~imm:7 ~label;
    e "WAIT" ~imm:p ~label:bit_label;e "DIR";e "WAITPIN" ~a:scl ~b:1;wait p;
    e "IN" ~a:sda ~c:1;scl_low ();e "LOOP" ~target:bit_label;
    wait p;e "DIR";e "WAITPIN" ~a:scl ~b:1;wait p;scl_low ();e "PUSH" in
  (* STOP from SCL low: SDA low, release SCL, wait for it high and tSU;STO.
     Register [flag] zero: release SDA (STOP), wait the bus-free time and
     start the next transaction.  Nonzero (a NACK): FAULT 65 releases SDA, so
     the STOP condition and the fault occur on the same edge. *)
  let i2c_stop flag=both_low ~label:"stop" ();wait p;release_scl ();wait p;
    e "JZ" ~a:flag ~target:"bus_free";e "FAULT" ~imm:65;
    e "DIR" ~label:"bus_free";wait p;jmp "transaction" in
  let engine,owned_pins,open_drain,notes = match name with
  | "uart-tx" ->
    let bit=2*p in
    e "SET" ~imm:1;e "DIR" ~imm:1;
    e "PULL" ~label:"next_byte";e "COUNT" ~imm:7;
    e "SET" ~imm:0;wait (bit-2);
    e "OUT" ~a:0 ~label:"data";wait (bit-3);e "LOOP" ~target:"data";
    e "SET" ~imm:1;wait (bit-1);jmp "next_byte";
    0,1,0,[Printf.sprintf "UART 8N1 TX on pin0, LSB first, exact bit period %d clocks (%d Hz nominal clock)." bit clock_hz;
      "TX FIFO low byte supplies each frame; FIFO-empty holding point is idle high; interframe idle includes loop/setup cycles."]
  | "uart-rx" ->
    let bit=2*p in
    e "LIMIT" ~imm:(min 0xffffff (bit*12));
    e "LOAD" ~a:1 ~label:"next_byte";e "COUNT" ~imm:7;
    e "WAITPIN" ~a:1 ~b:1;e "WAITPIN" ~a:1;
    wait (bit+bit/2-2);
    e "IN" ~a:1 ~label:"data";wait (bit-3);e "LOOP" ~target:"data";
    e "SHR" ~a:1 ~c:(width-8);e "MOV" ~a:3 ~b:1;
    e "LOAD" ~a:1;e "IN" ~a:1 ~c:1;
    e "JZ" ~a:1 ~target:"framing_error";
    e "MOV" ~a:1 ~b:3;e "PUSH" ~a:1;jmp "next_byte";
    e "FAULT" ~imm:64 ~label:"framing_error";
    1,0,0,[Printf.sprintf "UART 8N1 RX on pin1, %d clocks per bit, low-byte RX words." bit;
      "Two-flop input synchronization precedes center sampling; stop-bit low produces fault64. Idle/start waits are bounded by 12 bit periods.";
      "A full RX FIFO at completed-frame delivery halts with fault4; the captured byte remains inspectable in the RX register (READ_SELECT6)."]
  | "spi-controller" ->
    let sck=2 and mosi=3 and miso=4 and cs=5 in
    let cpol=mode lsr 1 and cpha=mode land 1 in
    let idle=if cpol=1 then pin sck else 0 in
    let active=if cpol=0 then pin sck else 0 in
    let mask=pin sck lor pin mosi lor pin cs in
    e "SET" ~imm:(idle lor pin cs);e "DIR" ~imm:mask;
    e "PINS" ~imm:(sck lor (mosi lsl 3) lor (miso lsl 6));
    e "PULL" ~label:"next_byte";align ();e "LOAD" ~a:1;
    e "SET" ~imm:idle;
    if architecture.issue="fused" then e "XFER" ~a:8 ~b:p ~c:(28 lor cpol lor (cpha lsl 1))
    else begin
      if byte_lane_shifts then begin e "LOAD" ~a:3 ~imm:0x80;e "SHL" ~a:3 ~c:(width-8) end
      else begin e "LOAD" ~a:3 ~imm:1;e "SHL" ~a:3 ~c:(width-1) end;
      e "COUNT" ~imm:7;
      e "MOV" ~a:2 ~b:0 ~label:"bit";e "AND" ~a:2 ~b:3;
      e "JZ" ~a:2 ~target:"zero";
      let bit_path v =
        if cpha=0 then begin e "SET" ~imm:(idle lor v);wait (p-1);
          e "SET" ~imm:(active lor v);e "IN" ~a:miso ~c:1;wait (p-2);e "SET" ~imm:(idle lor v) end
        else begin e "SET" ~imm:(active lor v);wait (p-1);
          e "SET" ~imm:(idle lor v);e "IN" ~a:miso ~c:1;wait (p-2) end in
      bit_path (pin mosi);jmp "shift";
      e "NOP" ~label:"zero";bit_path 0;
      if byte_lane_shifts then e "ADD" ~a:0 ~b:0 ~label:"shift"
      else e "SHL" ~a:0 ~c:1 ~label:"shift";
      e "LOOP" ~target:"bit"
    end;
    e "SET" ~imm:(idle lor pin cs);e "PUSH";jmp "next_byte";
    2 mod architecture.engine_count,mask,0,[Printf.sprintf "SPI controller mode%d, MSB-first 8-bit full duplex; pins SCK2/MOSI3/MISO4/CSn5." mode;
      Printf.sprintf "Fused transfers have %d-cycle half periods; scalar expansion preserves mode but adds data-dependent instruction overhead between bits." p;
      "One CS assertion per FIFO byte; low-byte receive words. Input synchronization requires external return/setup margin; no maximum external rate asserted."]
  | "spi-target" ->
    let cpol=mode lsr 1 and cpha=mode land 1 in
    e "LIMIT" ~imm:(p*1024);e "SET";e "DIR";
    e "PULL" ~label:"next_byte";align ();e "LOAD" ~a:1;e "COUNT" ~imm:7;
    e "WAITPIN" ~a:5 ~b:1;e "WAITPIN" ~a:5;
    e "DIR" ~imm:16;
    if cpha=0 then begin
      e "OUT" ~a:4 ~c:1 ~label:"bit";
      e "WAITPIN" ~a:2 ~b:(1-cpol);e "IN" ~a:3 ~c:1;e "WAITPIN" ~a:2 ~b:cpol
    end else begin
      e "WAITPIN" ~a:2 ~b:(1-cpol) ~label:"bit";e "OUT" ~a:4 ~c:1;
      e "WAITPIN" ~a:2 ~b:cpol;e "IN" ~a:3 ~c:1
    end;
    e "LOOP" ~target:"bit";e "WAITPIN" ~a:5 ~b:1;e "DIR";e "PUSH" ~a:1;jmp "next_byte";
    2 mod architecture.engine_count,16,0,[Printf.sprintf "SPI target mode%d, MSB-first 8-bit full duplex; SCK2/MOSI3/MISO4/CSn5." mode;
      "CS must be high between bytes, asserted at least 8 system clocks before first edge; each external half period at least 8 clocks.";
      "TX word must be queued before CS assertion; CS is checked at frame boundaries, interrupted frames terminate through LIMIT timeout. MISO releases at deassertion observation.";
      "Full RX FIFO at completed-frame delivery halts with fault4 and retains the captured byte in READ_SELECT6."]
  | "i2c-write" | "i2c-read" ->
    (* Address byte, then either one written byte or one read byte (NACKed by
       the controller), then STOP.  rx holds the last ACK sample (0 = ACK) on
       the way into STOP: an address NACK, or a data NACK in i2c-write, ends
       with STOP and fault 65 (i2c_stop) instead of releasing SCL mid-clock. *)
    e "SET";e "LIMIT" ~imm:(p*4096);e "PULL" ~label:"transaction";
    i2c_start ~label:"start" ();both_low ();i2c_bits "address";i2c_ack_clock ();
    e "JZ" ~a:1 ~target:"address_ack";jmp "stop";
    if name="i2c-write" then begin
      both_low ~label:"address_ack" ();e "PULL";i2c_bits "write_data";i2c_ack_clock ()
    end else begin
      i2c_receive ~label:"address_ack" ~bit_label:"read_data";e "LOAD" ~a:1
    end;
    i2c_stop 1;
    3 mod architecture.engine_count,pin scl lor pin sda,pin scl lor pin sda,
    ["I2C controller pins SCL6/SDA7, external pull-ups required. SCL release always waits for synchronized high and LIMIT bounds stretching.";
      (if name="i2c-write" then "TX words: address+W then one data byte; an address or data NACK ends the transaction with STOP, completed by the fault65 pin release."
       else "TX word: address+R; one returned RX byte followed by NACK and STOP; an address NACK ends the transaction with STOP, completed by the fault65 pin release.");
      "Single-controller bus only; no arbitration-loss detection. Queue a complete transaction: the first PULL waits with the bus idle, later PULL/PUSH holding points keep SCL low.";
      i2c_speed_note ~clock_hz p]
  | "i2c-repeated-start" ->
    (* One shared transmit routine for address+W, register and address+R.  y
       holds -1; x counts down from 4 at next_byte: 3 before address+W, 2
       before the register byte, 1 before address+R and 0 for the read.  An
       odd x needs a START (x=3) or repeated START (x=1).  Each TX word is
       pulled before the START or repeated START that precedes its byte, so
       the bus is idle while the engine waits for a new transaction.  A NACK
       ends with STOP and fault 65 (x is then nonzero, i2c_stop). *)
    e "LIMIT" ~imm:(p*4096);e "NOT" ~a:3;
    e "LOAD" ~a:2 ~imm:4 ~label:"transaction";
    e "ADD" ~a:2 ~b:3 ~label:"next_byte";e "JZ" ~a:2 ~target:"receive";
    e "PULL";e "LOAD" ~a:1 ~imm:1;e "AND" ~a:1 ~b:2;e "JZ" ~a:1 ~target:"send";
    (* Before a repeated START SCL has been low since the ACK clock: keep it
       low for a whole phase (tLOW; the target releases its ACK meanwhile). *)
    e "WAIT" ~imm:p ~label:"start";i2c_start ();
    both_low ~label:"send" ();i2c_bits "bit";i2c_ack_clock ();
    e "JZ" ~a:1 ~target:"next_byte";jmp "stop";
    i2c_receive ~label:"receive" ~bit_label:"read_bit";
    i2c_stop 2;
    3 mod architecture.engine_count,pin scl lor pin sda,pin scl lor pin sda,
    ["I2C register read: TX address+W, register, address+R; repeated START, one RX byte, NACK, STOP.";
      "Pins SCL6/SDA7 require pull-ups. Every released SCL is checked for stretching; LIMIT bounds pin waits; a NACK ends the transaction with STOP, completed by the fault65 pin release.";
      "Shared transmit routine fits 64 words. Queue all three TX words: the first PULL waits with the bus idle, later FIFO holding points keep SCL low. Single-controller bus, no arbitration-loss detection.";
      i2c_speed_note ~clock_hz p]
  | "i2c-target-write" | "i2c-target-read" ->
    let scl=6 and sda=7 in
    let read=name="i2c-target-read" in
    e "SET";e "DIR";e "LIMIT" ~imm:(p*4096);
    e "WAITPIN" ~a:sda ~b:1 ~label:"transaction";e "WAITPIN" ~a:scl ~b:1;
    e "WAITPIN" ~a:sda;e "WAITPIN" ~a:scl;
    e "LOAD" ~a:1;e "COUNT" ~imm:7;
    e "WAITPIN" ~a:scl ~b:1 ~label:"address";e "IN" ~a:sda ~c:1;
    e "WAITPIN" ~a:scl;e "LOOP" ~target:"address";
    e "LOAD" ~a:2 ~imm:(0x84 lor (if read then 1 else 0));e "XOR" ~a:2 ~b:1;
    e "JZ" ~a:2 ~target:"selected";e "FAULT" ~imm:66;
    (* Hold SCL low at a safe edge, then assert ACK and obtain needed resources. *)
    e "DIR" ~imm:192 ~label:"selected";
    if read then begin e "PULL";align () end;
    e "DIR" ~imm:128;e "WAITPIN" ~a:scl ~b:1;e "WAITPIN" ~a:scl;e "DIR";
    e "LOAD" ~a:1;e "COUNT" ~imm:7;
    if read then begin
      e "DIR" ~imm:128;
      e "OUT" ~a:sda ~c:1 ~label:"data";e "WAITPIN" ~a:scl ~b:1;e "WAITPIN" ~a:scl;
      e "LOOP" ~target:"data";e "DIR";
      e "WAITPIN" ~a:scl ~b:1;e "IN" ~a:sda ~c:1;e "WAITPIN" ~a:scl;
      e "JZ" ~a:1 ~target:"unexpected_ack";
      e "WAITPIN" ~a:scl ~b:1;e "WAITPIN" ~a:sda ~b:1;jmp "transaction";
      e "FAULT" ~imm:67 ~label:"unexpected_ack"
    end else begin
      e "WAITPIN" ~a:scl ~b:1 ~label:"data";e "IN" ~a:sda ~c:1;e "WAITPIN" ~a:scl;
      e "LOOP" ~target:"data";
      e "DIR" ~imm:192;e "PUSH";e "DIR" ~imm:128;
      e "WAITPIN" ~a:scl ~b:1;e "WAITPIN" ~a:scl;e "DIR";
      e "WAITPIN" ~a:scl ~b:1;e "WAITPIN" ~a:sda ~b:1;jmp "transaction"
    end;
    3 mod architecture.engine_count,192,192,["I2C 7-bit target address0x42, pins SCL6/SDA7 with external pull-ups; one data byte per START/STOP transaction.";
      "Controller must supply each low/high phase at least 16 system clocks and honor stretching. Target stretches before address ACK for TX availability and before data ACK for RX space.";
      "Different address/direction faults66 and releases bus; this is a dedicated point-to-point target example, not a multi-address shared-bus target.";
      (if read then "Read returns one queued byte; controller must NACK it (ACK faults67), then STOP."
       else "Write accepts one byte, ACKs after RX enqueue, then requires STOP.");
      "Repeated START and multi-byte target framing are outside this example's contract; controller firmware provides repeated START."]
  | "jtag" ->
    e "SET";e "DIR" ~imm:11;
    let cycle tms = e "SET" ~imm:(if tms then 8 else 0);wait p;
      e "SET" ~imm:(1 lor (if tms then 8 else 0));wait p;e "SET" ~imm:(if tms then 8 else 0) in
    (* Six TMS-high cycles guarantee Test-Logic-Reset, then select Shift-DR. *)
    e "COUNT" ~imm:5;e "SET" ~imm:8 ~label:"tap_reset";wait p;
    e "SET" ~imm:9;wait p;e "LOOP" ~target:"tap_reset";
    cycle false;cycle true;cycle false;cycle false;
    e "PINS" ~imm:(0 lor (1 lsl 3) lor (2 lsl 6));
    e "PULL" ~label:"scan";e "LOAD" ~a:1;
    if architecture.issue="fused" then e "XFER" ~a:8 ~b:p ~c:24
    else begin
      if byte_lane_shifts then
        invalid_arg "jtag scalar expansion needs a 1-bit SHR, which byte-lane shifts do not provide";
      e "LOAD" ~a:3 ~imm:1;e "COUNT" ~imm:7;
      e "MOV" ~a:2 ~b:0 ~label:"bit";e "AND" ~a:2 ~b:3;
      e "JZ" ~a:2 ~target:"zero";
      let scan_bit value =
        e "SET" ~imm:value;wait p;e "SET" ~imm:(value lor 1);
        e "IN" ~a:2;wait p;e "SET" ~imm:value in
      scan_bit 2;jmp "shift";e "NOP" ~label:"zero";scan_bit 0;
      e "SHR" ~a:0 ~c:1 ~label:"shift";e "LOOP" ~target:"bit"
    end;
    e "SHR" ~a:1 ~c:(width-8);e "PUSH";jmp "scan";
    0,11,0,["JTAG pins TCK0/TDI1/TDO2/TMS3: TAP reset, enter Shift-DR, stream LSB-first eight-bit scan chunks indefinitely.";
      "Scan-only fixture: no device-specific IR commands or update/exit sequence; halt releases outputs. Use a source program for full device TAP transactions."]
  | "waveform" ->
    e "SET";e "DIR" ~imm:1;e "COUNT" ~imm:15;
    e "SET" ~imm:1 ~label:"pulse";wait (p-2);e "SET";wait (2*p-3);
    e "LOOP" ~target:"pulse";e "TIME" ~a:1;e "PUSH";e "HALT";
    0,1,0,["Custom timed waveform: sixteen pin0 pulses, high for half-period clocks and low for twice half-period clocks; final timestamp in RX FIFO."]
  | "event-transmitter" ->
    e "SET" ~imm:1;e "DIR" ~imm:1;e "WAITEVENT" ~label:"await";
    e "SET";wait (p-2);e "SET" ~imm:1;jmp "await";
    0,1,0,["Event-triggered pin0 low pulse; mailbox delivery does not require host byte service. WAITEVENT has reset LIMIT65535."]
  | _ -> assert false in
  {Assembler.name=name^(if name="spi-controller" || name="spi-target" then Printf.sprintf "-mode%d" mode else "");
   architecture;engine;owned_pins;open_drain;clock_hz;instructions= !code;notes}
