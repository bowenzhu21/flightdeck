# Flightdeck

[![Build and tests](https://github.com/bowenzhu21/flightdeck/actions/workflows/ci.yml/badge.svg)](https://github.com/bowenzhu21/flightdeck/actions/workflows/ci.yml)


**A C++17 telemetry recorder and replay engine that finds faults hidden between packets.**

[Interactive report](https://bowenzhu21.github.io/flightdeck/) · [Architecture](docs/DESIGN.md) · [Binary format](docs/FORMAT.md) · [Raw measurements](docs/benchmark.json)

Flightdeck ingests a streaming vehicle-telemetry CSV, records a checksummed binary log, reorders bounded out-of-order events, and checks each vehicle's state and motion. Replaying the same log restores the original rules and reproduces the same findings. Corrupted logs fail explicitly; prefix salvage can recover intact frames without pretending the mission is complete.

The engineering problem is intentionally concrete: a telemetry source can repeat packets, arrive late, jump its clock, disappear, or report an impossible transition. The tool exposes which packets were dropped, why a finding fired, and what state was retained. It runs locally with no cloud account or paid API.

## Run it

Requires a C++17 compiler, Make, and Python 3.10+ for fixtures, integration tests, and the report. Tested locally with Apple Clang 21 on macOS arm64; CI is configured for Clang and GCC on Linux.

```sh
make test
make demo
open docs/index.html        # macOS; open this file in any browser on Linux
```

Or use GCC:

```sh
make clean
make test CXX=g++
```

No external C++ dependencies. The generated report is a self-contained HTML file with vehicle/rule/text filters and an interactive fault timeline. JSON download links require its sibling JSON files or a simple static server:

```sh
python3 -m http.server 8080 --directory docs
```

## What the demo actually does

The checked-in fixture is **explicitly synthetic**: six simulated vehicles, thirty seconds, seed 2026. It contains deliberate duplicate packets, clock faults, a telemetry blackout, a position spike, an invalid state transition, and low battery.

| Observed result | Value |
|---|---:|
| Recorded packets | 721 |
| Emitted packets | 706 |
| Duplicate / late / clock drops | 12 / 1 / 2 |
| Findings | 46 across 14 categories |
| Peak pending packets | 6 of the configured 4,096 |
| Vehicle states retained | 6 of the configured 256 |

These are outputs from the executable, saved in [`docs/report.json`](docs/report.json). They are not operational flight data. CI regenerates the fixture and checks that recorded and replayed configurations, findings, metrics, and log metadata match.

## CLI

```sh
# Record with a 250 ms event-time reorder window and a 500 m circular fence.
./build/flightdeck record examples/synthetic.csv build/mission.fdlog \
  --report build/record.json --lateness-ms 250 --radius-m 500

# Replay restores configuration from the log; rule overrides are rejected.
./build/flightdeck replay build/mission.fdlog --report build/replay.json

# Stop at the first damaged/incomplete frame and report only the valid prefix.
./build/flightdeck replay build/damaged.fdlog --salvage --report build/recovered.json

# Exercise a deliberately small memory budget.
./build/flightdeck record examples/synthetic.csv build/tiny.fdlog \
  --report build/tiny.json --max-buffer 2 --max-vehicles 3 --dedup-window 8

# Regenerate the interactive report from any supported result JSON.
python3 scripts/render_report.py docs/report.json docs/index.html
```

CSV schema (strict, unquoted fields; exact header):

```csv
arrival_ms,event_ms,vehicle,sequence,state,latitude,longitude,altitude_m,battery_pct
100,0,alpha,0,ground,43.4723,-80.5449,0,100
200,100,alpha,1,armed,43.4723,-80.5449,0,99
300,200,alpha,2,airborne,43.4723,-80.5449,40,98
```

Time is relative to a shared capture epoch in milliseconds. Vehicle IDs contain 1–32 ASCII letters, digits, underscores, or hyphens. Sequence and time integers are at most `2^53-1`, preserving exact JSON/JavaScript representation. Nonfinite numbers, out-of-range coordinates/battery, extra fields, quoted fields, and lines over 512 bytes are rejected before ingestion. `--help` lists every rule option.

## Core design

```text
bounded CSV reader ──→ append-only log [config | events | seal]
         │                           │
         │                           └──→ CRC + schema validation ──┐
         └──────────────────────────────────────────────────────────┤
                                                                  ▼
                          clock guards → dedup → lateness admission
                                                                  │
                                 bounded min-heap ← event watermark
                                                                  │
                              per-vehicle state + motion validation
                                                                  │
                            streaming JSON findings + fixed metrics
```

- **Explicit ordering:** watermark = maximum eligible event time minus allowed lateness, saturated at zero. Pending packets sort by event time, vehicle, sequence, and ingress ordinal. EOF drains without advancing time to infinity.
- **Bounded state:** at most `B` pending events, `V` vehicle records, `D` recent sequences per vehicle, plus at most `B` pending-sequence memberships. Runtime state is `O(B + V·D)`, independent of stream duration. Oversized frames are rejected before allocation. JSON is streamed, not accumulated.
- **Duplicate protection:** recent admission history is capped; sequences still pending in the reorder heap remain protected even after they age out of that history. Reuse of an old emitted sequence outside the window can be admitted and produce a sequence-regression finding. This is not unbounded exactly-once processing.
- **Failure visibility:** late, duplicate, capacity, unknown-vehicle-capacity, and clock drops have separate counters and findings. A full vehicle table does not silently forget prior state.
- **Replay contract:** the binary header includes all rules, frame payloads carry CRC-32, and a terminal event-count seal detects a log truncated exactly at a frame boundary. Strict CLI failures do not replace an existing successful JSON report.
- **State checks:** ground → armed → airborne → landing → ground, with armed abort and landing go-around paths. Illegal state transitions are findings, not modifications to recorded truth.

Details and tradeoffs are in [DESIGN.md](docs/DESIGN.md).

## Verification

```sh
make test       # C++ assertions and end-to-end CLI scenarios
make sanitize   # both suites with AddressSanitizer and UndefinedBehaviorSanitizer
```

The current local run passes **1,254 assertions and 30 CLI scenarios**, including:

- All 24 permutations of a four-state mission inside the reorder window produce identical results.
- A queued duplicate stays blocked after its recent-history entry is evicted.
- Every single-byte truncation after the valid configuration is rejected by strict replay and safely prefix-salvaged.
- CRC corruption, oversized frames, missing seals, trailing data, invalid configuration, and sink exceptions.
- Watermark boundary, stale/recovered heartbeat, sequence/arrival-clock regression, geofence, velocity, altitude, battery, and identical-time conflicting positions.
- Invalid numeric syntax, NaN/infinity, extreme input lines, CLI overflow, input/output aliasing, and preservation of existing reports on failure.

## Measured performance

```sh
make benchmark
python3 scripts/render_report.py docs/report.json docs/index.html
```

The saved local measurement uses five independent 200,000-event runs per setting, after a 50,000-event warmup. It constructs and processes a stream of sixteen synthetic vehicles. On the recorded macOS arm64 / Apple Clang 21 `-O2` environment:

| Reorder window | Median | Observed range |
|---|---:|---:|
| 0 ms | 5.63M events/s | 5.37M–5.68M events/s |
| 250 ms | 5.47M events/s | 5.17M–5.49M events/s |

The timer includes event construction, ingestion, and final drain. It excludes CSV parsing, binary recorder I/O, and JSON serialization. This is a local processor measurement, not an end-to-end throughput claim or a flight-hardware benchmark. Raw samples, compiler, OS, architecture, settings, and timestamps are committed in [benchmark.json](docs/benchmark.json). Re-run on your own machine; the report displays the latest saved measurements.

## Scope

Flightdeck is a **simulation engineering toolkit, not certified flight software**. It does not control aircraft, implement navigation, or provide authenticated telemetry. Horizontal speed uses a spherical haversine approximation; the fence is circular; stale detection depends on event-watermark progress. A totally silent stream needs an external time/tick source, which this finite-file tool does not provide.

The recorder flushes standard-library output after every frame and detects stream write errors. It does **not** call `fsync`, promise power-loss durability, rotate logs, or authenticate data. CRC-32 detects accidental corruption. Files grow with the input stream; bounded memory does not mean bounded disk usage. Library strict replay can invoke its sink before a later corruption is discovered; the CLI contains this by publishing its report atomically only on success.

MIT © 2026 Bowen Zhu.
