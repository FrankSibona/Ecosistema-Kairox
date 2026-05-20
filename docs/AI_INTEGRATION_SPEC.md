# KAIROX — AI Integration Specification

**Version:** 1.0  
**Audience:** External AI engine developer  
**Backend version:** KAIROX v3.4  
**Last updated:** 2026-05-20

---

## 1. Integration objective

KAIROX is an industrial IoT platform for monitoring, automation, and diagnostics of reverse osmosis (RO) water treatment systems.

The backend periodically sends operational context for each managed device to an external AI API. The AI engine analyzes this context and returns a **suggested decision**. The AI does **not** directly control any hardware.

The execution chain is:

```
KAIROX backend → AI API (suggestion) → backend policy layer → optional execution
```

**The AI role is advisory.** The backend applies independent safety rules before acting on any suggestion. The AI can always be overridden, disabled, or ignored.

---

## 2. System architecture

### Data flow

```
ESP32 firmware
  └─ MQTT telemetry (1 Hz)
       └─ KAIROX Backend
            └─ AI Decision Engine (poll every N seconds)
                 └─ POST /your-endpoint  ← your API receives this
                      └─ AI response (JSON)
                           └─ Validation + Policy Layer
                                └─ CommandEngine (optional)
                                     └─ MQTT command → ESP32
```

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

### Schema

| Field | Type | Description |
|-------|------|-------------|
| `request_id` | `string` (UUID4) | Unique identifier for this specific request. Use for tracing across logs. |
| `device_id` | `string` | Unique device identifier derived from hardware MAC address. |
| `timestamp` | `string` (ISO 8601 UTC) | Time of context generation. |
| `fsm_state` | `string` | Current FSM state of the device. See [Section 7](#7-fsm-states). |
| `connectivity` | `"ONLINE"` \| `"OFFLINE"` | `ONLINE` if last telemetry was received within 90 seconds. |
| `risk_level` | `"LOW"` \| `"MEDIUM"` \| `"HIGH"` \| `"CRITICAL"` \| `"UNKNOWN"` | Composite risk assessment computed by KAIROX diagnostics engine. |
| `health_status` | `"HEALTHY"` \| `"WARNING"` \| `"CRITICAL"` \| `"UNKNOWN"` | Device health category. |
| `health_message` | `string` \| `null` | Human-readable description of the current health condition. May be `null`. |
| `seconds_since_seen` | `integer` \| `null` | Seconds elapsed since last telemetry message. `null` if never seen. |
| `metrics` | `object` | Raw sensor readings and computed KPIs. See sub-schema below. |
| `active_alerts` | `array` | Diagnostic alerts active in the last hour. May be empty. |

### `metrics` sub-schema

| Field | Type | Unit | Description |
|-------|------|------|-------------|
| `flow_perm_lpm` | `float` \| `null` | L/min | Permeate (purified water) flow rate |
| `flow_rechazo_lpm` | `float` \| `null` | L/min | Reject (brine) flow rate |
| `pressure_in_bar` | `float` \| `null` | bar | Inlet pressure (membrane feed) |
| `pressure_out_bar` | `float` \| `null` | bar | Outlet pressure (brine side) |
| `pressure_membrane` | `float` \| `null` | bar | Membrane differential pressure (aggregated) |
| `volume_perm_l` | `float` \| `null` | L | Total cumulative permeate volume |
| `volume_rechazo_l` | `float` \| `null` | L | Total cumulative reject volume |
| `tds_in_raw` | `float` \| `null` | raw | Feed water TDS (conductivity raw value) |
| `tds_out_raw` | `float` \| `null` | raw | Permeate TDS (conductivity raw value) |
| `recovery` | `float` \| `null` | 0–1 | Water recovery ratio: permeate / (permeate + reject) |
| `efficiency` | `float` \| `null` | 0–1 | Membrane rejection efficiency |
| `risk_score` | `float` \| `null` | 0–100 | Numeric risk score (backing the risk_level category) |
| `liters_today` | `float` \| `null` | L | Permeate produced today |
| `waste_pct` | `float` \| `null` | % | Waste water percentage today |

### `active_alerts` array element schema

```json
{
  "code":     "MEMBRANE_FOULING",
  "message":  "High differential pressure with low flow",
  "severity": "WARNING"
}
```

### Realistic example payload

```json
{
  "request_id":         "f3a1c9d2-7e45-4b2a-a831-bc09ef112034",
  "device_id":          "ESP32_ECBA88C92DF4",
  "timestamp":          "2026-05-20T14:32:07.441Z",
  "fsm_state":          "PRODUCING",
  "connectivity":       "ONLINE",
  "risk_level":         "HIGH",
  "health_status":      "WARNING",
  "health_message":     "Membrane fouling detected: rising differential pressure over 4 hours",
  "seconds_since_seen": 3,
  "metrics": {
    "flow_perm_lpm":    1.2,
    "flow_rechazo_lpm": 4.8,
    "pressure_in_bar":  8.6,
    "pressure_out_bar": 0.4,
    "pressure_membrane": 8.2,
    "volume_perm_l":    24150.5,
    "volume_rechazo_l": 9820.3,
    "tds_in_raw":       1.8,
    "tds_out_raw":      0.04,
    "recovery":         0.20,
    "efficiency":       0.978,
    "risk_score":       74.0,
    "liters_today":     312.0,
    "waste_pct":        80.0
  },
  "active_alerts": [
    {
      "code":     "MEMBRANE_FOULING",
      "message":  "Differential pressure rising, flow declining",
      "severity": "WARNING"
    },
    {
      "code":     "LOW_RECOVERY",
      "message":  "Recovery below threshold (20% vs target 65%)",
      "severity": "WARNING"
    }
  ]
}
```

> **Note on missing fields:** Some metrics may be `null` if the device has not yet produced data for that field (e.g., a freshly connected device). The AI must handle `null` values gracefully without raising errors.

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
  "decision":     "NONE",
  "confidence":   0.94,
  "reason":       "System operating within normal parameters",
  "suggested_cmd": null
}
```

#### Decision: execute flush

```json
{
  "decision":     "EXECUTE",
  "confidence":   0.89,
  "reason":       "Membrane fouling indicators rising — preventive flush recommended",
  "suggested_cmd": "FLUSH"
}
```

#### Invalid response — will be rejected

```json
{
  "decision":     "EXECUTE",
  "confidence":   0.91,
  "reason":       "Stopping system",
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

5. **Respond `NONE` under uncertainty.** If connectivity is `OFFLINE`, health is `UNKNOWN`, or key metrics are missing, prefer `NONE` unless there is a specific, evidence-backed reason to act.

6. **Keep reasons concise and actionable.** The `reason` field appears in operator logs. A useful reason is: `"TDS output 0.08 (threshold 0.05) — flush recommended"`. An unhelpful reason is: `"Anomaly detected"`.

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
| `OFFLINE` / `UNKNOWN` | `fsm_state` will be `"UNKNOWN"` if the backend has not received a state update since startup. Not a firmware state — a backend observation. |

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

These are representative scenarios the AI is expected to handle:

### HIGH risk — stop system

```
Input:
  fsm_state:    PRODUCING
  risk_level:   CRITICAL
  health_message: "No permeate flow detected for 45 seconds while pump active"
  metrics.flow_perm_lpm: 0.0

Expected response:
  decision: EXECUTE
  suggested_cmd: STOP
  confidence: 0.95
  reason: "Zero permeate flow in PRODUCING state for >45s — system failure, stopping"
```

### Elevated output TDS — flush

```
Input:
  fsm_state:    PRODUCING
  metrics.tds_out_raw: 0.09  (threshold ~0.05)
  active_alerts: [{"code": "MEMBRANE_SCALING", ...}]

Expected response:
  decision: EXECUTE
  suggested_cmd: FLUSH
  confidence: 0.82
  reason: "Output TDS elevated at 0.09, membrane scaling alert active — flush recommended"
```

### System stable — no action

```
Input:
  fsm_state:    PRODUCING
  risk_level:   LOW
  metrics.recovery: 0.64
  metrics.efficiency: 0.97
  active_alerts: []

Expected response:
  decision: NONE
  confidence: 0.97
  reason: "All parameters within normal range"
  suggested_cmd: null
```

### Persistent fault — reset

```
Input:
  fsm_state:    FAULT
  health_status: CRITICAL
  health_message: "Maximum retries exceeded — system locked"

Expected response:
  decision: EXECUTE
  suggested_cmd: RST
  confidence: 0.88
  reason: "System in FAULT state — resetting to allow restart attempt"
```

### Device offline — no action

```
Input:
  connectivity: OFFLINE
  fsm_state:    UNKNOWN

Expected response:
  decision: NONE
  confidence: 0.99
  reason: "Device offline — insufficient data to make a safe decision"
  suggested_cmd: null
```

---

## 11. Recommendations for the AI developer

| Topic | Recommendation |
|-------|---------------|
| **Latency** | Target < 3 seconds response time. The default timeout is 10 seconds; exceeding it counts as a failure. |
| **Determinism** | For a given context input, the decision should be stable across calls. Stochastic outputs are harder to audit and debug in industrial settings. |
| **Null tolerance** | Any metric may be `null`. The model must degrade gracefully — not error — when values are missing. |
| **OFFLINE handling** | When `connectivity = "OFFLINE"`, always respond `NONE`. There is no actionable information. |
| **UNKNOWN state** | When `fsm_state = "UNKNOWN"`, respond `NONE` unless another strong signal warrants action. |
| **Logging** | Log the `request_id` from every incoming request. This enables KAIROX operators to correlate AI behavior with device events. |
| **Reason field** | Write reasons that a field technician can act on. Include the specific metric value that triggered the decision when applicable. |
| **Confidence calibration** | Calibrate confidence to reflect actual decision quality. Avoid always returning 0.99 — it reduces audit value. |
| **Avoid repeat commands** | If the same command has been suggested in consecutive cycles without state change, reconsider whether the action was appropriate or whether the AI is stuck in a loop. |

---

## 12. Complete request/response examples

### Full request

```http
POST https://your-ai-api.example.com/decide
Content-Type: application/json

{
  "request_id":         "a1b2c3d4-0000-4000-8000-112233445566",
  "device_id":          "ESP32_ECBA88C92DF4",
  "timestamp":          "2026-05-20T14:32:07.441Z",
  "fsm_state":          "PRODUCING",
  "connectivity":       "ONLINE",
  "risk_level":         "MEDIUM",
  "health_status":      "WARNING",
  "health_message":     "Efficiency declining over last 2 hours",
  "seconds_since_seen": 2,
  "metrics": {
    "flow_perm_lpm":     2.1,
    "flow_rechazo_lpm":  3.9,
    "pressure_in_bar":   7.4,
    "pressure_out_bar":  0.3,
    "pressure_membrane": 7.1,
    "volume_perm_l":     24890.0,
    "volume_rechazo_l":  10200.0,
    "tds_in_raw":        1.6,
    "tds_out_raw":       0.06,
    "recovery":          0.35,
    "efficiency":        0.963,
    "risk_score":        48.0,
    "liters_today":      890.0,
    "waste_pct":         65.0
  },
  "active_alerts": [
    {
      "code":     "LOW_EFFICIENCY",
      "message":  "Efficiency below warning threshold (0.963 vs 0.970)",
      "severity": "WARNING"
    }
  ]
}
```

### Response: NONE

```http
HTTP/1.1 200 OK
Content-Type: application/json

{
  "decision":      "NONE",
  "confidence":    0.78,
  "reason":        "Efficiency marginally below threshold but trend is stable — monitoring",
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
  "reason":        "Output TDS at 0.06 exceeds 0.05 threshold with declining efficiency — flush recommended",
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

The current integration is intentionally minimal. The following extensions are under consideration for future versions:

| Extension | Description |
|-----------|-------------|
| **Extended command set** | Additional commands may be added (e.g., `CALIBRATE`, `SERVICE_MODE`). All new commands will appear in the whitelist documentation before deployment. |
| **Temporal history window** | A rolling window of recent telemetry samples (e.g., last 10 minutes) may be included in the context payload for trend analysis. |
| **Fault prediction** | Pre-fault detection based on gradual sensor degradation patterns before the system enters `FAULT`. |
| **Predictive maintenance** | Estimating membrane replacement intervals based on efficiency degradation curves. |
| **Multi-device fleet context** | Aggregate metrics across a client's fleet to detect systemic issues. |
| **Per-device model adaptation** | Device-specific baseline learning to personalize thresholds per installation. |
| **AI feedback loop** | Optional structured feedback from KAIROX to the AI API indicating whether suggested commands were executed and what the outcome was. |

---

## Appendix: Validation rules reference

This table summarizes all checks the backend applies to an AI response before it is considered valid:

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

*This document describes the KAIROX AI integration protocol as of backend version 3.4. For questions or clarifications, contact the KAIROX integration team.*
