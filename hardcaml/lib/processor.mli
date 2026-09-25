(** Complete Tiny Tapeout top generated directly from production Hardcaml.
    [options] (default [Variant_options.default]) applies the variant knobs to
    the register-store top as well. *)
val create : ?debug:bool -> ?options:Variant_options.t -> Config.t -> Hardcaml.Circuit.t

(** Fixed physical SRAM refinement, preserving the external ISA2 interface
    unless the refinement's options change the ISA (then READ_SELECT 7 is 3). *)
val create_refinement : ?debug:bool -> Refinement_config.t -> Hardcaml.Circuit.t

(** Test-only synchronous memory model; no production CLI selects this model. *)
val create_refinement_model : ?debug:bool -> Refinement_config.t -> Hardcaml.Circuit.t
