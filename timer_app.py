import tkinter as tk
from tkinter import ttk
import time
import threading
import argparse
from typing import Optional
import csv
try:
    import serial
except Exception:
    serial = None


class Lane:
    """Represents a single timer lane with independent stop control."""
    def __init__(self, parent, lane_index, format_func):
        self.parent = parent
        self.lane_index = lane_index
        self.format = format_func

        self.running = False
        self._start_time = None
        self.elapsed_ms = 0
        self._timer_id = None

        # Frame for lane
        self.frame = ttk.Frame(parent, relief=tk.RIDGE, padding=8)

        # Lane number header
        self.header = ttk.Label(self.frame, text=f"Lane {self.lane_index + 1}", font=(None, 10, "bold"))
        self.header.pack(side=tk.TOP)

        # Display text for the lane (use StringVar so updates are cheap)
        self.text_var = tk.StringVar(value=self.format(self.elapsed_ms))
        # Create a horizontal container so the marker sits to the right of the timer
        content = ttk.Frame(self.frame)
        content.pack(side=tk.TOP, pady=(0, 8), fill=tk.X)
        self.label = ttk.Label(content, textvariable=self.text_var, font=(None, 24))
        # add a bit more right padding so the marker doesn't collide with the timer
        # allow the label to expand so the marker doesn't change overall frame size
        self.label.pack(side=tk.LEFT, padx=(0, 12), fill=tk.X, expand=True)
        # whether this lane has been stopped by serial input
        self.stopped = False
        # no manual controls in display-only mode
        # (stopping can still be done via numbered serial messages)
        # marker to the right of the timer (D = disqualified, K = final ok)
        # reserve a small fixed-width area for the marker so showing/hiding it
        # won't resize the lane frame. initialize with a space so the width is kept.
        self.marker_var = tk.StringVar(value=" ")
        # make the marker larger and bold
        self.marker_label = ttk.Label(content, textvariable=self.marker_var, font=(None, 20, 'bold'), width=2, anchor='center')
        # place marker immediately to the right of the timer
        self.marker_label.pack(side=tk.LEFT, padx=(0, 8))

    # No per-lane controls in display-only mode

    def grid(self, row, column):
        self.frame.grid(row=row, column=column, padx=8, pady=8, sticky="nsew")

    # Lane is display-only; no internal timing methods


class TimerApp:
    def __init__(self, root, lanes=6, cols=3, serial_port: Optional[str] = None, serial_baud: int = 9600, simulate: bool = True):
        self.root = root
        self.root.title("6-Lane Timer")

        self.lanes_count = lanes
        self.cols = cols
        self.rows = (lanes + cols - 1) // cols

        # Container for lanes
        self.container = ttk.Frame(root, padding=12)
        self.container.pack(fill=tk.BOTH, expand=True)

        # Create lanes
        self.lanes = []
        for i in range(self.lanes_count):
            lane = Lane(self.container, i, self._format_time)
            # marker to know which epoch the lane was stopped in
            lane.stopped_epoch = -1
            row = i // self.cols
            col = i % self.cols
            lane.grid(row=row, column=col)
            self.lanes.append(lane)

        # Configure grid weights so lanes expand
        for c in range(self.cols):
            self.container.columnconfigure(c, weight=1)
        for r in range(self.rows):
            self.container.rowconfigure(r, weight=1)

        # (Removed visible informational/status labels while keeping the internal
        # status var so serial reader updates remain safe.)
        self.status_var = tk.StringVar(value="No serial data")

        # Serial handling
        self.serial_port = serial_port
        self.serial_baud = serial_baud
        self._serial_thread = None
        self._serial_stop = threading.Event()
        # epoch counter increments on each Start Race — used to ignore stale callbacks
        self.race_epoch = 0
        # whether a race is currently running; Start Race sets True
        self.race_running = False

        # buffer for last N times (newest first)
        self.max_history = self.lanes_count
        self.history = []  # list of milliseconds, newest first
        # results CSV file (overwritten on each reset)
        self.results_file = 'results.csv'

        # Start serial reader if requested and pyserial is available
        if self.serial_port and serial is not None:
            self._start_serial_reader(self.serial_port)
        elif self.serial_port and serial is None:
            # pyserial not available — silently continue
            pass

        # Optional simulator UI with buttons for testing (Start Race + per-lane Stop/DQ/K)
        self.simulate = bool(simulate)
        if self.simulate:
            try:
                sim_frame = ttk.Frame(root, padding=(8, 4))
                sim_frame.pack(fill=tk.X)
                # Start Race button
                start_btn = ttk.Button(sim_frame, text="Start Race", command=lambda: self._handle_serial_line('Start Race'))
                start_btn.pack(side=tk.LEFT, padx=(0, 8))

                # Per-lane control buttons
                lanes_frame = ttk.Frame(root, padding=(4, 4))
                lanes_frame.pack(fill=tk.X)
                for i in range(self.lanes_count):
                    lf = ttk.Frame(lanes_frame, padding=(2, 2))
                    lf.pack(side=tk.LEFT, padx=4)
                    ttk.Label(lf, text=f"{i+1}").pack()
                    # Stop button -> sends e.g. '1TIME' (stop-only)
                    btn_stop = ttk.Button(lf, text="Stop", width=6, command=(lambda n=i: self._handle_serial_line(f"{n+1}TIME")))
                    btn_dq = ttk.Button(lf, text="DQ", width=4, command=(lambda n=i: self._handle_serial_line(f"{n+1}DISQUALIFIED")))
                    btn_k = ttk.Button(lf, text="K", width=4, command=(lambda n=i: self._handle_serial_line(f"{n+1}FINALTIME")))
                    btn_stop.pack()
                    btn_dq.pack(fill=tk.X, pady=(2, 0))
                    btn_k.pack(fill=tk.X, pady=(2, 0))
            except Exception:
                pass

        # tick updater id for local running timers (started on Start Race)
        self._tick_id = None
        self._race_start_time = None

        # Bind close
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _format_time(self, ms: int) -> str:
        # ms is integer milliseconds
        seconds = ms // 1000
        # hundredths of a second = centiseconds (1 cs = 10 ms)
        ms_remainder = (ms % 1000) // 10  # centiseconds (00-99)
        mins = seconds // 60
        secs = seconds % 60
        return f"{mins:02}:{secs:02}.{ms_remainder:02}"

    # Serial parsing: expect lines like 'Time:m:ss:ff' where m is 1-digit minutes, ss are seconds, ff are centiseconds
    def _parse_serial_line(self, line: str):
        """Parse line and return a tuple (kind, value, lane_index)

        kind: 'time' | 'dq' | 'final' | None
        value: milliseconds for 'time', else None
        lane_index: 0-based lane index or None
        """
        raw = line.strip()
        lane_index = None
        if raw and raw[0].isdigit():
            lane_index = int(raw[0]) - 1
            raw = raw[1:].lstrip()
        # Normalize
        token = raw.upper()
        # Start with TIME: or TIME without colon
        if token.startswith('TIME:') or token.startswith('TIME'):
            # Extract payload after first ':' if present
            payload = raw.split(':', 1)[1] if ':' in raw else raw[len('TIME'):]
            payload = payload.lstrip(':').strip()
            # If there's no payload after TIME (e.g. "1TIME" or "TIME"), treat as stop-only
            if not payload:
                return ('stop', None, lane_index)
            try:
                parts = payload.split(":")
                if len(parts) != 3:
                    return (None, None, lane_index)
                mins_part, secs_part, cs_part = parts
                mins = int(mins_part)
                secs = int(secs_part)
                cs = int(cs_part)
                if not (0 <= cs <= 99):
                    return (None, None, lane_index)
                total_ms = (mins * 60 + secs) * 1000 + (cs * 10)
                return ('time', total_ms, lane_index)
            except Exception:
                return (None, None, lane_index)
        # DISQUALIFIED (write 'D')
        if token.startswith('DISQUAL') or token.startswith('DISQUALIFIED'):
            return ('dq', None, lane_index)
        # FINALTIME or FINAL (write 'K')
        if token.startswith('FINAL') or token.startswith('FINALTIME'):
            return ('final', None, lane_index)
        return (None, None, lane_index)

    def _start_serial_reader(self, port: str):
        def reader():
            try:
                # shorter timeout so shutdown is more responsive
                ser = serial.Serial(port, baudrate=self.serial_baud, timeout=0.2)
                self._ser = ser
            except Exception:
                # failed to open serial port — silently return
                # Try to list available ports to help diagnose the issue
                try:
                    from serial.tools import list_ports
                    ports = list(list_ports.comports())
                    if ports:
                        # listing suppressed
                        pass
                    else:
                        pass
                except Exception:
                    pass
                return
            # opened serial port
            try:
                while not self._serial_stop.is_set():
                    try:
                        raw_bytes = ser.readline()
                        raw = raw_bytes.decode(errors="ignore")
                    except Exception:
                        break
                    raw_str = raw.strip()
                    # schedule handling of this serial line on the main thread
                    try:
                        self.root.after(0, lambda s=raw_str: self._handle_serial_line(s))
                    except Exception:
                        try:
                            self.container.after(0, lambda s=raw_str: self._handle_serial_line(s))
                        except Exception:
                            pass
            finally:
                try:
                    ser.close()
                except Exception:
                    pass
                try:
                    self._ser = None
                except Exception:
                    pass

        t = threading.Thread(target=reader, daemon=True)
        t.start()
        self._serial_thread = t
    
    def _handle_serial_line(self, raw: str):
        """Central handler for a single serial line. Runs on the main thread."""
        raw_str = raw.strip()
        # update status var
        try:
            self.status_var.set(raw_str)
        except Exception:
            pass

        # Start Race signal
        try:
            if raw_str.lower() == 'start race' or 'start race' in raw_str.lower():
                try:
                    self._start_race()
                except Exception:
                    pass
                return
        except Exception:
            pass

        parsed = self._parse_serial_line(raw_str)
        if parsed is None:
            return

        try:
            kind, value, target_lane = parsed
        except Exception:
            return

        # ignore messages if a race has not been started
        if not self.race_running:
            return

        current_epoch = self.race_epoch

        try:
            if kind == 'time':
                total_ms = value
                if target_lane is not None and 0 <= target_lane < self.lanes_count:
                    # stop and set
                    lane = self.lanes[target_lane]
                    lane.stopped = True
                    try:
                        lane.stopped_epoch = current_epoch
                    except Exception:
                        pass
                    try:
                        lane.text_var.set(self._format_time(total_ms))
                    except Exception:
                        pass
                    try:
                        self.root.update_idletasks()
                    except Exception:
                        pass
                else:
                    # mirror to unstopped lanes
                    for idx, lane in enumerate(self.lanes):
                        if not getattr(lane, 'stopped', False):
                            try:
                                lane.text_var.set(self._format_time(total_ms))
                            except Exception:
                                pass
                    try:
                        self.root.update_idletasks()
                    except Exception:
                        pass

            elif kind == 'stop':
                if target_lane is not None and 0 <= target_lane < self.lanes_count:
                    lane = self.lanes[target_lane]
                    lane.stopped = True
                    try:
                        lane.stopped_epoch = current_epoch
                    except Exception:
                        pass
                    try:
                        self.root.update_idletasks()
                    except Exception:
                        pass

            elif kind == 'dq':
                if target_lane is not None and 0 <= target_lane < self.lanes_count:
                    lane = self.lanes[target_lane]
                    try:
                        lane.marker_var.set('D')
                        self.root.update_idletasks()
                    except Exception:
                        pass

            elif kind == 'final':
                if target_lane is not None and 0 <= target_lane < self.lanes_count:
                    lane = self.lanes[target_lane]
                    try:
                        lane.marker_var.set('K')
                        self.root.update_idletasks()
                    except Exception:
                        pass

        except Exception:
            pass
    # display-only app: no start_all/reset_all

    def _on_close(self):
        # Cancel any pending after callbacks in lanes
        for lane in self.lanes:
            if getattr(lane, "_timer_id", None):
                try:
                    lane.parent.after_cancel(lane._timer_id)
                except Exception:
                    pass
        # Stop serial reader thread if running
        if self._serial_thread is not None:
            try:
                self._serial_stop.set()
            except Exception:
                pass
            # close the serial port to unblock reads
            try:
                if getattr(self, '_ser', None) is not None:
                    try:
                        self._ser.cancel_read()
                    except Exception:
                        pass
                    try:
                        self._ser.close()
                    except Exception:
                        pass
            except Exception:
                pass
            try:
                self._serial_thread.join(timeout=1.0)
            except Exception:
                pass
        # finally destroy the root to close the window
        try:
            self.root.destroy()
        except Exception:
            try:
                self.root.quit()
            except Exception:
                pass

    def _start_race(self):
        """Reset internal state for a new race: clear stopped flags, reset displays and history."""
        # Before starting a new race, write the previous race's displayed results
        # to a CSV file (overwrite each reset). We only write if there is any
        # non-default data (a non-zero time or a marker).
        try:
            rows = []
            any_data = False
            for idx, lane in enumerate(self.lanes):
                try:
                    t = lane.text_var.get()
                except Exception:
                    t = self._format_time(0)
                try:
                    marker = lane.marker_var.get().strip()
                except Exception:
                    marker = ""
                stopped = bool(getattr(lane, 'stopped', False))
                rows.append((idx + 1, t, marker, int(stopped)))
                if t != self._format_time(0) or marker:
                    any_data = True
            if any_data:
                try:
                    # prepare CSV rows: replace time with 'D' when marker == 'D'
                    csv_rows = []
                    for lane_idx, t, marker, stopped in rows:
                        out_time = t
                        if marker == 'D':
                            out_time = 'D'
                        csv_rows.append((lane_idx, out_time, marker))
                    with open(self.results_file, 'w', newline='', encoding='utf-8') as f:
                        writer = csv.writer(f)
                        writer.writerow(['lane', 'time', 'marker'])
                        for r in csv_rows:
                            writer.writerow(r)
                    try:
                        self.status_var.set(f"Saved results to {self.results_file}")
                    except Exception:
                        pass
                except Exception:
                    pass
        except Exception:
            pass

        # bump epoch so any queued callbacks from previous races are ignored
        try:
            self.race_epoch += 1
        except Exception:
            self.race_epoch = 0
        # mark race as running
        try:
            self.race_running = True
        except Exception:
            pass
        # start local ticking so the UI shows running times until lanes are stopped
        try:
            # cancel previous tick if any
            if getattr(self, '_tick_id', None):
                try:
                    self.root.after_cancel(self._tick_id)
                except Exception:
                    pass
            self._race_start_time = time.time()
            def tick():
                try:
                    if not self.race_running:
                        return
                    now = time.time()
                    elapsed_ms = int((now - self._race_start_time) * 1000)
                    for lane in self.lanes:
                        if not getattr(lane, 'stopped', False):
                            try:
                                lane.text_var.set(self._format_time(elapsed_ms))
                            except Exception:
                                pass
                    try:
                        self._tick_id = self.root.after(50, tick)
                    except Exception:
                        try:
                            self._tick_id = self.container.after(50, tick)
                        except Exception:
                            self._tick_id = None
                except Exception:
                    pass
            # schedule first tick
            try:
                self._tick_id = self.root.after(50, tick)
            except Exception:
                try:
                    self._tick_id = self.container.after(50, tick)
                except Exception:
                    self._tick_id = None
        except Exception:
            pass
        # Clear history buffer
        self.history.clear()
        # Clear stopped flags and reset displays
        for lane in self.lanes:
            lane.stopped = False
            # reset any stopped epoch marker so future callbacks won't be considered stopped
            try:
                lane.stopped_epoch = -1
            except Exception:
                pass
            try:
                lane.text_var.set(self._format_time(0))
            except Exception:
                pass
            # clear any D/K marker from previous races
            try:
                lane.marker_var.set("")
            except Exception:
                pass
        # Force a redraw
        try:
            self.root.update_idletasks()
        except Exception:
            try:
                self.container.update_idletasks()
            except Exception:
                pass


    def _refresh_labels(self, history_snapshot):
        """Update all lane labels from a history snapshot (newest first). Runs on main thread."""
        for idx, lane in enumerate(self.lanes):
            try:
                if idx < len(history_snapshot):
                    lane.text_var.set(self._format_time(history_snapshot[idx]))
                else:
                    lane.text_var.set(self._format_time(0))
            except Exception:
                pass
        # Force a single redraw pass
        try:
            self.root.update_idletasks()
        except Exception:
            try:
                self.container.update_idletasks()
            except Exception:
                pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--serial", help="Serial port to read Time lines from (e.g. COM6)")
    parser.add_argument("--baud", type=int, default=9600, help="Serial baud rate (default 9600)")
    args = parser.parse_args()

    root = tk.Tk()
    app = TimerApp(root, serial_port=args.serial, serial_baud=args.baud)
    root.mainloop()


if __name__ == "__main__":
    main()
