# MQTT Broker — Cierre de acceso anónimo

**Fecha:** 2026-06-11
**Broker:** `159.112.132.176:1883` (`ro-mosquitto`)

## Cambio aplicado

- `allow_anonymous true` → `allow_anonymous false`
- Agregado `password_file /mosquitto/config/passwd`
- `password_file` generado con las credenciales **ya existentes** en firmware/backend: `kairox` / `admin0102` (sin rotación).
- `docker-compose.yml`: agregado bind mount `./mosquitto/config/passwd:/mosquitto/config/passwd` en el servicio `mosquitto`.
- Permisos de `mosquitto/config/passwd`: `0700`, owner `mosquitto:mosquitto` (uid/gid 1883) — requerido por mosquitto 2.1.x.

## Motivación

El broker estaba expuesto públicamente en `159.112.132.176:1883` con `allow_anonymous true`. Esto permitía:
- Conexión anónima de scanners de internet (CENSYS, ONYPHE, nmap, ~11 clientes `iw-*`, etc.)
- Conexión anónima de un cliente `mqtt-explorer-*` desde la IP pública del sitio LAB, suscrito probablemente a `#` — fuente más probable de los datos consumidos por Ergo-IA/Fede sin pasar por nuestro backend.

Tanto el firmware ESP32 (`comms.cpp`) como el backend (`app.py`) ya enviaban `kairox`/`admin0102` en cada `CONNECT`, ignorados hasta ahora por `allow_anonymous true`. El cierre no requirió reflashear ningún equipo.

## Verificación post-cambio

- `ESP32_D0448EC92DF4` y `ESP32_ECBA88C92DF4`: reconectaron solos con `u'kairox'`, `last_seen` actualizándose normalmente (<2s de antigüedad).
- Backend (`ro-python-worker`): reconectó MQTT automáticamente (`✅ MQTT conectado → fyntek/#`).
- Conexión anónima (`mosquitto_pub` sin user/pass): `Connection Refused: not authorised`.
- Conexión con `kairox`/`admin0102`: aceptada.
- Tras el cambio, ya no se observan reconexiones de `mqtt-explorer-*` ni de los scanners (`CENSYS`, `ONYPHE`, `nmap`, `iw-*`, `test`, `go`, etc.) — todos rechazados por `not authorised`.

## Rollback

Backups en `mosquitto/backups/20260611_041700/`:
- `mosquitto.conf.bak`
- `docker-compose.yml.bak`
- `ro-mosquitto_inspect.json` (config del contenedor previo, referencia)

Pasos para revertir:

```bash
cd /home/ubuntu/iot-server
cp mosquitto/backups/20260611_041700/mosquitto.conf.bak mosquitto/config/mosquitto.conf
cp mosquitto/backups/20260611_041700/docker-compose.yml.bak docker-compose.yml
docker-compose up -d mosquitto
```

Esto restaura `allow_anonymous true` y quita el mount de `passwd` (el archivo
`mosquitto/config/passwd` queda en disco pero deja de usarse — no requiere
borrarse para el rollback).

> Nota operativa: con docker-compose 1.29.2, `--force-recreate` sobre un
> contenedor existente puede fallar con `KeyError: 'ContainerConfig'` (bug
> conocido con metadata de imagen nueva). Si ocurre: `docker rm <container
> renombrado por compose>` y volver a correr `docker-compose up -d
> <servicio>` (sin `--force-recreate`) crea el contenedor desde cero sin
> problema.

## Estado final / pendiente para próxima etapa

- Único usuario en `password_file`: `kairox` (password actual `admin0102`, sin rotar — pedido explícito del usuario para esta etapa).
- Todos los clientes legítimos (2× ESP32 + backend) usan el mismo par de credenciales compartido.
- **Próxima etapa (no ejecutada)**: rotación de credenciales `kairox`/`admin0102` y, opcionalmente, credenciales separadas por dispositivo/cliente (un usuario por ESP32 + uno para el backend) para limitar blast radius si una credencial se filtra. Requiere actualizar `comms.cpp` (reflash) y env vars del backend en conjunto con la regeneración de `password_file`.
- Si `mqtt-explorer-*` correspondía a un consumidor legítimo (Fede/LAB), deberá solicitarse credencial propia para esa próxima etapa.
