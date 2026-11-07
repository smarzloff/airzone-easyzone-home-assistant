#!/usr/bin/env python3
"""Passive RS-485 sniffer for the Airzone bus.

Read-only: the serial port is never written to, so the bus is not disturbed.

  scan     try common baud rates / parities and score them
  capture  record framed traffic to a JSONL file, with typed markers
"""
import argparse
import json
import sys
import threading
import time
from collections import Counter

import serial

BAUDS = [2400, 4800, 9600, 19200, 38400, 57600, 115200]
PARITIES = {"N": serial.PARITY_NONE, "E": serial.PARITY_EVEN, "O": serial.PARITY_ODD}


def crc16_modbus(data):
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def modbus_ok(frame):
    return len(frame) >= 4 and crc16_modbus(frame[:-2]) == int.from_bytes(frame[-2:], "little")


def frame_ok(frame):
    """Plain Modbus RTU, or the Airzone bus format: AA | addr | len | payload | CRC16-Modbus over all but AA."""
    return modbus_ok(frame) or (frame[:1] == b"\xaa" and modbus_ok(frame[1:]))


def default_gap(baud, parity):
    """Silence that ends a frame: 3.5 character times, never below 3 ms (USB latency)."""
    bits = 11 if parity != "N" else 10
    return max(3.5 * bits / baud, 0.003)


def open_port(port, baud, parity):
    return serial.Serial(port, baud, bytesize=8, parity=PARITIES[parity], stopbits=1, timeout=0)


def read_frames(ser, gap_s, duration=None, stop=None):
    """Yield (unix_time, bytes) frames, split on silence longer than gap_s."""
    buf = bytearray()
    t_start = last = None
    end = time.monotonic() + duration if duration else None
    while not (stop and stop.is_set()):
        now = time.monotonic()
        if end and now >= end:
            break
        chunk = ser.read(ser.in_waiting or 1)
        now = time.monotonic()
        if chunk:
            if not buf:
                t_start = time.time()
            buf += chunk
            last = now
        elif buf and now - last > gap_s:
            yield t_start, bytes(buf)
            buf.clear()
        else:
            time.sleep(0.0005)
    if buf:
        yield t_start, bytes(buf)


def cmd_scan(args):
    print(f"Scan de {args.port}, {args.seconds}s par combinaison (lecture seule)\n")
    print(f"{'baud':>7} {'par':>3} {'octets':>7} {'trames':>6} {'répét.':>6} {'CRC ok':>6} {'val.':>4} {'score':>5}  exemple")
    results = []
    for baud in BAUDS:
        for parity in args.parities:
            with open_port(args.port, baud, parity) as ser:
                ser.reset_input_buffer()
                frames = [f for _, f in read_frames(ser, default_gap(baud, parity), args.seconds)]
            total = sum(len(f) for f in frames)
            counts = Counter(frames)
            repeated = sum(c for c in counts.values() if c > 1) / len(frames) if frames else 0
            mb = sum(frame_ok(f) for f in frames) / len(frames) if frames else 0
            # Reading faster than the real rate yields only a handful of byte values (00 78 80 f8...)
            distinct = len({b for f in frames for b in f})
            score = (2 * mb + repeated) * min(distinct / 32, 1) if total else 0
            results.append((score, baud, parity, frames))
            sample = counts.most_common(1)[0][0][:16].hex(" ") if frames else ""
            print(f"{baud:>7} {parity:>3} {total:>7} {len(frames):>6} {repeated:>6.0%} {mb:>6.0%} {distinct:>4} {score:>5.2f}  {sample}")
    best = max(results, key=lambda r: r[0])
    if best[0] == 0:
        print("\nAucune trame exploitable. Vérifier le câblage A/B (essayer de les inverser).")
        return
    print(f"\nMeilleur candidat : {best[1]} bauds, parité {best[2]}. Exemples de trames :")
    for f in best[3][:10]:
        print("  " + f.hex(" "))


def cmd_capture(args):
    gap = args.gap / 1000 if args.gap else default_gap(args.baud, args.parity)
    lock = threading.Lock()
    stop = threading.Event()
    out = open(args.output, "a", buffering=1)

    def write(rec):
        with lock:
            out.write(json.dumps(rec) + "\n")

    def markers():
        for line in sys.stdin:
            text = line.strip()
            if text:
                write({"t": time.time(), "marker": text})
                print(f"--- MARQUEUR : {text}")

    write({"t": time.time(), "meta": {"port": args.port, "baud": args.baud, "parity": args.parity, "gap_ms": gap * 1000}})
    threading.Thread(target=markers, daemon=True).start()
    print(f"Capture {args.baud} bauds {args.parity}, coupure trame {gap * 1000:.1f} ms -> {args.output}")
    print("Tape une description puis Entrée pour poser un marqueur (ex : 'salon 21 -> 22'). Ctrl+C pour arrêter.\n")
    n = 0
    try:
        with open_port(args.port, args.baud, args.parity) as ser:
            for t, frame in read_frames(ser, gap, stop=stop):
                n += 1
                write({"t": t, "hex": frame.hex()})
                if not args.quiet:
                    flag = " [CRC ok]" if frame_ok(frame) else ""
                    print(f"{time.strftime('%H:%M:%S', time.localtime(t))}.{int(t % 1 * 1000):03d} "
                          f"({len(frame):3d}) {frame.hex(' ')}{flag}")
    except KeyboardInterrupt:
        pass
    finally:
        out.close()
        print(f"\n{n} trames enregistrées dans {args.output}")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--port", default="/dev/ttyUSB0")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("scan", help="tester les vitesses courantes")
    s.add_argument("--seconds", type=float, default=5)
    s.add_argument("--parities", nargs="+", default=["N", "E"], choices=PARITIES)
    s.set_defaults(func=cmd_scan)

    c = sub.add_parser("capture", help="enregistrer le trafic")
    c.add_argument("--baud", type=int, required=True)
    c.add_argument("--parity", default="N", choices=PARITIES)
    c.add_argument("--gap", type=float, help="silence de fin de trame en ms (défaut : 3,5 caractères, min 3 ms)")
    c.add_argument("-o", "--output", default=time.strftime("capture_%Y%m%d_%H%M%S.jsonl"))
    c.add_argument("-q", "--quiet", action="store_true", help="ne pas afficher chaque trame")
    c.set_defaults(func=cmd_capture)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
