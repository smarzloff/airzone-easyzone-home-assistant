#!/usr/bin/env python3
"""Send ONE Modbus request to the Airzone central on the domotic bus, next to the webserver.

Without --send nothing is transmitted: the request is only built and printed.

The webserver polls the central in bursts separated by ~0.8 s of silence (domo1.jsonl).
The request is sent once, never retried, after at least --silence ms without traffic,
then the reply is read and checked.

  python3 domo.py lire 6                       read zone 6 (state, setpoint, temperature, name)
  python3 domo.py marche 6 / arret 6           zone on/off, as the app does (fn 0x16 mask write, bit 0x0004)
  python3 domo.py consigne 6 --valeur 16.5     zone setpoint (fn 06, register zone*256+3)
  python3 domo.py registres 0x2100 --valeur 33  raw read of any registers (read only)
"""
import argparse
import json
import sys
import time

from capture import crc16_modbus, modbus_ok, open_port
from modbus import candidates

SLAVE = 1
ON_BIT = 0x0004
DEMAND_BIT = 0x0080


def with_crc(body):
    return body + crc16_modbus(body).to_bytes(2, "little")


def u16(v):
    return v.to_bytes(2, "big")


def mask_write(reg, bit, on):
    """fn 0x16 mask write of one bit, exactly as the app does it (AND ~bit, OR bit or 0)."""
    return with_crc(bytes([SLAVE, 0x16]) + u16(reg) + u16(0xFFFF ^ bit) + u16(bit if on else 0))


def field_shift(mask):
    return (mask & -mask).bit_length() - 1


def field_write(reg, mask, value):
    """fn 0x16 mask write of a multi-bit field, as the app does (e.g. night mode hours, domo3.jsonl)."""
    raw = value << field_shift(mask)
    if raw & ~mask:
        raise ValueError(f"valeur {value} trop grande pour le champ 0x{mask:04x}")
    return with_crc(bytes([SLAVE, 0x16]) + u16(reg) + u16(0xFFFF ^ mask) + u16(raw))


def request(action, zone, value=None):
    base = zone * 256
    if action == "lire":
        return with_crc(bytes([SLAVE, 0x03]) + u16(base) + u16(32))
    if action == "registres":  # zone = start address, value = count
        return with_crc(bytes([SLAVE, 0x03]) + u16(zone) + u16(int(value)))
    if action in ("marche", "arret"):
        return with_crc(bytes([SLAVE, 0x16]) + u16(base) + u16(0xFFFF ^ ON_BIT) + u16(ON_BIT if action == "marche" else 0))
    if action == "consigne":
        raw = round(value * 10)
        if not 150 <= raw <= 300:
            sys.exit("Consigne hors plage 15–30 °C.")
        return with_crc(bytes([SLAVE, 0x06]) + u16(base + 3) + u16(raw))
    raise ValueError(action)


# Requests the official webserver sent (domo1.jsonl): the encoder must reproduce them exactly.
KNOWN = {
    ("marche", 2, None): "01160200fffb0004b7c2",
    ("arret", 2, None): "01160200fffb0000b601",
    ("consigne", 2, 20.5): "0106020300cd",
}

# Field writes the webserver sent (domo3.jsonl): reg, mask, value -> frame.
KNOWN_FIELDS = {
    (0x2121, 0x007C, 2): "01162121ff8300088c8a",    # night mode start 2 h
    (0x2121, 0x0F80, 10): "01162121f07f05004d38",   # night mode end 10 h
    (0x211F, 0x00FF, 50): "0116211fff000032d4b4",   # cyclic interval 50 min
    (0x211F, 0xFF00, 30): "0116211f00ff1e005ce5",   # cyclic minimum 30 min
    (0x2120, 0x00FF, 25): "01162120ff00001980ae",   # free-cooling min 25 °C
    (0x2120, 0xFF00, 7): "0116212000ff07004370",    # free-cooling differential 7
    (0x0400, 0x0F00, 9): "01160400f0ff0900f2e2",    # zone 4 mode cool, sent by a schedule (domo6)
    (0x0400, 0x0F00, 5): "01160400f0ff0500f7e2",    # zone 4 mode heat (domo6)
    (0x0200, 0x0F00, 0): "01160200f0ff0000f4d4",    # zone 2 mode stop, from the app (domo7)
    (0x0200, 0x0F00, 3): "01160200f0ff0300f424",    # zone 2 mode fan (domo7)
    (0x0200, 0x0F00, 6): "01160200f0ff0600f774",    # zone 2 mode dry (domo7)
}


def self_test():
    for args, prefix in KNOWN.items():
        got = request(*args).hex()
        if not got.startswith(prefix):
            sys.exit(f"Encodeur faux pour {args} : {got}. Rien n'est envoyé.")
    for args, frame in KNOWN_FIELDS.items():
        got = field_write(*args).hex()
        if got != frame:
            sys.exit(f"Encodeur faux pour {args} : {got}. Rien n'est envoyé.")


def split(data):
    out, buf = [], bytearray(data)
    while len(buf) >= 4:
        for n in candidates(buf):
            if n <= len(buf) and modbus_ok(bytes(buf[:n])):
                out.append(bytes(buf[:n]))
                del buf[:n]
                break
        else:
            del buf[0]
    return out


ZONE_MODES = {0: "stop", 1: "froid", 3: "ventilation", 5: "chaud", 6: "déshumidification"}  # bits 8-11, as in the AZ6 doc


def parse_zone(data):
    """Decode the 32 first registers of a zone block."""
    regs = [int.from_bytes(data[i:i + 2], "big") for i in range(0, len(data) - 1, 2)]
    if len(regs) < 32:
        raise ValueError(f"bloc de zone trop court ({len(regs)} registres au lieu de 32)")
    name = b"".join(r.to_bytes(2, "big") for r in regs[14:30]).split(b"\0")[0].decode("latin-1", "replace")
    return {
        "name": name,
        "on": bool(regs[0] & ON_BIT),
        "mode": ZONE_MODES.get((regs[0] >> 8) & 0x0F, f"inconnu {(regs[0] >> 8) & 0x0F}"),
        "setpoint": regs[3] / 10,
        "temperature": regs[8] / 10,
        "humidity": regs[31],
        "raw0": regs[0],
        # +9 bit 0x0080: set in the zone that was heating, clear once it reached its setpoint (confirmed 06/11/2026).
        "demand": bool(regs[9] & DEMAND_BIT),
        "raw9": regs[9],
    }


def describe_zone(data):
    z = parse_zone(data)
    return (f"zone « {z['name']} » : {'marche' if z['on'] else 'arrêt'} (état 0x{z['raw0']:04x}, mode {z['mode']}), "
            f"consigne {z['setpoint']:.1f} °C, température {z['temperature']:.1f} °C, humidité {z['humidity']} %, "
            f"{'demande' if z['demand'] else 'pas de demande'} (+9 = 0x{z['raw9']:04x})")


class BusError(RuntimeError):
    pass


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("action", choices=["lire", "marche", "arret", "consigne", "registres"])
    p.add_argument("zone", type=lambda v: int(v, 0), metavar="zone (1-8), ou adresse pour registres")
    p.add_argument("--valeur", type=float, help="consigne en °C (action consigne)")
    p.add_argument("--port", default="/dev/ttyUSB0")
    p.add_argument("--send", action="store_true", help="émettre réellement la requête (une seule fois)")
    p.add_argument("--silence", type=float, default=400, help="silence minimal avant émission, en ms")
    p.add_argument("-o", "--output", default="domo-envoi.jsonl")
    args = p.parse_args()
    if args.action in ("consigne", "registres") and args.valeur is None:
        p.error("--valeur est obligatoire (consigne en °C, ou nombre de registres)")
    if args.action == "registres" and not 1 <= args.valeur <= 125:
        p.error("nombre de registres : 1 à 125")
    if args.action != "registres" and not 1 <= args.zone <= 8:
        p.error("zone : 1 à 8")

    self_test()
    req = request(args.action, args.zone, args.valeur)
    print(f"Requête : {req.hex(' ')}")
    if not args.send:
        print("Mode simulation : rien n'a été envoyé (ajouter --send pour émettre).")
        return

    ser = open_port(args.port, 19200, "E")
    out = open(args.output, "w")
    log = lambda rec: out.write(json.dumps(rec) + "\n")
    log({"t": time.time(), "meta": {"port": args.port, "baud": 19200, "parity": "E", "envoi": req.hex()}})

    try:
        answer, reply = transact(ser, req, args.silence, log)
    except BusError as e:
        sys.exit(f"{e} : rien n'a été envoyé.")
    if answer is None:
        print(f"Aucune réponse de la centrale (reçu : {reply.hex(' ') or 'rien'}). Trafic dans {args.output}.")
    elif answer[1] & 0x80:
        print(f"La centrale refuse : exception code {answer[2]} ({answer.hex(' ')})")
    elif args.action == "lire":
        print("Réponse : " + describe_zone(answer[3:-2]))
    elif args.action == "registres":
        data = answer[3:-2]
        for i in range(0, len(data) - 1, 2):
            v = int.from_bytes(data[i:i + 2], "big")
            print(f"  0x{args.zone + i // 2:04x} = {v:5d}  (0x{v:04x})")
    else:
        print(f"Réponse de la centrale : {answer.hex(' ')}")
        time.sleep(1)
        try:
            check, _ = transact(ser, request("lire", args.zone), args.silence, log)
        except BusError:
            check = None
        print("Relecture : " + (describe_zone(check[3:-2]) if check and not check[1] & 0x80 else "pas de réponse"))
    out.close()


def transact(ser, req, silence_ms, log=lambda rec: None):
    """Send req once, in a silence of the webserver's polling; return (matching answer or None, raw reply).

    Raises BusError, without sending, if no traffic followed by a silence is seen within 10 s."""
    ser.reset_input_buffer()
    seen, last, deadline = False, time.monotonic(), time.monotonic() + 10
    while True:
        chunk = ser.read(ser.in_waiting or 1)
        now = time.monotonic()
        if chunk:
            seen, last = True, now
            log({"t": time.time(), "hex": chunk.hex()})
        elif seen and now - last >= silence_ms / 1000:
            break
        if now > deadline:
            raise BusError("Pas de trafic suivi d'un silence en 10 s (webserver absent ?)")
        time.sleep(0.001)

    ser.write(req)
    ser.flush()
    log({"t": time.time(), "marker": f"ENVOI {req.hex()}"})
    reply, end = bytearray(), time.monotonic() + 0.3
    while time.monotonic() < end:
        chunk = ser.read(ser.in_waiting or 1)
        if chunk:
            reply += chunk
            log({"t": time.time(), "hex": chunk.hex()})

    # The webserver may resume polling inside the listening window: its requests and the central's
    # answers to it look like ours. Only accept the first frame that answers OUR request.
    answer = next((f for f in split(reply) if answers(req, f)), None)
    return answer, bytes(reply)


def answers(req, f):
    """True if frame f is the central's reply to request req (not the webserver's traffic)."""
    if f[0] != SLAVE or f[1] & 0x7F != req[1]:
        return False
    if f[1] & 0x80:
        return len(f) == 5  # exception
    if req[1] == 0x03:
        count = int.from_bytes(req[4:6], "big")
        return len(f) == 5 + 2 * count and f[2] == 2 * count  # a read reply, of the size we asked
    return f[2:4] == req[2:4]  # writes (06, 16): the reply names the same register


if __name__ == "__main__":
    main()
