#!/usr/bin/env python3
"""Analyse a capture.py JSONL file: frame structure, varying bytes, changes around markers."""
import argparse
import json
from collections import Counter, defaultdict

from capture import frame_ok


def load(path):
    frames, markers, meta = [], [], None
    with open(path) as f:
        for line in f:
            rec = json.loads(line)
            if "hex" in rec:
                frames.append((rec["t"], bytes.fromhex(rec["hex"])))
            elif "marker" in rec:
                markers.append((rec["t"], rec["marker"]))
            elif "meta" in rec:
                meta = rec["meta"]
    return meta, frames, markers


def signature(frame, prefix):
    """Frames sharing length and first bytes are assumed to be the same message type."""
    return (len(frame), frame[:prefix].hex())


def summary(frames, top):
    print(f"== {len(frames)} trames")
    lengths = Counter(len(f) for _, f in frames)
    print("Longueurs : " + ", ".join(f"{l}o x{c}" for l, c in sorted(lengths.items())))
    mb = sum(frame_ok(f) for _, f in frames)
    print(f"CRC valide (Modbus ou format AA) : {mb}/{len(frames)} ({mb / len(frames):.0%})")
    print(f"\n== {top} trames les plus fréquentes")
    for frame, c in Counter(f for _, f in frames).most_common(top):
        print(f"  x{c:<5} {frame.hex(' ')}")


def groups(frames, prefix):
    by_sig = defaultdict(list)
    for t, f in frames:
        by_sig[signature(f, prefix)].append((t, f))
    print(f"\n== Types de trame (longueur + {prefix} premiers octets) et octets variables")
    for (length, head), items in sorted(by_sig.items(), key=lambda kv: -len(kv[1])):
        varying = []
        for i in range(length):
            values = sorted({f[i] for _, f in items})
            if len(values) > 1:
                shown = " ".join(f"{v:02x}" for v in values[:12]) + (" …" if len(values) > 12 else "")
                varying.append(f"    [{i:3d}] {len(values):3d} valeurs : {shown}")
        print(f"  {length:3d}o début {head or '-'} : x{len(items)}, {len(varying)} octet(s) variable(s)")
        for line in varying[:20]:
            print(line)
    return by_sig


def around_markers(by_sig, markers, before_s, window):
    """Compare each frame type as it was before_s seconds before the marker with its last state in the window."""
    if not markers:
        return
    print(f"\n== Changements autour des marqueurs (état {before_s}s avant -> {window}s après)")
    for mt, text in markers:
        print(f"\n-- {text}")
        found = False
        for (length, head), items in by_sig.items():
            before = [f for t, f in items if t < mt - before_s]
            after = [f for t, f in items if mt - before_s <= t <= mt + window]
            if not before or not after:
                continue
            old, new = before[-1], after[-1]
            diffs = [i for i in range(length) if old[i] != new[i]]
            if diffs:
                found = True
                changes = ", ".join(f"[{i}] {old[i]:02x}->{new[i]:02x} ({old[i]}->{new[i]})" for i in diffs[:10])
                print(f"  {length}o début {head} : {changes}")
        if not found:
            print("  aucun changement détecté (allonger --window ?)")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("file")
    p.add_argument("--prefix", type=int, default=4, help="octets de début utilisés pour regrouper les trames")
    p.add_argument("--top", type=int, default=15)
    p.add_argument("--before", type=float, default=10, help="secondes avant le marqueur (le marqueur est souvent tapé après l'action)")
    p.add_argument("--window", type=float, default=20, help="secondes observées après chaque marqueur")
    args = p.parse_args()

    meta, frames, markers = load(args.file)
    if meta:
        print(f"Capture : {meta}")
    if not frames:
        print("Aucune trame.")
        return
    summary(frames, args.top)
    by_sig = groups(frames, args.prefix)
    around_markers(by_sig, markers, args.before, args.window)


if __name__ == "__main__":
    main()
