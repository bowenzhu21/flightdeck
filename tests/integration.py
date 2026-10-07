#!/usr/bin/env python3
"""End-to-end CLI contracts, including atomic report publication on corrupt input."""
import json
import pathlib
import subprocess
import sys
import tempfile

exe = str(pathlib.Path(sys.argv[1]).resolve())
header = "arrival_ms,event_ms,vehicle,sequence,state,latitude,longitude,altitude_m,battery_pct\n"
valid = "100,0,alpha,0,ground,43.4723,-80.5449,0,100\n200,100,alpha,1,armed,43.4723,-80.5449,0,99\n300,200,alpha,2,airborne,43.4723,-80.5449,40,98\n"
checks = 0


def run(*args, ok=True):
    global checks
    result = subprocess.run([exe, *map(str, args)], capture_output=True, text=True)
    assert (result.returncode == 0) == ok, (args, result.stdout, result.stderr)
    checks += 1
    return result


with tempfile.TemporaryDirectory(prefix="flightdeck-integration-") as directory:
    d = pathlib.Path(directory)
    source, log, report, replay = [d / name for name in ("input.csv", "flight.fdlog", "report.json", "replay.json")]
    source.write_text(header + valid)
    run("record", source, log, "--report", report, "--lateness-ms", 987, "--radius-m", 600)
    run("replay", log, "--report", replay)
    a, b = json.loads(report.read_text()), json.loads(replay.read_text())
    assert a["config"]["lateness_ms"] == 987
    for key in ("anomalies", "metrics", "config", "log"):
        assert a[key] == b[key], key
    run("replay", log, "--report", replay, "--lateness-ms", 1, ok=False)
    # A malformed CSV never publishes a successful report. The partial log remains recoverable.
    for bad_row in ("1,0,a,0,ground,nan,0,0,100\n", "1,0,a,0,ground,inf,0,0,100\n", "x" * 100000 + "\n", "1,0,a,0,ground,0,0,0,100,extra\n", "1,0,a,0,ground,0,0,0,100\x00\n"):
        source.write_text(header + valid + bad_row)
        report.write_text("unchanged")
        run("record", source, log, "--report", report, ok=False)
        assert report.read_text() == "unchanged"
        assert not (d / "report.json.tmp").exists()
        run("replay", log, "--report", replay, ok=False)
        run("replay", log, "--report", replay, "--salvage")
        recovered = json.loads(replay.read_text())
        assert recovered["log"]["events"] == 3
        assert recovered["log"]["recovered_prefix"] is True
    source.write_text(header + valid)
    run("record", source, log, "--report", report)
    sealed = log.read_bytes()
    log.write_bytes(sealed[:-3])
    replay.write_text("previous successful report")
    run("replay", log, "--report", replay, ok=False)
    assert replay.read_text() == "previous successful report"
    run("replay", log, "--report", replay, "--salvage")
    assert json.loads(replay.read_text())["log"]["events"] == 3
    log.write_text("keep me")
    run("record", source, log, "--report", report, "--max-buffer", 0, ok=False)
    assert log.read_text() == "keep me"
    run("record", source, source, "--report", report, ok=False)
    run("record", source, log, "--report", source, ok=False)
    run("record", source, log, "--report", report, "--max-buffer", 4294967297, ok=False)
    run("record", source, log, "--report", report, "--radius-m", "nan", ok=False)
    run("record", source, log, "--report", report, "--radius-m", "0x1p2", ok=False)
    run("record", source, log, "--report", report, "--max-buffer", 1, "--max-buffer", 2, ok=False)
    source.write_text("wrong,header\n" + valid)
    run("record", source, log, "--report", report, ok=False)
    run("benchmark", "--events", 1000, "--report", report)
    bench = json.loads(report.read_text())
    assert bench["metrics"]["received"] == 1000
    assert bench["events_per_second"] > 0
print(f"PASS: {checks} CLI scenarios; strict parsing, identical replay, prefix salvage, atomic report output, bounded options")
