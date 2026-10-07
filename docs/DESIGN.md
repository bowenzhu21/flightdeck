# Design and invariants

## Separation of responsibilities

`parse_csv_line` validates individual events. The CLI's bounded line reader prevents a malformed multi-megabyte line from first allocating a multi-megabyte string. `Recorder` persists every syntactically valid input event, including events the processor will subsequently drop by policy. `Processor` owns temporal and vehicle rules. `LogReader` validates framing and restores the original configuration. The report sink never owns the full result set.

The library has no network, GUI, GPU, or Python dependency. Python is limited to fixtures, end-to-end tests, report generation, and benchmark orchestration.

## Admission order

For each syntactically valid packet:

1. Count it as received.
2. Reject an arrival clock older than the last seen arrival clock. The arrival clock is global to the capture stream, not per vehicle.
3. Reject an event timestamp farther into the future than the permitted skew relative to arrival.
4. Reject a sequence present in the vehicle's recent-admission or still-pending sets.
5. Reject timestamps strictly before the current event watermark. Equality is permitted.
6. Reject new vehicle IDs when the fixed vehicle cap is full.
7. Advance the event watermark from this eligible timestamp and drain older pending events.
8. Reject the newest event if the pending-event cap is still full. This eligible timestamp may already have advanced the watermark.
9. Register the vehicle and bounded sequence history, enqueue, drain through the watermark, and check stale heartbeats.

Duplicate, late, and clock-invalid packets cannot advance the watermark. Capacity-rejected new events can, because their timestamps already passed temporal admission. The policy intentionally sheds new data rather than silently removing a previously accepted event.

## Ordering and watermark boundary

The min-heap orders `(event_ms, vehicle, sequence, ingress_ordinal)`. This guarantees deterministic replay of an identical recorded ingress stream. Permuting packets that all remain buffered produces the same event-time evaluation. It does **not** promise equivalent output for arbitrary permutations that change drop decisions or cross the watermark.

The strict-lower late policy allows a new packet with `event_ms == watermark`. A prior equal-time packet may already have been emitted; sequence-regression and same-time position-conflict findings make that ambiguity visible. For a strict finalized-prefix use case, the boundary could be changed to reject equality, but would need different handling for the initial zero timestamp and a format/config version change.

`finish()` drains pending events, is idempotent, and rejects later ingestion. It does not invent an infinite end timestamp or mark every vehicle stale merely because the file ended.

## State and findings

Legal state transitions:

```text
ground → armed → airborne → landing → ground
           └──→ ground         └──→ airborne
```

Remaining in any state is legal. A non-ground first sample is reported as incomplete initial history. Invalid transitions still update the last-observed state, because filtering them out would rewrite the source's history and potentially hide the next transition.

For each emitted sample, the engine checks monotone sequence, elapsed time, horizontal velocity, same-time position disagreement, distance from the circular fence center, altitude ceiling, and battery reserve. Ground speed uses great-circle distance divided by event-time difference. The haversine term is clamped to `[0,1]` to prevent floating-point drift from producing NaN.

Heartbeat scanning is bounded by the vehicle cap. A stale finding fires once when `watermark - last_emitted_event_time > heartbeat_ms`. The next emitted packet produces recovery and, where applicable, a telemetry-gap finding. The stale finding refers to the last known sample; its `observed` field captures the elapsed silence at detection. Continuous low battery or repeated position violations are reported per sample rather than debounced.

## Bounds and complexity

Let `B = max_buffer`, `V = max_vehicles`, and `D = dedup_window`.

| State | Bound |
|---|---:|
| Pending event heap | B events |
| Per-vehicle last sample + flags | V records |
| Recent-admission deque | V × D sequence IDs |
| Recent-admission membership set | V × D sequence IDs |
| Pending membership sets, summed over vehicles | B sequence IDs |
| Rule counter map | Fixed set of rule codes |
| CSV line | 512 bytes |
| Binary payload | 256 bytes |
| Vehicle identifier | 32 ASCII bytes |

Input limits additionally cap B at 1,000,000, V at 4,096, D at 65,536, and V×D at 4,000,000. Containers retain allocator overhead and may retain peak capacity; this is a bound on logical retained records, not a byte-exact RSS or real-time allocation guarantee.

Insertion/drain costs `O(log B)` per event, vehicle lookup costs `O(log V)`, and heartbeat scanning costs `O(V)` per eligible ingest. Hash-set lookup has expected constant complexity. The design favors inspectable single-threaded determinism over multicore throughput. For fleets with thousands of vehicles, a deadline heap could replace the heartbeat scan; this implementation does not claim that optimization.

The recent window is counted in accepted arrivals. A still-pending sequence remains protected independently of that window. Once an emitted sequence is outside the window it may be admitted again; monotonic sequence validation can flag it. IDs never silently evict, so an attacker cannot cycle IDs to obtain unbounded retained state, but a full table prevents admitting legitimate new vehicles until a new processor is created.

## Failure and durability contract

- CSV parse errors abort the run with a line number where applicable. The log may contain an unsealed valid prefix, recoverable with `--salvage`.
- Every frame is flushed through `std::ofstream`. No `fsync` is performed; power-loss durability is not promised.
- Replay validates lengths, CRC, typed payloads, configuration constraints, and the terminal count seal. Salvage stops at the first bad frame; it never skips ahead looking for possible resynchronization.
- Corrupted or missing initial configuration cannot be salvaged because the original rules are required for meaningful replay.
- Library callbacks may have received a valid prefix before strict mode notices later corruption. Sink exceptions propagate, even in salvage mode.
- The CLI writes findings to a sibling `.tmp` file and renames only after successful analysis. Prior successful reports survive parse/replay failures. The CLI is intended for one writer per output path on macOS/Linux; it is not a multiwriter filesystem transaction service.
- Existing log outputs are overwritten deliberately. Configuration and input/output aliases are checked before opening; use unique output names when retaining captures.
- CRC-32 is corruption detection, not a signature or MAC. Input identity, transport authentication, encrypted storage, and adversarial tamper resistance are outside scope.

## Evidence

The C++ suite checks reorder permutations, every post-configuration byte truncation, checksums, semantic limits, state rules, capacity pressure, dedup eviction with queued duplicates, and error propagation. Python tests exercise CLI exit codes and output preservation, then compare record/replay structures exactly. Both suites run under ASan/UBSan through `make sanitize`.

The saved benchmark intentionally measures only the processor and generated event construction. The recorder's per-frame flush cost is excluded, so the throughput number must never be presented as disk recording throughput.
