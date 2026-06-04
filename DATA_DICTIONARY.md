# KAIROX — Data Dictionary

**Version:** 2.0  
**Date:** 2026-05-30  
**Status:** Definitive — source of truth for all layers  

> This document defines the authoritative field names for KAIROX across firmware, MQTT, PostgreSQL, Python backend, AI payload, and Grafana dashboards. No field in any layer should deviate from this dictionary.

---

## Naming Convention

| Rule | Example |
|---|---|
| All names in English | `flow_reject_lpm` not `flow_rechazo_lpm` |
| `snake_case` only | `raw_water_ok` not `rawWaterOk` |
| Unit suffix on physical measurements | `_bar`, `_lpm`, `_l`, `_ppm`, `_v`, `_kw`, `_m3` |
| Spell out abbreviations | `permeate` not `perm`, `reject` not `rechazo` |
| Consistent pair naming | `*_permeate_*` / `*_reject_*` throughout |
| Boolean flags: `noun_ok` or `noun_active` | `raw_water_ok`, `dose_ok` |

---

## Migration Status

| Status | Meaning |
|---|---|
| ✅ Active | Current name, no change needed |
| 🔄 Renamed | Old name → new name applied in this migration |
| 🔄 Renamed (Phase 2) | Changed in firmware — requires reflash |
| ⚠️ Fixed | Was broken/inconsistent, now corrected |

---

## Section 1 — Process Telemetry

**MQTT topic:** `fyntek/{device_id}/process`  
**DB table:** `telemetry_process`  
**Also in:** `metrics`, `device_status`, `device_baseline`, AI payload, Grafana

| Old Name | New Name | Status | Type | Unit | Description | Layers |
|---|---|---|---|---|---|---|
| `device_id` | `device_id` | ✅ Active | string | — | Unique device identifier (ESP32 MAC-derived) | All |
| `fw_version` | `fw_version` | ✅ Active | string | — | Firmware version string | FW, DB, API |
| `ts` | `ts` | ✅ Active | integer | Unix epoch | Firmware-side timestamp (NTP-synced). Distinct from DB `time` (ingestion). | FW, MQTT |
| `flow_perm_lpm` | `flow_permeate_lpm` | 🔄 Renamed | float | L/min | Permeate (purified water) flow rate | FW, DB, Backend, AI, Grafana |
| `flow_rechazo_lpm` | `flow_reject_lpm` | 🔄 Renamed (Phase 2) | float | L/min | Reject (brine/concentrate) flow rate | FW, DB, Backend, AI, Grafana |
| `pressure_membrane_bar` | `pressure_membrane_bar` | ✅ Active | float | bar | High-pressure side feed pressure (pump outlet, membrane inlet) | FW, DB, Backend, AI |
| `pressure_brine_bar` | `pressure_brine_bar` | ✅ Active | float | bar | Brine/concentrate side pressure (membrane outlet) | FW, DB, Backend, AI |
| `volume_perm_l` | `volume_permeate_l` | 🔄 Renamed | float | L | Cumulative permeate volume (totalizer, resets on device reboot) | FW, DB, Backend, AI, Grafana |
| `volume_rechazo_l` | `volume_reject_l` | 🔄 Renamed (Phase 2) | float | L | Cumulative reject volume (totalizer) | FW, DB, Backend, AI, Grafana |

**Derived fields (computed by backend, stored in `metrics`):**

| Name | Status | Type | Unit | Formula | Description |
|---|---|---|---|---|---|
| `recovery` | ✅ Active | float | 0–1 | `flow_permeate / (flow_permeate + flow_reject)` | Water recovery ratio |
| `rejection_ratio` | ✅ Active | float | ratio | `flow_reject / flow_permeate` | Reject-to-permeate flow ratio |
| `delta_pressure_bar` | ✅ Active | float | bar | `pressure_membrane - pressure_brine` | Differential pressure across membrane |
| `efficiency` | ✅ Active | float | 0–1 | `1 - (tds_out / tds_in)` | TDS rejection efficiency |
| `cost_per_liter` | ✅ Active | float | currency/L | `(pump_kw * kwh_cost + water_cost) / flow_permeate` | Operating cost per liter produced |

---

## Section 2 — Quality Telemetry

**MQTT topic:** `fyntek/{device_id}/quality`  
**DB table:** `telemetry_quality`  
**Also in:** AI payload, Grafana

| Name | Status | Type | Unit | Description | Layers |
|---|---|---|---|---|---|
| `tds_in_voltage` | ✅ Active | float | V | Raw ADC voltage from feed water TDS sensor (DFRobot SEN0244) | FW, DB, Backend, AI |
| `tds_out_voltage` | ✅ Active | float | V | Raw ADC voltage from permeate TDS sensor | FW, DB, Backend, AI |
| `tds_in_ppm` | ✅ Active | float | ppm | Feed water TDS — temperature-compensated via polynomial | FW, DB, Backend, AI, Grafana |
| `tds_out_ppm` | ✅ Active | float | ppm | Permeate TDS — temperature-compensated | FW, DB, Backend, AI, Grafana |

> `tds_out_raw` ⚠️ Fixed — column does not exist in DB. Two Grafana panels referenced it erroneously; corrected to `tds_out_ppm`.

---

## Section 3 — FSM State

**MQTT topic:** `fyntek/{device_id}/state`  
**DB table:** `telemetry_state`

| Name | Status | Type | Values | Description |
|---|---|---|---|---|
| `state` | ✅ Active | string | `IDLE`, `STARTING`, `PRODUCING`, `FLUSHING`, `STOPPING`, `FAULT` | Current FSM state name |
| `state_numeric` | ✅ Active | integer | 0–5 | FSM state as integer (for Grafana numeric panels) |
| `running` | ✅ Active | boolean | — | True if high-pressure pump is active |
| `retry` (FW) / `retry_count` (DB) | ✅ Active | integer | 0–MAX_RETRIES | Startup retry counter. Firmware sends `retry`, DB stores `retry_count`. |

---

## Section 4 — Digital Inputs

**MQTT topic:** `fyntek/{device_id}/inputs`  
**DB table:** `telemetry_inputs`  
**Also in:** Backend diagnostics, API v1 context, Grafana

| Old Name | New Name | Status | Type | Description |
|---|---|---|---|---|
| `demand` | `demand` | ✅ Active | boolean | Water demand signal (float switch or external trigger) |
| `crudo_ok` | `raw_water_ok` | 🔄 Renamed (Phase 2) | boolean | Raw (feed) water availability — tank float switch |
| `dose_ok` | `dose_ok` | ✅ Active | boolean | Chemical dosing system status |
| `presostato` | `pressure_switch` | 🔄 Renamed (Phase 2) | boolean | High-pressure safety switch |
| `reserva1` / `flotante_pozo` | `feed_tank_level_low` | 🔄 Renamed (Phase 2) | boolean | Feed tank/cistern low level — `TRUE` = low level detected → well pump ON. INPUT_PULLUP: float switch hanging = HIGH = low level. |
| `reserva2` | `spare2` | 🔄 Renamed (Phase 2) | boolean | Spare digital input D6 — unassigned |

---

## Section 5 — Digital Outputs

**MQTT topic:** `fyntek/{device_id}/outputs`  
**DB table:** `telemetry_outputs`

| Name | Status | Type | Description |
|---|---|---|---|
| `pump_low` | ✅ Active | boolean | Low-pressure booster pump relay |
| `pump_high` | ✅ Active | boolean | High-pressure RO pump relay |
| `pump_inlet` | ✅ Active | boolean | Inlet solenoid valve |
| `pump_dose` | ✅ Active | boolean | Chemical dosing pump relay |
| `valve_flush` | ✅ Active | boolean | Flush valve relay |
| `valve_inlet` | ✅ Active | boolean | Inlet valve relay |

---

## Section 6 — Commands

**MQTT topic:** `fyntek/{device_id}/cmd` (backend → device)  
**MQTT topic:** `fyntek/{device_id}/ack` (device → backend)  
**DB table:** `device_commands`

| Name | Status | Type | Description |
|---|---|---|---|
| `command_id` | ✅ Active | string (UUID4) | Unique command identifier |
| `cmd` | ✅ Active | string | Command: `START`, `STOP`, `FLUSH`, `RST` |
| `deadline_at` | ✅ Active | integer (Unix) | Command expiry timestamp |
| `status` | ✅ Active | string | ACK status: `EXECUTED`, `REJECTED` |
| `reason` | ✅ Active | string | Rejection reason (when status=REJECTED) |

---

## Section 7 — Device Configuration

**MQTT topic:** `fyntek/{device_id}/config` (backend → device, retained)  
**DB table:** `device_config`

| Name | Status | Type | Unit | Description |
|---|---|---|---|---|
| `flow_factor_1` | ✅ Active | float | pulses/L | Flow meter 1 calibration (permeate side) |
| `flow_factor_2` | ✅ Active | float | pulses/L | Flow meter 2 calibration (reject side) |
| `tds_temperature` | ✅ Active | float | °C | TDS temperature compensation reference |
| `pump_power_kw` | ✅ Active | float | kW | High-pressure pump rated power |
| `cost_kwh` | ✅ Active | float | $/kWh | Electricity cost per kWh |
| `cost_water_m3` | ✅ Active | float | $/m³ | Feed water cost per m³ |
| `daily_target_liters` | ✅ Active | float | L | Daily permeate production target |
| `target_recovery` | ✅ Active | float | 0–1 | Target water recovery ratio |
| `target_efficiency` | ✅ Active | float | 0–1 | Target membrane rejection efficiency |

---

## Section 8 — Device Status (Aggregated)

**DB table:** `device_status` (one row per device, upserted on every message)

| Old Name | New Name | Status | Type | Unit | Description |
|---|---|---|---|---|---|
| `device_id` | `device_id` | ✅ Active | string | — | Device identifier |
| `last_seen` | `last_seen` | ✅ Active | timestamptz | — | Last telemetry received |
| `online` | `online` | ✅ Active | boolean | — | Derived: `last_seen < 90s ago` |
| `state` | `state` | ✅ Active | string | — | Latest FSM state |
| `flow_perm_lpm` | `flow_permeate_lpm` | 🔄 Renamed | float | L/min | Latest permeate flow (snapshot) |
| `pressure_membrane` | `pressure_membrane_bar` | ⚠️ Fixed | float | bar | Latest feed pressure — was missing `_bar` unit suffix |
| `recovery` | `recovery` | ✅ Active | float | 0–1 | Latest computed recovery |
| `efficiency` | `efficiency` | ✅ Active | float | 0–1 | Latest computed efficiency |
| `last_severity` | `last_severity` | ✅ Active | string | — | Latest diagnostic severity |
| `last_diag_code` | `last_diag_code` | ✅ Active | string | — | Latest diagnostic code |
| `biz_liters_today` | `biz_liters_today` | ✅ Active | float | L | Permeate produced today |
| `biz_target_liters` | `biz_target_liters` | ✅ Active | float | L | Daily target |
| `biz_fulfillment_pct` | `biz_fulfillment_pct` | ✅ Active | float | % | Target fulfillment percentage |
| `biz_waste_liters_today` | `biz_waste_liters_today` | ✅ Active | float | L | Reject volume today |
| `biz_waste_pct` | `biz_waste_pct` | ✅ Active | float | % | Reject as % of total water processed |
| `biz_risk_level` | `biz_risk_level` | ✅ Active | string | — | Composite risk: LOW/MEDIUM/HIGH/CRITICAL |
| `biz_risk_score` | `biz_risk_score` | ✅ Active | float | 0–100 | Numeric risk score |
| `biz_degradation_pct` | `biz_degradation_pct` | ✅ Active | float | % | Efficiency degradation vs baseline |

---

## Section 9 — Alerts

**DB table:** `alerts`

| Name | Status | Type | Description |
|---|---|---|---|
| `id` | ✅ Active | bigint | Alert row ID |
| `device_id` | ✅ Active | string | Device that triggered the alert |
| `code` | ✅ Active | string | Alert code (e.g. `DEVICE_OFFLINE`, `HIGH_TDS_OUTPUT`) |
| `severity` | ✅ Active | string | `INFO`, `WARNING`, `CRITICAL` |
| `message` | ✅ Active | string | Human-readable description |
| `active` | ✅ Active | boolean | True = still active, False = resolved |
| `created_at` | ✅ Active | timestamptz | When alert was first created |
| `updated_at` | ✅ Active | timestamptz | Last update (message refresh) |
| `resolved_at` | ✅ Active | timestamptz | When condition cleared |
| `last_notified_at` | ✅ Active | timestamptz | Last Telegram/email notification sent |
| `notification_count` | ✅ Active | integer | Total notifications sent for this alert |

**Alert codes (MVP):**

| Code | Severity | Trigger |
|---|---|---|
| `DEVICE_OFFLINE` | CRITICAL | No telemetry > 90s |
| `DEVICE_RECONNECTED` | INFO | Reconnection detected |
| `HIGH_TDS_OUTPUT` | WARNING | `tds_out_ppm` > 80 ppm (trigger 30s) |
| `LOW_PRESSURE` | WARNING | `pressure_membrane_bar` < 2.0 bar in PRODUCING (trigger 30s) |
| `HIGH_PRESSURE` | CRITICAL | `pressure_membrane_bar` > 9.0 bar (immediate) |
| `NO_PERMEATE_FLOW` | CRITICAL | `flow_permeate_lpm` < 0.05 in PRODUCING > 30s |
| `LOW_EFFICIENCY` | WARNING | `efficiency` < 0.85 (trigger 60s) |
| `SENSOR_INVALID` | WARNING | NaN/Inf or value outside physical range |

---

## Section 10 — AI Payload (telemetry_window samples)

Sent by backend to AI API every `AI_POLL_INTERVAL_SEC` seconds.

| Name | Status | Type | Unit | Description |
|---|---|---|---|---|
| `ts` | ✅ Active | string (ISO 8601) | — | Sample timestamp |
| `flow_permeate_lpm` | 🔄 Renamed | float | L/min | Permeate flow rate |
| `flow_reject_lpm` | 🔄 Renamed | float | L/min | Reject flow rate |
| `pressure_in_bar` | ✅ Active | float | bar | Inlet pressure (= `pressure_membrane_bar`) |
| `pressure_out_bar` | ✅ Active | float | bar | Outlet pressure (= `pressure_brine_bar`) |
| `pressure_membrane_bar` | ✅ Active | float | bar | Differential pressure (= in − out) |
| `volume_permeate_l` | 🔄 Renamed | float | L | Cumulative permeate volume |
| `volume_reject_l` | 🔄 Renamed | float | L | Cumulative reject volume |
| `tds_in_voltage` | ✅ Active | float | V | Feed TDS raw voltage |
| `tds_out_voltage` | ✅ Active | float | V | Permeate TDS raw voltage |
| `tds_in_ppm` | ✅ Active | float | ppm | Feed TDS (temperature-compensated) |
| `tds_out_ppm` | ✅ Active | float | ppm | Permeate TDS (temperature-compensated) |
| `recovery` | ✅ Active | float | 0–1 | Water recovery ratio |
| `efficiency` | ✅ Active | float | 0–1 | TDS rejection efficiency |
| `waste_pct` | ✅ Active | float | % | Reject as % of total flow |

---

## Section 11 — Multi-Tenant

**DB tables:** `clients`, `devices`

| Name | Status | Type | Description |
|---|---|---|---|
| `client_id` | ✅ Active | integer FK | Links device to a client |
| `grafana_org_id` | ✅ Active | integer | Grafana org ID for this client |
| `display_name` | ✅ Active | string | Human-readable device name shown in UI |
| `enabled` | ✅ Active | boolean | If false, device is hidden from all queries |
| `ai_mode` | ✅ Active | string | `OFF`, `VIEWER`, `AUTO` |

---

## Section 12 — Phase Summary

### Phase 1 (implemented — backend compatible with old and new firmware)

Backend adds compatibility aliases. The existing firmware (publishing Spanish names) continues to work. New names go to DB.

| Old MQTT field | New internal name | Tables updated |
|---|---|---|
| `flow_rechazo_lpm` | `flow_reject_lpm` | `telemetry_process`, `metrics` |
| `volume_rechazo_l` | `volume_reject_l` | `telemetry_process` |
| `flow_perm_lpm` | `flow_permeate_lpm` | `telemetry_process`, `metrics`, `device_status` |
| `volume_perm_l` | `volume_permeate_l` | `telemetry_process` |
| `crudo_ok` | `raw_water_ok` | `telemetry_inputs` |
| `presostato` | `pressure_switch` | `telemetry_inputs` |
| `reserva1` | `spare1` | `telemetry_inputs` |
| `reserva2` | `spare2` | `telemetry_inputs` |
| `pressure_membrane` (device_status) | `pressure_membrane_bar` | `device_status` |

### Phase 2 (pending — requires firmware reflash)

Update `comms.cpp` string literals to publish new field names. Remove backend aliases.

### Phase 3 (after verifying Phase 2)

Remove `# PHASE1_COMPAT` aliases from `app.py`.

---

*KAIROX Data Dictionary v2.0 — 2026-05-30*
