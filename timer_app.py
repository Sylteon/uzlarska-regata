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

        # Serial handling
        self.serial_port = serial_port
        self.serial_baud = serial_baud
        self._serial_thread = None
        self._serial_stop = threading.Event()

        # buffer for last N times (newest first)
        self.max_history = self.lanes_count
        self.history = []  # list of milliseconds, newest first

        # Start serial reader if requested and pyserial is available
        if self.serial_port and serial is not None:
            self._start_serial_reader(self.serial_port)
        elif self.serial_port and serial is None:
            print(f"Warning: pyserial not available; cannot open serial port {self.serial_port}")

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
            return total_ms
        except Exception:
            return None

    def _start_serial_reader(self, port: str):
        def reader():
            try:
                ser = serial.Serial(port, baudrate=self.serial_baud, timeout=1)
            except Exception:
                print(f"Failed to open serial port {port} at baud {self.serial_baud}")
                # Try to list available ports to help diagnose the issue
                try:
                    from serial.tools import list_ports
                    ports = list(list_ports.comports())
                    if ports:
                        print("Available serial ports:")
                        for p in ports:
                            print(f"  {p.device} - {p.description}")
                    else:
                        print("No serial ports found by pyserial.")
                except Exception:
                    print("pyserial.tools.list_ports not available to enumerate ports.")
                return
            print(f"Opened serial port {port} at baud {self.serial_baud}")
            with ser:
                while not self._serial_stop.is_set():
                    try:
                        raw_bytes = ser.readline()
                        raw = raw_bytes.decode(errors="ignore")
                    except Exception as e:
                        print(f"Serial read error: {e}")
                        break
                    # Read and parse line (do not print to terminal)
                    raw_str = raw.strip()
                    parsed = self._parse_serial_line(raw)
                    if parsed is None:
                        # didn't parse, continue
                        continue
                    # Mirror mode: set all lanes to the same parsed timestamp immediately
                    try:
                        hist_snapshot = [parsed] * self.lanes_count
                        try:
                            self.root.after(0, lambda h=hist_snapshot: self._refresh_labels(h))
                        except Exception:
                            try:
                                self.container.after(0, lambda h=hist_snapshot: self._refresh_labels(h))
                            except Exception:
                                pass
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
        # Stop serial reader
        if self._serial_thread:
            self._serial_stop.set()
            try:
                self._serial_thread.join(timeout=1.0)
            except Exception:
                pass
        self.root.destroy()

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
