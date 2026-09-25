type source = { name : string; architecture : Isa.architecture; engine : int;
                owned_pins : int; open_drain : int; clock_hz : int;
                instructions : Yojson.Safe.t list; notes : string list }
type image = { source : source; words : int32 list; labels : (string * int) list;
               decoded : Isa.instruction list; source_sha256 : string }
let fail fmt = Printf.ksprintf invalid_arg fmt
let sha256 s = Digestif.SHA256.(to_hex (digest_string s))
let source_of_json j =
  let f = Isa.object_fields "source" ["schema_version";"name";"architecture";
    "engine";"owned_pins";"open_drain";"clock_hz";"instructions";"notes"] j in
  if Isa.required "schema_version" f <> `String "protocol-emulator.firmware-source.v1" then fail "source schema version mismatch";
  let s k = Isa.json_string k (Isa.required k f) and i k = Isa.json_int k (Isa.required k f) in
  let name=s "name" and architecture=Isa.architecture_of_json (Isa.required "architecture" f) in
  let engine=i "engine" and owned_pins=i "owned_pins" and open_drain=i "open_drain" and clock_hz=i "clock_hz" in
  if name="" || String.length name>128 then fail "name must contain 1..128 characters";
  if engine<0 || engine>=architecture.engine_count then fail "engine outside architecture";
  if owned_pins<0 || owned_pins>255 || open_drain<0 || open_drain>255 || open_drain land (lnot owned_pins)<>0 then fail "invalid pin ownership/open-drain masks";
  if clock_hz<1 || clock_hz>1_000_000_000 then fail "clock_hz must be 1..1000000000";
  let instructions=match Isa.required "instructions" f with `List xs -> xs | _ -> fail "instructions must be a list" in
  let notes=match Isa.required "notes" f with `List xs -> List.map (Isa.json_string "note") xs | _ -> fail "notes must be a string list" in
  if instructions=[] || List.length instructions>architecture.program_words then fail "image length must be 1..%d" architecture.program_words;
  {name;architecture;engine;owned_pins;open_drain;clock_hz;instructions;notes}
let source_to_json s = `Assoc [
  "schema_version",`String "protocol-emulator.firmware-source.v1";
  "name",`String s.name;"architecture",Isa.architecture_to_json s.architecture;
  "engine",`Int s.engine;"owned_pins",`Int s.owned_pins;
  "open_drain",`Int s.open_drain;"clock_hz",`Int s.clock_hz;
  "instructions",`List s.instructions;"notes",`List (List.map (fun n -> `String n) s.notes)]
let identifier s =
  String.length s>0 && String.length s<=64 &&
  let good = function 'a'..'z'|'A'..'Z'|'_'|'0'..'9' -> true | _ -> false in
  let first = match s.[0] with '0'..'9' -> false | c -> good c in
  first && String.for_all good s
let node ?label ?target i =
  let base = ["mnemonic",`String i.Isa.mnemonic] in
  let base=List.fold_left (fun xs (k,v) -> if v=0 then xs else xs@[k,`Int v]) base
    ["a",i.a;"b",i.b;"c",i.c;"imm",i.imm] in
  let base=match label with None -> base | Some s -> base@["label",`String s] in
  let base=match target with None -> base | Some s -> base@["target",`String s] in `Assoc base
let assemble ~source_bytes =
  let source=source_of_json (Yojson.Safe.from_string source_bytes) in
  let fields=List.map (Isa.object_fields "instruction" ["mnemonic";"a";"b";"c";"imm";"label";"target"]) source.instructions in
  let labels=List.mapi (fun pc f -> match List.assoc_opt "label" f with
    | None -> None | Some j ->
      let name=Isa.json_string "label" j in
      if not (identifier name) then fail "invalid label %s" name;
      Some (name,pc)) fields |> List.filter_map Fun.id in
  let seen=Hashtbl.create 16 in
  List.iter (fun (label,pc) -> if Hashtbl.mem seen label then fail "duplicate label %s" label;Hashtbl.add seen label pc) labels;
  let decoded=List.mapi (fun pc f ->
    let mnemonic=Isa.json_string "mnemonic" (Isa.required "mnemonic" f) in
    let i k=match List.assoc_opt k f with None -> 0 | Some j -> Isa.json_int k j in
    let a=i "a" and b=i "b" and c=i "c" in
    let imm=match List.assoc_opt "target" f with
      | None -> i "imm"
      | Some j ->
        if List.mem_assoc "imm" f then fail "pc %d: target and imm are mutually exclusive" pc;
        if not (List.mem mnemonic ["JMP";"LOOP";"JZ"]) then fail "pc %d: target is only legal for branches" pc;
        let target=Isa.json_string "target" j in
        (match Hashtbl.find_opt seen target with Some n -> n | None -> fail "pc %d: undefined target %s" pc target) in
    if List.mem mnemonic ["JMP";"LOOP";"JZ"] && (imm<0 || imm>=List.length fields) then fail "pc %d: branch leaves committed image" pc;
    Isa.instruction ~a ~b ~c ~imm mnemonic) fields in
  let words=List.mapi (fun pc i -> try Isa.encode source.architecture ~owned_pins:source.owned_pins i
      with Invalid_argument why -> fail "pc %d (%s): %s" pc i.mnemonic why) decoded in
  {source;words;labels;decoded;source_sha256=sha256 source_bytes}
let bytecode image =
  let b=Bytes.create (4*List.length image.words) in
  List.iteri (fun n word ->
    for i=0 to 3 do Bytes.set b (4*n+i) (Char.chr (Int32.to_int (Int32.logand (Int32.shift_right_logical word (8*i)) 0xffl))) done) image.words;
  Bytes.to_string b
let image_to_json image =
  let s=image.source in
  `Assoc ["schema_version",`String "protocol-emulator.firmware-image.v1";
    "isa_version",`Int 2;"name",`String s.name;
    "architecture",Isa.architecture_to_json s.architecture;
    "engine",`Int s.engine;"owned_pins",`Int s.owned_pins;"open_drain",`Int s.open_drain;
    "clock_hz",`Int s.clock_hz;"source_sha256",`String image.source_sha256;
    "bytecode_sha256",`String (sha256 (bytecode image));
    "words",`List (List.map (fun n -> `Int (Int32.to_int n)) image.words);
    "labels",`Assoc (List.map (fun (k,v) -> k,`Int v) image.labels);
    "timing",`List (List.mapi (fun pc i -> `Assoc ["pc",`Int pc;
      "minimum_cycles",`Int (Isa.minimum_cycles i);"blocking",`String (Isa.blocking i)]) image.decoded);
    "notes",`List (List.map (fun n -> `String n) s.notes)]
