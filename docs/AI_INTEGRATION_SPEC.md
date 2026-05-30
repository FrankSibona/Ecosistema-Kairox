# KAIROX — AI Integration Specification

**Version:** 2.0  
**Audience:** External AI engine developer  
**Backend version:** KAIROX v3.4+  
**Last updated:** 2026-05-27

---

## 1. Integration objective

KAIROX is an industrial IoT platform for monitoring, automation, and diagnostics of reverse osmosis (RO) water treatment systems.

The backend periodically sends raw operational telemetry for each managed device to an external AI API. The AI engine analyzes this telemetry and returns a **suggested decision**. The AI does **not** directly control any hardware.

The execution chain is:

```
KAIROX backend → AI API (suggestion) → backend policy layer → optional execution
```

**The AI role is advisory.** The backend applies independent safety rules before acting on any suggestion. The AI can always be overridden, disabled, or ignored.

### Separation of concerns

| Responsibility | Owner |
|---------------|-------|
| Telemetry acquisition | ESP32 firmware |
| Persistence, buffering, polling rate control | KAIROX backend |
| Inference, anomaly detection, diagnostics | **AI engine** |
| Command execution safety | KAIROX backend policy layer |
| Physical actuation | ESP32 firmware |

The backend sends **raw sensor data only** — no pre-computed risk scores, health labels, or diagnostic summaries. All inference is the AI's responsibility.

---

## 2. System architecture

### Data flow

```
ESP32 firmware (1 Hz MQTT)
  └─ KAIROX Backend
       ├─ Persists all telemetry to PostgreSQL
       └─ AI Decision Engine (poll every AI_POLL_INTERVAL_SEC)
            └─ Builds telemetry window payload
                 └─ POST /your-endpoint  ← your API receives this
                      └─ AI response (JSON)
                           └─ Validation + Policy Layer
                                └─ CommandEngine (optional)
                                     └─ MQTT command → ESP32
```

### Polling model

**KAIROX controls the polling rate. The AI does not control when it is called.**

- Firmware publishes telemetry at 1 Hz continuously
- Backend persists every sample to the database
- Every `AI_POLL_INTERVAL_SEC` seconds, the backend builds a context payload and calls the AI API
- The AI receives a window of recent samples, not a single point snapshot

| Variable | Default | Description |
|----------|---------|-------------|
| `AI_POLL_INTERVAL_SEC` | `60` | Seconds between AI calls per device (min: 10) |
| `AI_WINDOW_SECONDS` | `60` | How far back the telemetry window looks |
| `AI_SAMPLE_PERIOD_SEC` | `1` | Minimum seconds between consecutive samples in window |
| `AI_WINDOW_MAX_SAMPLES` | `120` | Hard cap on samples per payload |

### AI modes per device

Each device has an `ai_mode` field configured by the KAIROX operator:

| Mode | AI called? | Command executed? | Description |
|------|-----------|-------------------|-------------|
| `OFF` | No | No | AI integration disabled for this device |
| `VIEWER` | Yes | No | Suggestions logged only — no execution ever |
| `AUTO` | Yes | If policy allows | Backend may execute if all checks pass |

In `VIEWER` mode, the backend logs every suggestion for audit and human review without taking action. This mode is used for observation and model validation before promoting to `AUTO`.

---

## 3. Expected AI endpoint

The KAIROX backend calls your API as follows:

| Parameter | Value |
|-----------|-------|
| **Method** | `POST` |
| **Content-Type** | `application/json` |
| **Accept** | `application/json` |
| **Timeout** | Configurable (default: **10 seconds**) |
| **Frequency** | Once per device per poll interval (default: **60 seconds**) |
| **Authentication** | To be agreed (Bearer token recommended) |

> The backend drops requests that exceed the timeout and logs the failure. **The AI must respond within the configured timeout.** If the AI is unavailable, the backend continues normally without executing any command.

---

## 4. Context payload sent by KAIROX

The backend POSTs the following JSON body on each call.

### Top-level schema

| Field | Type | Description |
|-------|------|-------------|
| `request_id` | `string` (UUID4) | Unique identifier for this request. Log it for end-to-end traceability. |
| `device_id` | `string` | Unique device identifier derived from hardware MAC address. |
| `timestamp` | `string` (ISO 8601 UTC) | Time of context generation on the backend. |
| `fsm_state` | `string` | Current FSM state. See [Section 7](#7-fsm-states). |
| `connectivity` | `"ONLINE"` \| `"OFFLINE"` \| `"UNKNOWN"` | `ONLINE` if last telemetry was received within 90 seconds. |
| `seconds_since_seen` | `integer` \| `null` | Seconds elapsed since last telemetry message. `null` if never seen. |
| `telemetry_window` | `object` | Rolling window of recent raw sensor samples. See sub-schema below. |

### `telemetry_window` sub-schema

| Field | Type | Description |
|-------|------|-------------|
| `window_seconds` | `integer` | Depth of the window in seconds (configured by KAIROX operator). |
| `sample_period_seconds` | `integer` | Minimum gap between consecutive samples. |
| `samples` | `array` | Ordered array of sample objects (ascending by `ts`). May be empty if device has no recent data. |

### Sample object schema

Each element of `samples` contains one time-stamped sensor reading. Any field may be `null` if the sensor did not produce data at that timestamp.

| Field | Type | Unit | Description |
|-------|------|------|-------------|
| `ts` | `string` (ISO 8601 UTC) | — | Timestamp of this sample |
| `flow_perm_lpm` | `float` \| `null` | L/min | Permeate (purified water) flow rate |
| `flow_rechazo_lpm` | `float` \| `null` | L/min | Reject (brine) flow rate |
| `pressure_in_bar` | `float` \| `null` | bar | Inlet pressure (membrane feed side) |
| `pressure_out_bar` | `float` \| `null` | bar | Outlet pressure (brine/concentrate side) |
| `pressure_membrane_bar` | `float` \| `null` | bar | Differential pressure across membrane (`pressure_in - pressure_out`). `null` if either pressure is missing. |
| `volume_perm_l` | `float` \| `null` | L | Cumulative permeate volume |
| `volume_rechazo_l` | `float` \| `null` | L | Cumulative reject volume |
| `tds_in_voltage` | `float` \| `null` | V | Raw TDS sensor voltage — feed water |
| `tds_out_voltage` | `float` \| `null` | V | Raw TDS sensor voltage — permeate |
| `tds_in_ppm` | `float` \| `null` | ppm | Feed water TDS — temperature-compensated |
| `tds_out_ppm` | `float` \| `null` | ppm | Permeate TDS — temperature-compensated |
| `recovery` | `float` \| `null` | 0–1 | `flow_perm / (flow_perm + flow_rechazo)`. `null` if flows unavailable. |
| `efficiency` | `float` \| `null` | 0–1 | `1 - (tds_out / tds_in)`. `null` if TDS unavailable or `tds_in = 0`. |
| `waste_pct` | `float` \| `null` | % | `flow_rechazo / (flow_perm + flow_rechazo) × 100`. |

### Realistic example payload

```json
{
  "request_id":         "f3a1c9d2-7e45-4b2a-a831-bc09ef112034",
  "device_id":          "ESP32_ECBA88C92DF4",
  "timestamp":          "2026-05-27T15:32:07Z",
  "fsm_state":          "PRODUCING",
  "connectivity":       "ONLINE",
  "seconds_since_seen": 2,
  "telemetry_window": {
    "window_seconds":        60,
    "sample_period_seconds": 1,
    "samples": [
      {
        "ts":                    "2026-05-27T15:31:08Z",
        "flow_perm_lpm":         1.85,
        "flow_rechazo_lpm":      4.10,
        "pressure_in_bar":       7.8,
        "pressure_out_bar":      0.4,
        "pressure_membrane_bar": 7.4,
        "volume_perm_l":         24887.0,
        "volume_rechazo_l":      10198.0,
        "tds_in_voltage":        1.82,
        "tds_out_voltage":       0.22,
        "tds_in_ppm":            410.0,
        "tds_out_ppm":           41.0,
        "recovery":              0.3109,
        "efficiency":            0.9,
        "waste_pct":             68.9
      },
      {
        "ts":                    "2026-05-27T15:31:09Z",
        "flow_perm_lpm":         1.87,
        "flow_rechazo_lpm":      4.12,
        "pressure_in_bar":       7.9,
        "pressure_out_bar":      0.4,
        "pressure_membrane_bar": 7.5,
        "volume_perm_l":         24888.0,
        "volume_rechazo_l":      10199.0,
        "tds_in_voltage":        1.83,
        "tds_out_voltage":       0.23,
        "tds_in_ppm":            412.0,
        "tds_out_ppm":           43.0,
        "recovery":              0.3122,
        "efficiency":            0.8956,
        "waste_pct":             68.8
      }
    ]
  }
}
```

> **Note on empty windows:** If the device has not sent telemetry recently (e.g., connectivity just restored), `samples` will be an empty array `[]`. The AI must handle this gracefully.

> **Note on null fields:** Any individual field within a sample may be `null` if the corresponding sensor did not produce data at that moment. Treat `null` the same as missing data.

---

## 5. Response contract

The AI must return a JSON object with **exactly** the following fields. **No additional fields are permitted.**

### Schema

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `decision` | `"NONE"` \| `"EXECUTE"` | Yes | Whether the AI suggests taking action. |
| `confidence` | `float` (0.0–1.0) | Yes | AI's confidence in its decision. Informational only — does not affect execution. |
| `reason` | `string` (1–500 chars) | Yes | Brief, human-readable explanation. Must not be empty. |
| `suggested_cmd` | `null` \| `"START"` \| `"STOP"` \| `"FLUSH"` \| `"RST"` | Conditional | Required when `decision = "EXECUTE"`. Must be `null` or absent when `decision = "NONE"`. |

### Strict rules

- `suggested_cmd` values are case-insensitive — `"flush"` and `"FLUSH"` are both accepted.
- `reason` must be a non-empty string of at most 500 characters. Do not include HTML or markdown.
- `confidence` is structural validation only — the backend does **not** apply a threshold to block execution. The AI is responsible for deciding when confidence warrants an `EXECUTE` decision.
- Any response with unexpected fields will be **rejected entirely**. The backend will log the violation and skip the cycle for that device.

### Response examples

#### Decision: no action

```json
{
  "decision":      "NONE",
  "confidence":    0.94,
  "reason":        "All 60 samples within normal parameters — flow stable, TDS nominal",
  "suggested_cmd": null
}
```

#### Decision: execute flush

```json
{
  "decision":      "EXECUTE",
  "confidence":    0.89,
  "reason":        "TDS output rising over last 60s (41→68 ppm), differential pressure increasing — preventive flush recommended",
  "suggested_cmd": "FLUSH"
}
```

#### Invalid response — will be rejected

```json
{
  "decision":      "EXECUTE",
  "confidence":    0.91,
  "reason":        "Stopping system",
  "suggested_cmd": "STOP",
  "model_version": "v2.1"
}
```

**Rejection reason:** field `model_version` is not in the allowed set. The backend rejects the entire response and logs `validation failed: unexpected fields in response: ['model_version']`.

---

## 6. Operational rules for the AI

These are recommendations for correct and safe AI behavior, not mechanical constraints applied by the backend:

1. **Do not assume absolute authority.** The backend applies independent policy checks. An `EXECUTE` decision may still be blocked — this is by design.

2. **Prioritize stability.** When in doubt, respond `NONE`. An unnecessary command is worse than a missed opportunity.

3. **Avoid oscillations.** Do not alternate `START` and `STOP` commands in successive cycles. The backend's anti-oscillation guard will block rapid reversals, but the AI should avoid generating them in the first place.

4. **Respect FSM semantics.** Each command is only valid in certain states (see [Section 8](#8-valid-commands)). Suggesting `START` when the system is already `PRODUCING` will be rejected by the policy layer.

5. **Respond `NONE` under uncertainty.** If connectivity is `OFFLINE`, `samples` is empty, or key metrics are all `null`, prefer `NONE` unless there is a specific evidence-backed reason to act.

6. **Use the full window.** The telemetry window is provided precisely so the AI can detect trends. A single anomalous sample is less meaningful than a consistent pattern across 30–60 samples.

7. **Keep reasons concise and actionable.** The `reason` field appears in operator logs. A useful reason is: `"TDS output rising 41→68 ppm over 60 samples — flush recommended"`. An unhelpful reason is: `"Anomaly detected"`. Include specific values when possible.

---

## 7. FSM states

The `fsm_state` field reflects the current state of the device's finite state machine.

| State | Description |
|-------|-------------|
| `IDLE` | System stopped, all outputs off. Waiting for demand signal. |
| `STARTING` | Startup sequence in progress: low-pressure pump on, filling lines. |
| `PRODUCING` | High-pressure pump active, generating permeate. Normal operating state. |
| `FLUSHING` | Post-cycle membrane flush in progress. Lasts approximately 60 seconds. |
| `STOPPING` | Graceful shutdown transition (brief, immediately moves to `IDLE`). |
| `FAULT` | System locked out after repeated startup failures. Requires `RST` to recover. |
| `UNKNOWN` | Backend has not received a state update since startup. Not a firmware state — a backend observation. |

---

## 8. Valid commands

| Command | Valid from states | Effect |
|---------|------------------|--------|
| `START` | `IDLE` | Initiates startup sequence → `STARTING` → `PRODUCING` |
| `STOP` | `STARTING`, `PRODUCING`, `FLUSHING` | Initiates graceful shutdown: from `PRODUCING` → `FLUSHING` → `IDLE`; from others → `IDLE` immediately |
| `FLUSH` | `PRODUCING` | Forces a membrane flush cycle → `FLUSHING` → `IDLE` |
| `RST` | `FAULT`, `STARTING`, `FLUSHING` | Clears fault state, resets retry counter → `IDLE`. **Not valid in `IDLE`** (backend will reject). |

> If the AI suggests a command for a state in which it is not valid, the backend policy layer will block execution and log the rejection reason.

---

## 9. Backend policy layer

Even if the AI returns `decision = "EXECUTE"`, the backend applies its own independent validation before calling `CommandEngine`. The AI suggestion may be **silently blocked** by any of the following checks:

| Check | Description |
|-------|-------------|
| **Whitelist** | `suggested_cmd` must be one of `START`, `STOP`, `FLUSH`, `RST`. |
| **FSM compatibility** | The command must be valid for the current `fsm_state` (see table above). |
| **Cooldown** | A minimum interval (default: **300 seconds**) must elapse between consecutive AI-initiated commands for the same device. |
| **Anti-oscillation** | Alternating between `START` and `STOP` within 60 seconds is blocked regardless of cooldown. |

The backend records every decision (including blocked ones) in its `ai_decisions` audit table with `exec_status = REJECTED` and the specific policy violation as `exec_result`. This allows post-hoc analysis of AI behavior.

**The AI does not receive feedback about whether its suggestion was executed.** If audit feedback is needed, it must be implemented at the application layer outside this protocol.

---

## 10. Example use cases

These are representative scenarios the AI is expected to handle using only the telemetry window.

### Trend detection — rising TDS, recommend flush

```
Input:
  fsm_state: PRODUCING
  samples: 60 samples over 60 seconds
    first 30 samples: tds_out_ppm ~40–45 ppm
    last 30 samples:  tds_out_ppm ~70–85 ppm (rising trend)
    pressure_membrane_bar stable at 7.4 bar

Expected response:
  decision: EXECUTE
  suggested_cmd: FLUSH
  confidence: 0.87
  reason: "TDS output rose from 42 to 83 ppm over last 60s — membrane scaling likely, flush recommended"
```

### Zero permeate flow in PRODUCING — stop

```
Input:
  fsm_state: PRODUCING
  samples: 30 samples
    flow_perm_lpm: 0.0 across all samples
    pressure_in_bar: 7.5 bar (pump running)

Expected response:
  decision: EXECUTE
  suggested_cmd: STOP
  confidence: 0.96
  reason: "Zero permeate flow for 30 consecutive samples while pump active — possible blockage or membrane failure"
```

### System stable — no action

```
Input:
  fsm_state: PRODUCING
  samples: 60 samples, all within normal bands
    flow_perm_lpm: 1.8–2.1
    tds_out_ppm: 38–44
    recovery: 0.30–0.33

Expected response:
  decision: NONE
  confidence: 0.95
  reason: "All parameters stable over 60 samples — flow, TDS, and recovery within normal range"
  suggested_cmd: null
```

### Device offline — no action

```
Input:
  connectivity: OFFLINE
  fsm_state:    UNKNOWN
  telemetry_window.samples: []

Expected response:
  decision: NONE
  confidence: 0.99
  reason: "Device offline — no telemetry available, cannot make a safe decision"
  suggested_cmd: null
```

### Fault state — reset

```
Input:
  fsm_state: FAULT
  connectivity: ONLINE
  samples: present but all from before the fault transition

Expected response:
  decision: EXECUTE
  suggested_cmd: RST
  confidence: 0.88
  reason: "Device in FAULT state — resetting to allow restart attempt"
```

---

## 11. Recommendations for the AI developer

| Topic | Recommendation |
|-------|---------------|
| **Latency** | Target < 3 seconds response time. Default timeout is 10 seconds; exceeding it counts as a failure and is logged. |
| **Temporal reasoning** | Use the full sample window for trend detection. A single anomalous sample is not sufficient evidence for an `EXECUTE` decision. |
| **Null tolerance** | Any metric in any sample may be `null`. The model must degrade gracefully — not error — when values are missing. |
| **Empty window** | `samples: []` means no recent telemetry. Always respond `NONE` when the window is empty. |
| **OFFLINE handling** | When `connectivity = "OFFLINE"`, always respond `NONE`. There is no actionable information. |
| **UNKNOWN state** | When `fsm_state = "UNKNOWN"`, respond `NONE` unless another strong signal warrants action. |
| **Logging** | Log the `request_id` from every incoming request. This enables KAIROX operators to correlate AI behavior with device events. |
| **Reason field** | Write reasons that a field technician can act on. Include specific metric values and trends: `"TDS rising 42→83 ppm over 60s"` not `"anomaly detected"`. |
| **Confidence calibration** | Calibrate confidence to reflect actual decision quality. Avoid always returning 0.99 — it reduces audit value. |
| **Stability** | For a given context input, the decision should be stable across calls. Stochastic outputs are harder to audit in industrial settings. |

---

## 12. Complete request/response examples

### Full request

```http
POST https://your-ai-api.example.com/decide
Content-Type: application/json

{
  "request_id":         "a1b2c3d4-0000-4000-8000-112233445566",
  "device_id":          "ESP32_ECBA88C92DF4",
  "timestamp":          "2026-05-27T15:32:07Z",
  "fsm_state":          "PRODUCING",
  "connectivity":       "ONLINE",
  "seconds_since_seen": 2,
  "telemetry_window": {
    "window_seconds":        60,
    "sample_period_seconds": 1,
    "samples": [
      {
        "ts":                    "2026-05-27T15:31:08Z",
        "flow_perm_lpm":         2.1,
        "flow_rechazo_lpm":      3.9,
        "pressure_in_bar":       7.4,
        "pressure_out_bar":      0.3,
        "pressure_membrane_bar": 7.1,
        "volume_perm_l":         24890.0,
        "volume_rechazo_l":      10200.0,
        "tds_in_voltage":        1.80,
        "tds_out_voltage":       0.21,
        "tds_in_ppm":            285.0,
        "tds_out_ppm":           10.9,
        "recovery":              0.3500,
        "efficiency":            0.9617,
        "waste_pct":             65.0
      }
    ]
  }
}
```

### Response: NONE

```http
HTTP/1.1 200 OK
Content-Type: application/json

{
  "decision":      "NONE",
  "confidence":    0.78,
  "reason":        "Flow and TDS stable across all samples — no action required",
  "suggested_cmd": null
}
```

### Response: EXECUTE

```http
HTTP/1.1 200 OK
Content-Type: application/json

{
  "decision":      "EXECUTE",
  "confidence":    0.85,
  "reason":        "TDS output trending up 10.9→62.3 ppm over 60 samples with stable inlet — flush recommended",
  "suggested_cmd": "FLUSH"
}
```

### Invalid response and why it is rejected

```json
{
  "decision":      "EXECUTE",
  "confidence":    0.92,
  "reason":        "Shutting down",
  "command":       "STOP"
}
```

**Rejection:** Field `command` is not in the allowed set `{decision, confidence, reason, suggested_cmd}`. Additionally, `suggested_cmd` is missing while `decision = "EXECUTE"`. The backend logs both violations and discards the response.

---

## 13. Future extensions (informational)

| Extension | Description |
|-----------|-------------|
| **Extended command set** | Additional commands may be added (e.g., `CALIBRATE`, `SERVICE_MODE`). All new commands will appear in the whitelist documentation before deployment. |
| **Longer history windows** | Window depth is configurable via `AI_WINDOW_SECONDS`. Deeper windows (10–60 minutes) may be enabled per device for degradation trend analysis. |
| **Per-device model adaptation** | Device-specific baseline learning to personalize thresholds per installation. |
| **AI feedback loop** | Optional structured feedback from KAIROX to the AI API indicating whether suggested commands were executed and what the outcome was. |
| **Multi-device fleet context** | Aggregate metrics across a client's fleet to detect systemic issues. |

---

## Appendix: Validation rules reference

| Rule | Check | Rejection message example |
|------|-------|--------------------------|
| Response type | Must be a JSON object | `response must be a JSON object` |
| No extra fields | Only `decision`, `confidence`, `reason`, `suggested_cmd` allowed | `unexpected fields in response: ['model_version']` |
| `decision` value | Must be `"NONE"` or `"EXECUTE"` | `decision='RESTART' not in allowed values` |
| `confidence` type | Float in `[0.0, 1.0]` | `confidence must be a float in [0.0, 1.0], got 1.5` |
| `reason` type | Non-empty string, max 500 chars | `reason must be a non-empty string` |
| `suggested_cmd` if EXECUTE | Must be present and non-empty | `decision=EXECUTE requires a non-empty 'suggested_cmd' field` |
| `suggested_cmd` whitelist | Must be one of `START`, `STOP`, `FLUSH`, `RST` | `suggested_cmd='REBOOT' not in whitelist` |

---

*This document describes the KAIROX AI integration protocol as of backend version 3.4+. For questions or clarifications, contact the KAIROX integration team.*
