let names = ["uart-tx";"uart-rx";"spi-controller";"spi-target";
  "i2c-write";"i2c-read";"i2c-repeated-start";"i2c-target-write";
  "i2c-target-read";"jtag";"waveform";"event-transmitter"]
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
    let scl=6 and sda=7 in
    let both=pin scl lor pin sda in
    let low ()=e "DIR" ~imm:both in
    let high ()=e "DIR" ~imm:(pin sda);e "WAITPIN" ~a:scl ~b:1 in
    let start label = e "DIR" ~imm:0 ~label;e "WAITPIN" ~a:scl ~b:1;e "WAITPIN" ~a:sda ~b:1;
      wait p;e "DIR" ~imm:(pin sda);wait p;low () in
    let send ?(prefilled=false) label =
      low ();if not prefilled then e "PULL";align ();e "COUNT" ~imm:7;
      e "OUT" ~a:sda ~c:1 ~label;wait p;high ();wait p;low ();e "LOOP" ~target:label;
      (* SDA value must be low so logical OE controls open-drain release. *)
      e "SET";e "DIR" ~imm:(pin scl);wait p;e "DIR";
      e "WAITPIN" ~a:scl ~b:1;wait p;e "LOAD" ~a:1;e "IN" ~a:sda ~c:1;
      e "DIR" ~imm:(pin scl);e "JZ" ~a:1 ~target:(label^"_ack");e "FAULT" ~imm:65;
      e "NOP" ~label:(label^"_ack") in
    let recv label =
      e "SET";e "DIR" ~imm:(pin scl);e "LOAD" ~a:1;e "COUNT" ~imm:7;
      e "WAIT" ~imm:p ~label;e "DIR";e "WAITPIN" ~a:scl ~b:1;wait p;
      e "IN" ~a:sda ~c:1;e "DIR" ~imm:(pin scl);e "LOOP" ~target:label;
      (* One-byte read ends with NACK, leaving SDA released. *)
      wait p;e "DIR";e "WAITPIN" ~a:scl ~b:1;wait p;e "DIR" ~imm:(pin scl);
      e "PUSH" in
    let stop ()=e "SET";low ();wait p;high ();wait p;e "DIR";wait p in
    e "SET";e "LIMIT" ~imm:(p*4096);e "PULL" ~label:"transaction";start "start";
    send ~prefilled:true "address";
    if name="i2c-write" then send "write_data" else recv "read_data";
    stop ();jmp "transaction";
    3 mod architecture.engine_count,both,both,["I2C controller pins SCL6/SDA7, external pull-ups required. SCL release always waits for synchronized high and LIMIT bounds stretching.";
      (if name="i2c-write" then "TX words: address+W then one data byte; address/data NACK faults65."
       else "TX word: address+R; one returned RX byte followed by NACK and STOP.");
      "Single-controller bus only; no arbitration-loss detection. Queue a complete transaction before START; PULL/PUSH holding points keep SCL low."]
  | "i2c-repeated-start" ->
    (* Share the send-byte routine between address(W), register, and address(R).
       x counts remaining bytes and y holds -1 across transactions. This leaves
       room for a complete repeated-START transaction in 64 instructions. *)
    let scl=6 and sda=7 in
    let low ()=e "DIR" ~imm:192 in
    let high ()=e "DIR" ~imm:128;e "WAITPIN" ~a:scl ~b:1 in
    e "LIMIT" ~imm:(p*4096);e "NOT" ~a:3;
    e "LOAD" ~a:2 ~imm:3 ~label:"transaction";
    e "DIR" ~label:"start";e "WAITPIN" ~a:scl ~b:1;e "WAITPIN" ~a:sda ~b:1;
    wait p;e "DIR" ~imm:128;wait p;low ();
    e "DIR" ~imm:192 ~label:"send";e "PULL";align ();e "COUNT" ~imm:7;
    e "OUT" ~a:sda ~c:1 ~label:"bit";wait p;high ();wait p;low ();e "LOOP" ~target:"bit";
    e "SET";e "DIR" ~imm:64;wait p;e "DIR";e "WAITPIN" ~a:scl ~b:1;
    wait p;e "LOAD" ~a:1;e "IN" ~a:sda ~c:1;e "DIR" ~imm:64;
    e "JZ" ~a:1 ~target:"ack";e "FAULT" ~imm:65;
    e "ADD" ~a:2 ~b:3 ~label:"ack";e "JZ" ~a:2 ~target:"receive";
    e "LOAD" ~a:0 ~imm:1;e "XOR" ~a:0 ~b:2;e "JZ" ~a:0 ~target:"start";jmp "send";
    e "SET" ~label:"receive";e "DIR" ~imm:64;e "LOAD" ~a:1;e "COUNT" ~imm:7;
    e "WAIT" ~imm:p ~label:"read_bit";e "DIR";e "WAITPIN" ~a:scl ~b:1;wait p;
    e "IN" ~a:sda ~c:1;e "DIR" ~imm:64;e "LOOP" ~target:"read_bit";
    wait p;e "DIR";e "WAITPIN" ~a:scl ~b:1;wait p;e "DIR" ~imm:64;e "PUSH";
    low ();wait p;high ();wait p;e "DIR";wait p;jmp "transaction";
    3 mod architecture.engine_count,192,192,["I2C register read: TX address+W, register, address+R; repeated START, one RX byte, NACK, STOP.";
      "Pins SCL6/SDA7 require pull-ups. Every released SCL is checked for stretching; LIMIT bounds pin waits; ACK failures fault65.";
      "Shared transmit routine fits 64 words. Queue all three TX words before START; FIFO holding points keep SCL low. Single-controller bus, no arbitration-loss detection."]
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
