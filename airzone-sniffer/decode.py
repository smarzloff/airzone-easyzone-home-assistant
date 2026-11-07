#!/usr/bin/env python3
"""Readable timeline of a capture.py JSONL file, using what is known of the Airzone bus format.

Frame: AA | address | length | type | payload | CRC16-Modbus (little-endian, over all but AA).
Field meanings below are inferred from captures and still partly hypothetical.
"""
import argparse
import json

from capture import frame_ok

MODES = {0x01: "stop", 0x02: "ventilation", 0x04: "froid", 0x20: "chaud", 0x40: "déshumidification"}
PERIODIC = {0x01, 0x02, 0x13}


def setpoint(raw):
    return 10 + raw / 10


def le16(b, i):
    return int.from_bytes(b[i:i + 2], "little")


def describe(b):
    addr, ftype, p = b[1], b[3], b[4:-2]
    if ftype == 0x02 and len(p) >= 8:
        return (f"état thermostat {addr:02x} : {le16(p, 0) / 10:.1f} °C, {p[2]} % HR, "
                f"inconnus {le16(p, 3)} {le16(p, 5)} {p[7]}")
    if ftype == 0x01 and len(p) >= 4:
        return f"écho état {addr:02x} : {le16(p, 0) / 10:.1f} °C, {p[2]} % HR, statut {p[3]:02x}"
    if ftype == 0x03 and len(p) >= 5:
        mode = MODES.get(p[2], f"{p[2]:02x}")
        return (f"COMMANDE thermostat {addr:02x} : consigne {setpoint(p[1]):.1f} °C, mode {mode}, "
                f"{'marche' if p[3] else 'arrêt'} (drapeaux {p[0]:02x})")
    if ftype == 0x0D and len(p) >= 9:
        mode = MODES.get(p[5], f"{p[5]:02x}")
        return (f"état centrale -> {addr:02x} : consigne {setpoint(p[2]):.1f} °C "
                f"(plage {setpoint(p[4]):.1f}–{setpoint(p[3]):.1f}), mode {mode}, "
                f"{'marche' if p[7] & 1 else 'arrêt'} (drapeaux {p[0]:02x} {p[1]:02x})")
    if ftype in (0x12, 0x0E):
        return f"acquittement {addr:02x} (type {ftype:02x})"
    if ftype in (0x53, 0x54) and p:
        return f"demande machine type {ftype:02x} -> {addr:02x} : {p[0]:02x} ({'marche' if p[0] == 4 else 'arrêt' if p[0] == 5 else '?'})"
    if ftype == 0x14:
        return f"diffusion type 14 -> {addr:02x} : {p.hex(' ')}"
    return f"type {ftype:02x} adr {addr:02x} : {p.hex(' ')}"


def reassembled(path):
    """Yield records, joining an AA frame split by USB latency with the following chunk(s) when its length says so."""
    pending = None
    for line in open(path):
        rec = json.loads(line)
        if "hex" not in rec:
            yield rec
            continue
        if pending:
            pending["hex"] += rec["hex"]
        else:
            pending = rec
        b = bytes.fromhex(pending["hex"])
        if b[:1] == b"\xaa" and len(b) >= 3 and len(b) < b[2] + 3:
            continue
        yield pending
        pending = None
    if pending:
        yield pending


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("file")
    p.add_argument("--all", action="store_true", help="afficher aussi les trames périodiques inchangées et les diffusions 14")
    args = p.parse_args()

    t0, last = None, {}
    for rec in reassembled(args.file):
        if t0 is None:
            t0 = rec["t"]
        t = rec["t"] - t0
        if "marker" in rec:
            print(f"{t:7.2f}  ===== {rec['marker']}")
            continue
        if "hex" not in rec:
            continue
        b = bytes.fromhex(rec["hex"])
        if not (b[:1] == b"\xaa" and len(b) >= 6 and frame_ok(b)):
            if args.all:
                print(f"{t:7.2f}  ?? {b.hex(' ')}")
            continue
        key = (b[1], b[3])
        if not args.all:
            if b[3] in PERIODIC and last.get(key) == b:
                continue
            if b[3] == 0x14:
                continue
        last[key] = b
        print(f"{t:7.2f}  {describe(b)}")


if __name__ == "__main__":
    main()
