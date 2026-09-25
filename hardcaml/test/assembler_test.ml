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
  (* Variant targets: FIFO depths 2/4 and byte-lane shifts (shift=byte_lane). *)
  let byte_lane nodes = Assembler.assemble_with ~byte_lane_shifts:true
      ~source_bytes:(Yojson.Safe.to_string (Assembler.source_to_json (source_of_nodes nodes))) in
  List.iter (fun c ->
    check (Printf.sprintf "byte-lane SHL/SHR by %d accepted" c)
      ((byte_lane [node ~a:1 ~c "SHL";node ~a:2 ~c "SHR"]).words
       =[Int32.of_int (0x18010000 lor c);Int32.of_int (0x19020000 lor c)]);
    check "byte-lane restriction off by default" (List.length (assemble_source (source_of_nodes [node ~c "SHL"])).words=1))
    [0;8;16;24];
  List.iter (fun c ->
    rejected (Printf.sprintf "byte-lane SHL by %d" c) (fun () -> byte_lane [node ~c "SHL"]);
    rejected (Printf.sprintf "byte-lane SHR by %d" c) (fun () -> byte_lane [node ~c "SHR"]);
    if c<32 then check "barrel accepts every count below the width"
      (List.length (assemble_source (source_of_nodes [node ~c "SHL"])).words=1))
    [1;4;7;9;12;23;25;31;32];
  let sixteen={Isa.flagship with data_width=16} in
  rejected "byte-lane count 16 on a 16-bit datapath" (fun () ->
    Isa.encode ~byte_lane_shifts:true sixteen ~owned_pins:0 (Isa.instruction ~c:16 "SHL"));
  check "byte-lane count 8 on a 16-bit datapath"
    (Isa.encode ~byte_lane_shifts:true sixteen ~owned_pins:0 (Isa.instruction ~c:8 "SHL")=0x18000008l);
  let arch_json fifo_words = Isa.architecture_to_json {Isa.flagship with fifo_words} in
  List.iter (fun fifo_words ->
    check "FIFO depth accepted by the assembler"
      ((Isa.architecture_of_json (arch_json fifo_words)).fifo_words=fifo_words)) [2;4;8;32];
  List.iter (fun fifo_words ->
    rejected "FIFO depth" (fun () -> Isa.architecture_of_json (arch_json fifo_words))) [1;3;16];
  let variant_images=ref 0 in
  List.iter (fun fifo_words -> List.iter (fun width -> List.iter (fun issue ->
    List.iter (fun name ->
      for mode=0 to (if name="spi-controller" || name="spi-target" then 3 else 0) do
        let architecture={Isa.flagship with data_width=width;issue;fifo_words} in
        if name="jtag" && issue="scalar" then
          rejected "scalar jtag with byte-lane shifts" (fun () ->
            Firmware.make ~architecture ~mode ~byte_lane_shifts:true name)
        else begin
          let source=Firmware.make ~architecture ~mode ~byte_lane_shifts:true name in
          let image=Assembler.assemble_with ~byte_lane_shifts:true
              ~source_bytes:(Yojson.Safe.to_string (Assembler.source_to_json source)) in
          check ("byte-lane image binds FIFO depth "^name) (image.source.architecture.fifo_words=fifo_words);
          check ("byte-lane image uses only lane counts "^name)
            (List.for_all (fun (i:Isa.instruction) ->
               not (List.mem i.mnemonic ["SHL";"SHR"]) || i.c land 7=0) image.decoded);
          (* Fused built-ins are unchanged; scalar SPI keeps its length. *)
          let plain=assemble_source (Firmware.make ~architecture ~mode name) in
          if issue="fused" then check ("fused built-in unchanged "^name) (plain.words=image.words)
          else check ("scalar built-in keeps its length "^name)
              (List.length plain.words=List.length image.words);
          incr variant_images
        end
      done) Firmware.names) ["scalar";"fused"]) [16;32]) [2;4;8];
  Printf.printf "Assembler validation, %d firmware/configuration combinations and %d byte-lane/FIFO-depth variant images passed.\n" !compiled !variant_images
