# Nightjar — Vehicle Privacy-Exposure Awareness Platform

> **Working codename:** *Nightjar* (a nocturnal bird known for silent, near-invisible flight — fitting for a passive RF observer). Rename freely.

## 1. Purpose & Framing

Modern vehicles and their occupants continuously broadcast unique, persistent radio
identifiers that require **no authentication to receive** and were designed with little
or no privacy protection:

- **TPMS** (Tire Pressure Monitoring Systems) transmit a factory-fixed 28–32 bit sensor
  ID on 315/433 MHz every time the wheels turn.
- **Bluetooth / BLE** infotainment head units advertise stable addresses, service UUIDs,
  and manufacturer data.
- **Occupant smartphones / wearables** emit BLE advertisements (many with resolvable or,
  in older/misconfigured devices, static addresses).

Fixed-camera ALPR networks (e.g. Flock Safety) already de-anonymize vehicles by license
plate. Nightjar demonstrates — for **privacy research, awareness, and defensive
assessment** — how trivially those same vehicles can be *fingerprinted and re-identified
by their RF exhaust alone*, and how RF identity can be **correlated to a plate** using a
single opportunistic camera capture.

The goal of this project is to make that invisible exposure **visible and measurable**, so
drivers, researchers, and policymakers understand the tracking surface that low-cost
receivers create.

### 1.1 Intended use & guardrails

This is a **defensive / educational** instrument, analogous to a Wi-Fi spectrum analyzer
or a published TPMS-eavesdropping paper. It is intended to be operated:

- On RF that is **already being broadcast in the clear** (passive receive only — Nightjar
  never transmits).
- By the operator, on their **own premises / test environment**, or in an authorized
  research context.
- To assess and communicate **privacy risk**, not to surveil specific individuals.

The software ships with:

- A prominent legal/ethical notice on first run.
- **Baseline learning** so the operator's *own* devices are tagged and excluded.
- Local-only storage; no cloud, no external exfiltration.
- Configurable **retention / auto-purge** of captured data and imagery.

Operators are responsible for compliance with local law regarding RF interception,
recording, and imagery (this varies by jurisdiction). See `README.md` §Legal.

## 2. High-Level Architecture

```
                        ┌──────────────────────────────────────────────┐
   Hardware             │                 Nightjar node                 │
 ┌───────────┐  315/433 │  ┌────────────┐                               │
 │ RTL-SDR   │─────────▶│  │ TPMS sensor │─┐                            │
 └───────────┘   MHz    │  │ (rtl_433)   │ │                            │
 ┌───────────┐   BLE    │  ├────────────┤ │   ┌──────────┐  ┌────────┐  │
 │ SENA UD100│─────────▶│  │ BLE/BT scan │ ├──▶│ Ingestor │─▶│ SQLite │  │
 │ (BT dongle)│  2.4GHz │  │ (bleak/bluez)│ │  │ pipeline │  └────────┘  │
 └───────────┘          │  ├────────────┤ │   │  - persist │     ▲       │
 ┌───────────┐  frames  │  │ Camera+ALPR │─┘   │  - correlate│    │       │
 │ Webcam    │─────────▶│  │ (opencv)    │     │  - baseline │    │       │
 └───────────┘          │  └────────────┘     │  - notify   │    │       │
                        │        │            └─────┬───────┘    │       │
                        │        │ mock mode        │ events     │       │
                        │        ▼                  ▼            │       │
                        │  ┌────────────┐    ┌──────────────┐    │       │
                        │  │ Simulator  │    │  Event Bus    │───┼──WS──▶│  Browser UI
                        │  └────────────┘    │ (async pubsub)│   │       │  (live console,
                        │                    └──────────────┘   REST     │   records,
                        │           FastAPI (uvicorn) ◀─────────┘        │   notifications)
                        └──────────────────────────────────────────────┘
```

**Design principles**

1. **Sensors are plugins.** Each capture source implements a small `Sensor` interface and
   emits normalized `Observation` / `Detection` objects. Adding a new RF source is one file.
2. **Mock-first.** A `SimulatorSensor` produces realistic synthetic traffic so the entire
   pipeline + UI is fully exercisable **before any hardware arrives**.
3. **One ingest pipeline.** All sensors feed a single `Ingestor` → persistence →
   correlation → baseline/notification → event bus. No sensor talks to the DB or UI directly.
4. **Offline, zero external dependency at runtime.** No CDNs, no cloud. The UI is a
   dependency-free SPA served by the backend.

## 3. Data Model

| Entity | Purpose | Key fields |
|---|---|---|
| **Signal** | A unique RF identifier ever observed | `kind` (tpms\|ble\|bt_classic), `identifier`, `label`, `first_seen`, `last_seen`, `count`, `rssi_last/best`, `meta` (json), `is_baseline`, `vehicle_id` |
| **Sighting** | One observation event (time series of a Signal) | `signal_id`, `ts`, `rssi`, `source`, `data` (json) |
| **Detection** | A camera capture w/ optional plate | `ts`, `image_path`, `plate_text`, `plate_confidence`, `region`, `bbox`, `vehicle_id`, `meta` |
| **Vehicle** | A correlated physical entity (cluster of signals + detections) | `label`, `first_seen`, `last_seen`, `score`, `color`, `status`, `notes` |
| **Notification** | An alert raised by a rule | `ts`, `level` (info\|alert\|critical), `title`, `body`, `signal_id`/`vehicle_id`/`detection_id`, `acknowledged` |
| **ServiceState** | Runtime state of one capture service | `name`, `status`, `enabled`, `last_error`, `stats` (json) |

Identity keys:
- TPMS `identifier` = `"<protocol>:<sensor_id>"` (e.g. `Toyota:0x1a2b3c4d`).
- BLE/BT `identifier` = device address (`aa:bb:cc:...`); infotainment vs phone inferred from
  advertised service UUIDs / manufacturer data / device class.

## 4. Capture Sources

### 4.1 TPMS — RTL-SDR via `rtl_433`
- Subprocess: `rtl_433 -F json -Y classic -M time:iso -R <tpms protocols>` on 315 & 433 MHz.
- Parse JSON lines; keep records where `type == "TPMS"` or model matches TPMS.
- Emit `Observation(kind="tpms", identifier=f"{model}:{id}", rssi, data={pressure,temp,...})`.
- Hop between 315.0 MHz and 433.92 MHz (config) to catch both bands.

### 4.2 Bluetooth / BLE — Parani SENA UD-100 (BlueZ hci adapter)
- **BLE**: `bleak.BleakScanner` in active/passive mode; detection callback yields address,
  RSSI, local name, manufacturer data, service UUIDs. Classify:
  - Known infotainment OUIs / service UUIDs → `entertainment`.
  - Apple/Google/Samsung continuity manufacturer data → `phone/wearable`.
- **Classic BT** (optional): periodic inquiry via `bluetoothctl`/`hcitool` for discoverable
  head units & hands-free profiles.
- Emit `Observation(kind="ble"|"bt_classic", identifier=address, rssi, data={name,uuids,mfg,class})`.
- Note: iOS/modern Android rotate BLE MACs (RPA) ~15 min — captured & flagged as
  *resolvable*; persistent-identifier fingerprinting focuses on infotainment + non-rotating
  devices, matching real-world TPMS-grade tracking.

### 4.3 Camera + ALPR — Webcam (arriving later)
- OpenCV frame grab, triggered by (a) an RF event of interest entering the scene, or
  (b) motion, or (c) manual/interval.
- ALPR backend is **pluggable** (`alpr.py`): `fast-alpr`/ONNX plate detector+OCR if
  installed, else a no-op stub that just stores the frame. Produces `plate_text`,
  `plate_confidence`, `region`, `bbox`.
- Frame saved to `data/captures/`; `Detection` row created and offered to the correlator.

### 4.4 Simulator (mock mode — default until hardware present)
- Generates a small population of synthetic "vehicles," each owning 1–4 TPMS IDs + 0–2 BLE
  devices, that periodically "drive past" (RSSI rises/falls), occasionally triggering a
  synthetic plate detection. Exercises new-signal, co-occurrence, correlation, and
  notification paths end-to-end.

## 5. Correlation Engine

**Unit model.** The atomic unit of identity is a single `Signal` — one TPMS sensor id, or
one BT/BLE address. Units are never pre-grouped; a vehicle is emergent, formed only from
correlated units. Identity is correlated **over time** via a persistent association graph.

**Implemented model (persistent, human-in-the-loop):**
1. A rolling in-memory **"scene"** = units sighted within the last `scene_window` seconds.
2. Each time two units are co-present in a *fresh* co-occurrence episode, their
   **`Association`** row (`co_count`, first/last seen) is strengthened in the DB — evidence
   **accumulates over time and survives restarts**.
3. **RSSI-profile gate.** Co-presence alone is weak evidence (two strangers can pass
   together). Units on the *same* vehicle also rise and fall in signal strength *together*.
   Each association keeps an **online Pearson correlation** of the two units' contemporaneous
   RSSI readings (streaming accumulators in the DB). A suggestion is raised only if that
   correlation clears `min_rssi_corr` once `min_rssi_samples` paired readings exist — and the
   correlation ranks the proposal. Co-present-but-independent pairs (r≈0) and anti-moving
   pairs (r<0) are suppressed.
4. When an anchor pair's evidence reaches `min_encounters` **and** passes the RSSI gate, the
   engine raises a **`Suggestion`** — it **never auto-merges**. The operator **confirms**
   (union the units into a `Vehicle`, via union-find) or **rejects** (the pair is `blocked`
   and never re-suggested).
4. **Anchoring:** only *durable* identifiers (TPMS + infotainment; `anchor_categories`) seed
   vehicles. Rotating phone/wearable addresses have low `durability` weight — their evidence
   is recorded and they can be *attached* to a confirmed vehicle, but they never drive
   formation. This matches reality: phones rotate their BLE MAC ~15 min; TPMS + infotainment
   are the persistent backbone.
5. A `Detection` (plate) is bound to the confirmed `Vehicle` of the anchors active at capture
   time; if none is confirmed yet, a suggestion (carrying the detection) is raised so the
   operator can confirm forming the vehicle *and* binding the plate together.
6. Vehicle `score` (exposure/notability) rises with # durable identifiers, # sightings, and a
   bound plate.

**Extension path (v2):** RSSI/arrival-time profile clustering to auto-rank suggestions,
Bayesian identity resolution, rotating-address stitching, geofenced multi-node fusion.

## 6. Baseline & Notifications

- **Baseline learning**: operator clicks *Learn baseline* (or runs for a warm-up window);
  all currently-known signals are flagged `is_baseline=true` (your car, house, phones).
- **Rules** (`notifications.py`):
  - `new_signal_over_baseline`: first-ever sighting of a non-baseline identifier → **alert**.
  - `new_vehicle`: correlator forms a new multi-identifier vehicle → **alert**.
  - `plate_bound`: a plate is correlated to RF identity → **critical**.
  - `reappearance`: a previously-seen non-baseline vehicle returns → **info/alert**.
- Notifications stream to the UI (toast + feed), are persisted, and are acknowledgeable.
- Pluggable sinks (future): desktop notify, webhook, MQTT.

## 7. Web Interface

Dependency-free SPA (custom CSS "ops console" theme, vanilla JS, WebSocket live feed,
hand-rolled SVG charts — fully offline). Views:

1. **Dashboard / Live** — service start/stop controls & health; real-time sighting stream;
   RSSI activity sparkline; counters (unique signals, vehicles, detections today); live
   notification toasts.
2. **Signals** — sortable/filterable table of every unique identifier (kind, label, seen
   count, first/last, baseline flag). Row → detail drawer with sighting timeline + RSSI chart.
3. **Vehicles** — cards/table of correlated entities; detail view aggregates all member
   signals, detections (with plate thumbnail), timeline, notability score; edit label/notes.
4. **Detections** — gallery of camera captures with plate text/confidence.
5. **Notifications** — feed with ack; filter by level.
6. **Settings** — mock mode toggle, baseline learn/reset, retention, radio config.

## 8. Tech Stack

- **Backend:** Python 3.12, FastAPI + Uvicorn, SQLAlchemy 2.0 (SQLite), Pydantic.
- **Capture libs:** `rtl_433` (system binary), `bleak` (BLE), BlueZ (`bluetoothctl`),
  `opencv-python`, optional `fast-alpr`.
- **Frontend:** vanilla HTML/CSS/JS SPA, WebSocket, SVG charts. No build step.
- **Storage:** SQLite at `data/nightjar.db`; imagery under `data/captures/`.

## 9. Borrowed / Referenced Open Source

- **rtl_433** (merbanan) — RF/TPMS decoding (invoked as subprocess).
- **bleak** — cross-platform BLE.
- **fast-alpr / fast-plate-ocr / open-image-models** — modern ONNX ALPR (optional backend).
- Prior art referenced for approach: `rtl_433`'s TPMS decoders, BlueZ, OpenALPR concepts.

## 10. Roadmap

- **M0 (this deliverable):** spec + runnable framework, mock mode, full pipeline + UI, DB,
  correlation v1, notifications, service control. *No hardware required.*
- **M1:** RTL-SDR/`rtl_433` live TPMS ingest.
- **M2:** SENA UD-100 BLE/classic ingest + device classification.
- **M3:** Webcam capture + ALPR + plate↔RF binding.
- **M4:** Correlation v2 (RSSI/time clustering), retention/purge, export, multi-node fusion.

## 11. Layout

```
nightjar/
  specification.md        this file
  README.md               quickstart, legal, hardware notes
  bootstrap.sh            create venv + install python deps
  requirements.txt
  config.example.yaml     radio/scan/retention config
  run.py                  entrypoint (uvicorn)
  backend/
    main.py               FastAPI app, lifespan, static mount
    settings.py           config loader
    database.py           engine/session
    models.py             ORM
    schemas.py            Pydantic DTOs
    eventbus.py           async pub/sub for WS
    ingest.py             the one ingest pipeline
    correlation.py        union-find co-occurrence correlator
    notifications.py      rule engine
    capture/
      base.py manager.py mock.py tpms.py bluetooth_ble.py camera.py alpr.py
    routers/
      system.py services.py signals.py vehicles.py detections.py
      notifications.py baseline.py ws.py
  frontend/
    index.html app.js styles.css
  data/  (db + captures, gitignored)
```
