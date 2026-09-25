let () =
  let config_path=ref "" and output_path=ref "" and target=ref "" in
  Arg.parse ["--config",Arg.Set_string config_path,"Closed refinement JSON";
             "--output",Arg.Set_string output_path,"Generated verification Verilog";
             "--target",Arg.Set_string target,"adapter, engine or processor"]
    (fun _->raise (Arg.Bad "positional arguments are not accepted"))
    "generate_refinement_formal --config refinement.json --target adapter|engine|processor --output verification.v";
  if !config_path="" || !output_path="" then
    (prerr_endline "--config and --output are required";exit 2);
  try
    let config=Refinement_config.load !config_path in
    let circuit=match !target with
      | "adapter" -> Instruction_sram_formal.adapter ()
      | "engine" -> Instruction_sram_formal.engine config
      | "processor" -> Instruction_sram_formal.processor config
      | _ -> invalid_arg "--target must be adapter, engine or processor" in
    Hardcaml.Rtl.output ~output_mode:(To_file !output_path) Verilog circuit
  with exn -> prerr_endline (Printexc.to_string exn);exit 1
