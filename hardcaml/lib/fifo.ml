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

(* Timing knob fifo_write_staging. The queue's occupancy logic is that of
   [create_with]; only the storage write is one edge later. Every cycle the
   incoming word and the write pointer are registered (stage_data, stage_slot)
   together with the accepted push (stage_valid); a staged word is written to
   its slot at the next edge. Until then a read of that slot returns the staged
   word (bypass), so ready, valid, level and the head are those of
   [create_with] at every cycle. Only the storage words, which are not
   observable, change one edge later. The push acceptance and the incoming
   data now load one register each instead of every storage word. *)
let create_staged_gated ~gate ~async_reset ~storage ~clock ~clear ~width ~depth ~push ~pop ~data =
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
  let put = push &: ready &: ~:gate in
  let take = pop &: valid &: ~:gate in
  (* Staging registers take the storage's reset form (none for a memory). *)
  let word_spec = match storage with
    | Memory -> Reg_spec.create ~clock ()
    | Registers_async_reset reset -> Reg_spec.create ~clock ~reset ()
    | Registers_sync_clear reset -> Reg_spec.create ~clock ~clear:reset () in
  let stage_valid = reg spec put in
  let stage_slot = reg word_spec wr.value in
  let stage_data = reg word_spec data in
  let stored = match storage with
    | Memory ->
      memory depth
        ~write_port:{write_clock=clock; write_address=stage_slot;
                     write_enable=stage_valid; write_data=stage_data}
        ~read_address:rd.value
    | Registers_sync_clear _ | Registers_async_reset _ ->
      mux rd.value (List.init depth (fun j ->
        reg word_spec ~enable:(stage_valid &: (stage_slot ==:. j)) stage_data)) in
  let head = mux2 (stage_valid &: (stage_slot ==: rd.value)) stage_data stored in
  let open Always in
  compile [when_ put [wr <-- wr.value +:. 1];
           when_ take [rd <-- rd.value +:. 1];
           when_ (put ^: take)
             [count <-- mux2 put (count.value +:. 1) (count.value -:. 1)]];
  {ready; valid; data=head; level=count.value}

(* Timing knob fifo_write_free_slot. Same occupancy logic as [create_with];
   the storage word at the write pointer is written with the incoming data in
   every cycle in which the queue is not full, whether or not a word is
   accepted. That slot is outside the valid entries whenever the queue is not
   full, so only unobservable storage changes: ready, valid, level and the head
   of a non-empty queue are those of [create_with] at every cycle. The storage
   write enable then depends on registers only; the push acceptance reaches
   the pointers and the count, not the storage. *)
let create_free_slot_gated ~gate ~async_reset ~storage ~clock ~clear ~width ~depth ~push ~pop
    ~data =
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
  let put = push &: ready &: ~:gate in
  let take = pop &: valid &: ~:gate in
  let head = match storage with
    | Memory ->
      memory depth
        ~write_port:{write_clock=clock; write_address=wr.value;
                     write_enable=ready; write_data=data}
        ~read_address:rd.value
    | Registers_sync_clear reset | Registers_async_reset reset ->
      let word_spec = match storage with
        | Registers_async_reset _ -> Reg_spec.create ~clock ~reset ()
        | _ -> Reg_spec.create ~clock ~clear:reset () in
      mux rd.value (List.init depth (fun j ->
        reg word_spec ~enable:(ready &: (wr.value ==:. j)) data)) in
  let open Always in
  compile [when_ put [wr <-- wr.value +:. 1];
           when_ take [rd <-- rd.value +:. 1];
           when_ (put ^: take)
             [count <-- mux2 put (count.value +:. 1) (count.value -:. 1)]];
  {ready; valid; data=head; level=count.value}

let create_free_slot ~async_reset ~storage ~clock ~clear ~width ~depth ~push ~pop ~data =
  create_free_slot_gated ~gate:clear ~async_reset ~storage ~clock ~clear ~width ~depth ~push ~pop
    ~data

let create_staged ~async_reset ~storage ~clock ~clear ~width ~depth ~push ~pop ~data =
  create_staged_gated ~gate:clear ~async_reset ~storage ~clock ~clear ~width ~depth ~push ~pop ~data

let create ~clock ~clear ~width ~depth ~push ~pop ~data =
  create_with ~async_reset:None ~storage:Memory ~clock ~clear ~width ~depth ~push ~pop ~data
