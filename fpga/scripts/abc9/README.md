# ABC9 scripts

`flow2.abc`, `flow3.abc` and `flow3mfs.abc` are Yosys' built-in ABC9 scripts
`abc9.script.flow2`, `abc9.script.flow3` and `abc9.script.flow3mfs` (Yosys
0.67, ISC license), one line each, with the `{W}`, `{D}` and `{R}`
placeholders Yosys fills in. `fpga/scripts/build.sh` (`ABC9_SCRIPT=NAME`)
and `fpga/formal/run.sh` replace `{W}` with `-W $ABC9_W` (default 300) and
drop `{D}` and `{R}`, then pass the file to Yosys as `abc9.script`.
docs/fpga.md ("Implementation study") compares them.
