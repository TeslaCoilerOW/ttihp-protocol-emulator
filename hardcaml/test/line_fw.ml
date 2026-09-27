(* Shared by line_test (engine level) and line_sys_test (processor level):
   instruction encoders for docs/isa.md plus the line-unit extension
   (docs/extension.md), independent bit-level references, and the firmware
   of docs/extension-study.md section 7 (firmware/ext/ holds the same
   programs as assembler sources). Ported from the extension study's
   prototype tests; the feature set is fixed to Line_options.Rec16 (CRC-16,
   polynomial presets). *)

let crc_w = 16
let crcpre = true

(* ---------- instruction encoding (docs/isa.md + extension) ---------- *)
let ins op a b c = (op lsl 24) lor (a lsl 16) lor (b lsl 8) lor c
let imm op v = (op lsl 24) lor (v land 0xffffff)
let halt = imm 1 0
let set_ v = imm 2 v and dir v = imm 3 v and wait n = imm 4 n and jmp t = imm 5 t
let pull = 6 lsl 24 and push = ins 7 0 0 0 and push_strict = ins 7 1 0 0
let count n = imm 10 n and loop t = imm 11 t and limit n = imm 12 n
let waitpin p v = ins 13 p v 0
let pins ~ck ~out ~inp = imm 16 (ck lor (out lsl 3) lor (inp lsl 6))
let mov d s = ins 18 d s 0 and load d v = ins 19 d 0 0 lor (v land 0xffff)
let add d s = ins 20 d s 0 and xor_ d s = ins 21 d s 0 and and_ d s = ins 22 d s 0
let or_ d s = ins 23 d s 0 and shl d n = ins 24 d 0 n and shr d n = ins 25 d 0 n
let jz r t = (26 lsl 24) lor (r lsl 16) lor t
let not_ d = ins 27 d 0 0 and fault_ code = imm 29 code
let tx_r = 0 and rx_r = 1 and x_r = 2 and y_r = 3
(* extension *)
let xline ?(msb=false) ?(drive=false) ?(sample=false) ?(crc=false) a =
  ins 17 a 0 (0x20 lor (if msb then 4 else 0) lor (if drive then 8 else 0)
              lor (if sample then 0x10 else 0) lor (if crc then 0x40 else 0))
let ltim ?(frac=0) ?(delay=0) p = imm 30 (p lor (frac lsl 8) lor (delay lsl 16))
let lcfg ?(code=0) ?(stuff=0) ?(any=false) ?(pair=false) ?(arb=false) ?(se0=false)
    ?(init=0) () =
  imm 31 (code lor (if stuff > 0 then 4 lor ((stuff - 1) lsl 4) else 0)
          lor (if any then 8 else 0) lor (if pair then 0x80 else 0)
          lor (if arb then 0x100 else 0) lor (if se0 then 0x200 else 0) lor (init lsl 10))
let crc_poly r = ins 32 0 r 0 and crc_set r = ins 32 0 r 1 and crc_get r = ins 32 r 0 2
let lstat r = ins 33 r 0 0
let load32 r v t = [load r (v land 0xffff); load t (v lsr 16); shl t 16; or_ r t]
(* polynomial setup: programmable register, or a 2-bit preset (crcpre knob) *)
let poly_ins ?(r=y_r) ?(t=x_r) v =
  if crcpre then
    (* either form of a preset (Line_unit.presets) *)
    let idx = if v = 0x14 || v = 0x2800 then 0 else if v = 0xa001 || v = 0x8005 then 1
      else if v = 0x4cd1 || v = 0x8b32 then 2
      else if v = 0x8408 || v = 0x1021 then 3 else invalid_arg "poly_ins: not a preset" in
    [ins 32 0 idx 3]
  else if v < 0x10000 then [load r v; crc_poly r] else load32 r v t @ [crc_poly r]

(* tiny label resolver for the firmware sketches *)
type item = L of string | I of int | R of ((string -> int) -> int)
let assemble items =
  let tbl = Hashtbl.create 16 and pc = ref 0 in
  List.iter (function L l -> Hashtbl.replace tbl l !pc | _ -> incr pc) items;
  let look l = try Hashtbl.find tbl l with Not_found -> failwith ("label " ^ l) in
  List.filter_map (function L _ -> None | I w -> Some w | R f -> Some (f look)) items

(* ---------- independent references ---------- *)
let bits_lsb v n = List.init n (fun k -> (v lsr k) land 1)
let bits_msb v n = List.init n (fun k -> (v lsr (n - 1 - k)) land 1)
let crc_reflected ~poly ~init bits =
  List.fold_left (fun c b -> let fb = (c land 1) lxor b in
    (c lsr 1) lxor (if fb = 1 then poly else 0)) init bits
let crc_normal ~width ~poly ~init bits =
  let mask = (1 lsl width) - 1 in
  List.fold_left (fun c b -> let fb = ((c lsr (width - 1)) land 1) lxor b in
    ((c lsl 1) land mask) lxor (if fb = 1 then poly else 0)) init bits
let bytes_of_string s = List.init (String.length s) (fun k -> Char.code s.[k])
let stuff_ones n bits = (* USB: a 0 after n consecutive 1s *)
  let rec go run acc = function
    | [] -> List.rev (if run = n then 0 :: acc else acc)
    | b :: tl -> if run = n then go 0 (0 :: acc) (b :: tl)
      else go (if b = 1 then run + 1 else 0) (b :: acc) tl in
  go 0 [] bits
let stuff_any n bits = (* CAN: complement after n equal bits *)
  let rec go last run acc = function
    | [] -> List.rev (if run = n then (1 - last) :: acc else acc)
    | b :: tl -> if run = n then go (1 - last) 1 ((1 - last) :: acc) (b :: tl)
      else if b = last then go b (run + 1) (b :: acc) tl else go b 1 (b :: acc) tl in
  go 2 0 [] bits
let nrzi init bits = (* 0 = transition *)
  let l = ref init in List.map (fun b -> if b = 0 then l := 1 - !l; !l) bits
let words_le bytes = (* little-endian 32-bit words, length a multiple of 4 *)
  List.init (List.length bytes / 4) (fun k ->
      List.fold_left (fun a j -> a lor (List.nth bytes (4*k+j) lsl (8*j))) 0 [0;1;2;3])

(* CAN 2.0A frame: SOF..data bits and CRC-15 *)
let can_frame_bits id data =
  let hdr = [0] @ bits_msb id 11 @ [0; 0; 0] @ bits_msb (List.length data) 4 in
  let body = hdr @ List.concat_map (fun b -> bits_msb b 8) data in
  let c15 = crc_normal ~width:15 ~poly:0x4599 ~init:0 body in
  body, c15
let can_word id ndata = (* SOF,ID,RTR,IDE,r0,DLC in bits 31..13 *)
  let hdr = [0] @ bits_msb id 11 @ [0; 0; 0] @ bits_msb ndata 4 in
  List.fold_left (fun (a, k) b -> (a lor (b lsl (31 - k)), k + 1)) (0, 0) hdr |> fst
(* TX queue words for a CAN node frame: header, then data MSB-first in
   32-bit words (DLC 8 only in this sketch) *)
let can_tx_words id data =
  let w k = List.fold_left (fun a j -> a lor (List.nth data (4*k+j) lsl (24 - 8*j))) 0 [0;1;2;3] in
  [can_word id (List.length data); w 0; w 1]
(* RX queue words the CAN node pushes for a received frame: header (low 19
   bits), then the data bytes packed MSB-first into 32-bit words.  Bytes
   arrive in groups ending at the last byte: when DLC > 4 the first word holds
   the first DLC-4 bytes and the second the last 4; the valid bytes of a word
   are its low 8*n bits (upper bits are stale). *)
let can_rx_expect id data =
  let body, _ = can_frame_bits id data in
  let hdr = List.fold_left (fun a b -> 2*a + b) 0 (List.filteri (fun k _ -> k < 19) body) in
  let pack l = List.fold_left (fun a b -> (a lsl 8) lor b) 0 l in
  let n = List.length data in
  let groups = if n = 0 then [] else if n <= 4 then [data]
    else [List.filteri (fun k _ -> k < n - 4) data; List.filteri (fun k _ -> k >= n - 4) data] in
  (hdr, 0x7ffff) :: List.map (fun g -> (pack g, (1 lsl (8 * List.length g)) - 1)) groups

(* 10BASE-T frame body: broadcast UDP datagram, zero padded to >= 60 bytes and
   to a multiple of 4, plus the FCS.  The preamble/SFD is made on chip. *)
let udp_frame payload =
  let mac_d = [0xff;0xff;0xff;0xff;0xff;0xff] and mac_s = [0x02;0x00;0x00;0x00;0x00;0x01] in
  let udp_len = 8 + List.length payload and ip_len = 20 + 8 + List.length payload in
  let ip_hdr0 = [0x45;0;ip_len lsr 8;ip_len land 0xff;0;1;0x40;0;64;17;0;0;
                 10;0;0;2; 255;255;255;255] in
  let csum = let rec sum = function a :: b :: tl -> (a lsl 8) + b + sum tl | _ -> 0 in
    let s = sum ip_hdr0 in let s = (s land 0xffff) + (s lsr 16) in
    let s = (s land 0xffff) + (s lsr 16) in (lnot s) land 0xffff in
  let ip_hdr = List.mapi (fun k b -> if k = 10 then csum lsr 8 else if k = 11 then csum land 0xff else b) ip_hdr0 in
  let udp = [0x04;0xd2; 0x16;0x2e; udp_len lsr 8; udp_len land 0xff; 0; 0] in
  let body = mac_d @ mac_s @ [0x08;0x00] @ ip_hdr @ udp @ payload in
  let target = let l = max 60 (List.length body) in l + ((4 - l mod 4) mod 4) in
  let body = body @ List.init (target - List.length body) (fun _ -> 0) in
  let fcs = crc_reflected ~poly:0xedb88320 ~init:0xffffffff (List.concat_map (fun b -> bits_lsb b 8) body)
            lxor 0xffffffff in
  body @ [fcs land 0xff; (fcs lsr 8) land 0xff; (fcs lsr 16) land 0xff; fcs lsr 24]
let preamble = [0x55;0x55;0x55;0x55;0x55;0x55;0x55;0xd5]

(* ======================= firmware sketches ======================= *)

(* Relay: copy TX queue to RX queue; with ROUTE this engine -> next it stages
   words for another engine (docs/extension-study.md 7.1). *)
let relay_prog = [pull; mov rx_r tx_r; push; jmp 0]

(* 10BASE-T UDP transmitter at 40 MHz (P = 2): preamble/SFD made on chip,
   frame (destination..FCS) streamed from the TX queue with no gap, TP_IDL,
   and an NLP every 16 ms while idle.  [nwords] frame words. *)
let eth_prog nwords = assemble [
  I (pins ~ck:1 ~out:0 ~inp:0); I (lcfg ~code:2 ~pair:true ()); I (ltim 2); I (set_ 0); I (dir 3);
  L "idle"; I (lstat x_r); I (load y_r 8); I (and_ x_r y_r); R (fun l -> jz x_r (l "nlp"));
  (* tx = 0x55555555, x = 0xD5555555 (LSB first: 7 x 0x55, then SFD 0xD5) *)
  I (load tx_r 0x5555); I (mov x_r tx_r); I (shl x_r 16); I (or_ tx_r x_r);
  I (load y_r 0x8000); I (shl y_r 16); I (mov x_r tx_r); I (or_ x_r y_r);
  I (count (nwords - 1));
  I (xline ~drive:true 32); I (mov tx_r x_r); I (xline ~drive:true 32);
  L "word"; I pull; I (xline ~drive:true 32); R (fun l -> loop (l "word"));
  I (wait 2); I (set_ 1); I (wait 9); I (set_ 0); R (fun l -> jmp (l "gap"));
  L "nlp"; I (set_ 1); I (wait 2); I (set_ 0);
  L "gap"; I (wait 639_990); R (fun l -> jmp (l "idle")) ]

(* CAN 2.0A node, 500 kbit/s at 50 MHz (P = 50), TXD pin 0, RXD pin 1.
   Transmit: header + 8 data bytes from the TX queue, CRC-15 by the unit,
   ACK check (fault 70 without ACK).  Arbitration loss: fault 72 (the header
   word has been consumed; the host requeues).  Receive: header push, then the
   data bytes packed into at most two words (can_rx_expect), CRC check
   (fault 71 on error), ACK.  START clears x, y and the CRC; y = -1 is kept.

   Frame tails (revision 3, the default).  Both the TX tail (CRC delimiter,
   ACK slot, ACK delimiter, EOF, 2 intermission bits: XFER 12 drive+sample)
   and the RX tail (ACK, ACK delimiter, EOF, 2 intermission bits: XFER 11
   drive+sample) complete at the mid-bit of the 2nd intermission bit, so the
   receive WAITPIN is armed before the earliest SOF another node may send
   (the 3rd intermission bit, which ISO 11898-1 treats as SOF).  A pending
   transmission starts its SOF [can_tx_delay] cycles after LTIM, i.e. just
   after the end of intermission.
   [~rev:2] rebuilds the revision-2 listing (TX tail XFER 14 ending at the
   mid-bit of the first bit after intermission, RX tail drive-only XFER 13,
   TX SOF P cycles after LTIM); it misses an SOF sent at the minimum spacing
   after its own frame and serves as a negative control. *)
let can_tx_delay = 150
let can_node_prog ?(rev=3) () =
  let r2 = rev = 2 in
  let poly = if crcpre then List.map (fun w -> I w) (poly_ins (0x4599 lsl (crc_w - 15)))
    else [I (load tx_r ((0x4599 lsl 1) land 0xffff))] @ (if crc_w = 32 then [I (shl tx_r 16)] else [])
         @ [I (crc_poly tx_r)] in
  let dsm = xline ~msb:true ~drive:true ~sample:true in
  assemble ([
    I (pins ~ck:2 ~out:0 ~inp:1); I (set_ 1); I (dir 1); I (limit 0xffffff)] @ poly @ [
    I (not_ y_r);
    L "idle"; I (lcfg ~stuff:5 ~any:true ~arb:true ~init:1 ());
    I (lstat x_r); I (load tx_r 8); I (and_ x_r tx_r); R (fun l -> jz x_r (l "rx"));
    I (if r2 then ltim 50 else ltim ~delay:can_tx_delay 50); I pull; I (dsm ~crc:true 19);
    I (lstat x_r); I (load tx_r 2); I (and_ x_r tx_r); R (fun l -> jz x_r (l "tx_ok")); I (fault_ 72);
    L "tx_ok"; I pull; I (dsm ~crc:true 32); I pull; I (dsm ~crc:true 32);
    I (crc_get tx_r); I (shl tx_r (32 - crc_w)); I (dsm ~crc:true 15);   (* CRC fed back: register returns to 0 *)
    (* delimiter, ACK slot, delimiter, EOF, 2 intermission bits *)
    I (lcfg ~init:1 ()); I (load tx_r 0xffff); I (shl tx_r 16); I (dsm (if r2 then 14 else 12));
    I (mov x_r rx_r); I (load tx_r (if r2 then 0x1000 else 0x400)); I (and_ x_r tx_r);
    R (fun l -> jz x_r (l "idle")); I (fault_ 70);
    L "rx"; I (waitpin 1 0); I (ltim ~delay:1 50); I (xline ~msb:true ~sample:true ~crc:true 19);
    I push_strict; I (mov x_r rx_r); I (load tx_r 15); I (and_ x_r tx_r);
    L "byte"; R (fun l -> jz x_r (l "crc")); I (xline ~msb:true ~sample:true ~crc:true 8); I (add x_r y_r);
    I (load tx_r 4); I (xor_ tx_r x_r); R (fun l -> jz tx_r (l "flush")); R (fun l -> jz x_r (l "flush"));
    R (fun l -> jmp (l "byte"));
    L "flush"; I push_strict; R (fun l -> jmp (l "byte"));
    L "crc"; I (xline ~msb:true ~sample:true ~crc:true 15); I (lcfg ~init:1 ()); I (xline ~msb:true ~sample:true 1);
    I (crc_get x_r); R (fun l -> jz x_r (l "ack")); I (fault_ 71);
    (* ACK dominant, delimiter, EOF, 2 intermission bits *)
    L "ack"; I (load tx_r 0x7fff); I (shl tx_r 16);
    I (if r2 then xline ~msb:true ~drive:true 13 else dsm 11);
    R (fun l -> jmp (l "idle"))])

(* ---------- behavioural CAN bus for the node tests ---------- *)
let can_line (id, d) = let b, c = can_frame_bits id d in stuff_any 5 (b @ bits_msb c 15)
(* stuffed index of the RTR bit, the last bit of the 2.0A arbitration field *)
let can_rtr_index (id, d) =
  let b, _ = can_frame_bits id d in List.length (stuff_any 5 (List.filteri (fun k _ -> k < 12) b))
(* first stuffed bit where frame [a] sends recessive and [b] dominant (a loses) *)
let can_loss_bit a b =
  let la = can_line a and lb = can_line b in
  let rec f k = function
    | x :: xs, y :: ys -> if x = 1 && y = 0 then k else if x <> y then -1 else f (k + 1) (xs, ys)
    | _ -> -1 in f 0 (la, lb)
let can_rx_words (_, d) = let n = List.length d in 1 + (if n > 0 then 1 else 0) + (if n > 4 then 1 else 0)

(* One other node on a wired-AND bus, 100 cycles per bit.  [step t ours]
   returns the bus level at cycle t given our TXD as it reaches the bus (the
   callers delay it by the transceiver loop).  The other node
   - sends [theirs] in order.  It starts a frame once it has seen [o_after]
     SOFs of ours, t >= [o_not_before], and the bus has been recessive for
     [o_r] bit times since the last dominant bit.  o_r = 11 is the end of
     intermission (the earliest legal SOF, the case of a node whose frame was
     pending); o_r = 10 is the 3rd intermission bit, which ISO 11898-1
     treats as SOF (a node with a slightly fast clock);
   - drives its stuffed SOF..CRC and samples at mid-bit.  Reading dominant
     while sending recessive up to the RTR bit loses arbitration: it stops
     driving and retries the same frame under the same rule.  Any other
     mismatch is recorded as an error;
   - acknowledges every frame of ours ([ours] in order gives each ACK slot)
     unless it is itself transmitting.  A frame of ours that started together
     with one of its frames that then completed lost arbitration and is not
     acknowledged.  A dominant edge of ours more than 1.5 bits into its frame
     is recorded as an error (a collision).
   Our own frames are checked bit for bit by the callers. *)
type can_other = { o_id : int; o_data : int list; o_r : int; o_after : int; o_not_before : int }
let other ?(after=1) ?(not_before=0) r (id, data) =
  { o_id = id; o_data = data; o_r = r; o_after = after; o_not_before = not_before }
type can_bus = {
  step : int -> int -> int;
  our_sofs : unit -> int list;              (* bus cycle of each SOF of ours *)
  sent : unit -> (int * int) list;          (* other node: (id, start) per completed frame *)
  lost : unit -> (int * int * int) list;    (* other node: (id, start, stuffed bit) per loss *)
  errors : unit -> string list;
}
let can_bus ?(ack_ours=true) ~ours theirs =
  let our_lens = ref (List.map (fun f -> List.length (can_line f)) ours) in
  let pending = ref theirs and sending = ref None in
  let rec_run = ref max_int and prev_ours = ref 1 and seen_ours = ref 0 in
  let cur = ref None and ack_block = ref (-1) in
  let sofs = ref [] and sent = ref [] and lost = ref [] and errors = ref [] in
  let err fmt = Printf.ksprintf (fun m -> errors := m :: !errors) fmt in
  let step t ours =
    (* our dominant bit makes the bus dominant this cycle: no start on a stale idle count *)
    if ours = 0 then rec_run := 0;
    (match !cur with Some (s, l) when t >= s + 100 * (l + 3) -> cur := None | _ -> ());
    if !prev_ours = 1 && ours = 0 && !cur = None && t >= !ack_block then begin
      (match !sending with
       | Some (_, _, s, _) when t - s >= 150 ->
         err "dominant edge of ours at cycle %d, bit %d of the other node's frame" t ((t - s) / 100)
       | _ -> ());
      match !our_lens with
      | l :: tl -> cur := Some (t, l); our_lens := tl; incr seen_ours; sofs := t :: !sofs
      | [] -> err "unexpected dominant edge of ours at cycle %d" t
    end;
    (match !sending, !pending with
     | None, f :: _ when !seen_ours >= f.o_after && t >= f.o_not_before && !rec_run >= 100 * f.o_r ->
       let fr = (f.o_id, f.o_data) in
       sending := Some (f, Array.of_list (can_line fr), t, can_rtr_index fr)
     | _ -> ());
    let theirs = match !sending with
      | None -> 1
      | Some (f, line, s, _) ->
        let k = (t - s) / 100 in
        if k < Array.length line then line.(k) else begin
          sending := None; pending := List.tl !pending; sent := (f.o_id, s) :: !sent;
          ack_block := s + 100 * (Array.length line + 2) + 50;
          (match !cur with Some (cs, _) when cs > s - 150 && cs < s + 150 -> cur := None | _ -> ());
          1 end in
    let ack = ack_ours && Option.is_none !sending && (match !cur with
        | Some (s, l) -> t >= s + 100 * (l + 1) && t < s + 100 * (l + 2)
        | None -> false) in
    let bus = ours land theirs land (if ack then 0 else 1) in
    (match !sending with
     | Some (f, line, s, rtr) when (t - s) mod 100 = 50 ->
       let k = (t - s) / 100 in
       if k < Array.length line && line.(k) = 1 && bus = 0 then begin
         if k <= rtr then (lost := (f.o_id, s, k) :: !lost; sending := None)
         else err "bit error in the other node's frame %#x at bit %d" f.o_id k
       end
     | _ -> ());
    rec_run := (if bus = 1 then (if !rec_run = max_int then max_int else !rec_run + 1) else 0);
    prev_ours := ours;
    bus in
  { step; our_sofs = (fun () -> List.rev !sofs); sent = (fun () -> List.rev !sent);
    lost = (fun () -> List.rev !lost); errors = (fun () -> List.rev !errors) }

(* USB-LS IN responder at 50 MHz: receive a token, check CRC5 and PID = IN,
   answer with the queued DATAx packet (SYNC/PID word + 2 data words), CRC16
   by the unit. *)
let usb_in_prog () =
  let p5 = List.map (fun w -> I w) (poly_ins 0x14) and p16 = List.map (fun w -> I w) (poly_ins 0xa001) in
  assemble ([I (pins ~ck:1 ~out:0 ~inp:0); I (limit 0xffffff)] @ p5 @ [
    L "rx"; I (lcfg ~code:1 ~stuff:6 ~pair:true ~se0:true ~init:1 ()); I (waitpin 0 1);
    I (ltim ~frac:171 ~delay:33 16); I (xline ~sample:true 7); I (xline ~sample:true 8);
    I (load x_r 0x1f); I (crc_set x_r); I (xline ~sample:true ~crc:true 16); I (xline ~sample:true 8);
    I (crc_get x_r); I (load y_r 6); I (xor_ x_r y_r); R (fun l -> jz x_r (l "crc_ok")); R (fun l -> jmp (l "rx"));
    L "crc_ok"; I (mov x_r rx_r); I (shr x_r 8); I (load y_r 0xff); I (and_ x_r y_r); I (load y_r 0x69);
    I (xor_ x_r y_r); R (fun l -> jz x_r (l "is_in")); R (fun l -> jmp (l "rx"));
    L "is_in"; I (waitpin 1 1);
    I (lcfg ~code:1 ~stuff:6 ~pair:true ~init:0 ()); I (ltim ~frac:171 ~delay:95 16); I (set_ 0b10); I (dir 0b11);
    I (load x_r 0xffff); I (crc_set x_r)] @ p16 @ [
    I pull; I (xline ~drive:true 16); I pull; I (xline ~drive:true ~crc:true 32);
    I pull; I (xline ~drive:true ~crc:true 32);
    I (crc_get x_r); I (not_ x_r); I (mov 0 x_r); I (xline ~drive:true 16);
    I (wait 31); I (set_ 0); I (wait 64); I (set_ 0b10); I (wait 31); I (dir 0)] @ p5 @ [
    R (fun l -> jmp (l "rx"))])

(* USB-LS host side for the IN responder test: IN token to addr 0x3a endp 1 *)
let usb_in_token_line () =
  let fields = bits_lsb 0x3a 7 @ bits_lsb 1 4 in
  let c5 = crc_reflected ~poly:0x14 ~init:0x1f fields lxor 0x1f in
  let token = List.concat_map (fun b -> bits_lsb b 8) [0x80; 0x69] @ fields @ bits_lsb c5 5 in
  nrzi 0 (stuff_ones 6 token)
