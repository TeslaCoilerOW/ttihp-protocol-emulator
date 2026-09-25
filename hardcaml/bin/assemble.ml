let read_file path =
  let channel = open_in_bin path in
  Fun.protect ~finally:(fun () -> close_in channel)
    (fun () -> really_input_string channel (in_channel_length channel))
let write_file path contents =
  let channel = open_out_bin path in
  Fun.protect ~finally:(fun () -> close_out channel)
    (fun () -> output_string channel contents)
(* --variant: architecture and shift restriction of an RTL variant, read from
   its refinement config (protocol-emulator.refinement.v1 with optional
   "options").  Parsed here, independently of the Hardcaml generator. *)
let variant_target path =
  let json=Yojson.Safe.from_file path in
  let fields=match json with `Assoc f -> f | _ -> invalid_arg "variant config must be an object" in
  if List.assoc_opt "schema_version" fields<>Some (`String "protocol-emulator.refinement.v1")
  then invalid_arg "variant config must be a protocol-emulator.refinement.v1 object";
  let architecture=match List.assoc_opt "architecture" fields with
    | Some a -> Isa.architecture_of_json a | None -> invalid_arg "variant config has no architecture" in
  let byte_lane=match List.assoc_opt "options" fields with
    | None -> false
    | Some (`Assoc options) ->
      (match List.assoc_opt "shift" options with
       | None | Some (`String "barrel") -> false
       | Some (`String "byte_lane") -> true
       | Some _ -> invalid_arg "variant options.shift must be barrel or byte_lane")
    | Some _ -> invalid_arg "variant options must be an object" in
  architecture,byte_lane
let () =
  let source=ref "" and firmware=ref "" and output=ref "" and source_output=ref "" in
  let width=ref 32 and engines=ref 4 and words=ref 64 and fifo_words=ref 8 in
  let half_period=ref 32 and mode=ref 0 and clock_hz=ref 50_000_000 in
  let issue=ref "fused" and prefetch=ref false and list_only=ref false in
  let byte_lane=ref false and variant=ref "" in
  let options=[
    "--source",Arg.Set_string source,"FILE strict firmware source JSON";
    "--firmware",Arg.Set_string firmware,"NAME built-in example";
    "--output",Arg.Set_string output,"FILE assembled image JSON";
    "--source-output",Arg.Set_string source_output,"FILE emit built-in source JSON";
    "--width",Arg.Set_int width,"16|32 datapath width for built-ins";
    "--engines",Arg.Set_int engines,"2|4 engines for built-ins";
    "--words",Arg.Set_int words,"32|64|128 program capacity for built-ins";
    "--fifo-words",Arg.Set_int fifo_words,"2|4|8|32 FIFO capacity for built-ins";
    "--half-period",Arg.Set_int half_period,"8..255 clocks; UART bit period is twice this";
    "--mode",Arg.Set_int mode,"0..3 SPI mode for built-ins";
    "--clock-hz",Arg.Set_int clock_hz,"HZ reference clock annotation";
    "--issue",Arg.Set_string issue,"scalar|fused issue for built-ins";
    "--prefetch",Arg.Set prefetch,"enable architecture prefetch flag";
    "--byte-lane-shifts",Arg.Set byte_lane,"target accepts only SHL/SHR counts 0/8/16/24";
    "--variant",Arg.Set_string variant,"FILE refinement config of the target RTL variant: sets the built-in architecture and the shift restriction; a --source must match its architecture";
    "--list",Arg.Set list_only,"list built-in firmware names"] in
  try
    Arg.parse options (fun arg -> raise (Arg.Bad ("unexpected argument "^arg)))
      "assemble (--source FILE | --firmware NAME) --output FILE";
    if !list_only then List.iter print_endline Firmware.names
    else begin
      if (!source="")=(!firmware="") then invalid_arg "choose exactly one of --source and --firmware";
      if !output="" then invalid_arg "--output is required";
      if !source_output<>"" && !source<>"" then invalid_arg "--source-output applies only to built-ins";
      if !source<>"" && (!output= !source || !source_output= !source) then invalid_arg "output must not overwrite input source";
      if !source_output<>"" && !source_output= !output then invalid_arg "image and source output paths must differ";
      let target=if !variant="" then None else Some (variant_target !variant) in
      let byte_lane_shifts= !byte_lane || (match target with Some (_,b) -> b | None -> false) in
      let source_bytes = if !source<>"" then read_file !source else
        let architecture=match target with
          | Some (architecture,_) -> architecture
          | None -> {Isa.engine_count= !engines;data_width= !width;
              program_words= !words;fifo_words= !fifo_words;issue= !issue;prefetch= !prefetch} in
        let s=Firmware.make ~architecture ~half_period:!half_period ~mode:!mode ~clock_hz:!clock_hz
            ~byte_lane_shifts !firmware in
        Yojson.Safe.pretty_to_string (Assembler.source_to_json s)^"\n" in
      let image=Assembler.assemble_with ~byte_lane_shifts ~source_bytes in
      (match target with
       | Some (architecture,_) when image.source.architecture<>architecture ->
         invalid_arg "source architecture differs from the --variant architecture"
       | _ -> ());
      (* Validate everything before opening either output. *)
      let image_bytes=Yojson.Safe.pretty_to_string (Assembler.image_to_json image)^"\n" in
      if !source_output<>"" then write_file !source_output source_bytes;
      write_file !output image_bytes;
      Printf.printf "Assembled %s: %d words; source_sha256=%s; bytecode_sha256=%s\n"
        image.source.name (List.length image.words) image.source_sha256
        (Assembler.sha256 (Assembler.bytecode image))
    end
  with
  | Invalid_argument msg | Sys_error msg | Yojson.Json_error msg ->
    Printf.eprintf "assemble: %s\n" msg;exit 2
