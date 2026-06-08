# KAIROX — AI Integration API Contract

**Version:** 4.5  
**Last updated:** 2026-06-08

---

## Overview

KAIROX sends operational telemetry to an external HTTP endpoint. The endpoint returns a suggested command. KAIROX validates the response and may execute the command.

The external endpoint is called by KAIROX — never the reverse. The endpoint does not have access to device infrastructure.

---

## Authentication

KAIROX includes a Bearer token in every outbound request:

```
Authorization: Bearer <token>
```

The token is configured on the KAIROX side and injected into every HTTP call. The receiving endpoint must validate it and return `401` if invalid.

The token for the current integration is provisioned by the KAIROX team and communicated out-of-band.

---

## Endpoints

KAIROX supports two call modes. Each is configured independently with its own endpoint URL.

| Mode | Status | Trigger | Rate | Timeout |
|---|---|---|---|---|
| `BATCH` | Available (not currently active) | Periodic timer | Configurable (default: every 60 s) | 10 s (configurable) |
| `REALTIME` | **Active** | Each incoming device sample | Up to 1 Hz | 2 s (configurable) |

Both modes use:

```
POST <configured-url>
Content-Type: application/json
Authorization: Bearer <token>
```

A single URL may handle both modes. Use the `integration_mode` field to distinguish.

On timeout, KAIROX logs the failure and takes no action. Timed-out calls are not retried.

For REALTIME: if the previous call has not yet returned, the new sample is dropped. Dropped calls are logged.

---

## Request — BATCH

### Schema

| Field | Type | Description |
|---|---|---|
| `request_id` | `string` (UUID4) | Unique call identifier. |
| `integration_mode` | `"BATCH"` | Constant. |
| `device_id` | `string` | Device identifier. |
| `timestamp` | `string` (ISO 8601 UTC) | Time this payload was generated. |
| `fsm_state` | `string` | Current device state. See [FSM states](#fsm-states). |
| `fault_reason` | `string` \| `null` | Fault cause. Populated only when `fsm_state = "FAULT"`. See [Fault reasons](#fault-reasons). |
| `retry_count` | `integer` | Number of consecutive failed startup attempts. |
| `connectivity` | `"ONLINE"` \| `"OFFLINE"` \| `"UNKNOWN"` | `ONLINE` if a sample was received within the last 90 s. |
| `seconds_since_seen` | `integer` \| `null` | Seconds since last sample. `null` if no sample ever received. |
| `inputs` | `object` | Digital input states. See [Inputs](#inputs). |
| `outputs` | `object` | Actuator states. See [Outputs](#outputs). |
| `telemetry_window` | `object` | Telemetry window. See [Telemetry window](#telemetry-window). |

### Telemetry window

| Field | Type | Description |
|---|---|---|
| `window_seconds` | `integer` | Duration of the window in seconds. |
| `sample_period_seconds` | `integer` | Minimum interval between consecutive samples. |
| `samples` | `array` | Sample objects, ascending by `ts`. May be `[]`. |

### Sample object

Any field may be `null`.

| Field | Type | Unit |
|---|---|---|
| `ts` | `string` (ISO 8601 UTC) | — |
| `flow_permeate_lpm` | `float` \| `null` | L/min |
| `flow_reject_lpm` | `float` \| `null` | L/min |
| `pressure_in_bar` | `float` \| `null` | bar |
| `pressure_out_bar` | `float` \| `null` | bar |
| `volume_permeate_l` | `float` \| `null` | L |
| `volume_reject_l` | `float` \| `null` | L |
| `tds_in_ppm` | `float` \| `null` | ppm |
| `tds_out_ppm` | `float` \| `null` | ppm |
| `tds_in_voltage` | `float` \| `null` | V |
| `tds_out_voltage` | `float` \| `null` | V |

### Example

```json
{
  "request_id":         "f3a1c9d2-7e45-4b2a-a831-bc09ef112034",
  "integration_mode":   "BATCH",
  "device_id":          "ESP32_D0448EC92DF4",
  "timestamp":          "2026-06-08T14:32:07Z",
  "fsm_state":          "PRODUCING",
  "fault_reason":       null,
  "retry_count":        0,
  "connectivity":       "ONLINE",
  "seconds_since_seen": 2,
  "inputs": {
    "demand":              true,
    "raw_water_ok":        true,
    "dose_ok":             true,
    "pressure_switch":     true,
    "feed_tank_level_low": false,
    "spare2":              false
  },
  "outputs": {
    "pump_low":    false,
    "pump_high":   true,
    "pump_inlet":  false,
    "pump_dose":   false,
    "valve_flush": false,
    "valve_inlet": true
  },
  "telemetry_window": {
    "window_seconds":        60,
    "sample_period_seconds": 1,
    "samples": [
      {
        "ts":                "2026-06-08T14:31:08Z",
        "flow_permeate_lpm": 1.85,
        "flow_reject_lpm":   4.10,
        "pressure_in_bar":   7.8,
        "pressure_out_bar":  0.4,
        "volume_permeate_l": 24887.0,
        "volume_reject_l":   10198.0,
        "tds_in_ppm":        410.0,
        "tds_out_ppm":       41.0,
        "tds_in_voltage":    1.82,
        "tds_out_voltage":   0.22
      }
    ]
  }
}
```

---

## Request — REALTIME

Identical top-level schema to BATCH with the following differences:

- `integration_mode` is `"REALTIME"`
- `telemetry_window` is **absent**
- `sample` (object) replaces it — the current sample, same schema as a BATCH sample object
- `context_window` (object) provides recent preceding samples

### `context_window`

**Present only when historical context is enabled by the KAIROX operator. Absent by default.**

When present, the receiving endpoint is responsible for maintaining its own historical state across calls. The context window is provided as convenience only.

| Field | Type | Description |
|---|---|---|
| `window_seconds` | `integer` | Duration of the context window in seconds. |
| `samples` | `array` | Samples preceding `sample`, ascending by `ts`. Does not include `sample`. May be `[]`. |

### Example

```json
{
  "request_id":         "b7f2a841-3c19-4e0a-9d12-aabbcc001122",
  "integration_mode":   "REALTIME",
  "device_id":          "ESP32_D0448EC92DF4",
  "timestamp":          "2026-06-08T14:35:22Z",
  "fsm_state":          "PRODUCING",
  "fault_reason":       null,
  "retry_count":        0,
  "connectivity":       "ONLINE",
  "seconds_since_seen": 1,
  "inputs": {
    "demand":              true,
    "raw_water_ok":        true,
    "dose_ok":             true,
    "pressure_switch":     true,
    "feed_tank_level_low": false,
    "spare2":              false
  },
  "outputs": {
    "pump_low":    false,
    "pump_high":   true,
    "pump_inlet":  false,
    "pump_dose":   false,
    "valve_flush": false,
    "valve_inlet": true
  },
  "sample": {
    "ts":                "2026-06-08T14:35:21Z",
    "flow_permeate_lpm": 0.3,
    "flow_reject_lpm":   4.1,
    "pressure_in_bar":   7.9,
    "pressure_out_bar":  0.4,
    "volume_permeate_l": 24920.0,
    "volume_reject_l":   10250.0,
    "tds_in_ppm":        410.0,
    "tds_out_ppm":       40.0,
    "tds_in_voltage":    1.82,
    "tds_out_voltage":   0.21
  },
  "context_window": {
    "window_seconds": 30,
    "samples": [
      {
        "ts":                "2026-06-08T14:34:52Z",
        "flow_permeate_lpm": 1.85,
        "flow_reject_lpm":   4.10,
        "pressure_in_bar":   7.8,
        "pressure_out_bar":  0.4,
        "volume_permeate_l": 24917.0,
        "volume_reject_l":   10247.0,
        "tds_in_ppm":        410.0,
        "tds_out_ppm":       41.0,
        "tds_in_voltage":    1.82,
        "tds_out_voltage":   0.22
      }
    ]
  }
}
```

---

## Response

The response must be a JSON object with **exactly** these fields. No additional fields are permitted.

| Field | Type | Required | Constraints |
|---|---|---|---|
| `decision` | `string` | Yes | `"NONE"` or `"EXECUTE"` |
| `confidence` | `float` | Yes | `[0.0, 1.0]` |
| `reason` | `string` | Yes | Non-empty, max 500 characters |
| `suggested_cmd` | `string` \| `null` | Conditional | Non-null when `decision = "EXECUTE"`. `null` or absent when `decision = "NONE"`. |

`suggested_cmd` accepted values: `START`, `STOP`, `FLUSH`, `RST` (case-insensitive).

### Example — no action

```json
{
  "decision":      "NONE",
  "confidence":    0.94,
  "reason":        "No action required",
  "suggested_cmd": null
}
```

### Example — suggest command

```json
{
  "decision":      "EXECUTE",
  "confidence":    0.89,
  "reason":        "TDS output rising over last 60 samples",
  "suggested_cmd": "FLUSH"
}
```

---

## Validation

Responses failing any check are rejected entirely. No partial processing.

| Condition | Error |
|---|---|
| Response is not a JSON object | `response must be a JSON object` |
| Unexpected field present | `unexpected fields in response: ['<field>']` |
| `decision` not in `{"NONE", "EXECUTE"}` | `decision='<value>' not in allowed values` |
| `confidence` not a float in [0.0, 1.0] | `confidence must be a float in [0.0, 1.0]` |
| `reason` empty or not a string | `reason must be a non-empty string` |
| `reason` exceeds 500 characters | `reason must be at most 500 characters` |
| `decision = "EXECUTE"` with null or absent `suggested_cmd` | `decision=EXECUTE requires a non-empty 'suggested_cmd'` |
| `suggested_cmd` not in `{START, STOP, FLUSH, RST}` | `suggested_cmd='<value>' not in whitelist` |

Rejected responses are logged. No feedback is sent to the endpoint.

---

## Reference

### FSM states

| Value | Description |
|---|---|
| `IDLE` | System stopped. |
| `STARTING` | Startup sequence in progress. |
| `PRODUCING` | Active production. |
| `FLUSHING` | Flush cycle in progress. |
| `STOPPING` | Brief transitional state before `IDLE`. |
| `FAULT` | System locked. `fault_reason` is populated. |
| `UNKNOWN` | No state received yet. |

### Fault reasons

| Value | Description |
|---|---|
| `FLOW_LOW` | Permeate flow below threshold. |
| `RECOVERY_LOW` | Water recovery ratio below threshold. |
| `RECOVERY_HIGH` | Water recovery ratio above threshold. |
| `MAX_RETRIES` | Startup retry limit reached. |

### Commands

| Value | Description |
|---|---|
| `START` | Start production. |
| `STOP` | Stop production. |
| `FLUSH` | Run flush cycle. |
| `RST` | Clear fault and reset. |

### Inputs

| Field | Description |
|---|---|
| `demand` | Storage tank requesting production. |
| `raw_water_ok` | Feed water supply available. |
| `dose_ok` | Dosing system ready. |
| `pressure_switch` | Pressure switch state. |
| `feed_tank_level_low` | Feed tank level low alarm. |
| `spare2` | Spare digital input. |

### Outputs

| Field | Description |
|---|---|
| `pump_low` | Low-pressure pump. |
| `pump_high` | High-pressure pump. |
| `pump_inlet` | Inlet/well pump. |
| `pump_dose` | Dosing pump. |
| `valve_flush` | Flush valve. |
| `valve_inlet` | Inlet valve. |

### Sensor fields

| Field | Unit | Description |
|---|---|---|
| `flow_permeate_lpm` | L/min | Permeate flow rate. |
| `flow_reject_lpm` | L/min | Reject flow rate. |
| `pressure_in_bar` | bar | Inlet pressure. |
| `pressure_out_bar` | bar | Outlet pressure. |
| `volume_permeate_l` | L | Cumulative permeate volume. Resets on device reboot. |
| `volume_reject_l` | L | Cumulative reject volume. Resets on device reboot. |
| `tds_in_ppm` | ppm | Feed water TDS. |
| `tds_out_ppm` | ppm | Permeate TDS. |
| `tds_in_voltage` | V | Raw ADC voltage — feed TDS sensor. |
| `tds_out_voltage` | V | Raw ADC voltage — permeate TDS sensor. |

---

*KAIROX AI Integration API Contract v4.5 — 2026-06-08*