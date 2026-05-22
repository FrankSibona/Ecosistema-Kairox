"""
ai_client.py — KAIROX external AI integration client

Responsibilities:
  - Build context payload from current DB state (with request_id for tracing)
  - POST context to external AI API with configurable timeout
  - Validate AI response strictly (no unexpected fields allowed)
  - Normalize suggested_cmd to uppercase before validation
  - Provide a policy layer that decides whether to execute a suggested command

Does NOT:
  - Execute commands (AIDecisionEngine in app.py is the executor)
  - Write to the database
  - Hold global mutable state

Naming convention: "suggested_cmd" is used consistently everywhere —
in the response JSON, logs, and DB inserts.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional, Tuple

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


# ── Context builder ────────────────────────────────────────────────────────────

def build_context(device_id: str, db: Any) -> dict:
    """
    Assemble the context payload from current DB state.

    Includes a request_id UUID for end-to-end traceability across
    logs, DB records, and MQTT audit trails.

    Returns a plain dict. No DB writes, no side effects.
    """
    now_utc    = datetime.now(timezone.utc)
    request_id = str(uuid.uuid4())

    st = db.fetchall(
        """SELECT state, online, last_seen,
                  biz_risk_level, biz_risk_score,
                  health_status, health_message,
                  flow_perm_lpm, pressure_membrane, recovery, efficiency,
                  biz_liters_today, biz_waste_pct
           FROM device_status WHERE device_id = %s""",
        (device_id,),
    )

    proc = db.fetchall(
        """SELECT flow_perm_lpm, flow_rechazo_lpm,
                  pressure_membrane_bar, pressure_brine_bar,
                  volume_perm_l, volume_rechazo_l
           FROM telemetry_process WHERE device_id = %s
           ORDER BY time DESC LIMIT 1""",
        (device_id,),
    )

    qual = db.fetchall(
        """SELECT tds_in_ppm, tds_out_ppm
           FROM telemetry_quality WHERE device_id = %s
           ORDER BY time DESC LIMIT 1""",
        (device_id,),
    )

    alerts = db.fetchall(
        """SELECT code, message, severity
           FROM diagnostics WHERE device_id = %s
             AND time > NOW() - INTERVAL '1 hour'
           ORDER BY time DESC LIMIT 5""",
        (device_id,),
    )

    ctx: dict = {
        "request_id":         request_id,
        "device_id":          device_id,
        "timestamp":          now_utc.isoformat(),
        "fsm_state":          "UNKNOWN",
        "connectivity":       "UNKNOWN",
        "risk_level":         "UNKNOWN",
        "health_status":      "UNKNOWN",
        "health_message":     None,
        "seconds_since_seen": None,
        "metrics":            {},
        "active_alerts":      [],
    }

    if st:
        s        = st[0]
        secs_ago = int((now_utc - s[2]).total_seconds()) if s[2] else None
        ctx.update({
            "fsm_state":          s[0] or "UNKNOWN",
            "connectivity":       (
                "ONLINE"
                if s[1] and secs_ago is not None and secs_ago < 90
                else "OFFLINE"
            ),
            "risk_level":         s[3] or "UNKNOWN",
            "health_status":      s[5] or "UNKNOWN",
            "health_message":     s[6],
            "seconds_since_seen": secs_ago,
        })
        ctx["metrics"].update({
            "risk_score":        s[4],
            "flow_perm_lpm":     s[7],
            "pressure_membrane": s[8],
            "recovery":          s[9],
            "efficiency":        s[10],
            "liters_today":      s[11],
            "waste_pct":         s[12],
        })

    if proc:
        p = proc[0]
        ctx["metrics"].update({
            "flow_perm_lpm":    p[0],
            "flow_rechazo_lpm": p[1],
            "pressure_in_bar":  p[2],
            "pressure_out_bar": p[3],
            "volume_perm_l":    p[4],
            "volume_rechazo_l": p[5],
        })

    if qual:
        ctx["metrics"]["tds_in_ppm"]  = qual[0][0]
        ctx["metrics"]["tds_out_ppm"] = qual[0][1]

    ctx["active_alerts"] = [
        {"code": r[0], "message": r[1], "severity": r[2]}
        for r in alerts
    ]

    return ctx


# ── AI API call ────────────────────────────────────────────────────────────────

def get_ai_decision(
    context: dict,
    endpoint_url: str,
    timeout_sec: int,
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
        resp = requests.post(
            endpoint_url,
            json=context,
            timeout=timeout_sec,
            headers={"Content-Type": "application/json"},
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
