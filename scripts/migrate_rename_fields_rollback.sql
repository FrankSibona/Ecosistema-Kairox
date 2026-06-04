-- =============================================================================
-- KAIROX — Field Naming Migration v2.0 — ROLLBACK
-- migrate_rename_fields_rollback.sql
--
-- Reverts migrate_rename_fields.sql. Idempotent.
-- Run this ONLY if you need to roll back the naming migration.
-- Date: 2026-05-30
-- =============================================================================

-- ── telemetry_process ─────────────────────────────────────────────────────────
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.columns
             WHERE table_name='telemetry_process' AND column_name='flow_permeate_lpm') THEN
    ALTER TABLE telemetry_process RENAME COLUMN flow_permeate_lpm TO flow_perm_lpm;
    RAISE NOTICE 'ROLLBACK telemetry_process: flow_permeate_lpm → flow_perm_lpm';
  END IF;
END $$;

DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.columns
             WHERE table_name='telemetry_process' AND column_name='flow_reject_lpm') THEN
    ALTER TABLE telemetry_process RENAME COLUMN flow_reject_lpm TO flow_rechazo_lpm;
    RAISE NOTICE 'ROLLBACK telemetry_process: flow_reject_lpm → flow_rechazo_lpm';
  END IF;
END $$;

DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.columns
             WHERE table_name='telemetry_process' AND column_name='volume_permeate_l') THEN
    ALTER TABLE telemetry_process RENAME COLUMN volume_permeate_l TO volume_perm_l;
    RAISE NOTICE 'ROLLBACK telemetry_process: volume_permeate_l → volume_perm_l';
  END IF;
END $$;

DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.columns
             WHERE table_name='telemetry_process' AND column_name='volume_reject_l') THEN
    ALTER TABLE telemetry_process RENAME COLUMN volume_reject_l TO volume_rechazo_l;
    RAISE NOTICE 'ROLLBACK telemetry_process: volume_reject_l → volume_rechazo_l';
  END IF;
END $$;

-- ── telemetry_inputs ──────────────────────────────────────────────────────────
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.columns
             WHERE table_name='telemetry_inputs' AND column_name='raw_water_ok') THEN
    ALTER TABLE telemetry_inputs RENAME COLUMN raw_water_ok TO crudo_ok;
    RAISE NOTICE 'ROLLBACK telemetry_inputs: raw_water_ok → crudo_ok';
  END IF;
END $$;

DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.columns
             WHERE table_name='telemetry_inputs' AND column_name='pressure_switch') THEN
    ALTER TABLE telemetry_inputs RENAME COLUMN pressure_switch TO presostato;
    RAISE NOTICE 'ROLLBACK telemetry_inputs: pressure_switch → presostato';
  END IF;
END $$;

DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.columns
             WHERE table_name='telemetry_inputs' AND column_name='feed_tank_level_low') THEN
    ALTER TABLE telemetry_inputs RENAME COLUMN feed_tank_level_low TO reserva1;
    RAISE NOTICE 'ROLLBACK telemetry_inputs: feed_tank_level_low → reserva1';
  END IF;
END $$;

DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.columns
             WHERE table_name='telemetry_inputs' AND column_name='spare2') THEN
    ALTER TABLE telemetry_inputs RENAME COLUMN spare2 TO reserva2;
    RAISE NOTICE 'ROLLBACK telemetry_inputs: spare2 → reserva2';
  END IF;
END $$;

-- ── metrics ───────────────────────────────────────────────────────────────────
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.columns
             WHERE table_name='metrics' AND column_name='flow_permeate_lpm') THEN
    ALTER TABLE metrics RENAME COLUMN flow_permeate_lpm TO flow_perm_lpm;
    RAISE NOTICE 'ROLLBACK metrics: flow_permeate_lpm → flow_perm_lpm';
  END IF;
END $$;

DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.columns
             WHERE table_name='metrics' AND column_name='flow_reject_lpm') THEN
    ALTER TABLE metrics RENAME COLUMN flow_reject_lpm TO flow_rechazo_lpm;
    RAISE NOTICE 'ROLLBACK metrics: flow_reject_lpm → flow_rechazo_lpm';
  END IF;
END $$;

-- ── device_status ─────────────────────────────────────────────────────────────
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.columns
             WHERE table_name='device_status' AND column_name='flow_permeate_lpm') THEN
    ALTER TABLE device_status RENAME COLUMN flow_permeate_lpm TO flow_perm_lpm;
    RAISE NOTICE 'ROLLBACK device_status: flow_permeate_lpm → flow_perm_lpm';
  END IF;
END $$;

DO $$ BEGIN
  -- Only rollback if the column was actually renamed (pressure_membrane_bar came from pressure_membrane)
  -- Check that pressure_membrane_bar exists AND pressure_membrane does NOT
  IF EXISTS (SELECT 1 FROM information_schema.columns
             WHERE table_name='device_status' AND column_name='pressure_membrane_bar')
  AND NOT EXISTS (SELECT 1 FROM information_schema.columns
                  WHERE table_name='telemetry_process' AND column_name='pressure_membrane_bar'
                  AND table_name='device_status') THEN
    ALTER TABLE device_status RENAME COLUMN pressure_membrane_bar TO pressure_membrane;
    RAISE NOTICE 'ROLLBACK device_status: pressure_membrane_bar → pressure_membrane';
  END IF;
END $$;

-- ── device_baseline ───────────────────────────────────────────────────────────
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.columns
             WHERE table_name='device_baseline' AND column_name='flow_permeate_mean') THEN
    ALTER TABLE device_baseline RENAME COLUMN flow_permeate_mean TO flow_perm_mean;
    RAISE NOTICE 'ROLLBACK device_baseline: flow_permeate_mean → flow_perm_mean';
  END IF;
END $$;

DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.columns
             WHERE table_name='device_baseline' AND column_name='flow_permeate_std') THEN
    ALTER TABLE device_baseline RENAME COLUMN flow_permeate_std TO flow_perm_std;
    RAISE NOTICE 'ROLLBACK device_baseline: flow_permeate_std → flow_perm_std';
  END IF;
END $$;

DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.columns
             WHERE table_name='device_baseline' AND column_name='flow_permeate_warn_low_learned') THEN
    ALTER TABLE device_baseline RENAME COLUMN flow_permeate_warn_low_learned TO flow_perm_warn_low_learned;
    RAISE NOTICE 'ROLLBACK device_baseline: flow_permeate_warn_low_learned → flow_perm_warn_low_learned';
  END IF;
END $$;

DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.columns
             WHERE table_name='device_baseline' AND column_name='flow_permeate_warn_low_manual') THEN
    ALTER TABLE device_baseline RENAME COLUMN flow_permeate_warn_low_manual TO flow_perm_warn_low_manual;
    RAISE NOTICE 'ROLLBACK device_baseline: flow_permeate_warn_low_manual → flow_perm_warn_low_manual';
  END IF;
END $$;

DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.columns
             WHERE table_name='device_baseline' AND column_name='flow_permeate_warn_low_source') THEN
    ALTER TABLE device_baseline RENAME COLUMN flow_permeate_warn_low_source TO flow_perm_warn_low_source;
    RAISE NOTICE 'ROLLBACK device_baseline: flow_permeate_warn_low_source → flow_perm_warn_low_source';
  END IF;
END $$;
