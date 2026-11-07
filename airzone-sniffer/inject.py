#!/usr/bin/env python3
"""Send ONE command frame on the Airzone AZ bus, impersonating the wired thermostat.

Without --send nothing is transmitted: the frame is only built and printed.

Bus timing seen in captures: the thermostat speaks about every 75 ms and the central
answers ~10 ms later; the next thermostat frame comes ~60 ms after that answer. The frame
is therefore sent just after a central answer (type 01 or 12) to the thermostat, in that silence.
The frame is sent once, never retried. The traffic that follows is saved to a JSONL file
readable by decode.py, then the script reports whether the central acknowledged it.
"""
import argparse
import json
import sys
import time

from capture import crc16_modbus, frame_ok, open_port
from decode import MODES, describe

THERMOSTAT = 0x32
FLAG_ONOFF, FLAG_SETPOINT, FLAG_MODE = 0x01, 0x21, 0x30
MODE_BY_NAME = {v: k for k, v in MODES.items()}

# Frames sent by the real wired thermostat (session4), used to check the encoder.
KNOWN = {
    "aa3208030164200101a85f": (FLAG_ONOFF, 20.0, "chaud", True),
    "aa32080321692001012b34": (FLAG_SETPOINT, 20.5, "chaud", True),
}


def build(flags, setpoint, mode, on, addr=THERMOSTAT):
    raw = round((setpoint - 10) * 10)
    if not 0x50 <= raw <= 0xC8:
        raise ValueError("consigne hors plage 18–30 °C")
    body = bytes([addr, 0x08, 0x03, flags, raw, MODE_BY_NAME[mode], 1 if on else 0, 0x01])
    return b"\xaa" + body + crc16_modbus(body).to_bytes(2, "little")


def self_test():
    for hexframe, args in KNOWN.items():
        got = build(*args).hex()
        if got != hexframe:
            sys.exit(f"Encodeur faux : {got} au lieu de {hexframe}. Rien n'est envoyé.")


class FrameReader:
    """Cut the byte stream into AA frames using their length byte."""

    def __init__(self, ser):
        self.ser, self.buf = ser, bytearray()

    def frames(self):
        self.buf += self.ser.read(self.ser.in_waiting or 1)
        while True:
            start = self.buf.find(b"\xaa")
            if start < 0:
                self.buf.clear()
                return
            del self.buf[:start]
            if len(self.buf) < 3 or len(self.buf) < self.buf[2] + 3:
                return
            frame = bytes(self.buf[:self.buf[2] + 3])
            if frame_ok(frame):
                del self.buf[:len(frame)]
                yield frame
            else:
                del self.buf[:1]


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--port", default="/dev/ttyUSB0")
    p.add_argument("action", choices=["marche", "arret", "consigne", "mode"])
    p.add_argument("--consigne", type=float, required=True, help="consigne actuelle (ou nouvelle pour l'action consigne), en °C")
    p.add_argument("--mode", choices=sorted(MODE_BY_NAME), required=True, help="mode actuel (ou nouveau pour l'action mode)")
    p.add_argument("--etat", choices=["marche", "arret"], help="état actuel de la zone (actions consigne et mode) ; défaut : marche")
    p.add_argument("--send", action="store_true", help="émettre réellement la trame (une seule fois)")
    p.add_argument("--listen", type=float, default=5, help="secondes d'écoute après l'envoi")
    p.add_argument("-o", "--output", default="inject.jsonl")
    args = p.parse_args()

    self_test()
    flags = {"marche": FLAG_ONOFF, "arret": FLAG_ONOFF, "consigne": FLAG_SETPOINT, "mode": FLAG_MODE}[args.action]
    on = args.action == "marche" if args.action in ("marche", "arret") else (args.etat or "marche") == "marche"
    frame = build(flags, args.consigne, args.mode, on)
    print(f"Trame : {frame.hex(' ')}")
    print(f"  = {describe(frame)}")
    if not args.send:
        print("Mode simulation : rien n'a été envoyé (ajouter --send pour émettre).")
        return

    ser = open_port(args.port, 9600, "N")
    reader = FrameReader(ser)
    out = open(args.output, "w")
    log = lambda rec: out.write(json.dumps(rec) + "\n")
    log({"t": time.time(), "meta": {"port": args.port, "baud": 9600, "parity": "N", "inject": frame.hex()}})

    deadline = time.monotonic() + 5
    sent_at = None
    while sent_at is None:
        if time.monotonic() > deadline:
            sys.exit("Aucune réponse de la centrale au thermostat en 5 s : rien n'a été envoyé.")
        for f in reader.frames():
            log({"t": time.time(), "hex": f.hex()})
            if f[1] == THERMOSTAT and f[3] in (0x01, 0x12):
                time.sleep(0.003)
                ser.write(frame)
                ser.flush()
                sent_at = time.time()
                log({"t": sent_at, "marker": f"INJECTION {frame.hex()}"})
                break

    acked = state = False
    end = time.monotonic() + args.listen
    while time.monotonic() < end:
        for f in reader.frames():
            log({"t": time.time(), "hex": f.hex()})
            if f == frame:
                continue  # our own frame read back
            if f[1] == THERMOSTAT and f[3] == 0x12 and not state and time.time() - sent_at < 0.2:
                acked = True
            if f[1] == THERMOSTAT and f[3] == 0x0D:
                state = state or describe(f)
    out.close()
    print(f"Envoyée. Acquittement (type 12) : {'oui' if acked else 'non'}")
    print(f"État renvoyé par la centrale : {state or 'aucun'}")
    print(f"Trafic enregistré dans {args.output} (python3 decode.py {args.output})")


if __name__ == "__main__":
    main()
