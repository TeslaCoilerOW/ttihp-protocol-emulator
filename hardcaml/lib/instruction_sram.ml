open Hardcaml
open Signal

type inputs = {
  clock : Signal.t; clear : Signal.t; write : Signal.t;
  write_address : Signal.t; write_data : Signal.t; next_pc : Signal.t;
}

type t = {
  instruction : Signal.t; address : Signal.t;
  memory_enable : Signal.t; write_enable : Signal.t; read_enable : Signal.t;
}

let controls ~engine_index (i:inputs) =
  if engine_index < 0 || engine_index >= 4 then invalid_arg "instruction SRAM engine index";
  List.iter (fun (signal,expected) ->
    if width signal <> expected then invalid_arg "instruction SRAM signal width")
    [i.clock,1;i.clear,1;i.write,1;i.write_address,6;i.write_data,32;i.next_pc,24];
  let memory_enable = ~:(i.clear) in
  let write_enable = i.write &: memory_enable in
  let read_enable = ~:write_enable in
  let address = mux2 write_enable i.write_address (select i.next_pc 5 0) in
  memory_enable,write_enable,read_enable,address

let create ~engine_index i =
  let memory_enable,write_enable,read_enable,address = controls ~engine_index i in
  let half suffix data =
    let outputs = Instantiation.create () ~name:"RM_IHPSG13_1P_64x16_c2"
      ~instance:(Printf.sprintf "instruction_sram_e%d_%s" engine_index suffix)
      ~inputs:["A_CLK",i.clock;"A_DLY",vdd;"A_MEN",memory_enable;
               "A_WEN",write_enable;"A_REN",read_enable;"A_ADDR",address;"A_DIN",data]
      ~outputs:["A_DOUT",16] in
    Base.Map.find_exn outputs "A_DOUT" in
  let low = half "lo" (select i.write_data 15 0) in
  let high = half "hi" (select i.write_data 31 16) in
  {instruction=concat_msb [high;low];address;memory_enable;write_enable;read_enable}

(* This fixed two-state model is exclusively for Hardcaml Cyclesim tests.
   It has the macro's registered read/held-output semantics on all controls
   admitted by [controls]: simultaneous write/read is never admitted.  Neither
   contents nor DOUT has a reset.  Four-state startup and the exact vendor
   FUNCTIONAL implementation must be checked independently in emitted RTL. *)
let create_model ~engine_index i =
  let memory_enable,write_enable,read_enable,address = controls ~engine_index i in
  let instruction = ram_rbw 64
    ~write_port:{Write_port.write_clock=i.clock;write_enable;
                 write_address=address;write_data=i.write_data}
    ~read_port:{Read_port.read_clock=i.clock;
                read_enable=memory_enable &: read_enable;read_address=address} in
  {instruction;address;memory_enable;write_enable;read_enable}
