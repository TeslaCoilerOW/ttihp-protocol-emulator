type t = { architecture : Config.t; options : Variant_options.t }

let implementation = "ihp_1p_64x16_pair_nextpc"

let create ?(options=Variant_options.default) architecture =
  let architecture = Config.validate architecture in
  if architecture.engine_count <> 4 || architecture.program_words <> 64
     || architecture.prefetch
  then invalid_arg "instruction SRAM refinement requires four engines, 64 words and prefetch=false";
  {architecture; options=Variant_options.validate architecture options}

let of_json = function
  | `Assoc fields ->
    let keys = ["schema_version";"architecture";"implementation"] in
    let present = List.sort String.compare (List.map fst fields) in
    if present <> List.sort String.compare keys
       && present <> List.sort String.compare ("options"::keys)
    then invalid_arg "refinement fields must be exact and unique";
    if List.assoc "schema_version" fields <> `String "protocol-emulator.refinement.v1"
    then invalid_arg "unsupported refinement schema";
    if List.assoc "implementation" fields <> `String implementation
    then invalid_arg "unsupported instruction memory implementation";
    let options = match List.assoc_opt "options" fields with
      | None -> Variant_options.default
      | Some json -> Variant_options.of_json json in
    create ~options (Config.of_json (List.assoc "architecture" fields))
  | _ -> invalid_arg "refinement must be an object"

let load path = Yojson.Safe.from_file path |> of_json
