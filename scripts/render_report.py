#!/usr/bin/env python3
"""Render a self-contained, offline report from the C++ CLI's actual JSON output."""
import argparse
import csv
import hashlib
import json
import math
import pathlib
import re
import sys


def load_telemetry(path, report):
    """Display source samples, never inferred or interpolated aircraft positions."""
    columns = "arrival_ms,event_ms,vehicle,sequence,state,latitude,longitude,altitude_m,battery_pct".split(",")
    rows = []
    with path.open(newline="") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != columns:
            raise ValueError("Telemetry fixture does not use the Flightdeck CSV schema")
        for ordinal, row in enumerate(reader):
            if ordinal >= 20000:
                raise ValueError("Static trajectory reports support at most 20,000 source samples")
            if set(row) != set(columns) or any(value is None or len(value) > 64 for value in row.values()):
                raise ValueError("Malformed telemetry fixture row")
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", row["vehicle"]):
                raise ValueError("Invalid telemetry vehicle ID")
            if any(not re.fullmatch(r"[0-9]+", row[name]) for name in ("arrival_ms", "event_ms", "sequence")):
                raise ValueError("Telemetry time and sequence must be unsigned decimal integers")
            arrival, time, sequence = (int(row[name]) for name in ("arrival_ms", "event_ms", "sequence"))
            if max(arrival, time, sequence) > 2**53 - 1:
                raise ValueError("Telemetry integer is outside the exact JSON range")
            lat, lon, altitude, battery = (float(row[name]) for name in ("latitude", "longitude", "altitude_m", "battery_pct"))
            if not all(math.isfinite(value) for value in (lat, lon, altitude, battery)) or abs(lat) > 90 or abs(lon) > 180 or not -1000 <= altitude <= 100000 or not 0 <= battery <= 100:
                raise ValueError("Telemetry fixture contains invalid numeric data")
            if row["state"] not in ("ground", "armed", "airborne", "landing"):
                raise ValueError("Invalid telemetry state")
            center = report["config"]
            x = math.radians(lon - center["center_lon"]) * math.cos(math.radians(center["center_lat"])) * 6371008.8
            y = math.radians(lat - center["center_lat"]) * 6371008.8
            rows.append({"t": time, "arrival": arrival, "v": row["vehicle"], "seq": sequence, "state": row["state"],
                         "x": round(x, 5), "y": round(y, 5), "lat": lat, "lon": lon,
                         "alt": altitude, "battery": battery, "ordinal": ordinal})
    if len(rows) != report["metrics"]["received"]:
        raise ValueError("Fixture packet count differs from this report; pass the corresponding CSV")
    keys = {(row["v"], row["seq"], row["t"]) for row in rows}
    if any((a["vehicle"], a["sequence"], a["event_ms"]) not in keys for a in report["anomalies"]):
        raise ValueError("Report findings reference samples absent from the supplied fixture")
    manifest_path = path.parent / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    synthetic = manifest.get("synthetic") is True and manifest.get("rows") == len(rows)
    return {"samples": rows, "source": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "synthetic": synthetic, "projection": "local equirectangular meters relative to configured geofence center"}


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=pathlib.Path)
    parser.add_argument("output", type=pathlib.Path)
    parser.add_argument("--fixture", type=pathlib.Path, help="Optional matching source CSV for trajectory display")
    parser.add_argument("--without-telemetry", action="store_true", help="Render report data alone, without a source trajectory")
    return parser.parse_args()


args = arguments()
source, output = args.source, args.output
report = json.loads(source.read_text())
benchmark_path = source.parent / "benchmark.json"
benchmark = json.loads(benchmark_path.read_text()) if benchmark_path.exists() else None
fixture = args.fixture
root = pathlib.Path(__file__).resolve().parents[1]
if fixture is None and source.resolve() == root / "docs/report.json":
    candidate = root / "examples/synthetic.csv"
    if candidate.exists():
        fixture = candidate
telemetry = load_telemetry(fixture, report) if fixture is not None and not args.without_telemetry else None
payload = json.dumps({"report": report, "benchmark": benchmark, "telemetry": telemetry}, separators=(",", ":"), allow_nan=False).replace("</", "<\\/")
template = r'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="description" content="A deterministic C++ telemetry recorder, fault analyzer and replay engine. Explore actual output from a clearly labeled synthetic mission.">
<title>Flightdeck · Telemetry replay laboratory</title>
<style>
:root{color-scheme:dark;--bg:#0b1116;--panel:#111b23;--border:#24323e;--ink:#ecf3f5;--muted:#99acba;--teal:#79dbc5;--orange:#ffad72;--red:#ff7e7e;--mono:ui-monospace,SFMono-Regular,Consolas,monospace}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.55 system-ui,-apple-system,sans-serif}a{color:var(--teal);text-decoration:none}a:hover{text-decoration:underline}button,select,input{font:inherit}button:focus-visible,a:focus-visible,select:focus-visible,input:focus-visible{outline:2px solid var(--teal);outline-offset:4px}.wrap{max-width:1320px;margin:auto;padding:0 42px}.top{height:84px;display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid var(--border)}.brand{font-weight:760;font-size:20px;letter-spacing:-.6px;display:flex;gap:13px;align-items:center}.logo{width:32px;height:32px;border:1px solid var(--teal);display:grid;place-items:center;transform:rotate(-8deg);border-radius:8px;color:var(--teal);font:22px var(--mono)}.nav{display:flex;gap:25px;font-size:13px}.eyebrow{font:11px var(--mono);letter-spacing:1.8px;color:var(--teal);text-transform:uppercase}.hero{padding:57px 0 38px;display:grid;grid-template-columns:1fr 360px;gap:60px;align-items:center}h1{font-size:clamp(36px,4.3vw,57px);font-weight:600;line-height:1.08;letter-spacing:-2.7px;margin:17px 0 18px;max-width:690px}p{color:var(--muted);margin:0}.intro{max-width:650px;font-size:16px}.mission{border:1px solid var(--border);background:linear-gradient(125deg,#14232b,#101b23);border-radius:12px;padding:25px;position:relative;overflow:hidden}.mission:after{content:'';position:absolute;width:190px;height:190px;border:1px solid #36574f;border-radius:100%;right:-85px;top:-50px}.mission .tag{display:inline-flex;align-items:center;gap:7px;color:var(--orange);font:10px var(--mono);letter-spacing:1.3px}.dot{height:6px;width:6px;border-radius:50%;background:currentColor}.mission strong{font-size:24px;display:block;margin:12px 0 5px;font-weight:500}.mission p{font-size:12px;max-width:265px}.meta{display:flex;gap:14px;margin-top:21px;font:11px var(--mono);color:var(--muted)}.stats{display:grid;grid-template-columns:repeat(4,1fr);border:1px solid var(--border);border-radius:10px;overflow:hidden}.stat{padding:22px 24px;background:var(--panel);border-right:1px solid var(--border)}.stat:last-child{border:0}.stat .label{font-size:12px;color:var(--muted)}.stat .value{font:35px var(--mono);letter-spacing:-1px;margin:9px 0 3px}.stat .note{font:10px var(--mono);color:var(--muted)}.value.teal{color:var(--teal)}.value.orange{color:var(--orange)}.section{margin-top:37px}.sectionhead{display:flex;justify-content:space-between;align-items:end;margin:0 0 17px;gap:20px}h2{font-size:19px;font-weight:550;margin:0;letter-spacing:-.4px}.sectionhead p{font-size:12px;margin-top:3px}.pill{border:1px solid var(--border);border-radius:50px;padding:5px 11px;font:10px var(--mono);color:var(--muted)}.panel{background:var(--panel);border:1px solid var(--border);border-radius:10px;padding:23px}.chart{height:115px;display:flex;align-items:flex-end;gap:5px;margin-top:16px;border-bottom:1px solid #40515f;padding-bottom:0}.bar{border:0;flex:1;padding:0;min-width:4px;background:#456c67;cursor:pointer;position:relative;border-radius:3px 3px 0 0;transition:background .15s}.bar:hover,.bar.active{background:var(--teal)}.bar:focus-visible{outline-offset:0}.axis{display:flex;justify-content:space-between;color:var(--muted);font:10px var(--mono);padding-top:10px}.legend{display:flex;align-items:center;gap:7px;font:10px var(--mono);color:var(--teal)}.controls{display:flex;gap:10px;margin-bottom:20px;align-items:center;flex-wrap:wrap}.controls select,.controls input{background:#0c141c;color:var(--ink);border:1px solid #344451;border-radius:6px;padding:9px 11px;font-size:12px;min-height:39px}.controls input{min-width:200px;flex:1}.controls label{color:var(--muted);font-size:11px;display:flex;align-items:center;gap:9px}.controls button,.reset{border:0;background:none;color:var(--teal);font-size:12px;cursor:pointer;padding:8px}.count{margin-left:auto;color:var(--muted);font:11px var(--mono)}.tablewrap{overflow:auto;max-height:450px}table{border-collapse:collapse;text-align:left;width:100%;font-size:12px;min-width:735px}th{position:sticky;top:0;background:#111b23;color:var(--muted);font:10px var(--mono);text-transform:uppercase;letter-spacing:1px;border-bottom:1px solid var(--border);padding:11px 13px}td{border-bottom:1px solid #1e2d37;padding:12px 13px;vertical-align:top}td:first-child{font:11px var(--mono);color:var(--muted);white-space:nowrap}.vehicle{font:11px var(--mono);color:var(--teal)}.code{display:inline-block;background:#302821;border:1px solid #4a3c2e;color:#f7bf95;padding:3px 7px;border-radius:4px;font:10px var(--mono);white-space:nowrap}.detail{color:#c2d0d8;max-width:430px}.measure{display:block;color:var(--muted);font:10px var(--mono);margin-top:3px}.bottom{display:grid;grid-template-columns:1.2fr 1fr;gap:20px;margin:32px 0}.subtle{font-size:12px;line-height:1.8}.flow{display:grid;grid-template-columns:repeat(4,1fr);gap:7px;margin:20px 0}.flow span{background:#0c151d;border:1px solid var(--border);border-radius:5px;padding:12px 5px;text-align:center;font:10px var(--mono);color:var(--teal)}.tech{display:flex;gap:7px;flex-wrap:wrap;margin:18px 0 4px}.tech span{color:var(--muted);border:1px solid var(--border);padding:3px 7px;border-radius:4px;font:10px var(--mono)}.benchmark{font:29px var(--mono);color:var(--teal);margin:15px 0 5px}.benchrow{display:flex;justify-content:space-between;font:11px var(--mono);padding:8px 0;border-bottom:1px solid var(--border);gap:20px}.benchrow span:last-child{color:var(--muted);text-align:right}.foot{border-top:1px solid var(--border);margin-top:37px;padding:25px 0 38px;display:flex;justify-content:space-between;gap:25px;color:var(--muted);font-size:11px}.empty{padding:35px;text-align:center;color:var(--muted)}.status{color:var(--teal);font:11px var(--mono)}.sr-only{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0,0,0,0)}@media(max-width:850px){.wrap{padding:0 22px}.hero{grid-template-columns:1fr;gap:25px;padding-top:38px}.mission{display:none}.stats{grid-template-columns:1fr 1fr}.stat:nth-child(2){border-right:0}.stat:nth-child(-n+2){border-bottom:1px solid var(--border)}.bottom{grid-template-columns:1fr}.nav{gap:14px}.sectionhead{align-items:start}.controls{align-items:stretch}.controls label{flex:1}.controls select{width:100%}.foot{flex-direction:column;gap:8px}h1{letter-spacing:-1.5px}.stat .value{font-size:30px}.panel{padding:17px}}

.mission-section{margin-top:32px}.mission-section[hidden]{display:none}.mission-view{border:1px solid var(--border);border-radius:12px;overflow:hidden;background:#0e1921;display:grid;grid-template-columns:minmax(0,1fr)250px}.map-column{min-width:0}.map-topline{height:55px;display:flex;align-items:center;justify-content:space-between;padding:0 23px;gap:12px;border-bottom:1px solid #21323c}.map-topline .eyebrow{font-size:10px}.segmented{display:flex;background:#0a141b;border:1px solid #2a3b48;border-radius:6px;padding:3px;gap:2px}.segmented button{background:transparent;border:0;color:var(--muted);border-radius:4px;padding:5px 10px;font-size:10px;cursor:pointer}.segmented button.active{background:#263e45;color:#c0f1e5}.map-stage{position:relative;overflow:hidden;background:radial-gradient(ellipse at 45% 48%,#182b31 0,#101d26 55%,#0d1720 100%)}.map-stage svg{display:block;width:100%;height:385px}.map-provenance{position:absolute;bottom:13px;left:20px;font:9px var(--mono);color:#7294a3;pointer-events:none}.map-fault-hint{position:absolute;top:17px;left:20px;display:flex;gap:7px;max-width:80%;flex-wrap:wrap}.map-fault-hint button{padding:5px 9px;background:#332b22e8;border:1px solid #78533a;border-radius:5px;color:#ffbf8b;font:10px var(--mono);cursor:pointer}.map-scrub{display:grid;grid-template-columns:37px minmax(0,1fr)81px;gap:14px;align-items:center;padding:15px 22px 9px;border-top:1px solid #21323c}.play-button{width:34px;height:34px;border:1px solid #35545a;border-radius:50%;color:var(--teal);background:#172d32;cursor:pointer;display:grid;place-items:center;padding:0;font-size:12px}.map-scrub input{width:100%;accent-color:var(--teal);cursor:pointer}.map-clock{font:16px var(--mono);text-align:right;color:var(--teal);font-variant-numeric:tabular-nums}.map-scrub-caption{padding:0 22px 15px 73px;font:9px var(--mono);color:#7893a2;display:flex;justify-content:space-between;gap:12px}.fleet-panel{border-left:1px solid var(--border);background:#101b24;padding:20px 16px}.fleet-heading{display:flex;justify-content:space-between;align-items:center;margin-bottom:13px}.fleet-heading strong{font-size:12px;font-weight:550}.fleet-heading button{border:0;background:none;color:var(--teal);font-size:10px;cursor:pointer;padding:4px}.fleet-button{width:100%;display:grid;grid-template-columns:10px 1fr auto;align-items:center;gap:9px;background:transparent;color:var(--ink);border:1px solid transparent;border-radius:7px;padding:10px 9px;text-align:left;cursor:pointer;margin-bottom:5px}.fleet-button:hover{background:#192b34}.fleet-button.selected{border-color:#3e6d71;background:#193039}.fleet-dot{width:6px;height:6px;border-radius:50%;background:var(--vehicle-color);box-shadow:0 0 9px color-mix(in srgb,var(--vehicle-color) 40%,transparent)}.fleet-name{font:12px var(--mono)}.fleet-state{display:block;color:#83a0af;font:9px var(--mono);margin-top:3px}.fleet-alt{font:11px var(--mono);color:#c5d8df;white-space:nowrap}.fleet-alt small{display:block;font:9px var(--mono);color:#7d99a6;margin-top:3px}.fleet-insight{margin:16px 4px 0;border-top:1px solid #263842;padding-top:14px;font-size:11px;line-height:1.7;color:#92adbb}.fleet-insight strong{color:#d3e5ed;font-size:11px;font-weight:500;display:block;margin-bottom:5px}.mission-note{display:flex;justify-content:space-between;gap:20px;align-items:start;margin-top:11px}.mission-note p{font-size:10px;color:#7f98a6;max-width:820px}.mission-note .pill{font-size:9px;white-space:nowrap}.time-link{padding:0;border:0;background:none;color:#b1d7dd;font:11px var(--mono);cursor:pointer;white-space:nowrap}.time-link:hover{color:var(--teal);text-decoration:underline}.selected-finding{background:#1a2b33}.map-item{cursor:pointer}.map-item:focus-visible{outline:none}.map-item:focus-visible circle{stroke:white;stroke-width:3}.map-hash{font:9px var(--mono);color:#6d8794}.chart{height:84px}.hero{padding-top:42px;padding-bottom:29px}@media(max-width:950px){.mission-view{grid-template-columns:minmax(0,1fr)205px}.fleet-panel{padding:17px 10px}.fleet-button{padding:9px 6px}}@media(max-width:700px){.mission-view{grid-template-columns:1fr}.fleet-panel{border-left:0;border-top:1px solid var(--border);padding:13px 15px}.fleet-list{display:grid;grid-template-columns:1fr 1fr;gap:0 10px}.fleet-insight{margin-top:8px;padding-top:9px}.map-stage svg{height:310px}.map-topline{padding:0 14px}.map-topline .eyebrow{letter-spacing:.6px;font-size:9px}.map-scrub{padding-left:14px;padding-right:14px;gap:9px}.map-scrub-caption{padding-left:60px;padding-right:14px}.mission-note{display:block}.mission-note .pill{display:inline-block;margin-top:8px}.map-fault-hint{left:12px;top:12px}.map-provenance{font-size:8px;left:12px}.fleet-alt{font-size:10px}.map-scrub-caption span:last-child{display:none}}

</style></head><body><div class="wrap">
<header class="top"><a class="brand" href="#" aria-label="Flightdeck home"><span class="logo" aria-hidden="true">↗</span>flightdeck</a><nav class="nav" aria-label="Primary"><a href="#findings">Explore findings</a><a href="https://github.com/bowenzhu21/flightdeck">Source ↗</a></nav></header>
<main><section class="hero"><div><div class="eyebrow">Deterministic telemetry laboratory</div><h1>Every packet tells<br>a part of the story.</h1><p class="intro">Record an unordered stream. Reconstruct the mission. Surface the faults that disappear between individual samples.</p></div><aside class="mission"><span class="tag"><span class="dot"></span>SYNTHETIC MISSION / 001</span><strong>Six vehicles. One timeline.</strong><p>A reproducible 30-second scenario with deliberately injected telemetry and flight-state faults.</p><div class="meta"><span>SEED 2026</span><span>·</span><span>NO REAL FLIGHT DATA</span></div></aside></section>
<section class="stats" aria-label="Mission metrics"><div class="stat"><div class="label">Packets recorded</div><div class="value" id="received"></div><div class="note">SYNTHETIC INPUT EVENTS</div></div><div class="stat"><div class="label">Findings surfaced</div><div class="value orange" id="findingsCount"></div><div class="note" id="kindCount"></div></div><div class="stat"><div class="label">Packets dropped by policy</div><div class="value" id="dropped"></div><div class="note">DUPLICATE / LATE / INVALID CLOCK</div></div><div class="stat"><div class="label">Reorder buffer peak</div><div class="value teal" id="buffer"></div><div class="note" id="bufferBound"></div></div></section>

<section class="mission-section" id="missionSection" hidden aria-label="Mission trajectory replay">
  <div class="sectionhead"><div><h2>Watch the mission unfold</h2><p>Scrub through source telemetry. Select a vehicle or jump directly to a finding.</p></div><span class="pill" id="missionLabel">SOURCE TELEMETRY</span></div>
  <div class="mission-view"><div class="map-column">
    <div class="map-topline"><span class="eyebrow">TOP-DOWN VIEW <span style="color:#7295a4">/ LOCAL METERS</span></span><div class="segmented" aria-label="Map zoom"><button id="pathView" class="active" aria-pressed="true">Flight paths</button><button id="fenceView" aria-pressed="false">Fit geofence</button></div></div>
    <div class="map-stage"><svg id="missionMap" viewBox="0 0 760 440" role="group" aria-label="Vehicle trajectories and fault locations"><title>Recorded vehicle positions, with three-second trails and rule findings</title><defs><clipPath id="mapClip"><rect x="14" y="12" width="732" height="406" rx="8"/></clipPath></defs><g id="mapGrid"></g><g clip-path="url(#mapClip)"><g id="mapHistory"></g><g id="mapTrails"></g><g id="mapFaults"></g><g id="mapVehicles"></g></g><g id="mapLabels"></g></svg><div class="map-fault-hint" id="faultShortcuts"></div><div class="map-provenance" id="mapProjection"></div></div>
    <div class="map-scrub"><button class="play-button" id="playMission" aria-label="Play mission replay">▶</button><label class="sr-only" for="missionTime">Mission replay time</label><input type="range" id="missionTime" min="0" max="30000" step="250" value="15000"><output class="map-clock" id="missionClock" for="missionTime">15.00s</output></div>
    <div class="map-scrub-caption"><span id="mapTimeRange">00:00 — 00:30</span><span>LATEST RECORDED POSITION · 3 SECOND TRAILS</span></div>
  </div><aside class="fleet-panel"><div class="fleet-heading"><strong>Vehicle channels</strong><button id="allVehicles">Show all</button></div><div class="fleet-list" id="fleetList"></div><div class="fleet-insight" id="mapInsight"><strong>Explore a recorded mission</strong>Colored trails connect recorded samples. Orange rings locate findings from the rule engine.</div></aside></div>
  <div class="mission-note"><p id="trajectoryNote">Positions come from source CSV samples, including packets rejected by processing policy. Lines connect observations; positions are held at the last sample without interpolation.</p><span class="pill" id="sourceFingerprint"></span></div>
</section>

<section class="section"><div class="sectionhead"><div><h2>Fault timeline</h2><p>Click a one-second interval to inspect its findings. Times refer to event time.</p></div><span class="pill">30 SEC SIMULATION</span></div><div class="panel"><div class="sectionhead"><span class="legend"><span class="dot"></span>ALL FINDINGS BY SECOND</span><button class="reset" id="clearTime" hidden>Clear time filter ×</button></div><div class="chart" id="chart" aria-label="Findings by second"></div><div class="axis"><span>00:00</span><span>00:10</span><span>00:20</span><span>00:30</span></div></div></section>
<section class="section" id="findings"><div class="sectionhead"><div><h2>Inspect the evidence</h2><p>Findings below come directly from the C++ replay engine's JSON output.</p></div><a class="pill" href="report.json" download>Download raw JSON ↓</a></div><div class="panel"><div class="controls"><label>Vehicle <select id="vehicle"><option value="">All vehicles</option></select></label><label>Rule <select id="code"><option value="">All rules</option></select></label><label class="sr-only" for="search">Search findings</label><input id="search" type="search" placeholder="Search finding details…"><button id="reset">Reset</button><span class="count" id="count" aria-live="polite"></span></div><div class="tablewrap"><table><thead><tr><th scope="col">Event time</th><th scope="col">Vehicle / seq</th><th scope="col">Rule</th><th scope="col">Evidence</th></tr></thead><tbody id="rows"></tbody></table><div id="empty" class="empty" hidden>No findings match these filters.</div></div></div></section>
<div class="bottom"><section class="panel"><div class="eyebrow">Replay is a contract</div><h2 style="margin-top:9px">Same log. Same rules. Same findings.</h2><div class="flow"><span>CSV stream</span><span>CRC-32 log</span><span>Watermark</span><span>Rule engine</span></div><p class="subtle">The binary recorder stores its rule configuration with the telemetry. Replay restores those rules, resolves bounded out-of-order arrival, and evaluates the same per-vehicle state machines. Strict mode rejects damaged logs; salvage mode exposes only the verified prefix.</p><div class="tech"><span>C++17</span><span>No runtime dependencies</span><span>Bounded state</span><span>ASan + UBSan</span></div><p class="subtle" style="margin-top:17px">CRC detects accidental corruption. Stream flushing is not an fsync durability guarantee. This is a simulation engineering toolkit, not certified flight software.</p></section><section class="panel"><div class="eyebrow">Measured, with boundaries</div><h2 style="margin-top:9px">Local processor benchmark</h2><div class="benchmark" id="benchRate">Not measured</div><p class="subtle" id="benchMethod">Run make benchmark to record local performance.</p><div id="benchDetails"></div><a class="subtle" style="display:inline-block;margin-top:17px" href="benchmark.json">View all raw measurements ↗</a></section></div>
</main><footer class="foot"><span>Flightdeck · Built by Bowen Zhu · <a href="https://github.com/bowenzhu21/flightdeck">Read architecture & tests</a></span><span class="status" id="logStatus"></span></footer></div>
<script id="data" type="application/json">__PAYLOAD__</script><script>
'use strict';
const data=JSON.parse(document.getElementById('data').textContent);
const r=data.report,m=r.metrics,c=r.config,$=id=>document.getElementById(id);
const number=n=>Number(n).toLocaleString('en-US');
const telemetry=data.telemetry;
const samples=telemetry?telemetry.samples:[];
const channels=[...new Set([...samples.map(s=>s.v),...r.anomalies.map(a=>a.vehicle)])].sort();
const colors=['#78ddc7','#88bfff','#ecaa82','#beabff','#e9d889','#91d9f0'];
const tracks=new Map(channels.map(v=>[v,samples.filter(s=>s.v===v).sort((a,b)=>a.t-b.t||a.seq-b.seq||a.ordinal-b.ordinal)]));
const sampleIndex=new Map(samples.map(s=>[s.v+'|'+s.seq+'|'+s.t,s]));
const sourceFor=a=>sampleIndex.get(a.vehicle+'|'+a.sequence+'|'+a.event_ms);
const anomalyKey=a=>[a.vehicle,a.sequence,a.event_ms,a.code].join('|');
const startTime=samples.length?Math.min(...samples.map(s=>s.t)):0;
const endTime=samples.length?Math.max(...samples.map(s=>s.t)):Math.max(30000,...r.anomalies.map(a=>a.event_ms));
let second=null,mapTime=startTime+(endTime-startTime)/2,wideView=false,selectedFinding=null,playTimer=null;
const palette=v=>colors[channels.indexOf(v)%colors.length];
const formatTime=t=>(t/1000).toFixed(2)+'s';
$('received').textContent=number(m.received);
$('findingsCount').textContent=number(m.anomalies);
$('kindCount').textContent=Object.keys(m.anomaly_counts).length+' DISTINCT RULE CATEGORIES';
$('dropped').textContent=number(m.received-m.emitted);
$('buffer').textContent=m.peak_buffer;
$('bufferBound').textContent='OF '+number(c.max_buffer)+' ALLOWED PENDING EVENTS';
$('logStatus').textContent=r.log.sealed?'● LOG SEALED · CHECKSUMS VERIFIED':'● PREFIX RECOVERY · INCOMPLETE LOG';
for(const [id,items] of [['vehicle',channels],['code',Object.keys(m.anomaly_counts).sort()]]){
  for(const value of items){const option=document.createElement('option');option.value=value;option.textContent=value.replaceAll('_',' ');$(id).append(option)}
}
const binWidth=Math.max(1000,Math.ceil((endTime-startTime+1)/60000)*1000);
const binCount=Math.min(61,Math.floor((endTime-startTime)/binWidth)+1);
const bins=Array(binCount).fill(0);
for(const a of r.anomalies){const n=Math.floor((a.event_ms-startTime)/binWidth);if(n>=0&&n<bins.length)bins[n]++}
const maximum=Math.max(1,...bins);
bins.forEach((n,i)=>{
  const button=document.createElement('button');button.className='bar';button.style.height=Math.max(3,n/maximum*100)+'%';
  const time=startTime+i*binWidth;button.title=formatTime(time)+'–'+formatTime(time+binWidth)+': '+n+' findings';button.setAttribute('aria-label',button.title);
  button.addEventListener('click',()=>{stopPlay();second=second===Math.floor(time/1000)?null:Math.floor(time/1000);mapTime=time;selectedFinding=null;render()});$('chart').append(button);
});
function cell(text,cls){const td=document.createElement('td');if(cls)td.className=cls;td.textContent=text;return td}
function matches(a,includeTime=true){
  const query=$('search').value.toLowerCase();
  return (!$('vehicle').value||a.vehicle===$('vehicle').value)&&(!$('code').value||a.code===$('code').value)&&
    (!includeTime||second===null||(a.event_ms>=second*1000&&a.event_ms<second*1000+binWidth))&&
    (!query||(a.detail+' '+a.code+' '+a.vehicle).toLowerCase().includes(query));
}
function focusFinding(a){
  stopPlay();selectedFinding=a;mapTime=a.event_ms;second=Math.floor(a.event_ms/1000);
  $('vehicle').value=a.vehicle;$('code').value=a.code;$('search').value='';
  const position=sourceFor(a);wideView=!!position&&(Math.abs(position.y)>180||Math.abs(position.x)>330);
  render();
}
function render(){
  const findings=r.anomalies.filter(a=>matches(a)).sort((a,b)=>a.event_ms-b.event_ms||a.vehicle.localeCompare(b.vehicle));
  $('rows').replaceChildren();
  for(const a of findings){
    const tr=document.createElement('tr');if(selectedFinding&&anomalyKey(a)===anomalyKey(selectedFinding))tr.className='selected-finding';
    const time=cell(''),button=document.createElement('button');button.className='time-link';button.textContent=(a.event_ms/1000).toFixed(3)+' s ↗';
    button.setAttribute('aria-label','Inspect '+a.vehicle+' '+a.code.replaceAll('_',' ')+' at '+formatTime(a.event_ms));button.onclick=()=>focusFinding(a);time.append(button);tr.append(time);
    tr.append(cell(a.vehicle+' / '+a.sequence,'vehicle'));
    const rule=cell(''),badge=document.createElement('span');badge.className='code';badge.textContent=a.code.replaceAll('_',' ');rule.append(badge);tr.append(rule);
    const detail=cell(a.detail,'detail');if(a.observed||a.limit){const small=document.createElement('span');small.className='measure';small.textContent='observed '+Number(a.observed.toFixed(3))+' · reference '+Number(a.limit.toFixed(3));detail.append(small)}
    tr.append(detail);$('rows').append(tr);
  }
  $('count').textContent=findings.length+' / '+m.anomalies+' findings';$('empty').hidden=findings.length>0;$('clearTime').hidden=second===null;
  [...$('chart').children].forEach((b,i)=>b.classList.toggle('active',second!==null&&i===Math.floor((second*1000-startTime)/binWidth)));
  if(samples.length)renderMap();
}
function svg(tag,attributes={},text){const node=document.createElementNS('http://www.w3.org/2000/svg',tag);for(const [key,value] of Object.entries(attributes))node.setAttribute(key,String(value));if(text!==undefined)node.textContent=text;return node}
function linePath(points,xy){return points.map((p,i)=>(i?'L':'M')+xy(p).map(n=>n.toFixed(2)).join(',')).join(' ')}
function mapAction(node,label,action){
  node.classList.add('map-item');node.setAttribute('role','button');node.setAttribute('tabindex','0');node.setAttribute('aria-label',label);
  node.addEventListener('click',action);node.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();action()}});
  node.append(svg('title',{},label));return node;
}
function selectVehicle(v){stopPlay();$('vehicle').value=$('vehicle').value===v?'':v;$('code').value='';$('search').value='';second=null;selectedFinding=null;render()}
function renderMap(){
  const extent=wideView?Math.max(650,...samples.map(s=>Math.max(Math.abs(s.x),Math.abs(s.y))*1.12)):180;
  const scale=190/extent,xy=p=>[380+p.x*scale,220-p.y*scale];
  const nodes={grid:$('mapGrid'),history:$('mapHistory'),trails:$('mapTrails'),faults:$('mapFaults'),vehicles:$('mapVehicles'),labels:$('mapLabels')};
  Object.values(nodes).forEach(n=>n.replaceChildren());
  const step=wideView?250:50;
  for(let offset=-Math.floor(365/scale/step)*step;offset<=365/scale;offset+=step){const x=380+offset*scale;nodes.grid.append(svg('line',{x1:x,y1:14,x2:x,y2:416,stroke:offset===0?'#39535e':'#20353e','stroke-width':offset===0?1.1:.65}))}
  for(let offset=-Math.floor(202/scale/step)*step;offset<=202/scale;offset+=step){const y=220-offset*scale;nodes.grid.append(svg('line',{x1:14,y1:y,x2:746,y2:y,stroke:offset===0?'#39535e':'#20353e','stroke-width':offset===0?1.1:.65}))}
  for(const radius of wideView?[c.radius_m]:[50,100,150])nodes.grid.append(svg('circle',{cx:380,cy:220,r:radius*scale,fill:'none',stroke:wideView?'#6ea18d':'#315257','stroke-width':wideView?1.3:.75,'stroke-dasharray':wideView?'5 6':'2 6'}));
  if(wideView)nodes.labels.append(svg('text',{x:390,y:220-c.radius_m*scale-9,fill:'#a2c3b6','font-size':10,'font-family':'monospace'},c.radius_m+' m geofence'));
  nodes.grid.append(svg('path',{d:'M380 214L386 220L380 226L374 220Z',fill:'#91afba',opacity:.7}));
  nodes.labels.append(svg('text',{x:390,y:235,fill:'#7395a3','font-size':9,'font-family':'monospace'},'ORIGIN'));
  nodes.labels.append(svg('path',{d:'M719 64L719 37M715 44L719 37L723 44',fill:'none',stroke:'#88a4b1','stroke-width':1.2}));
  nodes.labels.append(svg('text',{x:715,y:29,fill:'#b0cbd5','font-size':11,'font-family':'monospace'},'N'));
  const ruler=wideView?250:50,rulerWidth=ruler*scale;
  nodes.labels.append(svg('path',{d:'M'+(720-rulerWidth)+' 394V400H720V394',fill:'none',stroke:'#7996a4','stroke-width':1}));
  nodes.labels.append(svg('text',{x:720-rulerWidth/2,y:390,fill:'#9fbac6','text-anchor':'middle','font-size':9,'font-family':'monospace'},ruler+' m'));
  const chosen=$('vehicle').value;
  const snapshots=new Map();
  for(const vehicle of channels){
    const seen=tracks.get(vehicle).filter(s=>s.t<=mapTime);if(!seen.length)continue;
    const last=seen[seen.length-1];snapshots.set(vehicle,last);if(chosen&&chosen!==vehicle)continue;
    const color=palette(vehicle),tail=seen.filter(s=>s.t>=mapTime-3000);
    nodes.history.append(svg('path',{d:linePath(seen,xy),fill:'none',stroke:color,'stroke-width':1.3,opacity:.22,'stroke-linejoin':'round'}));
    if(tail.length)nodes.trails.append(svg('path',{d:linePath(tail,xy),fill:'none',stroke:color,'stroke-width':2.7,opacity:.92,'stroke-linecap':'round','stroke-linejoin':'round'}));
    let [x,y]=xy(last);const outside=x<25||x>735||y<25||y>405;
    x=Math.min(733,Math.max(27,x));y=Math.min(403,Math.max(27,y));
    const stale=mapTime-last.t>c.heartbeat_ms;
    const marker=svg('g');marker.append(svg('circle',{cx:x,cy:y,r:13,fill:color,opacity:stale?.05:.10}));
    marker.append(svg('circle',{cx:x,cy:y,r:5.5,fill:stale?'#15252f':color,stroke:color,'stroke-width':1.7}));
    if(outside)marker.append(svg('path',{d:'M'+(x-5)+' '+(y+18)+'L'+x+' '+(y+12)+'L'+(x+5)+' '+(y+18),fill:'none',stroke:color,'stroke-width':1.5}));
    marker.append(svg('text',{x:x>650?x-11:x+11,y:y-10,'text-anchor':x>650?'end':'start',fill:color,'font-size':10,'font-family':'monospace','paint-order':'stroke',stroke:'#0f1d25','stroke-width':3},vehicle+(stale?' · stale':'')+(outside?' · outside view':'')));
    nodes.vehicles.append(mapAction(marker,vehicle+' last sample '+formatTime(last.t)+(outside?', outside current view':''),()=>selectVehicle(vehicle)));
  }
  const grouped=new Map();
  for(const a of r.anomalies){if(!matches(a)||a.event_ms>mapTime)continue;const source=sourceFor(a);if(!source)continue;const key=a.vehicle+'|'+a.event_ms;if(!grouped.has(key))grouped.set(key,[]);grouped.get(key).push(a)}
  for(const findings of grouped.values()){
    const a=findings[0],position=sourceFor(a),[x,y]=xy(position);if(x<15||x>745||y<14||y>416)continue;
    const marker=svg('g');marker.append(svg('circle',{cx:x,cy:y,r:10,fill:'#f5a767',opacity:.10}));marker.append(svg('circle',{cx:x,cy:y,r:7,fill:'none',stroke:'#f2ac77','stroke-width':1.5}));
    nodes.faults.append(mapAction(marker,a.vehicle+': '+findings.map(f=>f.code.replaceAll('_',' ')).join(', ')+' at '+formatTime(a.event_ms),()=>focusFinding(a)));
  }
  $('fleetList').replaceChildren();
  for(const v of channels){
    const last=snapshots.get(v),button=document.createElement('button');button.className='fleet-button'+(chosen===v?' selected':'');button.style.setProperty('--vehicle-color',palette(v));button.setAttribute('aria-pressed',chosen===v?'true':'false');
    const dot=document.createElement('span');dot.className='fleet-dot';const name=document.createElement('span');name.className='fleet-name';name.textContent=v;
    const state=document.createElement('span');state.className='fleet-state';state.textContent=last?(mapTime-last.t>c.heartbeat_ms?'stale · last '+formatTime(last.t):last.state+' · seq '+last.seq):'awaiting first sample';name.append(state);
    const altitude=document.createElement('span');altitude.className='fleet-alt';altitude.textContent=last?Math.round(last.alt)+' m':'—';const battery=document.createElement('small');battery.textContent=last?Math.round(last.battery)+'% battery':'no sample';altitude.append(battery);button.append(dot,name,altitude);button.onclick=()=>selectVehicle(v);$('fleetList').append(button);
  }
  $('missionTime').value=mapTime;$('missionClock').textContent=formatTime(mapTime);$('missionTime').setAttribute('aria-valuetext',formatTime(mapTime)+' into mission');
  $('pathView').classList.toggle('active',!wideView);$('fenceView').classList.toggle('active',wideView);$('pathView').setAttribute('aria-pressed',String(!wideView));$('fenceView').setAttribute('aria-pressed',String(wideView));
  $('mapInsight').replaceChildren();const title=document.createElement('strong'),body=document.createElement('span');
  if(selectedFinding){title.textContent=selectedFinding.vehicle+' · '+selectedFinding.code.replaceAll('_',' ');body.textContent=selectedFinding.detail+(selectedFinding.observed||selectedFinding.limit?' Observed '+Number(selectedFinding.observed.toFixed(2))+'; reference '+Number(selectedFinding.limit.toFixed(2))+'.':'')}
  else{const stale=[...snapshots].filter(([v,s])=>(!chosen||chosen===v)&&mapTime-s.t>c.heartbeat_ms);title.textContent=stale.length?stale.length+' stale telemetry channel'+(stale.length>1?'s':''):'Source telemetry at '+formatTime(mapTime);body.textContent=stale.length?stale.map(([v,s])=>v+': no source sample for '+((mapTime-s.t)/1000).toFixed(2)+'s').join('. ')+'. Positions remain at the last observation.':'Bright trails show the previous three seconds. Orange rings link to engine findings; select any vehicle to isolate its path.'}
  $('mapInsight').append(title,body);
}
function stopPlay(){if(playTimer!==null){clearInterval(playTimer);playTimer=null}$('playMission').textContent='▶';$('playMission').setAttribute('aria-label','Play mission replay')}
if(samples.length){
  $('missionSection').hidden=false;$('missionLabel').textContent=telemetry.synthetic?'SYNTHETIC MISSION · '+channels.length+' VEHICLES':'SOURCE CSV · '+channels.length+' VEHICLES';
  $('missionTime').min=startTime;$('missionTime').max=endTime;$('mapTimeRange').textContent=formatTime(startTime)+' — '+formatTime(endTime);
  $('sourceFingerprint').textContent='CSV SHA256 '+telemetry.sha256.slice(0,12);$('mapProjection').textContent='LOCAL XY PROJECTION · '+(telemetry.synthetic?'SYNTHETIC SOURCE SAMPLES':'RECORDED SOURCE SAMPLES');
  const shortcuts=[['geofence','Position spike'],['telemetry_gap','Telemetry gap']];
  for(const [code,label] of shortcuts){const finding=r.anomalies.find(a=>a.code===code&&sourceFor(a));if(finding){const button=document.createElement('button');button.textContent=label+' · '+formatTime(finding.event_ms)+' ↗';button.onclick=()=>focusFinding(finding);$('faultShortcuts').append(button)}}
  $('missionTime').addEventListener('input',()=>{stopPlay();mapTime=Number($('missionTime').value);second=Math.floor(mapTime/1000);selectedFinding=null;render()});
  $('pathView').onclick=()=>{wideView=false;renderMap()};$('fenceView').onclick=()=>{wideView=true;renderMap()};
  $('allVehicles').onclick=()=>{$('vehicle').value='';$('code').value='';$('search').value='';second=null;selectedFinding=null;render()};
  $('playMission').onclick=()=>{if(playTimer!==null){stopPlay();return}if(mapTime>=endTime)mapTime=startTime;selectedFinding=null;$('playMission').textContent='Ⅱ';$('playMission').setAttribute('aria-label','Pause mission replay');playTimer=setInterval(()=>{mapTime=Math.min(endTime,mapTime+250);second=Math.floor(mapTime/1000);render();if(mapTime>=endTime)stopPlay()},100)};
  document.addEventListener('visibilitychange',()=>{if(document.hidden)stopPlay()});
}
for(const id of ['vehicle','code','search'])$(id).addEventListener('input',()=>{selectedFinding=null;render()});
$('reset').onclick=()=>{stopPlay();for(const id of ['vehicle','code','search'])$(id).value='';second=null;selectedFinding=null;wideView=false;render()};
$('clearTime').onclick=()=>{second=null;selectedFinding=null;render()};
render();
if(data.benchmark){
  const b=data.benchmark,active=b.results.find(s=>s.lateness_ms===250)||b.results[0];$('benchRate').textContent=number(Math.round(active.median_events_per_second))+' events / sec';
  $('benchMethod').textContent='Median of five 200,000-event runs, 16 synthetic vehicles, 250 ms reorder window. Includes event construction + processing; excludes CSV, recorder I/O and report serialization.';
  const rows=[['Build',b.environment.os+' / '+b.environment.architecture+' · '+b.environment.build_flags],['Range',number(Math.round(active.min_events_per_second))+'–'+number(Math.round(active.max_events_per_second))+' / sec'],['Zero-window baseline',number(Math.round(b.results[0].median_events_per_second))+' / sec'],['Measurement',b.measured_at_utc.slice(0,10)+' UTC']];
  for(const [label,value] of rows){const row=document.createElement('div');row.className='benchrow';const a=document.createElement('span'),v=document.createElement('span');a.textContent=label;v.textContent=value;row.append(a,v);$('benchDetails').append(row)}
}
</script></body></html>'''
output.parent.mkdir(parents=True, exist_ok=True)
output.write_text(template.replace("__PAYLOAD__", payload))
print(f"Rendered {report['metrics']['anomalies']} real engine findings into {output.name}.")
