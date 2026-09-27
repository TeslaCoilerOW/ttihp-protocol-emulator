(* Line-unit extension knob (docs/extension.md). With [default] every
   generator path is the one that existed before this knob. *)

type feature_set = No_line_unit | Rec16

type t = { line_unit : feature_set }

let default = { line_unit = No_line_unit }
let is_default t = t = default
let enabled t = t.line_unit <> No_line_unit

let keys = [ "line_unit" ]

let names = [ No_line_unit, "none"; Rec16, "rec16" ]

let of_options_json = function
  | `Assoc fields ->
    let line_unit =
      match List.assoc_opt "line_unit" fields with
      | None -> No_line_unit
      | Some (`String s) ->
        (match List.find_opt (fun (_, n) -> n = s) names with
         | Some (v, _) -> v
         | None ->
           invalid_arg
             (Printf.sprintf "options.line_unit: unsupported value %S (expected %s)" s
                (String.concat ", " (List.map snd names))))
      | Some _ -> invalid_arg "options.line_unit must be a string"
    in
    { line_unit }
  | _ -> invalid_arg "options must be an object"

let to_json_fields t =
  if t.line_unit = No_line_unit then [] else [ "line_unit", `String (List.assoc t.line_unit names) ]

let validate (architecture : Config.t) (timing : Timing_options.t) t =
  if enabled t then begin
    if architecture.issue <> "fused" then invalid_arg "options.line_unit needs issue=fused (line XFERs)";
    if architecture.data_width <> 32 then invalid_arg "options.line_unit needs data_width 32 (LSTAT)";
    if timing.Timing_options.split_instruction_decode then
      invalid_arg "options.line_unit cannot be combined with split_instruction_decode"
  end;
  t

let capabilities ~engine_count t =
  match t.line_unit with
  | No_line_unit -> 0
  | Rec16 ->
    (* line unit, fraction, stuffing, arbitration, CRC-16, presets; all engines *)
    0b1 lor 0b10 lor 0b100 lor 0b1000 lor 0b1_0000 lor 0b100_0000
    lor (((1 lsl engine_count) - 1) lsl 8)
