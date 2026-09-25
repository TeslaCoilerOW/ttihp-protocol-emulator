type t = { architecture : Config.t }

let implementation = "ihp_1p_64x16_pair_nextpc"

let create architecture =
  let architecture = Config.validate architecture in
  if architecture.engine_count <> 4 || architecture.program_words <> 64
     || architecture.prefetch
  then invalid_arg "instruction SRAM refinement requires four engines, 64 words and prefetch=false";
  {architecture}

let of_json = function
  | `Assoc fields ->
    let keys = ["schema_version";"architecture";"implementation"] in
    if List.sort String.compare (List.map fst fields) <> List.sort String.compare keys
    then invalid_arg "refinement fields must be exact and unique";
    if List.assoc "schema_version" fields <> `String "protocol-emulator.refinement.v1"
    then invalid_arg "unsupported refinement schema";
    if List.assoc "implementation" fields <> `String implementation
    then invalid_arg "unsupported instruction memory implementation";
    create (Config.of_json (List.assoc "architecture" fields))
  | _ -> invalid_arg "refinement must be an object"

let load path = Yojson.Safe.from_file path |> of_json
