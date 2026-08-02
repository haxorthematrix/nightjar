# Nightjar

**Vehicle privacy-exposure awareness console.** A receive-only research instrument that
shows how vehicles and their occupants are re-identifiable by the RF identifiers they
broadcast in the clear — TPMS sensor IDs (RTL-SDR), Bluetooth/BLE from infotainment and
phones (SENA UD-100) — and how those bind to a license plate via a webcam + ALPR, the same
way an ALPR camera network (e.g. Flock) de-anonymizes a plate.

See [`specification.md`](specification.md) for the full design.

> **Responsible use.** Nightjar never transmits. Operate only on RF already broadcast in the
> clear, in an environment you are authorized to monitor, for privacy assessment and
> education — not to surveil individuals. You are responsible for compliance with local law
> on RF interception, recording, and imagery.

---

## Requirements

- **Python 3.12+**
- **Python venv/pip.** On Debian/Ubuntu, either install the stdlib venv package
  (`sudo apt install python3-venv python3-pip`) **or** just run `./bootstrap.sh`, which
  falls back to bootstrapping `virtualenv` into your user site if that package is missing.
- **Per-capture-source (only when you attach that hardware):**
  | Source | System package | Python |
  |---|---|---|
  | TPMS via RTL-SDR | `sudo apt install rtl-433` + an RTL-SDR dongle | — |
  | BLE via SENA UD-100 | BlueZ (`bluez`, usually preinstalled) + the USB adapter | `bleak` (in requirements) |
  | Webcam + ALPR | a webcam | `opencv-python-headless numpy` (+ optional `fast-alpr`) |

Everything needed to run in **test mode** (below) is pure-Python and installed by
`bootstrap.sh`; no hardware required.

## Startup

```bash
cd nightjar
./bootstrap.sh                       # one-time: creates .venv, installs deps
source .venv/bin/activate
python run.py                        # serves http://127.0.0.1:8008
```

Open **http://127.0.0.1:8008** (accept the one-time responsible-use notice). Host/port are
configurable under `http:` in `config.yaml`.

## Test mode (synthetic data, no hardware)

Nightjar ships with a **Simulator** that feeds realistic synthetic TPMS/BLE traffic and
staged plate captures through the entire pipeline — correlation, plate-binding, alerts, and
the live UI all work with nothing plugged in.

Toggle it via `mock_mode` in `config.yaml` (copy from `config.example.yaml` if you don't
have one):

```yaml
mock_mode: true    # Simulator ON  — generates synthetic TEST DATA
mock_mode: false   # real hardware only — no test data
```

Or per-run without editing config: `NIGHTJAR_MOCK=1 python run.py`. In test mode the
Dashboard shows a **Simulator** service and a `MOCK` badge in the header.

> A fresh clone (no `config.yaml`) defaults to **test mode on**, so `python run.py` gives you
> a working demo immediately.

## Resetting the database

The database (`data/nightjar.db`) and captured imagery (`data/captures/`) are local and
disposable. To wipe everything back to a clean, empty state:

```bash
./scripts/reset_db.sh      # stop the server first; a fresh DB is created on next start
```

(That just removes `data/nightjar.db*` and `data/captures/*` — you can also delete them by
hand.) There is also `scripts/backfill_capture_images.py`, which regenerates synthetic
thumbnail images for any test detections that lack one.

## Going live (when hardware arrives)

1. Set `mock_mode: false` in `config.yaml`.
2. **TPMS (RTL-SDR):** `sudo apt install rtl-433`, plug in the dongle. Nightjar runs
   `rtl_433` on 315 MHz and 433.92 MHz.
3. **BLE (SENA UD-100):** plug in the adapter (shows up as an `hci` device); set
   `ble.adapter`. `bleak` is already installed.
4. **Camera + ALPR:** `pip install opencv-python-headless numpy` (and optionally
   `fast-alpr`); set `camera.device`.
5. Restart `python run.py` and start each service from the Dashboard.

Per-service milestones and the correlation roadmap are in `specification.md` §10.

## The console

- **Dashboard** — start/stop services, live activity sparkline + event feed, counters, toasts.
- **Signals** — every unique RF *unit* (one TPMS id / one BT address); drawer shows its RSSI
  trend, sighting history, and its co-occurrence + RSSI-correlation graph.
- **Vehicles** — units correlated into a single fingerprint, with exposure score & bound plate.
- **Review** — the engine *proposes* correlations (it never auto-merges); you Confirm/Reject.
- **Detections** — webcam captures and recovered plates.
- **Alerts** — new-over-baseline, new-vehicle, suggestion, and plate↔RF-bound notifications.

## Layout

`backend/` FastAPI app · `backend/capture/` pluggable sensors (one file per RF source) ·
`frontend/` dependency-free SPA · `scripts/` maintenance helpers · `data/` DB + imagery
(gitignored).

## Architecture in one line

Sensors → one **Ingestor** (persist → correlate → baseline/notify) → **event bus** →
WebSocket → live UI. Nothing but the Ingestor writes to the database.

## License

[MIT](LICENSE) © 2026 Larry Pesce.
