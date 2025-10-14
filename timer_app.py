import tkinter as tk
from tkinter import ttk
import time


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

        self.label = ttk.Label(self.frame, text=self.format(self.elapsed_ms), font=(None, 24))
        self.label.pack(side=tk.TOP, pady=(0, 8))

        # Individual Start/Stop button for the lane
        self.stop_text = tk.StringVar(value="Start")
        # We'll initialize as stopped; UI will be driven by parent start
        self.stop_btn = ttk.Button(self.frame, textvariable=self.stop_text, command=self.toggle)
        self.stop_btn.pack(side=tk.TOP)

    def grid(self, row, column):
        self.frame.grid(row=row, column=column, padx=8, pady=8, sticky="nsew")

    def _tick(self):
        if not self.running:
            return
        now = time.perf_counter()
        elapsed = (now - self._start_time) * 1000.0
        self.elapsed_ms = int(elapsed)
        self.label.config(text=self.format(self.elapsed_ms))
        self._timer_id = self.parent.after(10, self._tick)

    def start(self):
        if self.running:
            return
        self.running = True
        self.stop_text.set("Stop")
        self._start_time = time.perf_counter() - (self.elapsed_ms / 1000.0)
        self._timer_id = self.parent.after(10, self._tick)

    def stop(self):
        if not self.running:
            return
        self.running = False
        self.stop_text.set("Start")
        if self._timer_id:
            try:
                self.parent.after_cancel(self._timer_id)
            except Exception:
                pass
            self._timer_id = None

    def toggle(self):
        if self.running:
            self.stop()
        else:
            self.start()

    def reset(self):
        was_running = self.running
        if was_running:
            self.stop()
        self.elapsed_ms = 0
        self.label.config(text=self.format(self.elapsed_ms))
        self._start_time = None


class TimerApp:
    def __init__(self, root, lanes=6, cols=3):
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

        # Shared controls
        controls = ttk.Frame(root, padding=(8, 4))
        controls.pack(fill=tk.X)

        self.start_all_btn = ttk.Button(controls, text="Start All", command=self.start_all)
        self.start_all_btn.pack(side=tk.LEFT, padx=(0, 8))

        self.reset_all_btn = ttk.Button(controls, text="Reset All", command=self.reset_all)
        self.reset_all_btn.pack(side=tk.LEFT)

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

    def start_all(self):
        """Start all lanes."""
        for lane in self.lanes:
            lane.start()

    def reset_all(self):
        """Reset all lanes."""
        for lane in self.lanes:
            lane.reset()

    def _on_close(self):
        # Cancel any pending after callbacks in lanes
        for lane in self.lanes:
            if getattr(lane, "_timer_id", None):
                try:
                    lane.parent.after_cancel(lane._timer_id)
                except Exception:
                    pass
        self.root.destroy()


def main():
    root = tk.Tk()
    app = TimerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
