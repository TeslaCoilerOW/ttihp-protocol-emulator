open Hardcaml
open Signal

type t = {ready : Signal.t; valid : Signal.t; data : Signal.t; level : Signal.t}

(* Requests are accepted only against pre-edge occupancy. At full, a simultaneous
   pop creates space on the following cycle; there is no combinational bypass. *)
let create ~clock ~clear ~width ~depth ~push ~pop ~data =
  if Signal.width data <> width then invalid_arg "FIFO data width differs from configuration";
  let spec = Reg_spec.create ~clock ~clear () in
  let bits = Config.log2 depth in
  let count = Always.Variable.reg spec ~width:(bits+1) in
  let rd = Always.Variable.reg spec ~width:bits in
  let wr = Always.Variable.reg spec ~width:bits in
  let valid = count.value <>:. 0 in
  let ready = count.value <>:. depth in
  let put = push &: ready &: ~:clear in
  let take = pop &: valid &: ~:clear in
  let head = memory depth
      ~write_port:{write_clock=clock; write_address=wr.value;
                   write_enable=put; write_data=data}
      ~read_address:rd.value in
  let open Always in
  compile [when_ put [wr <-- wr.value +:. 1];
           when_ take [rd <-- rd.value +:. 1];
           when_ (put ^: take)
             [count <-- mux2 put (count.value +:. 1) (count.value -:. 1)]];
  {ready; valid; data=head; level=count.value}
