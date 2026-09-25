let check description condition = if not condition then failwith description
let rejected description f =
  match f () with
  | _ -> failwith ("accepted "^description)
  | exception Invalid_argument _ -> ()
let source_of_nodes nodes =
  {Assembler.name="test";architecture=Isa.flagship;engine=0;owned_pins=1;
   open_drain=0;clock_hz=50_000_000;instructions=nodes;notes=[]}
let assemble_source s = Assembler.assemble ~source_bytes:(Yojson.Safe.to_string (Assembler.source_to_json s))
let node ?label ?target ?a ?b ?c ?imm op = Assembler.node ?label ?target (Isa.instruction ?a ?b ?c ?imm op)
let () =
  let words=assemble_source (source_of_nodes [node ~imm:1 "SET";node ~imm:7 "COUNT";
    node ~label:"bit" ~a:0 "OUT";node ~target:"bit" "LOOP";node "HALT"]) in
  check "golden independent encoding" (words.words=[0x02000001l;0x0a000007l;0x08000000l;0x0b000002l;0x01000000l]);
  check "LE bytes" (String.sub (Assembler.bytecode words) 0 4="\001\000\000\002");
  check "SHA-256 golden" (Assembler.sha256 "abc"="ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
  let source=Assembler.source_to_json words.source |> Yojson.Safe.to_string in
  let altered=Assembler.assemble ~source_bytes:(source^"\n") in
  check "source whitespace identity" (words.source_sha256<>altered.source_sha256);
  check "semantic bytes stable" (Assembler.bytecode words=Assembler.bytecode altered);
  let bad nodes = rejected "invalid source" (fun () -> assemble_source (source_of_nodes nodes)) in
  bad [node ~label:"same" "NOP";node ~label:"same" "HALT"];
  bad [node ~target:"absent" "JMP"];
  bad [node ~imm:1 "JMP"];
  bad [node ~target:"x" ~imm:1 "JMP";node ~label:"x" "HALT"];
  bad [node ~imm:2 "SET"];
  bad [node ~a:1 "OUT"];
  bad [node ~a:8 "IN"];
  bad [node ~b:1 "HALT"];
  bad [node ~a:2 "PUSH"];
  bad [node ~a:1 ~b:1 "PUSH"];
  bad [node ~a:1 ~c:1 "PUSH"];
  bad [node ~a:1 ~imm:1 "PUSH"];
  bad [node ~imm:0 "LIMIT"];
  bad [node ~a:33 ~b:1 "XFER"];
  bad [node ~imm:16 "SIGNAL"];
  bad [node ~a:4 "TIME"];
  let malformed=`Assoc ["mnemonic",`String "HALT";"mnemonic",`String "NOP"] in
  bad [malformed];
  bad [`Assoc ["mnemonic",`String "HALT";"surprise",`Int 1]];
  let scalar={Isa.flagship with issue="scalar"} in
  let strict_push=Isa.instruction ~a:1 "PUSH" and blocking_push=Isa.instruction "PUSH" in
  check "strict PUSH encoding" (Isa.encode scalar ~owned_pins:0 strict_push=0x07010000l);
  check "strict PUSH timing" (Isa.minimum_cycles strict_push=1 && Isa.blocking strict_push="none");
  check "legacy PUSH timing" (Isa.minimum_cycles blocking_push=1 && Isa.blocking blocking_push="rx_fifo_unbounded");
  let image_json=Assembler.image_to_json words in
  check "ISA version 2" (Yojson.Safe.Util.member "isa_version" image_json=`Int 2);
  rejected "scalar fused opcode" (fun () -> Isa.encode scalar ~owned_pins:1 (Isa.instruction ~a:8 ~b:8 "XFER"));
  let compiled=ref 0 in
  List.iter (fun width -> List.iter (fun issue ->
    List.iter (fun name ->
      begin
        let architecture={Isa.flagship with data_width=width;issue;
          program_words=64} in
        for mode=0 to (if name="spi-controller" || name="spi-target" then 3 else 0) do
          let source=Firmware.make ~architecture ~mode name in
          let image=assemble_source source in
          check ("nonempty "^name) (image.words<>[]);
          check ("fits "^name) (List.length image.words<=architecture.program_words);
          let pushes=List.filter (fun i -> i.Isa.mnemonic="PUSH") image.decoded in
          if name="uart-rx" || name="spi-target" then
            check ("strict external receiver "^name) (pushes<>[] && List.for_all (fun i -> i.Isa.a=1) pushes);
          if name="i2c-target-write" || name="i2c-target-read" then
            check ("stretching I2C preserves blocking PUSH "^name) (List.for_all (fun i -> i.Isa.a=0) pushes);
          incr compiled
        done
      end) Firmware.names) ["scalar";"fused"]) [16;32];
  Printf.printf "Assembler validation and %d firmware/configuration combinations passed.\n" !compiled
