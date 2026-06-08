"""
ai_client.py — KAIROX external AI integration client

Responsibilities:
  - Build context payload from current DB state + rolling telemetry window
  - POST context to external AI API with configurable timeout
  - Validate AI response strictly (no unexpected fields allowed)
  - Normalize suggested_cmd to uppercase before validation
  - Provide a policy layer that decides whether to execute a suggested command

Does NOT:
  - Execute commands (AIDecisionEngine in app.py is the executor)
  - Write to the database
  - Hold global mutable state

Payload design:
  KAIROX sends raw telemetry — no backend-inferred diagnostics.
  The AI receives: connectivity state, FSM state, and a rolling window of raw
  sensor samples. Inference (risk, health, anomaly detection) is the AI's job.

Naming convention: "suggested_cmd" is used consistently everywhere —
in the response JSON, logs, and DB inserts.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import requests

log = logging.getLogger("ro_backend")

# Commands the AI is allowed to suggest. Enforced here AND in the DB CHECK.
AI_COMMAND_WHITELIST: frozenset = frozenset({"START", "STOP", "FLUSH", "RST"})
AI_VALID_DECISIONS:   frozenset = frozenset({"NONE", "EXECUTE"})

# Exact set of keys a valid AI response may contain (strict — extras rejected).
_ALLOWED_RESPONSE_KEYS  = frozenset({"decision", "confidence", "reason", "suggested_cmd"})
_REQUIRED_RESPONSE_KEYS = ("decision", "confidence", "reason")

# FSM states that permit each command (mirrors isValidTransition in firmware).
_FSM_ALLOWED_STATES: dict = {
    "START": frozenset({"IDLE"}),
    "STOP":  frozenset({"STARTING", "PRODUCING", "FLUSHING"}),
    "FLUSH": frozenset({"PRODUCING"}),
    "RST":   frozenset({"FAULT", "STARTING", "FLUSHING"}),
}


# ── Telemetry window builder ────────────────────────────────────────────────────

def _build_telemetry_window(
    device_id: str,
    db: Any,
    window_seconds: int,
    sample_period_sec: int,
    max_samples: int,
) -> dict:
    """
    Query raw telemetry tables and return a time-windowed sample list.

    Strategy:
      - Fetch process + quality rows for last window_seconds (time-bounded SQL)
      - Index quality rows by integer second for O(1) nearest-match lookup
      - Apply sample_period_sec filter in Python (skip rows too close to last kept)
      - Hard cap at max_samples
    """
    # DB fetch limit: enough rows to cover the window before Python sampling
    db_limit = min(max_samples * max(1, sample_period_sec) + 20, 10_000)

    proc_rows = db.fetchall(
        """
        SELECT time, flow_permeate_lpm, flow_reject_lpm,
               pressure_membrane_bar, pressure_brine_bar,
               volume_permeate_l, volume_reject_l
        FROM telemetry_process
        WHERE device_id = %s
          AND time > NOW() - MAKE_INTERVAL(secs => %s)
        ORDER BY time ASC
        LIMIT %s
        """,
        (device_id, window_seconds, db_limit),
    )

    qual_rows = db.fetchall(
        """
        SELECT time, tds_in_voltage, tds_out_voltage, tds_in_ppm, tds_out_ppm
        FROM telemetry_quality
        WHERE device_id = %s
          AND time > NOW() - MAKE_INTERVAL(secs => %s)
        ORDER BY time ASC
        LIMIT %s
        """,
        (device_id, window_seconds, db_limit),
    )

    # Index quality rows by integer second (UNIX epoch) for fast nearest lookup
    qual_index: Dict[int, tuple] = {}
    for r in qual_rows:
        qt = r[0]
        if qt.tzinfo is None:
            qt = qt.replace(tzinfo=timezone.utc)
        qual_index[int(qt.timestamp())] = r

    samples: List[dict] = []
    last_ts: Optional[datetime] = None

    for r in proc_rows:
        ts = r[0]
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)

        # Sample period filter
        if last_ts is not None:
            if (ts - last_ts).total_seconds() < sample_period_sec:
                continue

        # Nearest quality row (search ±3s around this timestamp)
        ts_sec = int(ts.timestamp())
        qual = None
        for delta in (0, -1, 1, -2, 2, -3, 3):
            qual = qual_index.get(ts_sec + delta)
            if qual:
                break

        # DB field mapping:
        #   pressure_membrane_bar → inlet pressure (feed side of membrane)
        #   pressure_brine_bar    → brine/concentrate pressure
        #   pressure_membrane_bar (in output) → differential = in - brine
        p_in    = r[3]  # inlet (DB column named pressure_membrane_bar)
        p_brine = r[4]
        p_diff  = (round(p_in - p_brine, 3)
                   if p_in is not None and p_brine is not None else None)

        flow_p = r[1]
        flow_r = r[2]
        total_flow = (flow_p or 0.0) + (flow_r or 0.0)

        recovery   = None
        waste_pct  = None
        efficiency = None

        if flow_p is not None and flow_r is not None and total_flow > 0:
            recovery  = round(flow_p / total_flow, 4)
            waste_pct = round((flow_r / total_flow) * 100, 2)

        tds_in_v = tds_out_v = tds_in_ppm = tds_out_ppm = None
        if qual:
            tds_in_v   = qual[1]
            tds_out_v  = qual[2]
            tds_in_ppm = qual[3]
            tds_out_ppm = qual[4]

        if tds_in_ppm and tds_in_ppm > 0 and tds_out_ppm is not None:
            efficiency = round(1.0 - (tds_out_ppm / tds_in_ppm), 4)

        samples.append({
            "ts":                    ts.isoformat(),
            "flow_permeate_lpm":         flow_p,
            "flow_reject_lpm":      flow_r,
            "pressure_in_bar":       p_in,
            "pressure_out_bar":      p_brine,
            "pressure_membrane_bar": p_diff,
            "volume_permeate_l":         r[5],
            "volume_reject_l":      r[6],
            "tds_in_voltage":        tds_in_v,
            "tds_out_voltage":       tds_out_v,
            "tds_in_ppm":            tds_in_ppm,
            "tds_out_ppm":           tds_out_ppm,
            "recovery":              recovery,
            "efficiency":            efficiency,
            "waste_pct":             waste_pct,
        })
        last_ts = ts

        if len(samples) >= max_samples:
            break

    return {
        "window_seconds":        window_seconds,
        "sample_period_seconds": sample_period_sec,
        "samples":               samples,
    }


# ── Context builder ────────────────────────────────────────────────────────────

def build_context(
    device_id: str,
    db: Any,
    window_seconds: int = 60,
    sample_period_sec: int = 1,
    max_samples: int = 120,
) -> dict:
    """
    Assemble the context payload from current DB state + telemetry window.

    Includes a request_id UUID for end-to-end traceability across
    logs, DB records, and MQTT audit trails.

    Returns a plain dict. No DB writes, no side effects.

    The payload contains raw telemetry only — no backend-inferred diagnostics.
    All inference is the AI's responsibility.
    """
    now_utc    = datetime.now(timezone.utc)
    request_id = str(uuid.uuid4())

    st = db.fetchall(
        "SELECT state, online, last_seen FROM device_status WHERE device_id = %s",
        (device_id,),
    )

    ctx: dict = {
        "request_id":         request_id,
        "device_id":          device_id,
        "timestamp":          now_utc.isoformat(),
        "fsm_state":          "UNKNOWN",
        "connectivity":       "UNKNOWN",
        "seconds_since_seen": None,
        "telemetry_window":   {"window_seconds": window_seconds,
                               "sample_period_seconds": sample_period_sec,
                               "samples": []},
    }

    if st:
        s        = st[0]
        last_seen = s[2]
        secs_ago  = None
        if last_seen is not None:
            if last_seen.tzinfo is None:
                last_seen = last_seen.replace(tzinfo=timezone.utc)
            secs_ago = int((now_utc - last_seen).total_seconds())

        ctx.update({
            "fsm_state":          s[0] or "UNKNOWN",
            "connectivity":       (
                "ONLINE"
                if secs_ago is not None and secs_ago < 90
                else "OFFLINE"
            ),
            "seconds_since_seen": secs_ago,
        })

    ctx["telemetry_window"] = _build_telemetry_window(
        device_id, db, window_seconds, sample_period_sec, max_samples
    )

    return ctx


# ── Realtime context builder ───────────────────────────────────────────────────

def build_realtime_context(
    device_id: str,
    process_data: dict,
    quality_cache: dict,
    fsm_state: str,
    fault_reason: Optional[str],
    retry_count: int,
    inputs: Optional[dict],
    outputs: Optional[dict],
    db: Any,
    context_seconds: int = 0,
) -> dict:
    """
    Build a REALTIME integration payload for a just-arrived process sample.

    process_data  — alias-translated MQTT process dict (already field-renamed)
    quality_cache — last known quality values from KPIEngine cache (no DB query)
    fsm_state     — current FSM state from DeviceStateTracker (no DB query)
    fault_reason  — current fault cause from DeviceStateTracker (no DB query)
    retry_count   — current retry counter from DeviceStateTracker (no DB query)
    inputs/outputs — current digital states from DeviceStateTracker (no DB query)

    context_seconds=0 (default): payload contains sample + metadata only.
      DB queries: 1 (device_status for connectivity).
    context_seconds>0: payload includes context_window with recent history.
      DB queries: 2 (device_status + telemetry window).
    """
    now_utc    = datetime.now(timezone.utc)
    request_id = str(uuid.uuid4())

    # ── Connectivity ──────────────────────────────────────────────────────────
    connectivity       = "UNKNOWN"
    seconds_since_seen = None

    st = db.fetchall(
        "SELECT online, last_seen FROM device_status WHERE device_id = %s",
        (device_id,),
    )
    if st:
        last_seen = st[0][1]
        if last_seen is not None:
            if last_seen.tzinfo is None:
                last_seen = last_seen.replace(tzinfo=timezone.utc)
            secs_ago           = int((now_utc - last_seen).total_seconds())
            seconds_since_seen = secs_ago
            connectivity       = "ONLINE" if secs_ago < 90 else "OFFLINE"

    # ── Current sample — firmware field names mapped to API names ─────────────
    ts_raw = process_data.get("ts")
    ts_str = (
        ts_raw.isoformat()
        if isinstance(ts_raw, datetime) else
        now_utc.isoformat()
    )

    sample: dict = {
        "ts":                ts_str,
        "flow_permeate_lpm": process_data.get("flow_permeate_lpm"),
        "flow_reject_lpm":   process_data.get("flow_reject_lpm"),
        "pressure_in_bar":   process_data.get("pressure_membrane_bar"),  # firmware→API rename
        "pressure_out_bar":  process_data.get("pressure_brine_bar"),     # firmware→API rename
        "volume_permeate_l": process_data.get("volume_permeate_l"),
        "volume_reject_l":   process_data.get("volume_reject_l"),
        "tds_in_ppm":        quality_cache.get("tds_in_ppm"),
        "tds_out_ppm":       quality_cache.get("tds_out_ppm"),
        "tds_in_voltage":    quality_cache.get("tds_in_voltage"),
        "tds_out_voltage":   quality_cache.get("tds_out_voltage"),
    }

    # ── Assemble payload ──────────────────────────────────────────────────────
    payload: dict = {
        "request_id":         request_id,
        "integration_mode":   "REALTIME",
        "device_id":          device_id,
        "timestamp":          now_utc.isoformat(),
        "fsm_state":          fsm_state or "UNKNOWN",
        "fault_reason":       fault_reason,
        "retry_count":        retry_count,
        "connectivity":       connectivity,
        "seconds_since_seen": seconds_since_seen,
        "inputs":             inputs  or {},
        "outputs":            outputs or {},
        "sample":             sample,
    }

    # ── Context window — only when configured (AI_REALTIME_CONTEXT_SECONDS > 0) ─
    if context_seconds > 0:
        payload["context_window"] = _build_telemetry_window(
            device_id, db,
            window_seconds=context_seconds,
            sample_period_sec=1,
            max_samples=context_seconds,
        )

    return payload


# ── AI API call ────────────────────────────────────────────────────────────────

def get_ai_decision(
    context: dict,
    endpoint_url: str,
    timeout_sec: int,
    api_token: str = "",
) -> Tuple[Optional[dict], Optional[str]]:
    """
    POST context to the external AI API and return the parsed decision.

    Returns:
      (decision_dict, None)  — success; suggested_cmd is normalized to uppercase
      (None, error_message)  — any failure

    Never raises. All errors are captured as the second tuple element.
    """
    if not isinstance(context, dict):
        return None, "context must be a dict"

    try:
        headers = {"Content-Type": "application/json"}
        if api_token:
            headers["Authorization"] = f"Bearer {api_token}"
        resp = requests.post(
            endpoint_url,
            json=context,
            timeout=timeout_sec,
            headers=headers,
        )
        resp.raise_for_status()
    except requests.exceptions.Timeout:
        return None, f"timeout after {timeout_sec}s"
    except requests.exceptions.ConnectionError as e:
        return None, f"connection error: {e}"
    except requests.exceptions.HTTPError as e:
        status = getattr(e.response, "status_code", "?")
        return None, f"HTTP {status}: {e}"
    except Exception as e:
        return None, f"unexpected error: {type(e).__name__}: {e}"

    try:
        data = resp.json()
    except ValueError:
        return None, "response is not valid JSON"

    ok, err = validate_decision_response(data)
    if not ok:
        return None, f"validation failed: {err}"

    return data, None


# ── Response validator ─────────────────────────────────────────────────────────

def validate_decision_response(data: Any) -> Tuple[bool, str]:
    """
    Validate the structure of an AI API response.

    Normalizes suggested_cmd to uppercase before validation so that
    responses like "flush" or "FLUSH" are both accepted.

    Expected format (exact keys, no extras allowed):
    {
      "decision":     "NONE" | "EXECUTE",
      "confidence":   0.0 – 1.0,
      "reason":       "non-empty string, max 500 chars",
      "suggested_cmd": null | "START" | "STOP" | "FLUSH" | "RST"
    }
    """
    if not isinstance(data, dict):
        return False, "response must be a JSON object"

    # Reject unexpected fields
    extra = set(data.keys()) - _ALLOWED_RESPONSE_KEYS
    if extra:
        return False, f"unexpected fields in response: {sorted(extra)}"

    # Required fields present
    for field in _REQUIRED_RESPONSE_KEYS:
        if field not in data:
            return False, f"missing required field: {field!r}"

    # decision
    decision = data["decision"]
    if decision not in AI_VALID_DECISIONS:
        return False, f"decision={decision!r} not in {sorted(AI_VALID_DECISIONS)}"

    # confidence — AI owns the threshold, we only validate structure
    confidence = data["confidence"]
    if not isinstance(confidence, (int, float)) or not 0.0 <= float(confidence) <= 1.0:
        return False, f"confidence must be a float in [0.0, 1.0], got {confidence!r}"

    # reason
    reason = data["reason"]
    if not isinstance(reason, str) or not reason.strip():
        return False, "reason must be a non-empty string"
    if len(reason) > 500:
        return False, f"reason too long ({len(reason)} chars, max 500)"

    # suggested_cmd — normalize to uppercase before checking
    if decision == "EXECUTE":
        cmd = data.get("suggested_cmd")
        if isinstance(cmd, str):
            cmd = cmd.upper().strip()
            data["suggested_cmd"] = cmd          # mutate in place: normalized for callers
        if not cmd:
            return False, "decision=EXECUTE requires a non-empty 'suggested_cmd' field"
        if cmd not in AI_COMMAND_WHITELIST:
            return False, (
                f"suggested_cmd={cmd!r} not in whitelist {sorted(AI_COMMAND_WHITELIST)}"
            )

    return True, ""


# ── Policy layer ───────────────────────────────────────────────────────────────

def is_ai_command_allowed(
    cmd: str,
    fsm_state: str,
    last_auto_cmd: Optional[str],
    last_auto_at: Optional[datetime],
    auto_cooldown_sec: int,
) -> Tuple[bool, str]:
    """
    Backend policy gate: the final check before AUTO execution.
    The AI only suggests; this function decides.

    Checks (in order):
      1. Command is in whitelist
      2. Cooldown between automatic commands has passed
      3. Current FSM state permits this command (mirrors firmware isValidTransition)
      4. Anti-oscillation: prevents rapid START↔STOP loops within 60s

    Returns (True, "") if execution is allowed, (False, reason) otherwise.
    """
    cmd_upper = cmd.upper()

    # 1. Whitelist
    if cmd_upper not in AI_COMMAND_WHITELIST:
        return False, f"command {cmd!r} not in whitelist"

    # 2. Cooldown
    if last_auto_at is not None:
        elapsed   = (datetime.now(timezone.utc) - last_auto_at).total_seconds()
        remaining = auto_cooldown_sec - elapsed
        if remaining > 0:
            return False, f"cooldown active — {remaining:.0f}s remaining"

    # 3. FSM state compatibility
    allowed_states = _FSM_ALLOWED_STATES.get(cmd_upper, frozenset())
    if fsm_state not in allowed_states:
        return False, (
            f"FSM state {fsm_state!r} does not allow {cmd_upper} "
            f"(requires one of {sorted(allowed_states)})"
        )

    # 4. Anti-oscillation: block START↔STOP flip within 60s
    _OSCILLATION_WINDOW_SEC = 60
    _OPPOSITES = {"START": "STOP", "STOP": "START"}
    if last_auto_cmd and last_auto_at:
        elapsed = (datetime.now(timezone.utc) - last_auto_at).total_seconds()
        if elapsed < _OSCILLATION_WINDOW_SEC and _OPPOSITES.get(last_auto_cmd) == cmd_upper:
            return False, (
                f"anti-oscillation: {last_auto_cmd}→{cmd_upper} "
                f"too fast ({elapsed:.0f}s < {_OSCILLATION_WINDOW_SEC}s)"
            )

    return True, ""
