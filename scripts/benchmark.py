#!/usr/bin/env python3
"""Five independent processor runs per window. No I/O or real-hardware flight claim."""
import datetime
import json
import pathlib
import platform
import statistics
import subprocess
import sys
import tempfile

root = pathlib.Path(__file__).resolve().parents[1]
exe = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else root / "build/flightdeck").resolve()
results = []
with tempfile.TemporaryDirectory(prefix="flightdeck-benchmark-") as directory:
    output = pathlib.Path(directory) / "run.json"
    for lateness in (0, 250):
        # Explicit warmup, excluded from reported samples.
        subprocess.run([str(exe), "benchmark", "--events", "50000", "--lateness-ms", str(lateness), "--report", str(output)], check=True, stdout=subprocess.DEVNULL)
        samples = []
        for _ in range(5):
            subprocess.run([str(exe), "benchmark", "--events", "200000", "--lateness-ms", str(lateness), "--report", str(output)], check=True, stdout=subprocess.DEVNULL)
            samples.append(json.loads(output.read_text()))
        rates = [s["events_per_second"] for s in samples]
        results.append({"lateness_ms": lateness, "median_events_per_second": statistics.median(rates), "min_events_per_second": min(rates), "max_events_per_second": max(rates), "samples": samples})
report = {
    "measured_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    "environment": {"os": platform.system(), "os_release": platform.release(), "architecture": platform.machine(), "compiler": results[0]["samples"][0]["compiler"], "build_flags": results[0]["samples"][0]["build_flags"]},
    "method": "Five independent 200,000-event runs per reorder setting after 50,000-event warmup. Single process, sixteen synthetic vehicles. Timer includes event construction and processor ingestion/finish. CSV parsing, binary I/O and anomaly JSON serialization excluded. Observational local measurement, no flight-hardware validation.",
    "results": results,
}
(root / "docs/benchmark.json").write_text(json.dumps(report, indent=2) + "\n")
for r in results:
    print(f"lateness={r['lateness_ms']}ms median={r['median_events_per_second']:,.0f} events/s (5 runs)")
