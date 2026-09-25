let () =
  let config_path=ref "" and output_path=ref "" and name=ref "" in
  Arg.parse ["--config",Arg.Set_string config_path,"Closed refinement JSON";
             "--output",Arg.Set_string output_path,"Generated Verilog";
             "--name",Arg.Set_string name,"Top module name (default tt_um_protocol_processor)"]
    (fun _->raise (Arg.Bad "positional arguments are not accepted"))
    "generate_refinement --config refinement.json --output processor.v [--name module]";
  if !config_path="" || !output_path="" then
    (prerr_endline "--config and --output are required"; exit 2);
  try
    let config=Refinement_config.load !config_path in
    let circuit=Processor.create_refinement config in
    let circuit=if !name="" then circuit else Hardcaml.Circuit.with_name circuit ~name:!name in
    Hardcaml.Rtl.output ~output_mode:(To_file !output_path) Verilog circuit
  with exn -> prerr_endline (Printexc.to_string exn); exit 1
