open Hardcaml

let check label condition = if not condition then failwith label

let circuit factory capacity =
  let input=Signal.input and output=Signal.output in
  let (m:Payload_sram.t)=factory capacity {
    Payload_sram.clock=input "clk" 1;clear=input "clear" 1;
    request=input "request" 1;write=input "write" 1;
    address=input "address" (Payload_sram.address_bits capacity);
    write_data=input "write_data" 32;tag=input "tag" 4} in
  Circuit.create_exn ~name:"protocol_payload_sram_adapter"
    [output "ready" m.ready;output "accepted" m.accepted;
     output "read_valid" m.read_valid;output "read_data" m.read_data;
     output "read_tag" m.read_tag;output "men" m.memory_enable;
     output "wen" m.write_enable;output "ren" m.read_enable]

let test capacity =
  let n=Payload_sram.words capacity in
  let sim=Cyclesim.create (circuit Payload_sram.create_model capacity) in
  let memory=Array.make n None in
  let cycle=ref 0 in
  let previous_read=ref false and previous_tag=ref 0 and previous_data=ref None in
  let set name width value=Cyclesim.in_port sim name := Bits.of_int ~width value in
  let get edge name=Bits.to_int !(Cyclesim.out_port ~clock_edge:edge sim name) in
  let tick ~clear ~request ~write ~address ~data ~tag =
    set "clear" 1 clear;set "request" 1 request;set "write" 1 write;
    set "address" (Payload_sram.address_bits capacity) address;
    set "write_data" 32 data;set "tag" 4 tag;
    Cyclesim.cycle_before_clock_edge sim;
    let accepted=clear=0 && request=1 in
    let read=accepted && write=0 in
    let bit b=if b then 1 else 0 in
    check "request accepted only outside clear"
      (get Before "accepted"=bit accepted && get Before "ready"=bit (clear=0));
    check "single port controls exclusive and idle disabled"
      (get Before "men"=bit accepted && get Before "wen"=bit (accepted && write=1)
       && get Before "ren"=bit read);
    if clear=1 then check "clear immediately masks pending response"
      (get Before "read_valid"=0 && get Before "read_data"=0 && get Before "read_tag"=0);
    if clear=0 then begin
      check "new request cannot change previous response before its edge"
        (get Before "read_valid"=bit !previous_read
         && get Before "read_tag"=(if !previous_read then !previous_tag else 0));
      if not !previous_read then check "idle response remains masked before edge"
        (get Before "read_data"=0);
      match !previous_data with
      | Some value -> check "read data is registered across address/input changes"
                        (get Before "read_data"=value)
      | None -> ()
    end;
    let expected=if read then memory.(address) else None in
    Cyclesim.cycle_at_clock_edge sim;Cyclesim.cycle_after_clock_edge sim;
    if accepted && write=1 then memory.(address)<-Some data;
    check "exact read response pulse" (get After "read_valid"=bit read);
    check "opaque tag follows read, all sixteen tags admitted"
      (get After "read_tag"=(if read then tag else 0));
    if not read then check "invalid response is masked" (get After "read_data"=0);
    (match expected with
     | Some value -> check (Printf.sprintf "payload mismatch at cycle %d address %d" !cycle address)
                       (get After "read_data"=value)
     | None -> ());
    previous_read:=read;previous_tag:=tag;previous_data:=expected;
    incr cycle in
  tick ~clear:1 ~request:1 ~write:1 ~address:0 ~data:42 ~tag:15;
  (* Distinct high/low bits at every address detect aliases and half-word loss. *)
  for address=0 to n-1 do
    tick ~clear:0 ~request:1 ~write:1 ~address
      ~data:(0x80000000 lor (address lsl 16) lor (address lxor 0xa55a)) ~tag:(address land 15)
  done;
  for address=n-1 downto 0 do
    tick ~clear:0 ~request:1 ~write:0 ~address ~data:0 ~tag:(address land 15)
  done;
  (* Clear cancels a pending return and a requested write, but does not erase data. *)
  tick ~clear:0 ~request:1 ~write:0 ~address:0 ~data:0 ~tag:9;
  tick ~clear:1 ~request:1 ~write:1 ~address:0 ~data:0xbad ~tag:8;
  tick ~clear:0 ~request:1 ~write:0 ~address:0 ~data:0 ~tag:7;
  tick ~clear:0 ~request:0 ~write:1 ~address:0 ~data:0xbad ~tag:6;
  tick ~clear:0 ~request:1 ~write:0 ~address:0 ~data:0 ~tag:5;
  let rng=Random.State.make [|0x51a4;n|] in
  for _=1 to 2000 do
    tick ~clear:(if Random.State.int rng 19=0 then 1 else 0)
      ~request:(Random.State.int rng 2) ~write:(Random.State.int rng 2)
      ~address:(Random.State.int rng n)
      ~data:(Random.State.bits rng lor ((Random.State.int rng 4) lsl 30))
      ~tag:(Random.State.int rng 16)
  done;
  (* Vendor-backed circuit elaborates separately; no blackbox is simulated here. *)
  check "fixed vendor macro elaborates"
    (Circuit.name (circuit Payload_sram.create capacity)="protocol_payload_sram_adapter");
  !cycle

let rejected_width () =
  let input=Signal.input in
  let i={Payload_sram.clock=input "clk" 1;clear=input "clear" 1;
         request=input "request" 1;write=input "write" 1;
         address=input "address" 9;write_data=input "write_data" 32;tag=input "tag" 4} in
  List.iter (fun changed ->
    match Payload_sram.create_model Payload_sram.KiB2 changed with
    | _ -> failwith "invalid payload interface width accepted"
    | exception Invalid_argument _ -> ())
    [{i with clock=input "wide_clock" 2};{i with clear=input "wide_clear" 2};
     {i with request=input "wide_request" 2};{i with write=input "wide_write" 2};
     {i with address=input "wrong_address" 10};{i with write_data=input "narrow_data" 16};
     {i with tag=input "narrow_tag" 3}]

let () =
  rejected_width ();
  let cycles=List.fold_left (fun total capacity -> total+test capacity) 0
    [Payload_sram.KiB2;Payload_sram.KiB4] in
  Printf.printf "Payload SRAM source/model: %d cycles across 512/1024 words, all addresses, tags, reset and idle; vendor circuits elaborated only.\n" cycles
