(* Optional RTL variant knobs of a closed refinement ("options" object).
   The default value selects exactly the design of record; every generator
   path taken with [default] must emit the same Verilog as before the knobs
   existed (scripts/gen_variants.sh checks this byte for byte). *)

type reset = Sync | Sync_registered | Async | Async_sync_release
type pc_bits = Full | Saturating_7
type shift = Barrel | Byte_lane

type t = {
  reset : reset;
  fifo_storage_reset : bool;
  narrow_image_regs : bool;
  debug_counters : bool;
  pc_bits : pc_bits;
  shift : shift;
}

let default = {reset=Sync; fifo_storage_reset=false; narrow_image_regs=false;
               debug_counters=true; pc_bits=Full; shift=Barrel}

let is_default t = t = default

let asynchronous t = match t.reset with
  | Async | Async_sync_release -> true
  | Sync | Sync_registered -> false

let isa_changed t = (not t.debug_counters) || t.pc_bits <> Full || t.shift <> Barrel
let isa_version t = if isa_changed t then 3 else 2
let pc_width t = match t.pc_bits with Full -> 24 | Saturating_7 -> 7

let reset_names = [Sync,"sync";Sync_registered,"sync_registered";Async,"async";
                   Async_sync_release,"async_sync_release"]
let pc_bits_names = [Full,"full";Saturating_7,"saturating_7"]
let shift_names = [Barrel,"barrel";Byte_lane,"byte_lane"]

let to_name table value = List.assoc value table
let of_name what table name =
  match List.find_opt (fun (_,n) -> n = name) table with
  | Some (value,_) -> value
  | None -> invalid_arg (Printf.sprintf "options.%s: unsupported value %S (expected %s)"
                           what name (String.concat ", " (List.map snd table)))

let keys = ["reset";"fifo_storage_reset";"narrow_image_regs";"debug_counters";"pc_bits";"shift"]

let of_json = function
  | `Assoc fields ->
    let seen = Hashtbl.create 8 in
    List.iter (fun (k,_) ->
      if not (List.mem k keys) then invalid_arg ("options: unknown field " ^ k);
      if Hashtbl.mem seen k then invalid_arg ("options: duplicate field " ^ k);
      Hashtbl.add seen k ()) fields;
    let boolean k default = match List.assoc_opt k fields with
      | None -> default | Some (`Bool b) -> b
      | Some _ -> invalid_arg ("options." ^ k ^ " must be a boolean") in
    let enum k table default = match List.assoc_opt k fields with
      | None -> default | Some (`String s) -> of_name k table s
      | Some _ -> invalid_arg ("options." ^ k ^ " must be a string") in
    {reset=enum "reset" reset_names default.reset;
     fifo_storage_reset=boolean "fifo_storage_reset" default.fifo_storage_reset;
     narrow_image_regs=boolean "narrow_image_regs" default.narrow_image_regs;
     debug_counters=boolean "debug_counters" default.debug_counters;
     pc_bits=enum "pc_bits" pc_bits_names default.pc_bits;
     shift=enum "shift" shift_names default.shift}
  | _ -> invalid_arg "options must be an object"

let to_json t = `Assoc [
  "reset",`String (to_name reset_names t.reset);
  "fifo_storage_reset",`Bool t.fifo_storage_reset;
  "narrow_image_regs",`Bool t.narrow_image_regs;
  "debug_counters",`Bool t.debug_counters;
  "pc_bits",`String (to_name pc_bits_names t.pc_bits);
  "shift",`String (to_name shift_names t.shift)]

(* A saturated PC (127) must lie outside every image, and the image length
   (<= program_words) must fit the 7-bit comparison: program_words <= 64. *)
let validate (architecture:Config.t) t =
  if t.pc_bits = Saturating_7 && architecture.program_words > 64 then
    invalid_arg "options.pc_bits saturating_7 needs program_words <= 64";
  t
