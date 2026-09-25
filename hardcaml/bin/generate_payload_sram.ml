(* Standalone vendor-backed adapter only. A fixed caller captures stdout; this
   interface accepts no source path, module name, command or environment hook. *)
let () =
  let capacity=match Array.to_list Sys.argv with
    | [_;"--capacity-kib";"2"] -> Payload_sram.KiB2
    | [_;"--capacity-kib";"4"] -> Payload_sram.KiB4
    | _ -> prerr_endline "generate_payload_sram --capacity-kib 2|4";exit 2 in
  let input=Hardcaml.Signal.input and output=Hardcaml.Signal.output in
  let m=Payload_sram.create capacity {
    clock=input "clk" 1;clear=input "clear" 1;request=input "request" 1;
    write=input "write" 1;address=input "address" (Payload_sram.address_bits capacity);
    write_data=input "write_data" 32;tag=input "tag" 4} in
  let circuit=Hardcaml.Circuit.create_exn ~name:"protocol_payload_sram_adapter"
    [output "ready" m.ready;output "accepted" m.accepted;
     output "read_valid" m.read_valid;output "read_data" m.read_data;
     output "read_tag" m.read_tag;output "men" m.memory_enable;
     output "wen" m.write_enable;output "ren" m.read_enable] in
  Hardcaml.Rtl.print Verilog circuit
