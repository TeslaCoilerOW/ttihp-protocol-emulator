type t = {
  engine_count : int;
  data_width : int;
  program_words : int;
  fifo_words : int;
  issue : Issue.t;
  prefetch : bool;
}

let default = {engine_count=4; data_width=32; program_words=64;
               fifo_words=8; issue=Fused; prefetch=false}

let check_sizes ~engine_count ~data_width ~program_words ~fifo_words =
  let member name value choices =
    if not (List.mem value choices) then invalid_arg ("invalid " ^ name) in
  member "engine_count" engine_count [2;4];
  member "data_width" data_width [16;32];
  member "program_words" program_words [32;64;128];
  member "fifo_words" fifo_words [2;4;8;32]

let validate t =
  check_sizes ~engine_count:t.engine_count ~data_width:t.data_width
    ~program_words:t.program_words ~fifo_words:t.fifo_words;
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
    (* The order of the checks decides which error a config with several bad
       fields reports: the field types from prefetch back to engine_count
       (up to 16cfc20 one record expression, which OCaml evaluates right to
       left), then the sizes, then the issue name. *)
    let prefetch = boolean "prefetch" in
    let issue = string "issue" in
    let fifo_words = integer "fifo_words" in
    let program_words = integer "program_words" in
    let data_width = integer "data_width" in
    let engine_count = integer "engine_count" in
    check_sizes ~engine_count ~data_width ~program_words ~fifo_words;
    let issue =
      match Issue.of_string issue with
      | issue -> issue
      | exception Invalid_argument _ -> invalid_arg "invalid issue" in
    {engine_count; data_width; program_words; fifo_words; issue; prefetch}
  | _ -> invalid_arg "architecture must be an object"

let load path = Yojson.Safe.from_file path |> of_json
let log2 n = let rec loop p b = if p >= n then b else loop (p*2) (b+1) in loop 1 0
