# Flight recorder format v1

All integers are unsigned little-endian. Floating-point values are IEEE 754 binary64 represented by their little-endian bit pattern. The reader requires an implementation with IEEE 754 doubles. The format contains no native struct padding or pointer values.

## File

```text
8-byte magic: ASCII "FDLOG01\n"
configuration frame (type 0)
zero or more telemetry frames (type 1)
terminal seal frame (type 2)
EOF
```

The fixed magic is also the format version. Exactly one initial configuration is required. Unknown frame types and data after the seal are invalid. An empty mission is allowed if it has a valid configuration and zero-count seal.

## Frame

| Field | Width | Description |
|---|---:|---|
| payload length | 4 bytes | Must be 1–256 bytes |
| payload | given length | Begins with one-byte frame type |
| CRC-32 | 4 bytes | IEEE CRC-32 of payload bytes only |

CRC uses polynomial `0xedb88320`, initial state `0xffffffff`, and final XOR `0xffffffff`. Test vector `123456789` produces `0xcbf43926`. The length itself is bounded and validated but is not in the CRC input. CRC provides no protection against intentional re-encoding by an attacker.

## Configuration payload

| Field, in order | Type |
|---|---|
| type = 0 | u8 |
| lateness_ms | u64 |
| heartbeat_ms | u64 |
| future_skew_ms | u64 |
| max_buffer | u32 |
| max_vehicles | u32 |
| dedup_window | u32 |
| center_lat | f64 |
| center_lon | f64 |
| radius_m | f64 |
| max_speed_mps | f64 |
| max_altitude_m | f64 |
| min_battery_pct | f64 |

Payload length is 85 bytes. Semantic validation is identical to CLI rule validation. No replay overrides are accepted, preventing accidental comparison under different rules.

## Telemetry payload

| Field, in order | Type |
|---|---|
| type = 1 | u8 |
| arrival_ms | u64 |
| event_ms | u64 |
| sequence | u64 |
| vehicle ID length | u8, 1–32 |
| vehicle ID | ASCII bytes, exact indicated length |
| state | u8: ground 0, armed 1, airborne 2, landing 3 |
| latitude | f64 |
| longitude | f64 |
| altitude_m | f64 |
| battery_pct | f64 |

Payload length is `59 + vehicle_id_length` bytes. All fields undergo the same constraints as CSV input, including finite numbers and exact-integer range. Extra payload bytes are rejected.

## Terminal seal payload

Type `2` (u8), followed by the total number of telemetry frames (u64). Payload length is 9 bytes. The count must match frames read. The seal makes a cleanly truncated frame boundary distinguishable from a completed recording. It does not prove the disk has durably persisted its data.

## Recovery

Strict replay rejects missing seals, partial length/payload/checksum, CRC mismatch, unknown type, semantic errors, count mismatch, and trailing data. The CLI publishes no report on such failure.

With `--salvage`, after a valid configuration, replay emits only completely validated telemetry frames before the first error. `log.recovered_prefix` is true, `log.sealed` is false, `log.reason` identifies the stopping condition, and `log.valid_bytes` gives the end of the last validated telemetry frame (or configuration if none). Bytes after the first error are never interpreted.

Malformed initial magic/configuration always fails. Recovery does not modify the original log and does not silently replace it with a repaired artifact.
