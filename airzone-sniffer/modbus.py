#!/usr/bin/env python3
"""Split a capture.py JSONL file of the Airzone domotic bus (Modbus RTU) into requests and responses.

At 19200 baud the USB adapter glues request and response together, so frames are
re-cut from the byte stream using Modbus lengths and CRCs instead of silences.

  python3 modbus.py domo1.jsonl              timeline of writes + register changes
  python3 modbus.py domo1.jsonl --reads      also list every read request
  python3 modbus.py domo1.jsonl --dump       last known value of every register read
"""
import argparse
import json

from capture import modbus_ok


def stream(path):
    """Yield (time, byte) for every captured byte, and ('marker', time, text) records."""
    for line in open(path):
        rec = json.loads(line)
        if "hex" in rec:
            for b in bytes.fromhex(rec["hex"]):
                yield rec["t"], b
        elif "marker" in rec:
            yield "marker", rec["t"], rec["marker"]


def candidates(buf):
    """Possible frame lengths at the start of buf, most specific first."""
    if len(buf) < 4:
        return []
    fn = buf[1] & 0x7F
    out = []
    if buf[1] & 0x80:
        out.append(5)  # exception
    if fn in (3, 4) and len(buf) >= 3:
        out += [8, 5 + buf[2]]
    elif fn in (5, 6):
        out.append(8)
    elif fn == 0x10:
        out.append(8)  # response
        if len(buf) >= 7:
            out.append(9 + buf[6])  # request
    elif fn == 0x16:
        out.append(10)
    elif fn == 0x17 and len(buf) >= 11:
        out.append(13 + buf[10])
    return out


def frames(path):
    """Yield ('frame', t, bytes), ('junk', t, bytes) and ('marker', t, text)."""
    buf, times, junk, junk_t = bytearray(), [], bytearray(), None

    def flush_junk():
        nonlocal junk, junk_t
        if junk:
            yield "junk", junk_t, bytes(junk)
            junk, junk_t = bytearray(), None

    def drain(final=False):
        nonlocal buf, times, junk, junk_t
        while len(buf) >= 4:
            for n in candidates(buf):
                if n <= len(buf) and modbus_ok(bytes(buf[:n])):
                    yield from flush_junk()
                    yield "frame", times[0], bytes(buf[:n])
                    del buf[:n], times[:n]
                    break
            else:
                longest = max(candidates(buf), default=0)
                if not final and longest > len(buf) and longest < 260:
                    return  # wait for more bytes
                if junk_t is None:
                    junk_t = times[0]
                junk.append(buf[0])
                del buf[0], times[0]
        if final:
            junk.extend(buf)
            yield from flush_junk()

    for item in stream(path):
        if item[0] == "marker":
            yield from drain()
            yield item
            continue
        t, b = item
        buf.append(b)
        times.append(t)
        if len(buf) > 300:
            yield from drain()
    yield from drain(final=True)


def regs(data):
    return [int.from_bytes(data[i:i + 2], "big") for i in range(0, len(data) - 1, 2)]


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("file")
    p.add_argument("--reads", action="store_true")
    p.add_argument("--dump", action="store_true")
    p.add_argument("--junk", action="store_true", help="afficher les octets non reconnus")
    args = p.parse_args()

    t0 = None
    pending = {}   # (slave, fn) -> (start, count) of the last read request
    table = {}     # (slave, register) -> value
    stats = {"frame": 0, "junk": 0}
    for kind, t, x in frames(args.file):
        t0 = t0 if t0 is not None else t
        ts = f"{t - t0:7.2f}"
        if kind == "marker":
            print(f"{ts}  ===== {x}")
            continue
        stats[kind] += 1
        if kind == "junk":
            if args.junk:
                print(f"{ts}  ?? {x.hex(' ')}")
            continue
        slave, fn, body = x[0], x[1], x[2:-2]
        if fn in (3, 4) and len(x) == 8:  # a response this short would carry an odd byte count
            start, count = int.from_bytes(body[0:2], "big"), int.from_bytes(body[2:4], "big")
            pending[(slave, fn)] = (start, count)
            if args.reads:
                print(f"{ts}  lecture  esclave {slave} fn {fn:02x} : {count} reg. depuis 0x{start:04x}")
        elif fn in (3, 4):
            start, count = pending.pop((slave, fn), (None, None))
            if start is None:
                continue
            for i, v in enumerate(regs(body[1:])):
                key = (slave, start + i)
                if key in table and table[key] != v and not args.dump:
                    print(f"{ts}  change   reg 0x{start + i:04x} : {table[key]} -> {v}  (0x{table[key]:04x} -> 0x{v:04x})")
                table[key] = v
        elif fn == 6:
            reg, val = int.from_bytes(body[0:2], "big"), int.from_bytes(body[2:4], "big")
            print(f"{ts}  ÉCRITURE fn 06 esclave {slave} : reg 0x{reg:04x} = {val} (0x{val:04x})")
        elif fn == 0x10 and len(x) > 8:
            reg = int.from_bytes(body[0:2], "big")
            print(f"{ts}  ÉCRITURE fn 10 esclave {slave} : depuis 0x{reg:04x} = {regs(body[5:])}")
        elif fn == 0x16:
            reg, andm, orm = (int.from_bytes(body[i:i + 2], "big") for i in (0, 2, 4))
            print(f"{ts}  ÉCRITURE fn 16 esclave {slave} : reg 0x{reg:04x} ET 0x{andm:04x} OU 0x{orm:04x}  ({x.hex(' ')})")
        elif fn & 0x80:
            print(f"{ts}  EXCEPTION esclave {slave} fn {fn & 0x7f:02x} code {body[0]}")

    print(f"\n{stats['frame']} trames Modbus reconnues, {stats['junk']} blocs non reconnus")
    if args.dump:
        for (slave, reg), v in sorted(table.items()):
            print(f"esclave {slave} reg 0x{reg:04x} = {v:5d} (0x{v:04x})")


if __name__ == "__main__":
    main()
