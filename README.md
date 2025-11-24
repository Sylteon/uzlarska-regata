# Regata 6-Lane Display

This application displays six synchronized lane timers in a 3×2 layout and mirrors authoritative time/command messages sent over a serial port.

Key behavior
- Display-only: the app does not independently time racers. Instead, it mirrors messages coming from a serial device (for example an Arduino) and renders them into the lane displays.
- Six lanes: the UI shows six frames (3 columns × 2 rows), each with a lane header and a large time display.
- Centisecond precision: times are displayed as MM:SS.cc (minutes:seconds.centiseconds).
- Stop-by-number: if a serial line starts with a lane number (1–6) it targets that lane.
   - Example: `1TIME` or `1 TIME` will stop lane 1 (stop-only), `1TIME:0:12:34` will set lane 1 time to 00:12.34 and stop it.
- Markers: special commands set small markers to the right of the timer:
   - `DISQUALIFIED` or `DISQUAL` — shows `D` in that lane
   - `FINAL` or `FINALTIME` — shows `K` in that lane
- Start Race: if the serial device sends a message containing `Start Race` (case-insensitive), the app resets displays and starts a new race epoch (queued/stale callbacks from previous races are ignored).
- Graceful shutdown: closing the window stops the serial thread and closes the serial port cleanly.

Serial format expectations
- Time payload: `TIME:m:ss:ff` where m=minutes, ss=seconds (00–59), ff=centiseconds (00–99). Example: `TIME:0:12:34` → 00:12.34
- Targeting per-lane (optional leading digit): `N` prefix targets lane `N` (1-based). Examples:
   - `2TIME:0:45:67` → set lane 2 to 00:45.67 and stop it
   - `3DISQUALIFIED` → mark lane 3 with `D`
   - `Start Race` → resets all lanes and starts a new race

Run
1. Make sure Python 3.8+ is installed and `pyserial` is available if you plan to use a serial port:

    pip install pyserial

2. Run the app and point it to your serial port (Windows example):

```powershell
python timer_app.py --serial COM6 --baud 9600
```

Testing without hardware
- If you don't have a device attached you can use a serial terminal program (or a virtual serial pair) to send the messages above and observe the UI.

Built-in simulator (no hardware required)
- The app includes a simple built-in simulator to make testing easy without connecting a serial device.
- Start the app with `--simulate` to show simulator controls:

```powershell
python timer_app.py --simulate
```

- Simulator controls:
   - `Start Race` — resets displays and starts local ticking so timers count up.
   - Per-lane controls: `Stop`, `DQ`, `K` — these simulate the same serial messages the app accepts (stop-only, disqualify, final).
   - Buttons call the same internal handler as the serial reader, so simulator behavior matches real inputs.

CSV export (results)
- On each new race (when `Start Race` is triggered), the app writes the last race's displayed results to `results.csv` in the app folder. The file is overwritten on each reset.
- CSV format:
   - Header: `lane,time,marker`
   - Each row: lane number (1-based), time string (MM:SS.cc) or `D` if the lane was disqualified, and the marker (`D` or `K` or empty).
   - Example row: `2,00:45.67,K`

Run both simulator and a serial port
- You can combine `--simulate` with `--serial` if you want to see simulator-driven events alongside real serial input:

```powershell
python timer_app.py --serial COM6 --baud 9600 --simulate
```

Notes and troubleshooting
- The app suppresses terminal prints and shows the last received raw serial line internally (used for diagnostics).
- If a message arrives before a `Start Race` event, time messages are ignored until a race is started.
- If you want custom lane names or different behavior (e.g., multi-digit lane IDs), open an issue or ask for that enhancement.

License
- MIT
