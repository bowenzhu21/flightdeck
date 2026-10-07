#!/usr/bin/env python3
"""Deterministic synthetic mission. No real aircraft or user telemetry is included."""
import csv
import json
import math
import random
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HEADER = "arrival_ms,event_ms,vehicle,sequence,state,latitude,longitude,altitude_m,battery_pct".split(",")


def generate():
    rng = random.Random(2026)
    rows = []
    vehicles = ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot"]
    for vehicle_index, vehicle in enumerate(vehicles):
        for sequence in range(121):
            if vehicle == "delta" and 50 <= sequence <= 67:
                continue  # An intentional 4.75 second loss of telemetry.
            timestamp = sequence * 250
            state = "ground" if sequence < 3 or sequence >= 118 else "armed" if sequence < 6 else "landing" if sequence >= 96 else "airborne"
            angle = sequence / 12 + vehicle_index
            radius = min(100, max(0, sequence - 5) * 4) if sequence < 96 else max(0, 118 - sequence) * 100 / 22
            lat = 43.4723 + math.sin(angle) * radius / 111195
            lon = -80.5449 + math.cos(angle) * radius / (111195 * math.cos(math.radians(43.4723)))
            altitude = 60 if state == "airborne" else 20 if state == "landing" else 0
            battery = max(10, 100 - sequence * (0.8 if vehicle == "alpha" else 0.3))
            arrival = timestamp + rng.randint(15, 180)
            seq = sequence
            if vehicle == "bravo" and sequence == 40:
                state = "ground"
            if vehicle == "charlie" and sequence == 64:
                lat += 0.008  # Position spike violates speed and the 500m fence.
                altitude = 160
            if vehicle == "echo" and sequence == 79:
                seq = 1000  # Next packet exposes a non-monotonic source sequence.
            if vehicle == "foxtrot" and sequence == 90:
                timestamp -= 250  # Conflicting location at identical event time.
            if vehicle == "bravo" and sequence == 48:
                arrival += 2000  # Arrives after the reorder watermark.
            if vehicle == "echo" and sequence == 95:
                timestamp += 5000  # Future device-clock jump.
            row = [arrival, timestamp, vehicle, seq, state, f"{lat:.8f}", f"{lon:.8f}", altitude, f"{battery:.2f}"]
            rows.append(row)
            if sequence in (20, 80):
                duplicate = row.copy()
                duplicate[0] += 1
                rows.append(duplicate)
    rows.sort(key=lambda row: (row[0], row[2], row[3]))
    # The capture arrival clock itself regresses; keep this packet deliberately out of order.
    bad_clock = rows[300].copy()
    bad_clock[0] = 1
    bad_clock[3] = 9000
    rows.insert(301, bad_clock)
    output = ROOT / "examples/synthetic.csv"
    output.parent.mkdir(exist_ok=True)
    with output.open("w", newline="") as file:
        writer = csv.writer(file, lineterminator="\n")
        writer.writerow(HEADER)
        writer.writerows(rows)
    manifest = {
        "synthetic": True,
        "seed": 2026,
        "description": "Six simulated aircraft over 30 seconds. Injected faults are engineering test cases, not operational incidents.",
        "rows": len(rows),
        "vehicles": vehicles,
        "faults": ["duplicate sequences", "late telemetry", "arrival clock regression", "device clock ahead", "invalid state transitions", "telemetry blackout", "geofence breach", "velocity spike", "altitude breach", "low battery", "sequence regression", "same-time conflicting positions"],
    }
    (ROOT / "examples/manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"Generated {len(rows)} explicitly synthetic samples (seed=2026).")


if __name__ == "__main__":
    generate()
