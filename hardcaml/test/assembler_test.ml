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
(* The opcode and host-command tables: [all] is in encoding order and every
   conversion round-trips. *)
let () =
  check "opcodes 0..33 in order" (List.map Opcode.to_int Opcode.all=List.init 34 Fun.id);
  List.iter (fun op ->
    check ("opcode round trip "^Opcode.mnemonic op)
      (Opcode.of_int (Opcode.to_int op)=Some op && Opcode.of_mnemonic (Opcode.mnemonic op)=Some op))
    Opcode.all;
  check "no opcode 34" (Opcode.of_int 34=None && Opcode.of_mnemonic "FOO"=None);
  check "line-unit opcodes are 30..33"
    (List.filter Opcode.requires_line_unit Opcode.all=List.filter_map Opcode.of_int [30;31;32;33]);
  check "host commands 0..11 in order" (List.map Host_command.to_int Host_command.all=List.init 12 Fun.id);
  List.iter (fun c ->
    check ("host command round trip "^Host_command.name c) (Host_command.of_int (Host_command.to_int c)=Some c))
    Host_command.all;
  check "no host command 12" (Host_command.of_int 12=None);
  List.iter (fun i -> check "issue round trip" (Issue.of_string (Issue.to_string i)=i)) Issue.all;
  rejected "issue name" (fun () -> Issue.of_string "custom")
let () =
  let words=assemble_source (source_of_nodes [node ~imm:1 Set;node ~imm:7 Count;
    node ~label:"bit" ~a:0 Out;node ~target:"bit" Loop;node Halt]) in
  check "golden independent encoding" (words.words=[0x02000001l;0x0a000007l;0x08000000l;0x0b000002l;0x01000000l]);
  check "LE bytes" (String.sub (Assembler.bytecode words) 0 4="\001\000\000\002");
  check "SHA-256 golden" (Assembler.sha256 "abc"="ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
  let source=Assembler.source_to_json words.source |> Yojson.Safe.to_string in
  let altered=Assembler.assemble ~source_bytes:(source^"\n") in
  check "source whitespace identity" (words.source_sha256<>altered.source_sha256);
  check "semantic bytes stable" (Assembler.bytecode words=Assembler.bytecode altered);
  let bad nodes = rejected "invalid source" (fun () -> assemble_source (source_of_nodes nodes)) in
  bad [node ~label:"same" Nop;node ~label:"same" Halt];
  bad [node ~target:"absent" Jmp];
  bad [node ~imm:1 Jmp];
  bad [node ~target:"x" ~imm:1 Jmp;node ~label:"x" Halt];
  bad [node ~imm:2 Set];
  bad [node ~a:1 Out];
  bad [node ~a:8 In];
  bad [node ~b:1 Halt];
  bad [node ~a:2 Push];
  bad [node ~a:1 ~b:1 Push];
  bad [node ~a:1 ~c:1 Push];
  bad [node ~a:1 ~imm:1 Push];
  bad [node ~imm:0 Limit];
  bad [node ~a:33 ~b:1 Xfer];
  bad [node ~imm:16 Signal];
  bad [node ~a:4 Time];
  let malformed=`Assoc ["mnemonic",`String "HALT";"mnemonic",`String "NOP"] in
  bad [malformed];
  bad [`Assoc ["mnemonic",`String "HALT";"surprise",`Int 1]];
  bad [`Assoc ["mnemonic",`String "FOO"]];
  bad [`Assoc ["mnemonic",`String "halt"]];
  let scalar={Isa.flagship with issue=Scalar} in
  let strict_push=Isa.instruction ~a:1 Push and blocking_push=Isa.instruction Push in
  check "strict PUSH encoding" (Isa.encode scalar ~owned_pins:0 strict_push=0x07010000l);
  check "strict PUSH timing" (Isa.minimum_cycles strict_push=1 && Isa.blocking strict_push="none");
  check "legacy PUSH timing" (Isa.minimum_cycles blocking_push=1 && Isa.blocking blocking_push="rx_fifo_unbounded");
  let image_json=Assembler.image_to_json words in
  check "ISA version 2" (Yojson.Safe.Util.member "isa_version" image_json=`Int 2);
  rejected "scalar fused opcode" (fun () -> Isa.encode scalar ~owned_pins:1 (Isa.instruction ~a:8 ~b:8 Xfer));
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
          let pushes=List.filter (fun (i:Isa.instruction) -> Opcode.equal i.op Push) image.decoded in
          if name="uart-rx" || name="spi-target" then
            check ("strict external receiver "^name) (pushes<>[] && List.for_all (fun i -> i.Isa.a=1) pushes);
          if name="i2c-target-write" || name="i2c-target-read" then
            check ("stretching I2C preserves blocking PUSH "^name) (List.for_all (fun i -> i.Isa.a=0) pushes);
          incr compiled
        done
      end) Firmware.names) [Issue.Scalar;Fused]) [16;32];
  (* Variant targets: FIFO depths 2/4 and byte-lane shifts (shift=byte_lane). *)
  let byte_lane nodes = Assembler.assemble_with ~byte_lane_shifts:true
      ~source_bytes:(Yojson.Safe.to_string (Assembler.source_to_json (source_of_nodes nodes))) in
  List.iter (fun c ->
    check (Printf.sprintf "byte-lane SHL/SHR by %d accepted" c)
      ((byte_lane [node ~a:1 ~c Shl;node ~a:2 ~c Shr]).words
       =[Int32.of_int (0x18010000 lor c);Int32.of_int (0x19020000 lor c)]);
    check "byte-lane restriction off by default" (List.length (assemble_source (source_of_nodes [node ~c Shl])).words=1))
    [0;8;16;24];
  List.iter (fun c ->
    rejected (Printf.sprintf "byte-lane SHL by %d" c) (fun () -> byte_lane [node ~c Shl]);
    rejected (Printf.sprintf "byte-lane SHR by %d" c) (fun () -> byte_lane [node ~c Shr]);
    if c<32 then check "barrel accepts every count below the width"
      (List.length (assemble_source (source_of_nodes [node ~c Shl])).words=1))
    [1;4;7;9;12;23;25;31;32];
  let sixteen={Isa.flagship with data_width=16} in
  rejected "byte-lane count 16 on a 16-bit datapath" (fun () ->
    Isa.encode ~byte_lane_shifts:true sixteen ~owned_pins:0 (Isa.instruction ~c:16 Shl));
  check "byte-lane count 8 on a 16-bit datapath"
    (Isa.encode ~byte_lane_shifts:true sixteen ~owned_pins:0 (Isa.instruction ~c:8 Shl)=0x18000008l);
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
        if name="jtag" && Issue.equal issue Scalar then
          rejected "scalar jtag with byte-lane shifts" (fun () ->
            Firmware.make ~architecture ~mode ~byte_lane_shifts:true name)
        else begin
          let source=Firmware.make ~architecture ~mode ~byte_lane_shifts:true name in
          let image=Assembler.assemble_with ~byte_lane_shifts:true
              ~source_bytes:(Yojson.Safe.to_string (Assembler.source_to_json source)) in
          check ("byte-lane image binds FIFO depth "^name) (image.source.architecture.fifo_words=fifo_words);
          check ("byte-lane image uses only lane counts "^name)
            (List.for_all (fun (i:Isa.instruction) ->
               not (List.mem i.op [Opcode.Shl;Shr]) || i.c land 7=0) image.decoded);
          (* Fused built-ins are unchanged; scalar SPI keeps its length. *)
          let plain=assemble_source (Firmware.make ~architecture ~mode name) in
          if Issue.equal issue Fused then check ("fused built-in unchanged "^name) (plain.words=image.words)
          else check ("scalar built-in keeps its length "^name)
              (List.length plain.words=List.length image.words);
          incr variant_images
        end
      done) Firmware.names) [Issue.Scalar;Fused]) [16;32]) [2;4;8];
  Printf.printf "Assembler validation, %d firmware/configuration combinations and %d byte-lane/FIFO-depth variant images passed.\n" !compiled !variant_images
