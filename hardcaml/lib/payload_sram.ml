open Hardcaml
open Signal

type capacity = KiB2 | KiB4
let words = function KiB2 -> 512 | KiB4 -> 1024
let address_bits = function KiB2 -> 9 | KiB4 -> 10
let macro_name = function
  | KiB2 -> "RM_IHPSG13_1P_512x32_c2_bm_bist"
  | KiB4 -> "RM_IHPSG13_1P_1024x32_c2_bm_bist"

type inputs = {
  clock : Signal.t; clear : Signal.t; request : Signal.t; write : Signal.t;
  address : Signal.t; write_data : Signal.t; tag : Signal.t;
}

type t = {
  ready : Signal.t; accepted : Signal.t; read_valid : Signal.t;
  read_data : Signal.t; read_tag : Signal.t; memory_enable : Signal.t;
  write_enable : Signal.t; read_enable : Signal.t;
}

let controls capacity (i:inputs) =
  List.iter (fun (signal,expected) ->
    if width signal <> expected then invalid_arg "payload SRAM signal width")
    [i.clock,1;i.clear,1;i.request,1;i.write,1;
     i.address,address_bits capacity;i.write_data,32;i.tag,4];
  let ready = ~:(i.clear) in
  let accepted = i.request &: ready in
  ready,accepted,accepted &: i.write,accepted &: ~:(i.write)

let response i ~ready ~accepted ~write_enable ~read_enable data =
  let spec = Reg_spec.create ~clock:i.clock ~clear:i.clear () in
  let read_valid = reg spec ~enable:vdd read_enable &: ~:(i.clear) in
  let tag = reg spec ~enable:read_enable i.tag in
  {ready;accepted;read_valid;read_data=mux2 read_valid data (zero 32);
   read_tag=mux2 read_valid tag (zero 4);memory_enable=accepted;
   write_enable;read_enable}

let create capacity i =
  let ready,accepted,write_enable,read_enable = controls capacity i in
  let outputs = Instantiation.create () ~name:(macro_name capacity)
    ~instance:"payload_sram"
    ~inputs:["A_CLK",i.clock;"A_DLY",vdd;"A_MEN",accepted;
             "A_WEN",write_enable;"A_REN",read_enable;"A_ADDR",i.address;
             "A_DIN",i.write_data;"A_BM",ones 32;
             "A_BIST_CLK",gnd;"A_BIST_EN",gnd;"A_BIST_MEN",gnd;
             "A_BIST_WEN",gnd;"A_BIST_REN",gnd;
             "A_BIST_ADDR",zero (address_bits capacity);
             "A_BIST_DIN",zero 32;"A_BIST_BM",zero 32]
    ~outputs:["A_DOUT",32] in
  response i ~ready ~accepted ~write_enable ~read_enable
    (Base.Map.find_exn outputs "A_DOUT")

let create_model capacity i =
  let ready,accepted,write_enable,read_enable = controls capacity i in
  (* The macro holds DOUT on writes/idle and has no memory or DOUT reset.
     Control exclusivity prevents read-during-write from reaching either model. *)
  let data = ram_rbw (words capacity)
    ~write_port:{Write_port.write_clock=i.clock;write_enable;
                 write_address=i.address;write_data=i.write_data}
    ~read_port:{Read_port.read_clock=i.clock;read_enable;read_address=i.address} in
  response i ~ready ~accepted ~write_enable ~read_enable data
