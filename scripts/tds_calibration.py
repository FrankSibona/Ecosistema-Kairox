#!/usr/bin/env python3
"""
Calculadora de calibración TDS (CAL_MODE_LINEAR) para KAIROX.

Ajusta por mínimos cuadrados: ppm = slope * mV + offset
para TDS1 y/o TDS2, a partir de un listado de mediciones
(mV crudo leído del ESP32 vs. ppm de un medidor de referencia).

El "mV" debe ser el valor `mv=` del log [TDS] de debug del firmware
(milivolts crudos de analogReadMilliVolts, sin compensación de
temperatura). El firmware aplica la compensación de tds_temperature
antes de multiplicar por slope, así que para que la calibración sea
válida las mediciones deben tomarse con el agua a una temperatura
cercana al valor configurado en tds_temperature (default 25°C).

Uso:
    python3 tds_calibration.py mediciones.csv
    python3 tds_calibration.py --device-id ESP32_XXXX mediciones.csv

CSV de entrada (encabezado obligatorio), una fila por medición:
    ref_ppm,tds1_mv,tds2_mv
    12,15,17
    26,87,90
    35,120,125
    45,174,184
    96,405,451
    114,495,611
    144,670,805

Columnas tds1_mv / tds2_mv son opcionales (se puede calibrar un
solo canal por vez si falta la otra columna o queda vacía).
"""

import argparse
import csv
import json
import sys


def linear_fit(xs, ys):
    """Mínimos cuadrados: y = slope*x + offset. Devuelve (slope, offset, r2)."""
    n = len(xs)
    sum_x = sum(xs)
    sum_y = sum(ys)
    sum_xy = sum(x * y for x, y in zip(xs, ys))
    sum_xx = sum(x * x for x in xs)

    denom = n * sum_xx - sum_x * sum_x
    if denom == 0:
        raise ValueError("No se puede ajustar: todos los valores de mV son iguales")

    slope = (n * sum_xy - sum_x * sum_y) / denom
    offset = (sum_y - slope * sum_x) / n

    mean_y = sum_y / n
    ss_tot = sum((y - mean_y) ** 2 for y in ys)
    ss_res = sum((y - (slope * x + offset)) ** 2 for x, y in zip(xs, ys))
    r2 = 1.0 - (ss_res / ss_tot) if ss_tot > 0 else 1.0

    return slope, offset, r2


def report_channel(name, key_mv, key_ppm, rows):
    pairs = [(float(r[key_mv]), float(r[key_ppm]))
             for r in rows if r.get(key_mv, "").strip() != ""]
    if len(pairs) < 2:
        print(f"\n[{name}] Menos de 2 puntos con datos — se omite.")
        return None

    xs, ys = zip(*pairs)
    slope, offset, r2 = linear_fit(xs, ys)

    print(f"\n[{name}] {len(pairs)} puntos")
    print(f"  ppm = {slope:.6f} * mV + {offset:.4f}")
    print(f"  R²  = {r2:.4f}" + ("  (¡baja linealidad! considerar más puntos o modelo no lineal)" if r2 < 0.95 else ""))

    print(f"  {'mV':>8} {'ppm_ref':>9} {'ppm_calc':>9} {'error':>8}")
    for x, y in pairs:
        calc = slope * x + offset
        print(f"  {x:8.1f} {y:9.2f} {calc:9.2f} {calc - y:+8.2f}")

    if slope <= 0:
        print("  ADVERTENCIA: slope <= 0 — el firmware lo interpreta como "
              "'sin calibración' (CAL_MODE_LEGACY, fallback a fórmula DFRobot).")
    if slope > 10:
        print("  ADVERTENCIA: slope > 10 — fuera del rango válido de "
              "isValidConfig() (0–10), el firmware rechazará este valor.")
    if not (-500 <= offset <= 500):
        print("  ADVERTENCIA: offset fuera del rango válido de "
              "isValidConfig() (-500–500), el firmware rechazará este valor.")

    return round(slope, 6), round(offset, 4)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv_file", help="CSV con columnas ref_ppm,tds1_mv,tds2_mv")
    ap.add_argument("--device-id", help="device_id para armar el comando curl de ejemplo")
    ap.add_argument("--api-host", default="http://localhost:8080",
                    help="host del backend Flask (default: http://localhost:8080)")
    args = ap.parse_args()

    with open(args.csv_file, newline="") as f:
        rows = list(csv.DictReader(f))

    if not rows:
        print("CSV vacío", file=sys.stderr)
        sys.exit(1)

    result1 = report_channel("TDS1", "tds1_mv", "ref_ppm", rows)
    result2 = report_channel("TDS2", "tds2_mv", "ref_ppm", rows)

    if not result1 and not result2:
        sys.exit(1)

    payload = {}
    if result1:
        payload["tds1_cal_slope"], payload["tds1_cal_offset"] = result1
    if result2:
        payload["tds2_cal_slope"], payload["tds2_cal_offset"] = result2

    print("\n--- Payload para POST /api/config/<device_id> ---")
    print(json.dumps(payload, indent=2))

    if args.device_id:
        print("\n--- curl de ejemplo ---")
        print(f"curl -X POST {args.api_host}/api/config/{args.device_id} \\")
        print("  -H 'Content-Type: application/json' \\")
        print(f"  -d '{json.dumps(payload)}'")
        print("\n(Nota: el form Flask del dashboard también persiste los demás campos de "
              "configuración; si usás curl directo, incluí los valores actuales de "
              "flow_factor_1/2, tds_temperature, etc. para no resetearlos a sus defaults.)")


if __name__ == "__main__":
    main()
