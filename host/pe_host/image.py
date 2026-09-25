# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""Firmware image (firmware/*.image.json) loading and verification.

Checks, following docs/firmware.md ("Source and image contracts"):

* schema_version is protocol-emulator.firmware-image.v1;
* words are 32-bit integers and their SHA-256 over consecutive little-endian
  words equals bytecode_sha256;
* when the sibling <name>.source.json is available, the SHA-256 of its exact
  bytes equals source_sha256;
* owned_pins/open_drain are 8-bit masks with open_drain inside owned_pins, the
  engine exists and the image fits the program store;
* architecture binding: the image's architecture object equals the device's
  (DESIGN_ARCHITECTURE unless the caller supplies another one);
* the device's ISA version (READ_SELECT 7) is at least the image's isa_version.

MicroPython compatible (json, hashlib.sha256, binascii, struct; no os.path).
"""

import binascii
import json
import struct

from .errors import ImageError
from .protocol import ARCHITECTURE_KEYS, DESIGN_ARCHITECTURE

try:
    from hashlib import sha256 as _native_sha256
except ImportError:  # a MicroPython build without hashlib.sha256
    _native_sha256 = None

IMAGE_SCHEMA = "protocol-emulator.firmware-image.v1"
SCENARIO_SCHEMA = "protocol-emulator.firmware-scenario.v1"
ARCHITECTURE_SCHEMA = "protocol-emulator.architecture.v1"

_K = (0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4,
      0xab1c5ed5, 0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe,
      0x9bdc06a7, 0xc19bf174, 0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f,
      0x4a7484aa, 0x5cb0a9dc, 0x76f988da, 0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7,
      0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967, 0x27b70a85, 0x2e1b2138, 0x4d2c6dfc,
      0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85, 0xa2bfe8a1, 0xa81a664b,
      0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070, 0x19a4c116,
      0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
      0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7,
      0xc67178f2)


def _rotr(x, n):
    return ((x >> n) | (x << (32 - n))) & 0xFFFFFFFF


def sha256_py(data):
    """Pure-Python SHA-256 (FIPS 180-4), used when hashlib.sha256 is missing."""
    h = [0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c,
         0x1f83d9ab, 0x5be0cd19]
    length = len(data)
    data = bytes(data) + b"\x80" + bytes((55 - length) % 64) + struct.pack(">Q", length * 8)
    for block in range(0, len(data), 64):
        w = list(struct.unpack(">16I", data[block:block + 64]))
        for i in range(16, 64):
            s0 = _rotr(w[i - 15], 7) ^ _rotr(w[i - 15], 18) ^ (w[i - 15] >> 3)
            s1 = _rotr(w[i - 2], 17) ^ _rotr(w[i - 2], 19) ^ (w[i - 2] >> 10)
            w.append((w[i - 16] + s0 + w[i - 7] + s1) & 0xFFFFFFFF)
        a, b, c, d, e, f, g, hh = h
        for i in range(64):
            t1 = (hh + (_rotr(e, 6) ^ _rotr(e, 11) ^ _rotr(e, 25))
                  + ((e & f) ^ (~e & g)) + _K[i] + w[i]) & 0xFFFFFFFF
            t2 = ((_rotr(a, 2) ^ _rotr(a, 13) ^ _rotr(a, 22))
                  + ((a & b) ^ (a & c) ^ (b & c))) & 0xFFFFFFFF
            hh, g, f, e, d, c, b, a = g, f, e, (d + t1) & 0xFFFFFFFF, c, b, a, (t1 + t2) & 0xFFFFFFFF
        h = [(x + y) & 0xFFFFFFFF for x, y in zip(h, (a, b, c, d, e, f, g, hh))]
    return struct.pack(">8I", *h)


def sha256_hex(data):
    if _native_sha256 is not None:
        digest = _native_sha256(data).digest()
    else:
        digest = sha256_py(data)
    return binascii.hexlify(digest).decode()


def bytecode_sha256(words):
    """SHA-256 of consecutive 32-bit little-endian words (bytecode_sha256)."""
    payload = bytearray()
    for word in words:
        payload.extend(struct.pack("<I", word))
    return sha256_hex(payload)


def read_bytes(path):
    with open(path, "rb") as handle:
        return handle.read()


def read_json(path):
    with open(path, "r") as handle:
        return json.load(handle)


def join(directory, name):
    if not directory:
        return name
    if directory.endswith("/"):
        return directory + name
    return directory + "/" + name


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def check_architecture(architecture):
    if not isinstance(architecture, dict):
        raise ImageError("architecture must be an object")
    for key in ARCHITECTURE_KEYS:
        if key not in architecture:
            raise ImageError("architecture lacks " + key)
    if architecture["schema_version"] != ARCHITECTURE_SCHEMA:
        raise ImageError("unknown architecture schema " + str(architecture["schema_version"]))
    for key in ("engine_count", "data_width", "program_words", "fifo_words"):
        if not _is_int(architecture[key]) or architecture[key] <= 0:
            raise ImageError("architecture " + key + " must be a positive integer")


def architecture_differences(image_arch, device_arch, ignore=()):
    """Keys whose values differ, as 'key: image!=device' strings."""
    out = []
    for key in ARCHITECTURE_KEYS:
        if key in ignore:
            continue
        a, b = image_arch.get(key), device_arch.get(key)
        if a != b:
            out.append("%s: image %r != device %r" % (key, a, b))
    return out


class FirmwareImage:
    """A verified firmware image. Construct with FirmwareImage.load(path)."""

    def __init__(self, data, source_bytes=None, path=None, architecture=None):
        self.path = path
        if not isinstance(data, dict):
            raise ImageError("image JSON must be an object")
        if data.get("schema_version") != IMAGE_SCHEMA:
            raise ImageError("not a firmware image: schema_version %r" % data.get("schema_version"))
        for key in ("isa_version", "name", "architecture", "engine", "owned_pins", "open_drain",
                    "source_sha256", "bytecode_sha256", "words"):
            if key not in data:
                raise ImageError("image lacks " + key)
        self.name = data["name"]
        self.isa_version = data["isa_version"]
        self.architecture = data["architecture"]
        self.engine = data["engine"]
        self.owned_pins = data["owned_pins"]
        self.open_drain = data["open_drain"]
        self.clock_hz = data.get("clock_hz")
        self.source_sha256 = data["source_sha256"]
        self.bytecode_sha256 = data["bytecode_sha256"]
        self.words = data["words"]
        self.labels = data.get("labels", {})
        self.notes = data.get("notes", [])
        self.source_verified = False
        self._verify(source_bytes)
        if architecture is not None:
            self.check_binding(architecture)

    @classmethod
    def load(cls, path, source=True, architecture=None):
        """Load and verify <path>.

        source=True verifies <name>.source.json when it exists next to the
        image; source="required" makes its absence an error; source=False skips.
        architecture: device architecture to bind to (None = check later).
        """
        try:
            data = read_json(path)
        except (OSError, ValueError) as exc:
            raise ImageError("cannot read image %s: %s" % (path, exc))
        source_bytes = None
        if source and path.endswith(".image.json"):
            source_path = path[:-len(".image.json")] + ".source.json"
            try:
                source_bytes = read_bytes(source_path)
            except OSError:
                if source == "required":
                    raise ImageError("source file %s not found" % source_path)
        return cls(data, source_bytes, path, architecture)

    def _verify(self, source_bytes):
        if not _is_int(self.isa_version) or self.isa_version < 1:
            raise ImageError("isa_version must be a positive integer")
        check_architecture(self.architecture)
        arch = self.architecture
        if not _is_int(self.engine) or not 0 <= self.engine < arch["engine_count"]:
            raise ImageError("engine %r outside the architecture" % (self.engine,))
        for key, value in (("owned_pins", self.owned_pins), ("open_drain", self.open_drain)):
            if not _is_int(value) or not 0 <= value <= 255:
                raise ImageError(key + " must be an 8-bit mask")
        if self.open_drain & ~self.owned_pins:
            raise ImageError("open_drain pins outside owned_pins")
        words = self.words
        if not isinstance(words, list) or not words:
            raise ImageError("words must be a non-empty list")
        if len(words) > arch["program_words"]:
            raise ImageError("image of %d words exceeds program capacity %d"
                             % (len(words), arch["program_words"]))
        for word in words:
            if not _is_int(word) or not 0 <= word <= 0xFFFFFFFF:
                raise ImageError("image word outside 32 bits")
        digest = bytecode_sha256(words)
        if digest != self.bytecode_sha256:
            raise ImageError("%s: bytecode SHA-256 mismatch (%s != %s)"
                             % (self.name, digest, self.bytecode_sha256))
        if source_bytes is not None:
            digest = sha256_hex(source_bytes)
            if digest != self.source_sha256:
                raise ImageError("%s: source SHA-256 mismatch (%s != %s)"
                                 % (self.name, digest, self.source_sha256))
            self.source_verified = True

    def check_binding(self, architecture=None, ignore=()):
        """Raise ImageError unless the image was assembled for this architecture."""
        device = DESIGN_ARCHITECTURE if architecture is None else architecture
        diffs = architecture_differences(self.architecture, device, ignore)
        if diffs:
            raise ImageError("%s is bound to another architecture: %s"
                             % (self.name, "; ".join(diffs)))

    def check_isa(self, device_version):
        if device_version < self.isa_version:
            raise ImageError("%s needs ISA %d, the device reports %d"
                             % (self.name, self.isa_version, device_version))

    def __len__(self):
        return len(self.words)

    def __repr__(self):
        return "<FirmwareImage %s engine %d, %d words, pins 0x%02x od 0x%02x%s>" % (
            self.name, self.engine, len(self.words), self.owned_pins, self.open_drain,
            ", source verified" if self.source_verified else "")


def load_scenario(path, architecture=None):
    """Load firmware/flagship-scenario.json-style files and check the binding."""
    try:
        data = read_json(path)
    except (OSError, ValueError) as exc:
        raise ImageError("cannot read scenario %s: %s" % (path, exc))
    if data.get("schema_version") != SCENARIO_SCHEMA:
        raise ImageError("not a firmware scenario: %r" % data.get("schema_version"))
    check_architecture(data["architecture"])
    diffs = architecture_differences(data["architecture"],
                                     DESIGN_ARCHITECTURE if architecture is None else architecture)
    if diffs:
        raise ImageError("scenario bound to another architecture: " + "; ".join(diffs))
    return data
