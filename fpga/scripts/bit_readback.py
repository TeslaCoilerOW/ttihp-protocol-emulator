#!/usr/bin/env python3
# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""Bit-level readback check of a generated 7-series bitstream.

Decodes the .bit with prjxray's bitread (every set configuration bit, as
frame address / word / bit) and compares it with the frames fasm2frames
produced from nextpnr's FASM. PASS means the bitstream carries exactly the
configuration bits the design's FASM asked for: no bit lost or added by
xc7frames2bit. Standard library only; bitread comes from the openXC7
package.

    python3 bit_readback.py BITREAD PART_YAML design.frames design.bit > readback.json
"""

import json
import os
import re
import subprocess
import sys
import tempfile


def frame_bits(path):
    bits = set()
    with open(path) as f:
        for line in f:
            if not line.startswith("0x"):
                continue
            addr, words = line.split()
            frame = int(addr, 16)
            for w, word in enumerate(words.split(",")):
                value = int(word, 16)
                b = 0
                while value:
                    if value & 1:
                        bits.add((frame, w, b))
                    value >>= 1
                    b += 1
    return bits


def bitstream_bits(bitread, part_yaml, bit_file):
    pattern = re.compile(r"bit_([0-9a-f]{8})_([0-9]{3})_([0-9]{2})")
    bits = set()
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "design.bits")
        subprocess.check_call([bitread, "--part_file", part_yaml, "-o", out, "-z", "-y", bit_file],
                              stdout=subprocess.DEVNULL)
        with open(out) as f:
            for line in f:
                m = pattern.match(line.strip())
                if m:
                    bits.add((int(m.group(1), 16), int(m.group(2)), int(m.group(3))))
    return bits


def main():
    bitread, part_yaml, frames, bit_file = sys.argv[1:5]
    want = frame_bits(frames)
    got = bitstream_bits(bitread, part_yaml, bit_file)
    missing, extra = sorted(want - got), sorted(got - want)
    fmt = lambda t: "frame %08x word %d bit %d" % t
    result = {
        "frames_bits_set": len(want),
        "bitstream_bits_set": len(got),
        "frames": len({t[0] for t in want}),
        "missing_in_bitstream": len(missing),
        "extra_in_bitstream": len(extra),
        "sample_missing": [fmt(t) for t in missing[:10]],
        "sample_extra": [fmt(t) for t in extra[:10]],
        "pass": not missing and not extra and len(want) > 0,
    }
    print(json.dumps(result, indent=2))
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
