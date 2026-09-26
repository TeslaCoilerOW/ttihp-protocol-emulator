# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""Hardware demonstration scenarios (docs/demo.md), MicroPython compatible.

The scenarios are written once against a small chip interface and run
unchanged on:

* a Raspberry Pi Pico driving the FPGA host-clock build through the host
  library (``LibChip`` around ``pe_host.ProtocolEmulator`` with ``PicoPort``);
* a PC driving the FPGA UART-bridge build (``BridgeChip`` in ``pc_demo.py``,
  around ``fpga/host/pe_host.py``);
* the cocotb simulations in ``demo/sim`` (the same two adapters against the
  simulated FPGA top) and the reference model (``LibChip`` with ``ModelPort``).

Scenarios:

``isolation``   Timing-isolation measurement. Engine 3 runs the timing probe
                (``demo/firmware/timing-probe``) on pins 6 and 7. Mode
                ``idle``: nothing else happens. Mode ``loaded``: engines 0-2
                run a closed UART -> mover -> SPI -> mover -> UART traffic
                loop through two jumpers (pin 0 to pin 1, pin 3 to pin 4)
                and the host issues a stream of host-port operations (status
                reads, TX writes into the probe's own FIFO, empty RX reads,
                EVENTs, route updates, rejected commands). The probe's pin
                timing is then captured with a logic analyser and compared
                (``pe_capture.py``).
``four``        Four protocols at once against real parts: UART TX and RX to
                a USB-UART adapter, SPI JEDEC-ID reads from a SPI flash, I2C
                reads from a temperature sensor.
``bridge``      Host-free chain: a ticker engine requests an I2C read, the
                mover forwards the request to the I2C engine, the reading
                goes through a hex-formatting engine to the UART engine. The
                host only loads, routes and starts.

The host never STOPs, STARTs, CLEARs, BEGINs, COMMITs or OWNs engine 3 while
the probe runs, which are the restrictions under which formal/README.md
("Timing-isolation proof") proves that engine 3's pins cannot depend on
anything else.
"""

# Host commands (docs/isa.md).
SELECT, BEGIN, COMMIT, OWN, START, STOP, ROUTE, CLEAR = 0, 1, 2, 3, 4, 5, 6, 7
READ_SELECT, EVENT, FLUSH, TRIGGER = 8, 9, 10, 11
RS_STATUS, RS_TIMESTAMP, RS_LEVELS, RS_PC, RS_EVENT, RS_COUNT, RS_HELD_RX, RS_VERSION = (
    0, 1, 2, 3, 4, 5, 6, 7)
CLEAR_HOST_FAULT = 1 << 23
W_TX, W_RX = 2, 3

PROBE_ENGINE = 3
PROBE_MASK = 1 << PROBE_ENGINE
LOOP_MASK = 0b0111
ROUTE_COUNT = 0xFFFF
# Population of the closed traffic loop (six bytes circulate: fewer than the
# 8-word FIFOs, so the loop can never overflow a queue).
LOOP_BYTES = (0x55, 0xA3, 0x0F, 0xC6, 0x39, 0xE1)

ISOLATION_IMAGES = ("timing-probe", "uart-tx", "uart-rx", "spi-controller-mode0")
FOUR_IMAGES = ("uart-tx-115200", "uart-rx-poll-115200", "spi-xfer32", "i2c-read-100k")
BRIDGE_IMAGES = ("uart-tx-115200", "read-ticker", "hex-formatter", "i2c-read-100k")

VERSION = "demo-1"


class DemoError(Exception):
    pass


class Rng:
    """xorshift32: the same hammer sequence under CPython and MicroPython."""

    def __init__(self, seed):
        self.state = (seed * 2654435761 + 1) & 0xFFFFFFFF or 1

    def next(self):
        x = self.state
        x ^= (x << 13) & 0xFFFFFFFF
        x ^= x >> 17
        x ^= (x << 5) & 0xFFFFFFFF
        self.state = x
        return x

    def below(self, n):
        return self.next() % n


# ------------------------------------------------------------------ adapters
class LibChip:
    """Chip interface over host/pe_host.ProtocolEmulator (Pico, demo board,
    reference model, cocotb). The host supplies every clock edge, so time is
    counted in host clocks and ``wait`` idles the host port."""

    host_clocked = True

    def __init__(self, pe):
        self.pe = pe
        self.pe.strict = False

    def reset(self):
        self.pe.reset()

    def isa(self):
        return self.pe.isa_version()

    def load(self, engine, image):
        self.pe.load_program(engine, image.words, image.owned_pins, image.open_drain)

    def command(self, op, payload=0):
        return self.pe.command(op, payload, check=False)

    def select(self, engine):
        self.pe.select(engine)

    def read_select(self, index, engine):
        self.pe.select(engine)
        return self.pe.read_status(index)

    def levels(self, engine):
        word = self.read_select(RS_LEVELS, engine)
        return word & 0xFFFF, word >> 16

    def status(self, engine):
        return self.read_select(RS_STATUS, engine)

    def tx_write(self, engine, words):
        self.pe.tx_write(list(words), engine=engine)

    def tx_try(self, engine, word, max_wait):
        return self.pe.tx_try_write(word, max_wait, engine=engine)

    def rx_read(self, engine, count):
        return self.pe.rx_read_many(count, engine=engine)

    def rx_try(self, engine, max_wait):
        return self.pe.rx_try_read(max_wait, engine=engine)

    def abandon_write(self, engine, word, nibbles):
        """Start a TX word and abandon it after ``nibbles`` nibbles (window bounce)."""
        self.pe.select(engine)
        self.pe.try_write_word(W_TX, word, 2, nibbles)

    def now(self):
        return self.pe.cycles

    def wait(self, cycles):
        self.pe.idle(cycles)


class ImageRef:
    """The fields of a firmware image that the scenarios use."""

    def __init__(self, name, engine, words, owned_pins, open_drain):
        self.name = name
        self.engine = engine
        self.words = list(words)
        self.owned_pins = owned_pins
        self.open_drain = open_drain


def lib_images(directories, names):
    """Load and verify images with the host library (hashes, architecture).
    ``directories`` are searched in order for <name>.image.json."""
    from pe_host import FirmwareImage
    images = {}
    for name in names:
        last = None
        for directory in directories:
            path = directory.rstrip("/") + "/" + name + ".image.json"
            try:
                images[name] = FirmwareImage.load(path)
                break
            except Exception as exc:  # ImageError, OSError
                last = exc
        if name not in images:
            raise DemoError("image %s not found or invalid: %s" % (name, last))
    return images


class Jumpers:
    """Pad environment for simulation and model runs: the two loopback jumper
    wires of the isolation scenario (pin 0 -> pin 1, pin 3 -> pin 4), as a
    pe_host.peers-style peer (step(cycle, pads) -> (enable, value))."""

    mask = (1 << 1) | (1 << 4)

    def __init__(self, pairs=((0, 1), (3, 4))):
        self.pairs = pairs

    def step(self, cycle, pads):
        en = val = 0
        for src, dst in self.pairs:
            en |= 1 << dst
            val |= ((pads >> src) & 1) << dst
        return en, val


# ------------------------------------------------------------------ helpers
def _check_isa(chip, log):
    version = chip.isa()
    log("ISA version %d" % version)
    if version != 2:
        raise DemoError("device reports ISA version %d, expected 2" % version)


def _fault(status):
    return (status >> 8) & 0xFF if status & 8 else 0


def engine_summary(chip, engine):
    status = chip.status(engine)
    tx, rx = chip.levels(engine)
    return {"engine": engine, "running": status & 1, "fault": _fault(status),
            "tx_level": tx, "rx_level": rx, "pc": chip.read_select(RS_PC, engine),
            "completed": chip.read_select(RS_COUNT, engine)}


# ------------------------------------------------------------ isolation
def isolation_setup(chip, images, log=print):
    """Identical in both modes: reset, ISA check, load all four images."""
    chip.reset()
    _check_isa(chip, log)
    for engine, name in enumerate(("uart-tx", "uart-rx", "spi-controller-mode0", "timing-probe")):
        chip.load(engine, images[name])
    log("loaded %s" % ", ".join(ISOLATION_IMAGES))


class Hammer:
    """Host-port traffic while the probe runs (loaded mode). Every operation
    stays inside the formal model's environment: nothing starts, stops,
    clears, loads or re-owns the probe engine."""

    OPS = ("status", "levels", "readsel", "event", "probe_tx", "probe_rx", "route",
           "reject", "timestamp", "abandon")

    def __init__(self, chip, seed, log=print):
        self.chip = chip
        self.rng = Rng(seed)
        self.log = log
        self.counts = {}
        self.probe_tx_ok = 0
        self.restarts = 0
        self.faults = []

    def _count(self, name):
        self.counts[name] = self.counts.get(name, 0) + 1

    def step(self):
        chip, rng = self.chip, self.rng
        op = self.OPS[rng.below(len(self.OPS))]
        engine = rng.below(4)
        if op == "status":
            status = chip.status(engine)
            if engine != PROBE_ENGINE and status & 8:
                self._restart(engine, status)
        elif op == "levels":
            chip.levels(engine)
        elif op == "readsel":
            chip.read_select(rng.below(8), engine)
        elif op == "event":
            chip.command(EVENT, rng.below(15) + 1)
        elif op == "probe_tx":
            # The probe never PULLs: its TX FIFO fills to 8 words, and later
            # writes stall with write-valid held until the host gives up.
            if chip.tx_try(PROBE_ENGINE, rng.next(), 8):
                self.probe_tx_ok += 1
        elif op == "probe_rx":
            chip.rx_try(PROBE_ENGINE, 8)          # the probe never PUSHes: always empty
        elif op == "route":
            if rng.below(2):
                chip.command(ROUTE, 1 | 2 << 2 | 16 | ROUTE_COUNT << 5)
            else:
                chip.command(ROUTE, 2 | 0 << 2 | 16 | ROUTE_COUNT << 5)
        elif op == "reject":
            # TRIGGER on a running engine is rejected (sticky host fault);
            # clear the host fault right away (CLEAR with mask 0, bit 23).
            chip.select(PROBE_ENGINE)
            chip.command(TRIGGER, 6 | 1 << 5)
            chip.command(CLEAR, CLEAR_HOST_FAULT)
        elif op == "timestamp":
            chip.read_select(RS_TIMESTAMP, engine)
        elif op == "abandon":
            if hasattr(chip, "abandon_write"):
                chip.abandon_write(PROBE_ENGINE, rng.next(), 1 + rng.below(7))
            else:
                chip.levels(PROBE_ENGINE)
        self._count(op)

    def _restart(self, engine, status):
        self.faults.append((engine, _fault(status)))
        chip = self.chip
        chip.command(CLEAR, 1 << engine)
        chip.command(START, 1 << engine)
        self.restarts += 1


def isolation(chip, images, mode, cycles, seed=1, log=print, marker=None):
    """Run one measurement phase. Returns a result dict (JSON-serialisable).

    ``marker(level)``, when given, is called with 1 right after the probe's
    START and with 0 when the measured phase ends (a GPIO for the analyser)."""
    if mode not in ("idle", "loaded"):
        raise DemoError("mode must be idle or loaded")
    isolation_setup(chip, images, log)
    chip.command(START, PROBE_MASK)
    t0 = chip.now()
    if marker is not None:
        marker(1)
    log("probe started on engine %d (%s mode)" % (PROBE_ENGINE, mode))
    result = {"scenario": "isolation", "mode": mode, "seed": seed, "cycles_requested": cycles,
              "version": VERSION, "probe_start_cycle": t0}
    if mode == "idle":
        chip.wait(cycles)
        if marker is not None:
            marker(0)
    else:
        chip.tx_write(0, LOOP_BYTES)
        chip.command(ROUTE, 1 | 2 << 2 | 16 | ROUTE_COUNT << 5)
        chip.command(ROUTE, 2 | 0 << 2 | 16 | ROUTE_COUNT << 5)
        chip.command(START, LOOP_MASK)
        hammer = Hammer(chip, seed, log)
        operations = 0
        while chip.now() - t0 < cycles:
            hammer.step()
            operations += 1
        if marker is not None:
            marker(0)
        before = [engine_summary(chip, e) for e in range(4)]
        chip.command(STOP, LOOP_MASK)
        after = [engine_summary(chip, e) for e in range(4)]
        rx1 = chip.rx_read(1, after[1]["rx_level"])
        rx2 = chip.rx_read(2, after[2]["rx_level"])
        stray = [w & 0xFF for w in rx1 + rx2 if (w & 0xFF) not in LOOP_BYTES]
        result.update({"host_operations": operations, "operation_counts": hammer.counts,
                       "probe_tx_words_accepted": hammer.probe_tx_ok,
                       "engine_restarts": hammer.restarts, "engine_faults": hammer.faults,
                       "engines_before_stop": before, "engines_after_stop": after,
                       "loop_rx_words": [w & 0xFF for w in rx1 + rx2],
                       "stray_loop_bytes": stray})
        if stray:
            raise DemoError("traffic loop corrupted: bytes %r are not loop bytes" % stray)
        log("loaded: %d host operations, %d engine restarts, UART engine completed %d "
            "instructions" % (operations, hammer.restarts, before[0]["completed"]))
    result["probe_after"] = engine_summary(chip, PROBE_ENGINE)
    result["cycles_elapsed"] = chip.now() - t0
    if not result["probe_after"]["running"] or result["probe_after"]["fault"]:
        log("probe engine is not running: %r" % (result["probe_after"],))
        result["probe_running"] = False
    else:
        result["probe_running"] = True
    return result


def digest(result):
    """Interpreter-independent summary of an isolation result (for replays)."""
    counts = result.get("operation_counts", {})
    parts = ["%s=%d" % (k, counts[k]) for k in sorted(counts)]
    probe = result.get("probe_after", {})
    parts += ["ops=%d" % result.get("host_operations", 0),
              "loop=%s" % ",".join("%02x" % b for b in result.get("loop_rx_words", [])),
              "probe=%d/%d/%d" % (probe.get("running", -1), probe.get("fault", -1), probe.get("pc", -1)),
              "elapsed=%d" % result.get("cycles_elapsed", 0)]
    return ";".join(parts)


# ------------------------------------------------------------ four protocols
JEDEC_READ = 0x9F000000
TMP_ADDRESS = 0x48            # TMP102 / LM75 7-bit address (A0 low)


def four_setup(chip, images, i2c_address=TMP_ADDRESS, log=print):
    chip.reset()
    _check_isa(chip, log)
    chip.load(0, images["uart-tx-115200"])
    chip.load(1, images["uart-rx-poll-115200"])
    chip.load(2, images["spi-xfer32"])
    chip.load(3, images["i2c-read-100k"])
    # Echo: every byte engine 1 receives goes to engine 0's TX FIFO.
    chip.command(ROUTE, 1 | 0 << 2 | 16 | ROUTE_COUNT << 5)
    chip.command(START, 0b1111)
    log("four protocols running: UART TX/RX (echo route 1->0), SPI, I2C")


def four_round(chip, message=b"", i2c_address=TMP_ADDRESS):
    """One round: send ``message`` on UART, request one JEDEC ID and one I2C
    read. Returns the requests issued (the caller collects the answers)."""
    if message:
        chip.tx_write(0, list(message))
    chip.tx_write(2, [JEDEC_READ])
    chip.tx_write(3, [(i2c_address << 1) | 1])


def four_collect(chip, spi_count, i2c_count, timeout_cycles, log=print):
    """Wait for ``spi_count`` SPI words and ``i2c_count`` I2C bytes."""
    start = chip.now()
    spi, i2c = [], []
    while len(spi) < spi_count or len(i2c) < i2c_count:
        if chip.now() - start > timeout_cycles:
            break
        _, rx2 = chip.levels(2)
        if rx2:
            spi += chip.rx_read(2, rx2)
        _, rx3 = chip.levels(3)
        if rx3:
            i2c += chip.rx_read(3, rx3)
        if chip.host_clocked:
            chip.wait(256)
        else:
            chip.wait(20000)
    return spi, i2c


def jedec_id(word):
    """Manufacturer, memory type, capacity from a 32-bit SPI word (the first
    byte is shifted in while the command goes out)."""
    return (word >> 16) & 0xFF, (word >> 8) & 0xFF, word & 0xFF


def tmp_celsius(byte):
    """TMP102/LM75 temperature register MSB: integer degrees C, two's complement."""
    return byte - 256 if byte & 0x80 else byte


# ------------------------------------------------------------ host-free bridge
def bridge_setup(chip, images, log=print):
    """Ticker (E1) -> I2C read (E3) -> hex formatter (E2) -> UART TX (E0),
    three mover routes, no host service afterwards."""
    chip.reset()
    _check_isa(chip, log)
    chip.load(0, images["uart-tx-115200"])
    chip.load(1, images[_ticker_name(images)])
    chip.load(2, images["hex-formatter"])
    chip.load(3, images["i2c-read-100k"])
    chip.command(ROUTE, 1 | 3 << 2 | 16 | ROUTE_COUNT << 5)
    chip.command(ROUTE, 3 | 2 << 2 | 16 | ROUTE_COUNT << 5)
    chip.command(ROUTE, 2 | 0 << 2 | 16 | ROUTE_COUNT << 5)
    chip.command(START, 0b1111)
    log("host-free chain running: ticker E1 -> I2C E3 -> hex E2 -> UART E0")


def _ticker_name(images):
    for name in images:
        if name.startswith("read-ticker"):
            return name
    raise DemoError("no read-ticker image given")


def bridge_status(chip):
    return [engine_summary(chip, e) for e in range(4)]


def parse_hex_lines(data):
    """Readings from the formatter's output ("19\\r\\n" per reading)."""
    out = []
    for line in bytes(data).split(b"\r\n"):
        if len(line) == 2:
            try:
                out.append(int(line.decode(), 16))
            except ValueError:
                pass
    return out
