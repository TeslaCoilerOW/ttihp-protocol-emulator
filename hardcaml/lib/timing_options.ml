(* Timing-restructuring knobs (docs/timing-closure.md). With [default] every
   generator path is the one that existed before these knobs, so the design of
   record and the earlier variants are emitted byte for byte. *)

type t = {
  host_nibble_slots : bool;
  split_command_decode : bool;
  split_engine_issue : bool;
  split_instruction_decode : bool;
  fifo_write_staging : bool;
  clear_outputs_only : bool;
  keep_counter_increments : bool;
  fifo_write_free_slot : bool;
}

let default = {host_nibble_slots=false; split_command_decode=false; split_engine_issue=false;
               split_instruction_decode=false; fifo_write_staging=false; clear_outputs_only=false;
               keep_counter_increments=false; fifo_write_free_slot=false}

let is_default t = t = default

let keys = ["host_nibble_slots";"split_command_decode";"split_engine_issue";
            "split_instruction_decode";"fifo_write_staging";"clear_outputs_only";
            "keep_counter_increments";"fifo_write_free_slot"]

let of_options_json = function
  | `Assoc fields ->
    let boolean k = match List.assoc_opt k fields with
      | None -> false | Some (`Bool b) -> b
      | Some _ -> invalid_arg ("options." ^ k ^ " must be a boolean") in
    {host_nibble_slots=boolean "host_nibble_slots";
     split_command_decode=boolean "split_command_decode";
     split_engine_issue=boolean "split_engine_issue";
     split_instruction_decode=boolean "split_instruction_decode";
     fifo_write_staging=boolean "fifo_write_staging";
     clear_outputs_only=boolean "clear_outputs_only";
     keep_counter_increments=boolean "keep_counter_increments";
     fifo_write_free_slot=boolean "fifo_write_free_slot"}
  | _ -> invalid_arg "options must be an object"

let to_json_fields t =
  List.filter_map (fun (k,v) -> if v then Some (k,`Bool true) else None)
    ["host_nibble_slots",t.host_nibble_slots;
     "split_command_decode",t.split_command_decode;
     "split_engine_issue",t.split_engine_issue;
     "split_instruction_decode",t.split_instruction_decode;
     "fifo_write_staging",t.fifo_write_staging;
     "clear_outputs_only",t.clear_outputs_only;
     "keep_counter_increments",t.keep_counter_increments;
     "fifo_write_free_slot",t.fifo_write_free_slot]
