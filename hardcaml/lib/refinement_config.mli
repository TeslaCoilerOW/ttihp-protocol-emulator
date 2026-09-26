(** Closed, separate implementation refinement.  Architecture.v1 is unchanged.
    The optional ["options"] object selects RTL variant knobs
    ({!Variant_options}); when it is absent the options are
    [Variant_options.default], the design of record. The same object also
    carries the {!Timing_options} knobs, which restructure logic without
    changing behaviour (default: none). *)
type t = private {
  architecture : Config.t; options : Variant_options.t; timing : Timing_options.t }
val implementation : string
val create : ?options:Variant_options.t -> ?timing:Timing_options.t -> Config.t -> t
val of_json : Yojson.Safe.t -> t
val load : string -> t
