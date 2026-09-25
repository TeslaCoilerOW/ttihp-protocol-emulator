open Hardcaml
open Signal

type t = {ready : Signal.t; valid : Signal.t; data : Signal.t; level : Signal.t}

type storage = Memory | Registers_sync_clear of Signal.t | Registers_async_reset of Signal.t

(* Requests are accepted only against pre-edge occupancy. At full, a simultaneous
   pop creates space on the following cycle; there is no combinational bypass.
   Variant knobs (both absent in the design of record):
   - [async_reset]: count/pointers also take this asynchronous reset; [clear]
     (FLUSH) stays a synchronous clear.
   - [storage]: storage words as write-enabled registers that take the chip
     reset (synchronous clear or asynchronous reset) instead of a memory with
     no reset.  Storage contents are never observable while the queue is
     empty, so this does not change behaviour. *)
let create_with ~async_reset ~storage ~clock ~clear ~width ~depth ~push ~pop ~data =
  if Signal.width data <> width then invalid_arg "FIFO data width differs from configuration";
  let spec = match async_reset with
    | None -> Reg_spec.create ~clock ~clear ()
    | Some reset -> Reg_spec.create ~clock ~reset ~clear () in
  let bits = Config.log2 depth in
  let count = Always.Variable.reg spec ~width:(bits+1) in
  let rd = Always.Variable.reg spec ~width:bits in
  let wr = Always.Variable.reg spec ~width:bits in
  let valid = count.value <>:. 0 in
  let ready = count.value <>:. depth in
  let put = push &: ready &: ~:clear in
  let take = pop &: valid &: ~:clear in
  let head = match storage with
    | Memory ->
      memory depth
        ~write_port:{write_clock=clock; write_address=wr.value;
                     write_enable=put; write_data=data}
        ~read_address:rd.value
    | Registers_sync_clear reset | Registers_async_reset reset ->
      let word_spec = match storage with
        | Registers_async_reset _ -> Reg_spec.create ~clock ~reset ()
        | _ -> Reg_spec.create ~clock ~clear:reset () in
      mux rd.value (List.init depth (fun j ->
        reg word_spec ~enable:(put &: (wr.value ==:. j)) data)) in
  let open Always in
  compile [when_ put [wr <-- wr.value +:. 1];
           when_ take [rd <-- rd.value +:. 1];
           when_ (put ^: take)
             [count <-- mux2 put (count.value +:. 1) (count.value -:. 1)]];
  {ready; valid; data=head; level=count.value}

let create ~clock ~clear ~width ~depth ~push ~pop ~data =
  create_with ~async_reset:None ~storage:Memory ~clock ~clear ~width ~depth ~push ~pop ~data
