(* Engine-level checks of the line unit (docs/extension.md), ported from
   the extension study's prototype (docs/extension-study.md section 5.1).
   Every expected value comes from an independent bit-level reference
   (encoders, decoders and bitwise CRCs in line_fw.ml), not from the RTL.
   Usage: line_test.exe [--mutation NAME]   (a seeded defect, Line_unit.mutation)
          line_test.exe --dump-firmware   (the firmware listings as words)
   Exit status 1 when any check fails. Processor-level checks on bounded
   queues: line_sys_test.ml. *)
open Hardcaml

let failures = ref 0 and checks = ref 0
let check name ok detail =
  Printf.printf "%s %s%s\n%!" (if ok then "PASS" else "FAIL") name
    (if ok || detail = "" then "" else "  -- " ^ detail);
  incr checks; if not ok then incr failures

open Line_fw

(* ---------- engine harness ---------- *)
type run = { pins_out : int array; oe : int array; pushed : int list; fault : int;
             cycles : int }

(* --dump-firmware: print the words of the firmware listings of line_fw.ml
   (compared with the assembled firmware/ext images by the cocotb suite). *)
let () =
  if Array.to_list Sys.argv = [Sys.argv.(0); "--dump-firmware"] then begin
    List.iter (fun (name, words) ->
        Printf.printf "%s %s\n" name (String.concat " " (List.map (Printf.sprintf "%08x") words)))
      ["relay", relay_prog; "10base-t-udp", eth_prog 17; "can-node", can_node_prog ();
       "can-node-rev2", can_node_prog ~rev:2 (); "usb-ls-in-responder", usb_in_prog ()];
    exit 0
  end

let mutation =
  match Array.to_list Sys.argv with
  | [_] -> None
  | [_; "--mutation"; m] -> Some (Line_unit.mutation_of_string m)
  | _ -> prerr_endline "usage: line_test.exe [--mutation NAME | --dump-firmware]"; exit 2

let make_sim () =
  let config = Config.default in
  let input = Signal.input and output = Signal.output in
  let e = Engine.create ~line:{Line_options.line_unit=Rec16} ?line_mutation:mutation config {
    clock=input "clk" 1; clear=input "clear" 1; start=input "start" 1;
    stop=input "stop" 1; clear_fault=input "clear_fault" 1;
    instruction=input "instruction" 32; image_length=input "image_length" 24;
    ownership=input "ownership" 8; pins=input "pins" 8; timestamp=input "timestamp" 32;
    tx_valid=input "tx_valid" 1; tx_data=input "tx_data" config.data_width;
    rx_ready=input "rx_ready" 1; event=input "event_pending" 1} in
  let circuit = Circuit.create_exn ~name:"ext_engine"
    [output "pc" e.pc; output "running" e.running; output "fault" e.fault;
     output "tx_pop" e.tx_pop; output "rx_push" e.rx_push; output "rx_data" e.rx_data;
     output "values" e.pin_values; output "enables" e.pin_enables] in
  Cyclesim.create circuit

(* [wave t] gives the external pin levels at cycle t; the engine sees them
   through the chip's two-flop synchronizer, modelled as a 2-cycle delay. *)
(* [rx_depth]: RX queue capacity with no host service (rx_ready drops when
   that many words were pushed); default unbounded.  [txq] is the TX queue
   content; callers keep it within the queue depth unless stated. *)
let run_engine ?(own=0xff) ?(max_cycles=20000) ?(wave=fun _ -> 0) ?(bus=None) ?(rx_depth=max_int) ?(tx_after=0) prog txq =
  let sim = make_sim () in
  let set name w v = Cyclesim.in_port sim name := Bits.of_int ~width:w v in
  let get name = Bits.to_int !(Cyclesim.out_port sim name) in
  let getb name = Bits.to_int !(Cyclesim.out_port ~clock_edge:Before sim name) in
  let prog = Array.of_list prog in
  let q = Queue.of_seq (List.to_seq txq) in
  let outs = Array.make max_cycles 0 and oes = Array.make max_cycles 0 in
  let pushed = ref [] in
  let step () = Cyclesim.cycle sim in
  set "clear" 1 1; step (); set "clear" 1 0;
  set "ownership" 8 own; set "image_length" 24 (Array.length prog); set "rx_ready" 1 1;
  set "start" 1 1; step (); set "start" 1 0;
  let t = ref 0 and stop = ref false in
  let hist = Array.make (max_cycles + 4) 0 in
  while not !stop && !t < max_cycles do
    let pc = get "pc" in
    set "instruction" 32 (if pc < Array.length prog then prog.(pc) else 0);
    let txv = not (Queue.is_empty q) && !t >= tx_after in
    set "tx_valid" 1 (if txv then 1 else 0);
    set "tx_data" 32 (if txv then Queue.peek q else 0);
    set "rx_ready" 1 (if List.length !pushed < rx_depth then 1 else 0);
    (* external level: either a scripted waveform or a bus function of our
       own recent output (CAN wired-AND); synchronizer delay 2 cycles *)
    let ext = match bus with
      | None -> wave !t
      | Some f -> f !t (fun k -> if k < 0 then 0xff else outs.(k) lor (lnot oes.(k) land 0xff)) in
    hist.(!t) <- ext;
    set "pins" 8 (if !t >= 2 then hist.(!t - 2) else 0);
    Cyclesim.cycle_before_clock_edge sim;
    if getb "tx_pop" = 1 then ignore (Queue.pop q);
    if getb "rx_push" = 1 then pushed := getb "rx_data" :: !pushed;
    Cyclesim.cycle_at_clock_edge sim; Cyclesim.cycle_after_clock_edge sim;
    outs.(!t) <- get "values"; oes.(!t) <- get "enables";
    if get "running" = 0 then stop := true;
    incr t
  done;
  { pins_out = Array.sub outs 0 !t; oe = Array.sub oes 0 !t; pushed = List.rev !pushed;
    fault = get "fault"; cycles = !t }


let run_engine_usb prog txq host =
  let max_cycles = 12000 in
  let sim = make_sim () in
  let set name w v = Cyclesim.in_port sim name := Bits.of_int ~width:w v in
  let get name = Bits.to_int !(Cyclesim.out_port sim name) in
  let getb name = Bits.to_int !(Cyclesim.out_port ~clock_edge:Before sim name) in
  let prog = Array.of_list prog in
  let q = Queue.of_seq (List.to_seq txq) in
  let outs = Array.make max_cycles 0 and oes = Array.make max_cycles 0 in
  let hist = Array.make max_cycles 0 in
  let pushed = ref [] in
  set "clear" 1 1; Cyclesim.cycle sim; set "clear" 1 0;
  set "ownership" 8 0x03; set "image_length" 24 (Array.length prog); set "rx_ready" 1 1;
  set "start" 1 1; Cyclesim.cycle sim; set "start" 1 0;
  let t = ref 0 and stop = ref false in
  while not !stop && !t < max_cycles do
    let pc = get "pc" in
    set "instruction" 32 (if pc < Array.length prog then prog.(pc) else 0);
    set "tx_valid" 1 (if Queue.is_empty q then 0 else 1);
    set "tx_data" 32 (if Queue.is_empty q then 0 else Queue.peek q);
    let ext = if !t >= 1 && oes.(!t - 1) land 3 = 3 then outs.(!t - 1) land 3 else host !t in
    hist.(!t) <- ext;
    set "pins" 8 (if !t >= 2 then hist.(!t - 2) else 2);
    Cyclesim.cycle_before_clock_edge sim;
    if getb "tx_pop" = 1 then ignore (Queue.pop q);
    if getb "rx_push" = 1 then pushed := getb "rx_data" :: !pushed;
    Cyclesim.cycle_at_clock_edge sim; Cyclesim.cycle_after_clock_edge sim;
    outs.(!t) <- get "values"; oes.(!t) <- get "enables";
    if get "running" = 0 then stop := true;
    incr t
  done;
  { pins_out = Array.sub outs 0 !t; oe = Array.sub oes 0 !t; pushed = List.rev !pushed;
    fault = get "fault"; cycles = !t }

(* ---------- 1. CRC unit against check values of "123456789" ---------- *)
let crc_case name ~msb ~poly ~init ~xorout ~width ~expect =
  let s = bytes_of_string "123456789" in
  let word k = (* 4 bytes, wire order *)
    List.fold_left (fun acc j -> let byte = try List.nth s (4*k + j) with _ -> 0 in
      if msb then acc lor (byte lsl (24 - 8*j)) else acc lor (byte lsl (8*j))) 0 [0;1;2;3] in
  let prog = [pins ~ck:1 ~out:2 ~inp:0; ltim 1; lcfg ()] @ load32 x_r init y_r
             @ [crc_set x_r] @ poly_ins poly @ [
             pull; xline ~msb ~drive:true ~crc:true 32; pull; xline ~msb ~drive:true ~crc:true 32;
             pull; xline ~msb ~drive:true ~crc:true 8; crc_get rx_r; push; halt] in
  let r = run_engine ~own:0x04 prog [word 0; word 1; word 2] in
  let got = match r.pushed with v :: _ -> v | [] -> -1 in
  let got = if msb then (got lsr (crc_w - width)) land ((1 lsl width) - 1)
    else got land ((1 lsl width) - 1) in
  let got = got lxor xorout in
  let bits = List.concat_map (fun b -> if msb then bits_msb b 8 else bits_lsb b 8) s in
  let reference = (if msb then crc_normal ~width ~poly:(poly lsr (crc_w - width)) ~init bits
                   else crc_reflected ~poly ~init bits) lxor xorout in
  check (Printf.sprintf "crc %s check value" name)
    (r.fault = 0 && got = expect && reference = expect)
    (Printf.sprintf "fault %d rtl %#x reference %#x expect %#x" r.fault got reference expect)

(* ---------- 2. USB-LS DATA0 transmit: NRZI, stuffing, CRC16, EOP ---------- *)
let usb_tx () =
  let data = [0xFF; 0xFF; 0x01; 0x80; 0x3F; 0x7E; 0x00; 0xAA] in
  let w k = List.fold_left (fun a j -> a lor (List.nth data (4*k+j) lsl (8*j))) 0 [0;1;2;3] in
  let prog = [pins ~ck:1 ~out:0 ~inp:0; lcfg ~code:1 ~stuff:6 ~pair:true ~init:0 ();
              ltim ~frac:171 16; set_ 0b10; dir 0b11;
              load x_r 0xffff; crc_set x_r] @ poly_ins 0xa001 @ [
              pull; xline ~drive:true 16;
              pull; xline ~drive:true ~crc:true 32; pull; xline ~drive:true ~crc:true 32;
              crc_get x_r; not_ x_r; mov 0 x_r; xline ~drive:true 16;
              wait 31; set_ 0; wait 64; set_ 0b10; wait 31; dir 0; halt] in
  let r = run_engine ~own:0x03 prog [0xC380; w 0; w 1] in
  (* independent decoder: D+ = bit0, D- = bit1; find first K (D+=1,D-=0) *)
  let n = r.cycles in
  let st t = let v = r.pins_out.(t) land 3 and e = r.oe.(t) land 3 in
    if e <> 3 then 'Z' else match v with 1 -> 'K' | 2 -> 'J' | 0 -> '0' | _ -> '1' in
  let t0 = let rec f t = if t >= n then n else if st t = 'K' then t else f (t+1) in f 0 in
  let bit_t = 50_000_000. /. 1_500_000. in
  let rec sample k acc prev =
    let t = t0 + int_of_float ((float k +. 0.5) *. bit_t) in
    if t >= n then List.rev acc, t else
    match st t with
    | '0' -> List.rev acc, t
    | s -> sample (k+1) ((if s = prev then 1 else 0) :: acc) s in
  let raw, se0_t = sample 0 [] 'J' in
  let rec destuff run acc = function
    | [] -> List.rev acc
    | b :: tl -> if run = 6 then (if b <> 0 then [] else destuff 0 acc tl)
      else destuff (if b = 1 then run+1 else 0) (b :: acc) tl in
  let bits = destuff 0 [] raw in
  let byte k = List.fold_left (+) 0 (List.init 8 (fun j ->
      try List.nth bits (8*k+j) lsl j with _ -> 0)) in
  let nbytes = List.length bits / 8 in
  let got = List.init nbytes byte in
  let crc = crc_reflected ~poly:0xa001 ~init:0xffff
      (List.concat_map (fun b -> bits_lsb b 8) data) lxor 0xffff in
  let expect = [0x80; 0xC3] @ data @ [crc land 0xff; crc lsr 8] in
  let se0_start = let rec f t = if t > 0 && st (t-1) = '0' then f (t-1) else t in f se0_t in
  let se0_len = let rec f t = if t < n && st t = '0' then f (t+1) else t - se0_start in f se0_start in
  let last_edge = (* last J/K transition before the SE0 *)
    let rec f t = if t <= t0 then t0 else if st t <> st (t-1) && st t <> '0' then t else f (t-1) in
    f (se0_start - 1) in
  let ones_run = let m = ref 0 and c = ref 0 in
    List.iter (fun b -> if b = 1 then (incr c; m := max !m !c) else c := 0) raw; !m in
  check "usb-ls tx: bytes after NRZI+destuff (SYNC, PID, 8 data, CRC16)"
    (r.fault = 0 && got = expect && List.length bits mod 8 = 0)
    (Printf.sprintf "fault %d got [%s]" r.fault (String.concat " " (List.map (Printf.sprintf "%02x") got)));
  check "usb-ls tx: stuffing present (max raw run of 1s = 6)" (ones_run = 6)
    (Printf.sprintf "max run %d" ones_run);
  let per = float (se0_start - t0) /. float (List.length raw) in
  check "usb-ls tx: mean bit period 33.33 cycles (+/-0.2%)"
    (abs_float (per -. bit_t) /. bit_t < 0.002) (Printf.sprintf "%.3f cycles" per);
  ignore last_edge;
  check "usb-ls tx: SE0 width 1.25-1.50 us (63-75 cycles) then J"
    (se0_len >= 63 && se0_len <= 75 && st (se0_start + se0_len) = 'J')
    (Printf.sprintf "se0 %d cycles, then %c" se0_len (st (se0_start + se0_len)))

(* ---------- 3. USB-LS receive: token + DATA0 with stuffing, SE0 end ---------- *)
let usb_rx () =
  let bit_t = 50_000_000. /. (1_500_000. *. 1.0025) in (* host 0.25% fast *)
  let pkt_bits bytes = List.concat_map (fun b -> bits_lsb b 8) bytes in
  let token = (* IN, addr 0x3a, endp 1, CRC5 *)
    let fields = bits_lsb 0x3a 7 @ bits_lsb 1 4 in
    let c5 = crc_reflected ~poly:0x14 ~init:0x1f fields lxor 0x1f in
    pkt_bits [0x80; 0x69] @ fields @ bits_lsb c5 5 in
  let data = [0xFF; 0xFF; 0xFF; 0x00; 0x7F; 0xFE; 0x12; 0x34] in
  let c16 = crc_reflected ~poly:0xa001 ~init:0xffff (pkt_bits data) lxor 0xffff in
  let data_pkt = pkt_bits ([0x80; 0xC3] @ data) @ bits_lsb c16 16 in
  (* waveform: idle J, packet, SE0 x2, J *)
  let segs = ref [] and t = ref 100. in
  let emit bits =
    let line = nrzi 0 (stuff_ones 6 bits) in (* line: 0 = J (D+ low) *)
    List.iter (fun l -> segs := (!t, if l = 1 then 1 else 2) :: !segs; t := !t +. bit_t) line;
    segs := (!t, 0) :: !segs; t := !t +. 2. *. bit_t;
    segs := (!t, 2) :: !segs; t := !t +. 400. in
  emit token; emit data_pkt;
  let segs = List.rev !segs in
  let wave tt = let tt = float tt in
    List.fold_left (fun acc (s, v) -> if tt >= s then v else acc) 2 segs in
  let rx_packet ~crc_init ~poly ~words =
    [lcfg ~code:1 ~stuff:6 ~pair:true ~se0:true ~init:1 (); waitpin 0 1; ltim ~frac:171 ~delay:33 16;
     xline ~sample:true 7; xline ~sample:true 8; push; load x_r crc_init; crc_set x_r]
    @ poly_ins poly
    @ List.concat_map (fun n -> [xline ~sample:true ~crc:true n; push]) words
    @ [xline ~sample:true 8; lstat rx_r; push; crc_get rx_r; push] in
  let prog = [pins ~ck:1 ~out:0 ~inp:0; limit 0xffffff]
             @ rx_packet ~crc_init:0x1f ~poly:0x14 ~words:[16]
             @ rx_packet ~crc_init:0xffff ~poly:0xa001 ~words:[32; 32; 16] @ [halt] in
  let r = run_engine ~own:0 ~max_cycles:20000 ~wave prog [] in
  let p = Array.of_list r.pushed in
  let ok_len = Array.length p = 10 in
  let g k = if k < Array.length p then p.(k) else -1 in
  let res5 = crc_reflected ~poly:0x14 ~init:0x1f
      (bits_lsb 0x3a 7 @ bits_lsb 1 4 @ bits_lsb
         (crc_reflected ~poly:0x14 ~init:0x1f (bits_lsb 0x3a 7 @ bits_lsb 1 4) lxor 0x1f) 5) in
  let res16 = crc_reflected ~poly:0xa001 ~init:0xffff (pkt_bits data @ bits_lsb c16 16) in
  let w0 = List.fold_left (fun a j -> a lor (List.nth data j lsl (8*j))) 0 [0;1;2;3] in
  let w1 = List.fold_left (fun a j -> a lor (List.nth data (4+j) lsl (8*j))) 0 [0;1;2;3] in
  check "usb-ls rx: token PID, addr/endp/CRC5 bits, SE0 status, CRC5 residue"
    (r.fault = 0 && ok_len && (g 0 lsr 24) = 0x69 && (g 1 lsr 16) = (0x3a lor (1 lsl 7) lor
       ((crc_reflected ~poly:0x14 ~init:0x1f (bits_lsb 0x3a 7 @ bits_lsb 1 4) lxor 0x1f) lsl 11))
     && g 2 land 1 = 1 && (g 2 lsr 8) land 0x3f = 8 && g 3 = res5 && res5 = 0x06)
    (Printf.sprintf "fault %d pushed [%s]" r.fault
       (String.concat " " (Array.to_list (Array.map (Printf.sprintf "%08x") p))));
  check "usb-ls rx: DATA0 destuffed words, CRC16 residue 0xb001, SE0 status"
    (ok_len && (g 4 lsr 24) = 0xC3 && g 5 = w0 && g 6 = w1 && g 8 land 1 = 1
     && (g 8 lsr 2) land 1 = 0 && g 9 = res16 && res16 = 0xb001)
    (Printf.sprintf "words %08x %08x crc %#x ref %#x" (g 5) (g 6) (g 9) res16)

(* ---------- 4. CAN: frame TX (stuff, CRC15) and arbitration loss ---------- *)
let can_prog ~ndata =
  [pins ~ck:2 ~out:0 ~inp:1; lcfg ~stuff:5 ~any:true ~arb:true ~init:1 ();
   ltim 50; set_ 1; dir 1; load x_r 0; crc_set x_r] @ poly_ins (0x4599 lsl (crc_w - 15))
  @ [
   pull; xline ~msb:true ~drive:true ~sample:true ~crc:true 19; push]
  @ (if ndata > 0 then [pull; xline ~msb:true ~drive:true ~sample:true ~crc:true (8*ndata); push] else [])
  @ [crc_get x_r; shl x_r (32 - crc_w); mov 0 x_r; xline ~msb:true ~drive:true ~sample:true 15; push;
     lstat rx_r; push;
     lcfg ~arb:true ~init:1 (); load 0 0xffff; shl 0 16; xline ~msb:true ~drive:true ~sample:true 10;
     push; halt]
let can_decode samples = (* destuff from SOF; returns data bits until 5-run limit ends *)
  let rec go last run acc = function
    | [] -> List.rev acc
    | b :: tl -> if run = 5 then go b 1 acc tl (* drop stuff bit *)
      else if b = last then go b (run+1) (b :: acc) tl else go b 1 (b :: acc) tl in
  go 2 0 [] samples
let can () =
  let data = [0xA5; 0x00] in
  let body, c15 = can_frame_bits 0x123 data in
  let bus_single t out = (* our TXD looped back through a 5-cycle transceiver *)
    out (t - 5) land 1 in
  let r = run_engine ~own:0x01 ~bus:(Some (fun t out -> 0xfd lor (bus_single t out lsl 1)))
      (can_prog ~ndata:2) [can_word 0x123 2; 0xA5000000] in
  (* sample our TXD at mid-bit from the SOF edge *)
  let n = r.cycles in
  let tx t = (r.pins_out.(t) lor lnot r.oe.(t)) land 1 in
  let sof = let rec f t = if t >= n then n else if tx t = 0 then t else f (t+1) in f 0 in
  let nstuffed = List.length (stuff_any 5 (body @ bits_msb c15 15)) in
  let samples = List.init nstuffed (fun k -> tx (sof + 100*k + 50)) in
  let bits = can_decode samples in
  let expect = body @ bits_msb c15 15 in
  check "can tx: destuffed frame = SOF..DATA + CRC15 (independent encoder)"
    (r.fault = 0 && bits = expect && samples = stuff_any 5 expect)
    (Printf.sprintf "fault %d got %d bits, expect %d" r.fault (List.length bits) (List.length expect));
  let st = try List.nth r.pushed 3 with _ -> -1 in
  check "can tx: no arbitration loss when alone" (st land 2 = 0) (Printf.sprintf "%#x" st);
  (* arbitration: another node sends id 0x120 (wins at ID bit 9) from the same SOF *)
  let other, oc15 = can_frame_bits 0x120 [0x5A] in
  let other_line = stuff_any 5 (other @ bits_msb oc15 15) in
  let other_start = ref (-1) in
  let bus t out =
    let ours = out (t - 5) land 1 in
    if !other_start < 0 && ours = 0 then other_start := t;
    let theirs = if !other_start < 0 then 1 else
        let k = (t - !other_start) / 100 in
        if k < List.length other_line then List.nth other_line k else 1 in
    0xfd lor ((ours land theirs) lsl 1) in
  let r2 = run_engine ~own:0x01 ~bus:(Some bus) (can_prog ~ndata:2) [can_word 0x123 2; 0xA5000000] in
  let st2 = try List.nth r2.pushed 3 with _ -> -1 in
  let rx_hdr = (try List.nth r2.pushed 0 with _ -> -1) land 0x7ffff in
  let expect_hdr = List.fold_left (fun a b -> 2*a + b) 0
      (List.filteri (fun k _ -> k < 19) other) in
  let n2 = r2.cycles in
  let tx2 t = (r2.pins_out.(t) lor lnot r2.oe.(t)) land 1 in
  let sof2 = let rec f t = if t >= n2 then n2 else if tx2 t = 0 then t else f (t+1) in f 0 in
  let lost_bit = 1 + 9 in (* SOF + 9 ID bits before the differing one *)
  let dominant_after = ref 0 in
  for t = sof2 + 100 * (lost_bit + 1) to n2 - 1 do
    if tx2 t = 0 then incr dominant_after done;
  check "can arbitration: lost flag set, received winner's header, never dominant after loss"
    (r2.fault = 0 && st2 land 2 = 2 && rx_hdr = expect_hdr && !dominant_after = 0)
    (Printf.sprintf "status %#x rx %#x expect %#x dominant-after %d" st2 rx_hdr expect_hdr
       !dominant_after)

(* ---------- 5. 10BASE-T Manchester streaming at 40 MHz (P = 2) ---------- *)
let manchester () =
  let words = [0x55555555; 0xD5555555; 0x12345678; 0x9ABCDEF0] in
  let prog = [pins ~ck:1 ~out:0 ~inp:0; lcfg ~code:2 ~pair:true (); ltim 2; set_ 0; dir 3;
              count 3; pull; xline ~drive:true 32; loop 6;
              wait 2; set_ 1; wait 11; set_ 0; wait 20; set_ 1; wait 2; set_ 0; halt] in
  let r = run_engine ~own:0x03 prog words in
  let n = r.cycles in
  let p t = r.pins_out.(t) land 1 and m t = (r.pins_out.(t) lsr 1) land 1 in
  let start = let rec f t = if t >= n then n else if p t <> m t then t else f (t+1) in f 0 in
  let bits = List.concat_map (fun w -> bits_lsb w 32) words in
  let ok = ref true and bad = ref "" in
  List.iteri (fun k b ->
      let c = start + 4*k in
      let first = [p c; p (c+1)] and second = [p (c+2); p (c+3)] in
      if first <> [1-b; 1-b] || second <> [b; b] || m c <> 1 - p c || m (c+2) <> 1 - p (c+2)
      then (if !ok then bad := Printf.sprintf "bit %d at cycle %d" k c; ok := false)) bits;
  check "10base-t: 128 Manchester cells, 4 cycles each, no gap between words, pair complementary"
    (r.fault = 0 && !ok) !bad;
  let fin = start + 4 * 128 in
  let hi = let rec f t = if t < n && p t = 1 && m t = 0 then f (t+1) else t in f (fin - 2) in
  check "10base-t: TP_IDL high after last bit then both low"
    (hi - (fin - 2) >= 10 && p hi = 0 && m hi = 0)
    (Printf.sprintf "high for %d cycles" (hi - (fin - 2)))


(* ======================= firmware sketches, engine level ======================= *)
let fw_words : (string * int) list ref = ref []
let note_size name prog = fw_words := (name, List.length prog) :: !fw_words

(* 10BASE-T: at engine level the TX queue is fed without limit, standing in
   for the relay chain that sys_test stages on bounded queues. *)
let ethernet () =
  let frame = udp_frame (bytes_of_string "TT protocol emulator") in
  let words = words_le frame in
  let prog = eth_prog (List.length words) in
  note_size "10base-t-udp" prog;
  let r = run_engine ~own:0x03 ~max_cycles:700_000 prog words in
  let n = r.cycles in
  let p t = r.pins_out.(t) land 1 and m t = (r.pins_out.(t) lsr 1) land 1 in
  let start = let rec f t = if t >= n then n else if p t <> m t then t else f (t+1) in f 0 in
  let all = preamble @ frame in
  let nbytes = List.length all in
  let nb = 8 * nbytes in
  let bits = List.init nb (fun k -> p (start + 4*k + 2)) in
  let valid = List.for_all (fun k -> let c = start + 4*k in
      p c = 1 - p (c+2) && p c = p (c+1) && p (c+2) = p (c+3) && m c = 1 - p c) (List.init nb Fun.id) in
  let got = List.init nbytes (fun k -> List.fold_left (+) 0 (List.init 8 (fun j -> List.nth bits (8*k+j) lsl j))) in
  let residue = crc_reflected ~poly:0xedb88320 ~init:0xffffffff
      (List.concat_map (fun b -> bits_lsb b 8) (List.filteri (fun k _ -> k >= 8) got)) in
  check (Printf.sprintf "10base-t udp (engine, TX queue unbounded): %d-byte frame (on-chip preamble..FCS) decoded, CRC-32 residue 0xdebb20e3" nbytes)
    (r.fault = 0 && valid && got = all && residue = 0xdebb20e3)
    (Printf.sprintf "fault %d valid %b residue %#x" r.fault valid residue);
  let fin = start + 4 * nb in
  let hi_end = let rec f t = if t < n && p t = 1 && m t = 0 then f (t+1) else t in f fin in
  let pulses = ref [] in
  for t = hi_end + 1 to n - 1 do
    if p t = 1 && p (t-1) = 0 then pulses := t :: !pulses done;
  let pulses = List.rev !pulses in
  let width t0 = let rec f t = if t < n && p t = 1 then f (t+1) else t - t0 in f t0 in
  check (Printf.sprintf "10base-t udp: TP_IDL positive %d ns after the last cell boundary, then silence"
           (25 * (hi_end - fin)))
    (hi_end - fin >= 10 && hi_end - fin <= 14 && p hi_end = 0 && m hi_end = 0) (Printf.sprintf "%d cycles" (hi_end - fin));
  (match pulses with
   | t1 :: _ -> check "10base-t udp: first NLP 16.0 ms after TP_IDL, 100 ns wide"
                  (abs (t1 - hi_end - 640_000) < 40 && width t1 = 4)
                  (Printf.sprintf "at +%d cycles, width %d" (t1 - hi_end) (width t1))
   | [] -> check "10base-t udp: NLP present" false "none")

(* CAN node: TX queue 3 words, RX queue bounded at 8 words, no host service *)
let can_node () =
  let prog = can_node_prog () in
  note_size "can-node" prog;
  (* (a) transmit a frame; an independent receiver acknowledges in the ACK slot *)
  let data = [0xDE;0xAD;0xBE;0xEF;0x01;0x23;0x45;0x67] in
  let body, c15 = can_frame_bits 0x2A5 data in
  let stuffed = stuff_any 5 (body @ bits_msb c15 15) in
  let ack_slot = List.length stuffed + 1 in
  let sof_t = ref (-1) in
  let bus t out =
    let ours = out (t - 5) land 1 in
    if !sof_t < 0 && ours = 0 then sof_t := t;
    let ack = !sof_t >= 0 && (t - !sof_t) / 100 = ack_slot in
    0xfd lor ((if ack then 0 else ours) lsl 1) in
  let r = run_engine ~own:0x01 ~max_cycles:30000 ~rx_depth:8 ~bus:(Some bus) prog (can_tx_words 0x2A5 data) in
  let tx t = (r.pins_out.(t) lor lnot r.oe.(t)) land 1 in
  let n = r.cycles in
  let sof = let rec f t = if t >= n then n else if tx t = 0 then t else f (t+1) in f 0 in
  let samples = List.init (List.length stuffed) (fun k -> tx (sof + 100*k + 50)) in
  let tail = List.init 14 (fun k -> tx (sof + 100 * (List.length stuffed + k) + 50)) in
  check "can node tx: stuffed frame + CRC15 on TXD, recessive tail, ACK seen (no fault 70)"
    (r.fault = 0 && samples = stuffed && List.for_all (( = ) 1) tail)
    (Printf.sprintf "fault %d match %b" r.fault (samples = stuffed));
  let bus_noack t out = 0xfd lor ((out (t - 5) land 1) lsl 1) in
  let r0 = run_engine ~own:0x01 ~max_cycles:30000 ~rx_depth:8 ~bus:(Some bus_noack) prog (can_tx_words 0x2A5 data) in
  check "can node tx: missing ACK faults with code 70" (r0.fault = 70) (Printf.sprintf "fault %d" r0.fault);
  (* (b) receive two frames (DLC 8 and DLC 5) at the minimum spacing: the
     second starts 10 recessive bits after our ACK (3rd intermission bit);
     check packed words, CRC and our ACK; RX queue 8 deep, never drained *)
  let frames = [0x3A5, [0x12;0x9A;0xF0;0x0F;0x55;0xAA;0x00;0xFF]; 0x0F0, [0x01;0x02;0x03;0x04;0x05]] in
  let bus_of (bus : can_bus) = fun t out -> 0xfd lor (bus.step t (out (t - 5) land 1) lsl 1) in
  let busb = can_bus ~ours:[] [other ~after:0 ~not_before:400 11 (List.nth frames 0); other ~after:0 10 (List.nth frames 1)] in
  let r2 = run_engine ~own:0x01 ~max_cycles:40000 ~rx_depth:8 ~bus:(Some (bus_of busb)) prog [] in
  let starts = List.map snd (busb.sent ()) in
  let expect = List.concat_map (fun (id, d) -> can_rx_expect id d) frames in
  let got = r2.pushed in
  let words_ok = List.length got = List.length expect
                 && List.for_all2 (fun g (e, m) -> g land m = e) got expect in
  let acks_ok r starts frames =
    let txr t = (r.pins_out.(t) lor lnot r.oe.(t)) land 1 in
    List.length starts = List.length frames && List.for_all2 (fun s f ->
      let len = List.length (can_line f) in
      let slot = s + 100 * (len + 1) in
      let mine = List.filter (fun t -> t < r.cycles && txr t = 0) (List.init (100 * (len + 14)) (fun k -> s + k)) in
      mine <> [] && List.for_all (fun t -> t >= slot - 5 && t < slot + 110) mine
      && List.length mine >= 95 && List.length mine <= 105) starts frames in
  let acks = acks_ok r2 starts frames in
  check (Printf.sprintf "can node rx: 2 frames (DLC 8, then DLC 5 in the 3rd intermission bit) -> %d packed pushes <= 8, CRC ok, ACK in each ACK slot only"
           (List.length expect))
    (r2.fault = 0 && words_ok && acks && busb.errors () = [] && busb.lost () = [])
    (Printf.sprintf "fault %d pushed [%s] acks %b errors [%s]" r2.fault
       (String.concat " " (List.map (Printf.sprintf "%x") got)) acks (String.concat "; " (busb.errors ())));
  (* (c) arbitration loss: another node sends 0x120 from the same SOF; the
     sketch stops with fault 72 and never drives dominant after the loss *)
  let oth, oc15 = can_frame_bits 0x120 [0x5A] in
  let other_line = stuff_any 5 (oth @ bits_msb oc15 15) in
  let other_start = ref (-1) in
  let bus_arb t out =
    let ours = out (t - 5) land 1 in
    if !other_start < 0 && ours = 0 then other_start := t;
    let theirs = if !other_start < 0 then 1 else
        let k = (t - !other_start) / 100 in
        if k < List.length other_line then List.nth other_line k else 1 in
    0xfd lor ((ours land theirs) lsl 1) in
  let r3 = run_engine ~own:0x01 ~max_cycles:30000 ~rx_depth:8 ~bus:(Some bus_arb) prog (can_tx_words 0x123 data) in
  let n3 = r3.cycles in
  let tx3 t = (r3.pins_out.(t) lor lnot r3.oe.(t)) land 1 in
  let sof3 = let rec f t = if t >= n3 then n3 else if tx3 t = 0 then t else f (t+1) in f 0 in
  let dominant_after = ref 0 in
  for t = sof3 + 100 * 11 to n3 - 1 do if tx3 t = 0 then incr dominant_after done;
  check "can node arbitration loss: fault 72, never dominant after the lost bit"
    (r3.fault = 72 && !dominant_after = 0)
    (Printf.sprintf "fault %d dominant-after %d" r3.fault !dominant_after);
  (* (d) TX, then RX at the minimum spacing: a DLC-0 frame in the 3rd
     intermission bit after our frame, then a DLC-4 frame at the end of
     intermission after it *)
  let txr (r : run) t = if t < 0 || t >= r.cycles then 1 else (r.pins_out.(t) lor lnot r.oe.(t)) land 1 in
  let frame_ok r st f = st >= 0 && List.for_all Fun.id (List.mapi (fun j b -> txr r (st + 100 * j + 50) = b) (can_line f)) in
  let a = (0x2A5, data) in
  let rx_frames = [(0x3F0, []); (0x001, [0xA5;0x5A;0xC3;0x3C])] in
  let run_d rev =
    let bus = can_bus ~ours:[a] [other 10 (List.nth rx_frames 0); other 11 (List.nth rx_frames 1)] in
    let r = run_engine ~own:0x01 ~max_cycles:40000 ~rx_depth:8 ~bus:(Some (bus_of bus)) (can_node_prog ~rev ()) (can_tx_words 0x2A5 data) in
    let st = match bus.our_sofs () with s :: _ -> s - 5 | [] -> -1 in
    let exp = List.concat_map (fun (id, d) -> can_rx_expect id d) rx_frames in
    let wok = List.length r.pushed = List.length exp && List.for_all2 (fun g (e, m) -> g land m = e) r.pushed exp in
    r.fault = 0 && frame_ok r st a && wok && acks_ok r (List.map snd (bus.sent ())) rx_frames && bus.errors () = []
    && bus.lost () = [],
    Printf.sprintf "fault %d tx %b pushed [%s] errors [%s]" r.fault (frame_ok r st a)
      (String.concat " " (List.map (Printf.sprintf "%x") r.pushed)) (String.concat "; " (bus.errors ())) in
  let ok, d = run_d 3 in
  check "can node tx then rx at the minimum spacing: DLC 0 in the 3rd intermission bit, DLC 4 at the end of intermission -> 3 pushes, CRC ok, ACKs" ok d;
  let ok2, d2 = run_d 2 in
  check "can node negative control: the revision-2 tails miss the SOF at the minimum spacing after our frame" (not ok2) d2;
  (* (e) two queued frames back to back; the other node starts with our 2nd
     frame, loses arbitration, retransmits at the end of intermission, and
     is received *)
  let b = (0x155, [0x11;0x22;0x33;0x44;0x55;0x66;0x77;0x88]) and x = List.nth frames 0 in
  let bus = can_bus ~ours:[a; b] [other 11 x] in
  let r = run_engine ~own:0x01 ~max_cycles:60000 ~rx_depth:8 ~bus:(Some (bus_of bus))
      prog (can_tx_words 0x2A5 data @ can_tx_words (fst b) (snd b)) in
  let sofs = List.map (fun s -> s - 5) (bus.our_sofs ()) in
  let off = match sofs with [s1; s2] -> s2 - s1 - 100 * (List.length (can_line a) + 13) | _ -> min_int in
  let exp_bit = can_loss_bit x b in
  let exp = can_rx_expect (fst x) (snd x) in
  let wok = List.length r.pushed = List.length exp && List.for_all2 (fun g (e, m) -> g land m = e) r.pushed exp in
  let fok = match sofs with [s1; s2] -> frame_ok r s1 a && frame_ok r s2 b | _ -> false in
  check (Printf.sprintf "can node back to back: 2nd SOF 0..20 cycles after the end of intermission; the other node's simultaneous 0x3A5 loses at stuffed bit %d, retransmits, is received" exp_bit)
    (r.fault = 0 && fok && off >= 0 && off <= 20 && wok && bus.errors () = []
     && (match bus.lost () with [(_, _, k)] -> k = exp_bit | _ -> false) && List.length (bus.sent ()) = 1)
    (Printf.sprintf "fault %d frames %b offset %d sofs [%s] pushed [%s] lost [%s] sent %d errors [%s]" r.fault fok off
       (String.concat " " (List.map string_of_int (bus.our_sofs ())))
       (String.concat " " (List.map (Printf.sprintf "%x") r.pushed))
       (String.concat " " (List.map (fun (i, s, k) -> Printf.sprintf "%#x@%d/bit%d" i s k) (bus.lost ())))
       (List.length (bus.sent ())) (String.concat "; " (bus.errors ())));
  Printf.printf "INFO can back-to-back: 2nd SOF %d cycles after the end of intermission\n" off;
  (* (f) RX, then a TX that became pending during the reception: SOF just
     after the end of intermission that follows our ACK *)
  let bus = can_bus ~ours:[a] [other ~after:0 ~not_before:400 11 x] in
  let r = run_engine ~own:0x01 ~max_cycles:40000 ~rx_depth:8 ~tx_after:3000 ~bus:(Some (bus_of bus))
      prog (can_tx_words 0x2A5 data) in
  let xs = match bus.sent () with [(_, s)] -> s | _ -> -1 in
  let ack_start = if xs < 0 then -1 else
      let rec f t = if t >= r.cycles then -1 else if txr r t = 0 then t else f (t + 1) in f (xs - 5) in
  let st = match bus.our_sofs () with [s] -> s - 5 | _ -> -1 in
  let off = if ack_start < 0 || st < 0 then min_int else st - (ack_start + 1200) in
  let wok = List.length r.pushed = List.length exp && List.for_all2 (fun g (e, m) -> g land m = e) r.pushed exp in
  check "can node rx then tx: a TX that became pending during reception starts 0..20 cycles after the end of intermission; frame exact and ACKed"
    (r.fault = 0 && wok && frame_ok r st a && off >= 0 && off <= 20 && bus.errors () = [] && bus.lost () = [])
    (Printf.sprintf "fault %d pushed [%s] tx %b offset %d errors [%s]" r.fault
       (String.concat " " (List.map (Printf.sprintf "%x") r.pushed)) (frame_ok r st a) off
       (String.concat "; " (bus.errors ())));
  Printf.printf "INFO can rx-then-tx: SOF %d cycles after the end of intermission\n" off

(* USB-LS: one engine answers an IN token with a queued DATA1 packet *)
let usb_in () =
  let prog = usb_in_prog () in
  note_size "usb-ls-in-responder" prog;
  let bit_t = 50_000_000. /. 1_500_000. in
  let line = usb_in_token_line () in
  let t0 = 200. in
  let eop_j = t0 +. float (List.length line + 2) *. bit_t in
  let host t = let tt = float t in
    if tt < t0 then 2 else
    let k = int_of_float ((tt -. t0) /. bit_t) in
    if k < List.length line then (if List.nth line k = 1 then 1 else 2)
    else if k < List.length line + 2 then 0 else 2 in
  let data = [0x01; 0x00; 0x05; 0x00; 0x00; 0x00; 0x00; 0x00] in
  let r = run_engine_usb prog (0x4B80 :: words_le data) host in
  let n = r.cycles in
  let st t = if r.oe.(t) land 3 = 3 then (match r.pins_out.(t) land 3 with 1 -> 'K' | 2 -> 'J' | 0 -> '0' | _ -> '1')
    else 'Z' in
  let first_k = let rec f t = if t >= n then n else if st t = 'K' then t else f (t+1) in f 0 in
  let gap = float first_k -. eop_j in
  let rec sample k acc prev =
    let t = first_k + int_of_float ((float k +. 0.5) *. bit_t) in
    if t >= n then List.rev acc else match st t with
      | '0' | 'Z' -> List.rev acc
      | s -> sample (k+1) ((if s = prev then 1 else 0) :: acc) s in
  let raw = sample 0 [] 'J' in
  let rec destuff run acc = function
    | [] -> List.rev acc
    | b :: tl -> if run = 6 then destuff 0 acc tl else destuff (if b = 1 then run+1 else 0) (b :: acc) tl in
  let bits = destuff 0 [] raw in
  let got = List.init (List.length bits / 8) (fun k ->
      List.fold_left (+) 0 (List.init 8 (fun j -> List.nth bits (8*k+j) lsl j))) in
  let c16 = crc_reflected ~poly:0xa001 ~init:0xffff (List.concat_map (fun b -> bits_lsb b 8) data) lxor 0xffff in
  check "usb-ls IN responder: DATA1 + 8 bytes + CRC16 sent after the IN token"
    (r.fault = 0 && got = [0x80; 0x4B] @ data @ [c16 land 0xff; c16 lsr 8])
    (Printf.sprintf "fault %d got [%s]" r.fault (String.concat " " (List.map (Printf.sprintf "%02x") got)));
  check (Printf.sprintf "usb-ls IN responder: turnaround %.2f bit times (2 .. 6.5 allowed)" (gap /. bit_t))
    (gap /. bit_t >= 2. && gap /. bit_t <= 6.5) ""

let () =
  (match mutation with
   | Some _ -> Printf.printf "INFO seeded defect %s\n%!" Sys.argv.(2)
   | None -> ());
  crc_case "CRC-16/USB" ~msb:false ~poly:0xa001 ~init:0xffff ~xorout:0xffff ~width:16 ~expect:0xb4c8;
  crc_case "CRC-5/USB" ~msb:false ~poly:0x14 ~init:0x1f ~xorout:0x1f ~width:5 ~expect:0x19;
  crc_case "CRC-15/CAN" ~msb:true ~poly:(0x4599 lsl (crc_w - 15)) ~init:0 ~xorout:0 ~width:15 ~expect:0x059e;
  (* The presets in the bit order of the other form (docs/extension.md). *)
  crc_case "CRC-16/CCITT-FALSE" ~msb:true ~poly:0x1021 ~init:0xffff ~xorout:0 ~width:16 ~expect:0x29b1;
  crc_case "CRC-16/ARC" ~msb:false ~poly:0xa001 ~init:0 ~xorout:0 ~width:16 ~expect:0xbb3d;
  crc_case "CRC-16/KERMIT" ~msb:false ~poly:0x8408 ~init:0 ~xorout:0 ~width:16 ~expect:0x2189;
  crc_case "CRC-16/UMTS" ~msb:true ~poly:0x8005 ~init:0 ~xorout:0 ~width:16 ~expect:0xfee8;
  usb_tx (); usb_rx (); can (); manchester ();
  let unit_checks = !checks and unit_failures = !failures in
  Printf.printf "unit checks: %d, %d failure(s)\n%!" unit_checks unit_failures;
  (try ethernet () with e -> check "10base-t udp" false (Printexc.to_string e));
  (try can_node () with e -> check "can node" false (Printexc.to_string e));
  (try usb_in () with e -> check "usb in" false (Printexc.to_string e));
  Printf.printf "firmware checks: %d, %d failure(s)\n" (!checks - unit_checks) (!failures - unit_failures);
  List.iter (fun (n, w) -> Printf.printf "SIZE %s %d words (%s the 64-word store)\n" n w
                (if w <= 64 then "fits" else "EXCEEDS")) (List.rev !fw_words);
  Printf.printf "TOTAL %d checks, %d failure(s)\n" !checks !failures;
  if !failures > 0 then exit 1
