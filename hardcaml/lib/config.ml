type t = {
  engine_count : int;
  data_width : int;
  program_words : int;
  fifo_words : int;
  issue : string;
  prefetch : bool;
}

let default = {engine_count=4; data_width=32; program_words=64;
               fifo_words=8; issue="fused"; prefetch=false}

let validate t =
  let member name value choices =
    if not (List.mem value choices) then invalid_arg ("invalid " ^ name) in
  member "engine_count" t.engine_count [2;4];
  member "data_width" t.data_width [16;32];
  member "program_words" t.program_words [32;64;128];
  member "fifo_words" t.fifo_words [2;4;8;32];
  if not (List.mem t.issue ["scalar";"fused"]) then invalid_arg "invalid issue";
  t

let of_json = function
  | `Assoc fields ->
    let keys = ["schema_version";"engine_count";"data_width";"program_words";
                "fifo_words";"issue";"prefetch"] in
    if List.sort String.compare (List.map fst fields) <> List.sort String.compare keys
    then invalid_arg "architecture fields must be exact and unique";
    let get k = List.assoc k fields in
    let integer k = match get k with `Int n -> n | _ -> invalid_arg k in
    let string k = match get k with `String s -> s | _ -> invalid_arg k in
    let boolean k = match get k with `Bool b -> b | _ -> invalid_arg k in
    if string "schema_version" <> "protocol-emulator.architecture.v1"
    then invalid_arg "unsupported architecture schema";
    validate {engine_count=integer "engine_count"; data_width=integer "data_width";
              program_words=integer "program_words"; fifo_words=integer "fifo_words";
              issue=string "issue"; prefetch=boolean "prefetch"}
  | _ -> invalid_arg "architecture must be an object"

let load path = Yojson.Safe.from_file path |> of_json
let log2 n = let rec loop p b = if p >= n then b else loop (p*2) (b+1) in loop 1 0
