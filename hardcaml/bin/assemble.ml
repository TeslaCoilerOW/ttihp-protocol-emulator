let read_file path =
  let channel = open_in_bin path in
  Fun.protect ~finally:(fun () -> close_in channel)
    (fun () -> really_input_string channel (in_channel_length channel))
let write_file path contents =
  let channel = open_out_bin path in
  Fun.protect ~finally:(fun () -> close_out channel)
    (fun () -> output_string channel contents)
let () =
  let source=ref "" and firmware=ref "" and output=ref "" and source_output=ref "" in
  let width=ref 32 and engines=ref 4 and words=ref 64 and fifo_words=ref 8 in
  let half_period=ref 32 and mode=ref 0 and clock_hz=ref 50_000_000 in
  let issue=ref "fused" and prefetch=ref false and list_only=ref false in
  let options=[
    "--source",Arg.Set_string source,"FILE strict firmware source JSON";
    "--firmware",Arg.Set_string firmware,"NAME built-in example";
    "--output",Arg.Set_string output,"FILE assembled image JSON";
    "--source-output",Arg.Set_string source_output,"FILE emit built-in source JSON";
    "--width",Arg.Set_int width,"16|32 datapath width for built-ins";
    "--engines",Arg.Set_int engines,"2|4 engines for built-ins";
    "--words",Arg.Set_int words,"32|64|128 program capacity for built-ins";
    "--fifo-words",Arg.Set_int fifo_words,"8|32 FIFO capacity for built-ins";
    "--half-period",Arg.Set_int half_period,"8..255 clocks; UART bit period is twice this";
    "--mode",Arg.Set_int mode,"0..3 SPI mode for built-ins";
    "--clock-hz",Arg.Set_int clock_hz,"HZ reference clock annotation";
    "--issue",Arg.Set_string issue,"scalar|fused issue for built-ins";
    "--prefetch",Arg.Set prefetch,"enable architecture prefetch flag";
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
      let source_bytes = if !source<>"" then read_file !source else
        let architecture={Isa.engine_count= !engines;data_width= !width;
          program_words= !words;fifo_words= !fifo_words;issue= !issue;prefetch= !prefetch} in
        let s=Firmware.make ~architecture ~half_period:!half_period ~mode:!mode ~clock_hz:!clock_hz !firmware in
        Yojson.Safe.pretty_to_string (Assembler.source_to_json s)^"\n" in
      let image=Assembler.assemble ~source_bytes in
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
