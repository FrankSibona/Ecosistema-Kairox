-- =============================================================================
-- KAIROX — Field Naming Migration v2.0
-- migrate_rename_fields.sql
--
-- Purpose: Rename Spanish/abbreviated column names to definitive English names.
-- Safe to run multiple times (idempotent via EXISTS checks).
-- Does NOT modify data — only column metadata changes.
--
-- Rollback: run migrate_rename_fields_rollback.sql
-- Date: 2026-05-30
-- =============================================================================

-- ── telemetry_process ─────────────────────────────────────────────────────────
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.columns
             WHERE table_name='telemetry_process' AND column_name='flow_perm_lpm') THEN
    ALTER TABLE telemetry_process RENAME COLUMN flow_perm_lpm TO flow_permeate_lpm;
    RAISE NOTICE 'telemetry_process: flow_perm_lpm → flow_permeate_lpm';
  END IF;
END $$;

DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.columns
             WHERE table_name='telemetry_process' AND column_name='flow_rechazo_lpm') THEN
    ALTER TABLE telemetry_process RENAME COLUMN flow_rechazo_lpm TO flow_reject_lpm;
    RAISE NOTICE 'telemetry_process: flow_rechazo_lpm → flow_reject_lpm';
  END IF;
END $$;

DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.columns
             WHERE table_name='telemetry_process' AND column_name='volume_perm_l') THEN
    ALTER TABLE telemetry_process RENAME COLUMN volume_perm_l TO volume_permeate_l;
    RAISE NOTICE 'telemetry_process: volume_perm_l → volume_permeate_l';
  END IF;
END $$;

DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.columns
             WHERE table_name='telemetry_process' AND column_name='volume_rechazo_l') THEN
    ALTER TABLE telemetry_process RENAME COLUMN volume_rechazo_l TO volume_reject_l;
    RAISE NOTICE 'telemetry_process: volume_rechazo_l → volume_reject_l';
  END IF;
END $$;

-- ── telemetry_inputs ──────────────────────────────────────────────────────────
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.columns
             WHERE table_name='telemetry_inputs' AND column_name='crudo_ok') THEN
    ALTER TABLE telemetry_inputs RENAME COLUMN crudo_ok TO raw_water_ok;
    RAISE NOTICE 'telemetry_inputs: crudo_ok → raw_water_ok';
  END IF;
END $$;

DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.columns
             WHERE table_name='telemetry_inputs' AND column_name='presostato') THEN
    ALTER TABLE telemetry_inputs RENAME COLUMN presostato TO pressure_switch;
    RAISE NOTICE 'telemetry_inputs: presostato → pressure_switch';
  END IF;
END $$;

DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.columns
             WHERE table_name='telemetry_inputs' AND column_name='reserva1') THEN
    ALTER TABLE telemetry_inputs RENAME COLUMN reserva1 TO feed_tank_level_low;
    RAISE NOTICE 'telemetry_inputs: reserva1 → feed_tank_level_low';
  END IF;
END $$;

-- spare1 is an intermediate name; rename to feed_tank_level_low if still present
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.columns
             WHERE table_name='telemetry_inputs' AND column_name='spare1') THEN
    ALTER TABLE telemetry_inputs RENAME COLUMN spare1 TO feed_tank_level_low;
    RAISE NOTICE 'telemetry_inputs: spare1 → feed_tank_level_low';
  END IF;
END $$;

DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.columns
             WHERE table_name='telemetry_inputs' AND column_name='reserva2') THEN
    ALTER TABLE telemetry_inputs RENAME COLUMN reserva2 TO spare2;
    RAISE NOTICE 'telemetry_inputs: reserva2 → spare2';
  END IF;
END $$;

-- ── metrics ───────────────────────────────────────────────────────────────────
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.columns
             WHERE table_name='metrics' AND column_name='flow_perm_lpm') THEN
    ALTER TABLE metrics RENAME COLUMN flow_perm_lpm TO flow_permeate_lpm;
    RAISE NOTICE 'metrics: flow_perm_lpm → flow_permeate_lpm';
  END IF;
END $$;

DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.columns
             WHERE table_name='metrics' AND column_name='flow_rechazo_lpm') THEN
    ALTER TABLE metrics RENAME COLUMN flow_rechazo_lpm TO flow_reject_lpm;
    RAISE NOTICE 'metrics: flow_rechazo_lpm → flow_reject_lpm';
  END IF;
END $$;

-- ── device_status ─────────────────────────────────────────────────────────────
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.columns
             WHERE table_name='device_status' AND column_name='flow_perm_lpm') THEN
    ALTER TABLE device_status RENAME COLUMN flow_perm_lpm TO flow_permeate_lpm;
    RAISE NOTICE 'device_status: flow_perm_lpm → flow_permeate_lpm';
  END IF;
END $$;

DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.columns
             WHERE table_name='device_status' AND column_name='pressure_membrane') THEN
    ALTER TABLE device_status RENAME COLUMN pressure_membrane TO pressure_membrane_bar;
    RAISE NOTICE 'device_status: pressure_membrane → pressure_membrane_bar';
  END IF;
END $$;

-- ── device_baseline ───────────────────────────────────────────────────────────
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.columns
             WHERE table_name='device_baseline' AND column_name='flow_perm_mean') THEN
    ALTER TABLE device_baseline RENAME COLUMN flow_perm_mean TO flow_permeate_mean;
    RAISE NOTICE 'device_baseline: flow_perm_mean → flow_permeate_mean';
  END IF;
END $$;

DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.columns
             WHERE table_name='device_baseline' AND column_name='flow_perm_std') THEN
    ALTER TABLE device_baseline RENAME COLUMN flow_perm_std TO flow_permeate_std;
    RAISE NOTICE 'device_baseline: flow_perm_std → flow_permeate_std';
  END IF;
END $$;

DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.columns
             WHERE table_name='device_baseline' AND column_name='flow_perm_warn_low_learned') THEN
    ALTER TABLE device_baseline RENAME COLUMN flow_perm_warn_low_learned TO flow_permeate_warn_low_learned;
    RAISE NOTICE 'device_baseline: flow_perm_warn_low_learned → flow_permeate_warn_low_learned';
  END IF;
END $$;

DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.columns
             WHERE table_name='device_baseline' AND column_name='flow_perm_warn_low_manual') THEN
    ALTER TABLE device_baseline RENAME COLUMN flow_perm_warn_low_manual TO flow_permeate_warn_low_manual;
    RAISE NOTICE 'device_baseline: flow_perm_warn_low_manual → flow_permeate_warn_low_manual';
  END IF;
END $$;

DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.columns
             WHERE table_name='device_baseline' AND column_name='flow_perm_warn_low_source') THEN
    ALTER TABLE device_baseline RENAME COLUMN flow_perm_warn_low_source TO flow_permeate_warn_low_source;
    RAISE NOTICE 'device_baseline: flow_perm_warn_low_source → flow_permeate_warn_low_source';
  END IF;
END $$;

-- =============================================================================
-- Verification
-- =============================================================================
SELECT table_name, column_name
FROM information_schema.columns
WHERE table_schema = 'public'
  AND column_name IN (
    'flow_permeate_lpm','flow_reject_lpm','volume_permeate_l','volume_reject_l',
    'raw_water_ok','pressure_switch','feed_tank_level_low','spare2',
    'pressure_membrane_bar','flow_permeate_mean','flow_permeate_std'
  )
ORDER BY table_name, column_name;
