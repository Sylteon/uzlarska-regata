import tkinter as tk
from tkinter import ttk
import time
import threading
import argparse
from typing import Optional
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
        self.label = ttk.Label(self.frame, textvariable=self.text_var, font=(None, 24))
        self.label.pack(side=tk.TOP, pady=(0, 8))
        # whether this lane has been stopped by serial input
        self.stopped = False
        # no manual controls in display-only mode
        # (stopping can still be done via numbered serial messages)

    # No per-lane controls in display-only mode

    def grid(self, row, column):
        self.frame.grid(row=row, column=column, padx=8, pady=8, sticky="nsew")

    # Lane is display-only; no internal timing methods


class TimerApp:
    def __init__(self, root, lanes=6, cols=3, serial_port: Optional[str] = None, serial_baud: int = 9600):
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

        # Informational label - display-only mode
        controls = ttk.Frame(root, padding=(8, 4))
        controls.pack(fill=tk.X)
        info = ttk.Label(controls, text="Display-only mode: showing latest serial times")
        info.pack(side=tk.LEFT)
        # status on the right (last serial line) — displayed in GUI, not printed to terminal
        self.status_var = tk.StringVar(value="No serial data")
        status_lbl = ttk.Label(controls, textvariable=self.status_var)
        status_lbl.pack(side=tk.RIGHT)

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

        # Start serial reader if requested and pyserial is available
        if self.serial_port and serial is not None:
            self._start_serial_reader(self.serial_port)
        elif self.serial_port and serial is None:
            # pyserial not available — silently continue
            pass

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
    def _parse_serial_line(self, line: str) -> Optional[int]:
        line = line.strip()
        # optionally accept a leading lane number indicator, e.g. "1 TIME:..." or "1TIME:..."
        lane_index = None
        if line and line[0].isdigit():
            lane_index = int(line[0]) - 1  # convert 1-based to 0-based
            # strip leading digit and any space
            line = line[1:].lstrip()
        # accept case-insensitive 'Time:' or 'TIME:' prefix
        if not line.lower().startswith("time:"):
            return None
        payload = line.split(":", 1)[1]
        # Expected format: m:ss:ff
        try:
            parts = payload.split(":")
            if len(parts) != 3:
                return None
            mins_part, secs_part, cs_part = parts
            mins = int(mins_part)
            secs = int(secs_part)
            cs = int(cs_part)  # centiseconds (00-99)
            if not (0 <= cs <= 99):
                return None
            total_ms = (mins * 60 + secs) * 1000 + (cs * 10)
            return (total_ms, lane_index)
        except Exception:
            return None

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
                    # Read the raw serial line and update GUI status
                    raw_str = raw.strip()
                    try:
                        # schedule update of status label on main thread
                        try:
                            self.root.after(0, lambda s=raw_str: self.status_var.set(s))
                        except Exception:
                            try:
                                self.container.after(0, lambda s=raw_str: self.status_var.set(s))
                            except Exception:
                                pass
                    except Exception:
                        pass
                    # Check for start race signal (case-insensitive)
                    if raw_str.lower() == 'start race' or 'start race' in raw_str.lower():
                        # Schedule a main-thread start/reset action
                        try:
                            self.root.after(0, self._start_race)
                        except Exception:
                            try:
                                self.container.after(0, self._start_race)
                            except Exception:
                                pass
                        # continue reading
                        continue
                    parsed = self._parse_serial_line(raw)
                    if parsed is None:
                        # didn't parse, continue
                        continue
                    # debug parse result
                    # parse debug suppressed
                    # ignore TIME messages if a race has not been started
                    if not self.race_running:
                        continue
                    # parsed may be a tuple (ms, lane_index)
                    if isinstance(parsed, tuple):
                        total_ms, target_lane = parsed
                    else:
                        total_ms, target_lane = parsed, None

                    # capture current epoch so scheduled callbacks don't apply after a Start Race
                    current_epoch = self.race_epoch

                    try:
                        # If a target lane is specified, stop that lane and set its time
                        if target_lane is not None and 0 <= target_lane < self.lanes_count:
                            def stop_and_set(idx=target_lane, ms=total_ms, epoch=current_epoch):
                                # ignore if a new race started since this callback was queued
                                if epoch != self.race_epoch:
                                    return
                                # action debug suppressed
                                lane = self.lanes[idx]
                                lane.stopped = True
                                # record the epoch this lane was stopped in
                                try:
                                    lane.stopped_epoch = epoch
                                except Exception:
                                    pass
                                lane.text_var.set(self._format_time(ms))
                                try:
                                    self.root.update_idletasks()
                                except Exception:
                                    pass

                            try:
                                self.root.after(0, stop_and_set)
                            except Exception:
                                try:
                                    self.container.after(0, stop_and_set)
                                except Exception:
                                    pass
                        else:
                            # Mirror mode for unstopped lanes only
                            hist_snapshot = []
                            for _ in range(self.lanes_count):
                                hist_snapshot.append(total_ms)
                            # apply only to lanes that are not stopped
                            def set_unstopped(h=hist_snapshot, epoch=current_epoch):
                                # ignore if a new race started
                                if epoch != self.race_epoch:
                                    return
                                # action debug suppressed
                                for idx, lane in enumerate(self.lanes):
                                    if not getattr(lane, 'stopped', False):
                                        lane.text_var.set(self._format_time(h[idx]))
                                try:
                                    self.root.update_idletasks()
                                except Exception:
                                    pass

                                # nothing else to update for Reset All in this mode

                            try:
                                self.root.after(0, set_unstopped)
                            except Exception:
                                try:
                                    self.container.after(0, set_unstopped)
                                except Exception:
                                    pass
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
