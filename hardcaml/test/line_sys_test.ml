(* Processor-level checks of the line-unit firmware on BOUNDED queues
   (docs/extension.md), ported from the extension study's prototype
   (docs/extension-study.md section 5.2).

   The whole chip (Processor.create_refinement_model: four engines, host
   nibble port, FIFOs of the configured depth, mover) runs in Cyclesim.  The
   host loads programs, sets routes and prefills queues through the host port,
   STARTs the protocol engine, and then performs NO host transaction for the
   rest of the run: this models the free-running chip clock, during which the
   synchronous host port cannot be used (risk R1).  Afterwards the host reads
   status and RX queues again.

   Usage: line_sys_test.exe CONFIG.json   (a config with options.line_unit).
   The config's reset style is used as is.  Asynchronous resets are emulated
   exactly as in variant_test (Cyclesim applies them only through
   Cyclesim.reset: the raw reset clears every register as soon as it is
   asserted and at every edge while asserted; for async_sync_release the two
   synchronizer flops keep clocking while the chip-wide net holds the other
   registers). Exit 1 on any failure. *)
[@@@warning "-32-69-26-27-35"]
open Hardcaml
open Line_fw
module O = Variant_options

let failures = ref 0 and checks = ref 0
let check name ok detail =
  Printf.printf "%s %s%s\n%!" (if ok then "PASS" else "FAIL") name
    (if ok || detail = "" then "" else "  -- " ^ detail);
  incr checks; if not ok then incr failures
let info fmt = Printf.printf ("INFO " ^^ fmt ^^ "\n%!")

let config_path = if Array.length Sys.argv > 1 then Sys.argv.(1) else failwith "usage: line_sys_test CONFIG.json"
let rc = Refinement_config.load config_path
let depth = rc.Refinement_config.architecture.Config.fifo_words
let options = rc.Refinement_config.options
let reset_style = options.O.reset
let async = O.asynchronous options
let () = if not (Line_options.enabled rc.Refinement_config.line) then failwith "config has no line unit"
let circuit = Processor.create_refinement_model rc

type sys = {
  sim : Cyclesim.t_port_list;
  mutable t : int;
  outs : int array; oes : int array;
  mutable env : int -> (int -> int) -> (int -> int) -> int;  (* t, out k, oe k -> uio_in *)
  mutable faulted_at : int;  (* first cycle with uo_out[7] (fault) high, -1 if none *)
  mutable rst_n : int;
}

let make ?(max_cycles=100_000) env =
  let s = { sim = Cyclesim.create ~config:Cyclesim.Config.trace_all circuit; t = 0;
            outs = Array.make max_cycles 0; oes = Array.make max_cycles 0; env; faulted_at = -1;
            rst_n = 1 } in
  s

let set s name w v = Cyclesim.in_port s.sim name := Bits.of_int ~width:w v
let reg_value sim name = match Cyclesim.lookup_reg_by_name sim name with
  | Some r -> Cyclesim.Reg.to_int r | None -> failwith ("no traced register " ^ name)
let set_reg sim name v = match Cyclesim.lookup_reg_by_name sim name with
  | Some r -> Cyclesim.Reg.of_int r v | None -> failwith ("no traced register " ^ name)
let tick s ui =
  let t = s.t in
  let out k = if k < 0 || k >= t then 0 else s.outs.(k)
  and oe k = if k < 0 || k >= t then 0 else s.oes.(k) in
  set s "ui_in" 8 ui; set s "uio_in" 8 (s.env t out oe land 0xff);
  set s "ena" 1 1; set s "rst_n" 1 s.rst_n;
  let raw = 1 - s.rst_n in
  if async && raw = 1 then Cyclesim.reset s.sim;
  Cyclesim.cycle_before_clock_edge s.sim;
  let uo = Bits.to_int !(Cyclesim.out_port ~clock_edge:Before s.sim "uo_out") in
  let held = match reset_style with
    | O.Async_sync_release -> raw = 1 || reg_value s.sim "reset_sync_2" = 0
    | O.Async -> raw = 1
    | O.Sync | O.Sync_registered -> false in
  Cyclesim.cycle_at_clock_edge s.sim;
  if held then begin
    if raw = 1 then Cyclesim.reset s.sim
    else begin
      let s1 = reg_value s.sim "reset_sync_1" and s2 = reg_value s.sim "reset_sync_2" in
      Cyclesim.reset s.sim; set_reg s.sim "reset_sync_1" s1; set_reg s.sim "reset_sync_2" s2
    end
  end;
  Cyclesim.cycle_after_clock_edge s.sim;
  s.outs.(t) <- Bits.to_int !(Cyclesim.out_port s.sim "uio_out");
  s.oes.(t) <- Bits.to_int !(Cyclesim.out_port s.sim "uio_oe");
  if uo land 128 <> 0 && s.faulted_at < 0 then s.faulted_at <- t;
  s.t <- t + 1; uo
let idle s n = for _ = 1 to n do ignore (tick s 0) done
let reset s = s.rst_n <- 0; idle s 2; s.rst_n <- 1; idle s 4

(* ---------- host port (docs/isa.md "Host interface") ---------- *)
let enter s w = ignore (tick s (w lsl 6)); ignore (tick s (w lsl 6))
(* write words in window w; stop at the first word whose first nibble is not
   accepted within [patience] cycles; returns the number of words accepted *)
let write_words ?(patience=400) s w words =
  enter s w;
  let accepted = ref 0 and stop = ref false in
  List.iter (fun word ->
      if not !stop then begin
        let ok = ref true in
        for nib = 0 to 7 do
          if !ok then begin
            let ui = (w lsl 6) lor 16 lor ((word lsr (4*nib)) land 15) in
            let rec accept k = k > 0 && (tick s ui land 16 <> 0 || accept (k - 1)) in
            ok := accept patience
          end
        done;
        if !ok then incr accepted else stop := true
      end) words;
  ignore (tick s (w lsl 6)); !accepted
let command s op v =
  if write_words s 0 [(op lsl 24) lor (v land 0xffffff)] <> 1 then failwith "host command not accepted"
let read_word ?(patience=400) s w =
  ignore (tick s (((w + 1) land 3) lsl 6));   (* window change: fresh snapshot *)
  enter s w;
  let v = ref 0 and ok = ref true in
  for nib = 0 to 7 do
    if !ok then begin
      let rec take k = if k = 0 then None else
          let uo = tick s ((w lsl 6) lor 32) in
          if uo land 32 <> 0 then Some (uo land 15) else take (k - 1) in
      match take patience with Some x -> v := !v lor (x lsl (4*nib)) | None -> ok := false
    end
  done;
  ignore (tick s (w lsl 6)); if !ok then Some !v else None
let select s e = command s 0 e
let load s e ~own prog =
  select s e; command s 1 0; command s 3 own;
  if write_words s 1 prog <> List.length prog then failwith "program write";
  command s 2 (List.length prog)
let route s ~src ~dst = command s 6 (src lor (dst lsl 2) lor (1 lsl 4) lor (0xffff lsl 5))
let start s mask = command s 4 mask
let prefill s e words = select s e; write_words s 2 words
let status s e = select s e; command s 8 0; read_word s 0   (* bit0 running, bit3 fault, 15:8 code *)
let drain s e = select s e;
  let rec go acc = match read_word ~patience:60 s 3 with Some v -> go (v :: acc) | None -> List.rev acc in
  go []
let fault_code s e = match status s e with Some v -> (v lsr 8) land 0xff | None -> -1

(* Relay chain for engine 0: engines 1..r run relay_prog, routes k+1 -> k;
   words enter at the tail (engine r, or engine 0 when r = 0). *)
let chain s r =
  for k = 1 to r do load s k ~own:0 relay_prog; route s ~src:k ~dst:(k - 1) done;
  if r > 0 then start s (((1 lsl (r + 1)) - 1) land 0xe)

(* ---------- 1. staging capacity ---------- *)
let measured = Array.make 4 (-1)
let capacity () =
  List.iter (fun r ->
      let s = make (fun _ _ _ -> 0) in
      reset s; chain s r;
      let n = prefill s r (List.init 80 (fun k -> 0x1000 + k)) in
      let expect = depth + r * (2 * depth + 1) in
      measured.(r) <- n;
      check (Printf.sprintf "staging capacity, engine 0 + %d relay(s), %d-deep queues: %d words (expect %d = d + r(2d+1))"
               r depth n expect) (n = expect && s.faulted_at < 0) "")
    [0; 1; 2; 3]

(* ---------- 2. 10BASE-T UDP frame staged on chip ---------- *)
(* [negative]: one relay fewer than needed; the host stages what fits and
   starts anyway, so engine 0's TX queue runs dry mid-frame and the decode
   must fail (negative control of the no-gap check). *)
let ethernet ?(negative=false) () =
  let frame = udp_frame (bytes_of_string "TT protocol emulator") in
  let words = words_le frame in
  let nw = List.length words in
  let needed = let rec f r = if r > 3 then -1 else if measured.(r) >= nw then r else f (r + 1) in f 0 in
  if not negative then
    info "10base-t: %d frame words (%d bytes + on-chip preamble), %d relay(s) needed at depth %d"
      nw (List.length frame) needed depth;
  let needed = if negative then needed - 1 else needed in
  if needed < 0 then check "10base-t udp: frame stageable on chip" false "exceeds 3-relay capacity" else begin
    let s = make ~max_cycles:720_000 (fun _ _ _ -> 0) in
    reset s; chain s needed;
    load s 0 ~own:0x03 (eth_prog nw);
    let staged = prefill s needed words in
    idle s 50;
    start s 1;
    let t_start = s.t in
    idle s (if negative then 20_000 else 660_000);
    let n = s.t in
    let p t = s.outs.(t) land 1 and m t = (s.outs.(t) lsr 1) land 1 in
    let start_c = let rec f t = if t >= n then n else if p t <> m t then t else f (t+1) in f t_start in
    let all = preamble @ frame in
    let nbytes = List.length all in
    let nb = 8 * nbytes in
    let valid = List.for_all (fun k -> let c = start_c + 4*k in c + 3 < n &&
        p c = 1 - p (c+2) && p c = p (c+1) && p (c+2) = p (c+3) && m c = 1 - p c) (List.init nb Fun.id) in
    let bits = List.init nb (fun k -> if start_c + 4*k + 2 < n then p (start_c + 4*k + 2) else 0) in
    let got = List.init nbytes (fun k -> List.fold_left (+) 0 (List.init 8 (fun j -> List.nth bits (8*k+j) lsl j))) in
    let residue = crc_reflected ~poly:0xedb88320 ~init:0xffffffff
        (List.concat_map (fun b -> bits_lsb b 8) (List.filteri (fun k _ -> k >= 8) got)) in
    let ok = staged = nw && s.faulted_at < 0 && valid && got = all && residue = 0xdebb20e3 in
    if negative then
      check (Printf.sprintf "10base-t udp (chip, %d-deep queues, %d relay(s), negative control): only %d of %d words stageable, frame corrupted as expected"
               depth needed staged nw)
        (staged < nw && not ok && start_c < n && List.filteri (fun k _ -> k < 8 + 4 * staged) got
                                                  = List.filteri (fun k _ -> k < 8 + 4 * staged) all)
        "frame must start and match up to the staged words, then break"
    else
    check (Printf.sprintf "10base-t udp (chip, %d-deep queues, %d relay(s)): %d words staged, %d-byte frame decoded, no gap, CRC-32 residue 0xdebb20e3"
             depth needed staged nbytes) ok
      (Printf.sprintf "staged %d fault-at %d valid %b residue %#x" staged s.faulted_at valid residue);
    if not negative then begin
    let fin = start_c + 4 * nb in
    let hi_end = let rec f t = if t < n && p t = 1 && m t = 0 then f (t+1) else t in f fin in
    let t1 = let rec f t = if t >= n then -1 else if p t = 1 && p (t-1) = 0 then t else f (t+1) in f (hi_end + 1) in
    check "10base-t udp (chip): TP_IDL 250-350 ns, first NLP 16.0 ms later"
      (hi_end - fin >= 10 && hi_end - fin <= 14 && t1 > 0 && abs (t1 - hi_end - 640_000) < 40)
      (Printf.sprintf "TP_IDL %d cycles, NLP at +%d" (hi_end - fin) (t1 - hi_end));
    (match status s 0 with
     | Some v -> check "10base-t udp (chip): engine 0 still running, no fault" (v land 1 = 1 && v land 8 = 0)
                   (Printf.sprintf "status %#x" v)
     | None -> check "10base-t status read" false "")
    end
  end

(* ---------- 3. CAN node on bounded queues ---------- *)
(* The other node is Ext_fw.can_bus: it starts its frames 11 (end of
   intermission) or 10 (3rd intermission bit) recessive bits after the last
   dominant bit, arbitrates, and acknowledges our frames.  TXD = uio0 (pulled
   up when undriven), RXD = uio1, 5-cycle transceiver loop. *)
let can_a = (0x2A5, [0xDE;0xAD;0xBE;0xEF;0x01;0x23;0x45;0x67])
let can_b = (0x155, [0x11;0x22;0x33;0x44;0x55;0x66;0x77;0x88])   (* beats 0x3A5 *)
let can_c = (0x7A5, [0x0F;0x1E;0x2D;0x3C;0x4B;0x5A;0x69;0x78])   (* loses to 0x120 *)
let b8 = (0x3A5, [0x12;0x9A;0xF0;0x0F;0x55;0xAA;0x00;0xFF]) and c5 = (0x0F0, [0x01;0x02;0x03;0x04;0x05])
and d8 = (0x111, [0x10;0x20;0x30;0x40;0x50;0x60;0x70;0x80])
and z0 = (0x3F0, []) and f4 = (0x001, [0xA5;0x5A;0xC3;0x3C]) and o1 = (0x555, [0x00])
and y1 = (0x120, [0x5A])

type can_obs = { staged : int; nw : int; n : int; txd : int -> int; code : int; got : int list;
                 bus : can_bus; faulted : int; feed : int; drain_route : bool; t_start : int;
                 sy : sys }

(* TX words go straight to engine 0 when they fit its queue, else through one
   relay (engine 2 -> 0; one relay stages >= 7 words at every depth).  RX goes
   through a drain relay (engine 0 -> 1, read back from engine 1) when the
   expected RX words exceed the queue, unless [rx_drain] says otherwise.
   [until]: stop the free run early (cycle after START), for the reset check. *)
let can_session ?(rev=3) ?rx_drain ?until ~ours ~theirs () =
  let bus = can_bus ~ours theirs in
  let env t out oe = let txd k = (out k lor lnot (oe k)) land 1 in 0xfd lor (bus.step t (txd (t - 5)) lsl 1) in
  let bits = List.fold_left (fun a f -> a + List.length (can_line f) + 13) 0
      (ours @ List.map (fun o -> (o.o_id, o.o_data)) theirs @ List.map (fun o -> (o.o_id, o.o_data)) theirs) in
  let span = 100 * bits + 3000 in
  let s = make ~max_cycles:(span + 60_000) env in
  reset s;
  let words = List.concat_map (fun (id, d) -> can_tx_words id d) ours in
  let nw = List.length words in
  let rx_words = List.fold_left (fun a o -> a + can_rx_words (o.o_id, o.o_data)) 0 theirs in
  let feed = if depth >= nw then 0 else 2 in
  let drain_route = match rx_drain with Some d -> d | None -> rx_words > depth in
  if feed > 0 then (load s 2 ~own:0 relay_prog; route s ~src:2 ~dst:0);
  if drain_route then (load s 1 ~own:0 relay_prog; route s ~src:0 ~dst:1);
  load s 0 ~own:0x01 (can_node_prog ~rev ());
  let mask = (if feed > 0 then 4 else 0) lor (if drain_route then 2 else 0) in
  if mask <> 0 then start s mask;
  let staged = prefill s feed words in
  idle s 50; start s 1;
  let t_start = s.t in
  idle s (match until with Some u -> u | None -> span);
  let n = s.t in
  let txd t = if t < 0 || t >= n then 1 else (s.outs.(t) lor lnot s.oes.(t)) land 1 in
  let code, got = if until <> None then 0, [] else
      let c = fault_code s 0 in c, drain s (if drain_route then 1 else 0) in
  { staged; nw; n; txd; code; got; bus; faulted = s.faulted_at; feed; drain_route; t_start; sy = s }

(* our frames on TXD, sampled mid-bit from each SOF the bus saw (bus time = TXD + 5) *)
let our_frames o ours =
  let sofs = o.bus.our_sofs () in
  List.mapi (fun k f -> match List.nth_opt sofs k with
      | None -> (f, -1, false)
      | Some sb -> let st = sb - 5 in
        let line = can_line f in
        (f, st, List.mapi (fun j b -> o.txd (st + 100 * j + 50) = b) line |> List.for_all Fun.id)) ours
let hex l = String.concat " " (List.map (Printf.sprintf "%x") l)
let rx_ok o frames =
  let expect = List.concat_map (fun (id, d) -> can_rx_expect id d) frames in
  List.length o.got = List.length expect && List.for_all2 (fun g (e, m) -> g land m = e) o.got expect,
  List.length expect
let relays o = (if o.feed > 0 then ", TX relay" else "") ^ (if o.drain_route then ", RX drain relay" else "")
let spacing_label theirs = String.concat "," (List.map (fun o -> string_of_int o.o_r) theirs)

(* TX of [can_a], then RX of [frames] at the given spacings, no host service *)
let can_rx_case ?(rev=3) ~label frames rs =
  let theirs = List.map2 (fun f r -> other r f) frames rs in
  let o = can_session ~rev ~ours:[can_a] ~theirs () in
  let tx_ok = List.for_all (fun (_, _, ok) -> ok) (our_frames o [can_a]) in
  let rok, ne = rx_ok o frames in
  let ok = o.staged = 3 && tx_ok && o.code = 0 && o.faulted < 0 && rok && o.bus.errors () = []
           && o.bus.lost () = [] && List.length (o.bus.sent ()) = List.length frames in
  let name = Printf.sprintf "can node (chip, %d-deep queues%s): TX 8-byte frame + ACK, then RX %s (other node starts %s recessive bits after the last dominant bit) -> %d packed words, no fault"
      depth (relays o) label (spacing_label theirs) ne in
  ok, name, Printf.sprintf "staged %d tx-match %b fault %d rx [%s] lost %d errors [%s]" o.staged tx_ok o.code (hex o.got)
    (List.length (o.bus.lost ())) (String.concat "; " (o.bus.errors ()))

let can_checks () =
  (* 1-2: minimum spacing after our TX and between received frames *)
  let ok, name, d = can_rx_case ~label:"DLC 8, DLC 5" [b8; c5] [11; 10] in check name ok d;
  let ok, name, d = can_rx_case ~label:"DLC 0, DLC 4, DLC 1" [z0; f4; o1] [10; 11; 10] in check name ok d;
  (* 3: two queued frames back to back; the other node starts at the end of
     intermission together with our 2nd frame, loses arbitration, and
     retransmits at the end of intermission after it; we receive it *)
  let o = can_session ~ours:[can_a; can_b] ~theirs:[other 11 b8] () in
  let fr = our_frames o [can_a; can_b] in
  let tx_ok = List.for_all (fun (_, _, ok) -> ok) fr in
  let off = match fr with [(_, s1, _); (_, s2, _)] when s1 >= 0 && s2 >= 0 ->
      s2 - s1 - 100 * (List.length (can_line can_a) + 13) | _ -> min_int in
  let rok, ne = rx_ok o [b8] in
  let lost = o.bus.lost () and sent = o.bus.sent () in
  let exp_bit = can_loss_bit b8 can_b in
  check (Printf.sprintf "can node (chip, %d-deep queues%s): 2 queued frames back to back; 2nd SOF 0..20 cycles after the end of intermission; other node's simultaneous SOF (0x3A5) loses at stuffed bit %d, retransmits at the end of intermission, received -> %d words"
           depth (relays o) exp_bit ne)
    (o.staged = 6 && tx_ok && off >= 0 && off <= 20 && o.code = 0 && o.faulted < 0 && rok
     && (match lost with [(0x3A5, _, k)] -> k = exp_bit | _ -> false)
     && List.map fst sent = [0x3A5] && o.bus.errors () = [])
    (Printf.sprintf "staged %d tx-match %b 2nd-SOF offset %d fault %d lost [%s] sent %d rx [%s] errors [%s]"
       o.staged tx_ok off o.code
       (String.concat " " (List.map (fun (i, _, k) -> Printf.sprintf "%#x@%d" i k) lost))
       (List.length sent) (hex o.got) (String.concat "; " (o.bus.errors ())));
  info "can back-to-back: 2nd SOF %d cycles after the end of intermission (bit = 100 cycles)" off;
  (* 4: our 2nd frame loses arbitration to a simultaneous SOF: fault 72, TXD
     recessive from the lost bit on, the winner's frame intact *)
  let o = can_session ~ours:[can_a; can_c] ~theirs:[other 11 y1] () in
  let fr = our_frames o [can_a; can_c] in
  let a_ok = match fr with (_, _, ok) :: _ -> ok | [] -> false in
  let lb = can_loss_bit can_c y1 in
  let s2 = match fr with [_; (_, s, _)] -> s | _ -> -1 in
  let prefix_ok = s2 >= 0 && List.for_all Fun.id (List.mapi (fun j b -> j >= lb || o.txd (s2 + 100 * j + 50) = b) (can_line can_c)) in
  let rec rec_after t = t >= o.n || (o.txd t = 1 && rec_after (t + 1)) in
  check (Printf.sprintf "can node (chip, %d-deep queues%s): 2nd frame (0x7A5) loses to a simultaneous SOF (0x120) at stuffed bit %d -> fault 72, TXD recessive from that bit on, winner's frame completes"
           depth (relays o) lb)
    (a_ok && prefix_ok && s2 >= 0 && rec_after (s2 + 100 * lb + 60) && o.code = 72
     && List.map fst (o.bus.sent ()) = [0x120] && o.bus.lost () = [] && o.bus.errors () = [])
    (Printf.sprintf "A %b prefix %b fault %d sent %d lost %d errors [%s]" a_ok prefix_ok o.code
       (List.length (o.bus.sent ())) (List.length (o.bus.lost ())) (String.concat "; " (o.bus.errors ())));
  (* 5: negative control: the revision-2 listing misses an SOF at the minimum spacing *)
  let ok, _, d = can_rx_case ~rev:2 ~label:"DLC 8, DLC 5" [b8; c5] [11; 10] in
  check (Printf.sprintf "can node (chip, %d-deep queues), negative control: revision-2 tails fail at the minimum spacing" depth)
    (not ok) d;
  info "revision-2 listing at the minimum spacing: %s" d;
  (* 6: negative control: RX without drain or host service overflows (fault 4) *)
  let nc = List.filteri (fun k _ -> k <= depth / 3) [b8; d8; b8; d8; b8] in
  let o = can_session ~rx_drain:false ~ours:[can_a] ~theirs:(List.map (other 11) nc) () in
  let tx_ok = List.for_all (fun (_, _, ok) -> ok) (our_frames o [can_a]) in
  check (Printf.sprintf "can node (chip, %d-deep queues%s, no RX drain, no host service): RX of %d frame(s) at the minimum spacing = %d pushes overflows -> fault 4 (negative control)"
           depth (relays o) (List.length nc) (3 * List.length nc))
    (tx_ok && o.code = 4) (Printf.sprintf "fault %d, %d words read back" o.code (List.length o.got))

(* ---------- 3b. reset in the middle of a CAN frame ---------- *)
let ext_prefixes = ["line_"; "stuff_"; "arbitration_lost"; "crc_state"; "crc_preset"; "transfer_mode"]
let ext_regs sim =
  let tr = Cyclesim.traced sim in
  let starts p n = String.length n >= String.length p && String.sub n 0 (String.length p) = p in
  List.sort_uniq compare (List.concat_map (fun (is : Cyclesim.Traced.internal_signal) ->
      List.filter_map (fun name ->
          if List.exists (fun p -> starts p name) ext_prefixes then
            Option.map (fun r -> name) (Cyclesim.lookup_reg_by_name sim name) else None) is.mangled_names)
      tr.internal_signals)
let reset_mid_frame () =
  (* run the TX of can_a to about bit 40 of the frame, then assert rst_n for 2 cycles *)
  let o = can_session ~until:(145 + 100 * 60) ~ours:[can_a] ~theirs:[] () in
  let s = o.sy in
  let names = ext_regs s.sim in
  let v n = reg_value s.sim n in
  let nonzero () = List.filter (fun n -> v n <> 0) names in
  let before = nonzero () in
  let sof_seen = o.bus.our_sofs () <> [] in
  s.rst_n <- 0; idle s 2;
  let oe_during = s.oes.(s.t - 1) land 1 in
  s.rst_n <- 1; idle s 8;
  let after = nonzero () in
  let oe_after = s.oes.(s.t - 1) land 3 in
  let st = status s 0 in
  check (Printf.sprintf "reset (%s) in the middle of a CAN frame: %d line-unit/CRC registers traced, %d nonzero before, all zero after; TXD released; engine 0 idle, no fault"
           (match reset_style with O.Sync -> "sync" | O.Sync_registered -> "sync_registered"
                                 | O.Async -> "async" | O.Async_sync_release -> "async_sync_release")
           (List.length names) (List.length before))
    (sof_seen && List.length names >= 20 && List.length before >= 5 && after = [] && oe_during = 0 && oe_after = 0
     && (match st with Some x -> x land 1 = 0 && x land 8 = 0 | None -> false))
    (Printf.sprintf "sof %b regs %d nonzero-before [%s] nonzero-after [%s] oe %d/%d status %s" sof_seen
       (List.length names) (String.concat " " before) (String.concat " " after) oe_during oe_after
       (match st with Some x -> Printf.sprintf "%#x" x | None -> "none"))

(* ---------- 4. USB-LS IN responder on bounded queues ---------- *)
let usb_in () =
  let bit_t = 50_000_000. /. 1_500_000. in
  let line = usb_in_token_line () in
  let t_tok = ref max_int in
  let host t = let tt = float (t - !t_tok) in
    if t < !t_tok then 2 else
    let k = int_of_float (tt /. bit_t) in
    if k < List.length line then (if List.nth line k = 1 then 1 else 2)
    else if k < List.length line + 2 then 0 else 2 in
  let env t out oe = if oe (t - 1) land 3 = 3 then out (t - 1) land 3 else host t in
  let s = make ~max_cycles:40_000 env in
  reset s;
  let feed = if depth >= 3 then 0 else 1 in
  if feed > 0 then (chain s 1);
  load s 0 ~own:0x03 (usb_in_prog ());
  let data = [0x01; 0x00; 0x05; 0x00; 0x00; 0x00; 0x00; 0x00] in
  let staged = prefill s feed (0x4B80 :: words_le data) in
  idle s 50; start s 1;
  t_tok := s.t + 200;
  idle s 12_000;
  let n = s.t in
  let st t = if s.oes.(t) land 3 = 3 then (match s.outs.(t) land 3 with 1 -> 'K' | 2 -> 'J' | 0 -> '0' | _ -> '1')
    else 'Z' in
  let first_k = let rec f t = if t >= n then n else if st t = 'K' then t else f (t+1) in f !t_tok in
  let eop_j = float !t_tok +. float (List.length line + 2) *. bit_t in
  let gap = (float first_k -. eop_j) /. bit_t in
  let rec sample k acc prev =
    let t = first_k + int_of_float ((float k +. 0.5) *. bit_t) in
    if t >= n then List.rev acc else match st t with
      | '0' | 'Z' -> List.rev acc
      | c -> sample (k+1) ((if c = prev then 1 else 0) :: acc) c in
  let raw = sample 0 [] 'J' in
  let rec destuff run acc = function
    | [] -> List.rev acc
    | b :: tl -> if run = 6 then destuff 0 acc tl else destuff (if b = 1 then run+1 else 0) (b :: acc) tl in
  let bits = destuff 0 [] raw in
  let got = List.init (List.length bits / 8) (fun k ->
      List.fold_left (+) 0 (List.init 8 (fun j -> List.nth bits (8*k+j) lsl j))) in
  let c16 = crc_reflected ~poly:0xa001 ~init:0xffff (List.concat_map (fun b -> bits_lsb b 8) data) lxor 0xffff in
  check (Printf.sprintf "usb-ls IN responder (chip, %d-deep queues, %d relay(s)): DATA1 + 8 bytes + CRC16, turnaround %.2f bit times (2..6.5)"
           depth feed gap)
    (staged = 3 && s.faulted_at < 0 && got = [0x80; 0x4B] @ data @ [c16 land 0xff; c16 lsr 8]
     && gap >= 2. && gap <= 6.5)
    (Printf.sprintf "staged %d fault-at %d got [%s]" staged s.faulted_at
       (String.concat " " (List.map (Printf.sprintf "%02x") got)))

let () =
  info "config %s: fifo_words %d" config_path depth;
  let guard name f = try f () with e -> check name false (Printexc.to_string e) in
  guard "capacity" capacity;
  guard "10base-t" (fun () -> ethernet ());
  guard "10base-t nc" (fun () -> ethernet ~negative:true ());
  guard "can" can_checks;
  guard "reset" reset_mid_frame;
  guard "usb" usb_in;
  Printf.printf "TOTAL %d checks, %d failure(s)\n" !checks !failures;
  if !failures > 0 then exit 1
