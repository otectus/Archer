"""Compact, responsive telemetry overview with theme-aware history rendering."""

import math
import time
from collections import deque

from gi.repository import Adw, Gdk, Gtk, Pango, PangoCairo

from archer.widgets.forms import Page, label


def reading(data, key):
    value = data.get(key)
    validity = data.get("metric_validity", {})
    if key in validity and not validity[key]:
        return None
    # Older daemons cannot distinguish a missing sensor from zero.
    if key not in validity and value == 0 and ("temp" in key or "rpm" in key):
        return None
    if not isinstance(value, (float, int)) or not math.isfinite(value):
        return None
    return value


class MetricCard(Gtk.Box):
    def __init__(self, title, icon, callback):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.add_css_class("metric-card")
        top = Gtk.Box(spacing=8)
        top.append(Gtk.Image(icon_name=icon))
        heading = label(title, "heading")
        heading.set_hexpand(True)
        top.append(heading)
        button = Gtk.Button(icon_name="go-next-symbolic", tooltip_text=f"Open {title.lower()} settings")
        button.add_css_class("flat")
        button.connect("clicked", lambda *_: callback())
        top.append(button)
        self.append(top)
        self.value = label("—", "metric-value")
        self.append(self.value)
        self.detail = label("Waiting for data", "dim-label")
        self.append(self.detail)
        self.bar = Gtk.LevelBar(min_value=0, max_value=100)
        self.append(self.bar)

    def update(self, value, detail, level=None):
        self.value.set_label(value)
        self.detail.set_label(detail)
        self.bar.set_visible(level is not None)
        if level is not None:
            self.bar.set_value(min(100, max(0, level)))


class OverviewPage(Page):
    def __init__(self, state, navigate):
        super().__init__("Overview", "Your laptop, at a glance.", state, wide=True)
        self.availability(True)
        self.content.set_spacing(16)
        identity = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.device = label("Connecting to your device…", "heading")
        identity.append(self.device)
        self.summary = label("", "dim-label")
        identity.append(self.summary)
        self.content.append(identity)
        self.grid = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, homogeneous=True,
                                column_spacing=16, row_spacing=16, min_children_per_line=1,
                                max_children_per_line=2)
        self.grid.add_css_class("metric-grid")
        self.cpu = MetricCard("CPU", "computer-symbolic", lambda: navigate("performance"))
        self.gpu = MetricCard("GPU", "video-display-symbolic", lambda: navigate("display"))
        self.fans = MetricCard("Cooling", "preferences-system-symbolic", lambda: navigate("performance"))
        self.battery = MetricCard("Battery", "battery-symbolic", lambda: navigate("battery"))
        for card in (self.cpu, self.gpu, self.fans, self.battery):
            self.grid.append(card)
        self.content.append(self.grid)
        chart_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        chart_box.add_css_class("chart-card")
        title = label("Temperature history", "heading")
        chart_box.append(title)
        chart_box.append(label("CPU — solid    GPU ··· dashed     ·     Last 2 minutes", "dim-label"))
        self.chart = Gtk.DrawingArea(content_height=150, hexpand=True)
        self.chart.set_draw_func(self._draw)
        self.chart.update_property([Gtk.AccessibleProperty.LABEL], ["Temperature history for the last two minutes. Current values are shown in CPU and GPU cards."])
        chart_box.append(self.chart)
        self.content.append(chart_box)
        self.age = label("Waiting for the first reading", "caption")
        self.content.append(self.age)
        self.history = deque(maxlen=180)
        self.models = {}
        state.connect("settings-changed", self.load)
        state.connect("telemetry", self.monitor)
        state.connect("connection-changed", self._connection)
        state.connect("freshness-changed", self._freshness)
        self.watch_style(self.chart.queue_draw)

    def _freshness(self, _state, age):
        if self.state.status != "Connected":
            self.age.set_label(f"Last updated {age}s ago · {self.state.status.lower()}")
            if self.chart.get_mapped():
                self.chart.queue_draw()

    def load(self, _state, data):
        self.models = data.get("system_info") or {}
        self.device.set_label(self.models.get("product_name") or "Acer laptop")
        profile = (data.get("thermal_profile") or "Not available").replace("-", " ").capitalize()
        self.summary.set_label(f"Profile: {profile}" + ("  ·  Game Mode on" if data.get("game_mode") else ""))

    def _connection(self, _state, status):
        if status != "Connected":
            age = int(time.monotonic() - self.state.last_sample) if self.state.last_sample else None
            self.age.set_label(f"Last updated {age}s ago · {status.lower()}" if age is not None else "Waiting for the first reading")

    def monitor(self, _state, data):
        for prefix, card in (("cpu", self.cpu), ("gpu", self.gpu)):
            temp, usage = reading(data, prefix + "_temp"), reading(data, prefix + "_usage")
            usage_text = f"{usage:.0f}% usage" if usage is not None else "Usage unavailable"
            model = self.models.get(prefix + "_model") or prefix.upper()
            card.update(f"{temp:.0f} °C" if temp is not None else "Unavailable", f"{usage_text}\n{model}", usage)
        cpu, gpu = reading(data, "fan_rpm_cpu"), reading(data, "fan_rpm_gpu")
        self.fans.update(f"{cpu:.0f} RPM" if cpu is not None else "Unavailable",
                         f"CPU fan  ·  GPU {gpu:.0f} RPM" if gpu is not None else "CPU fan  ·  GPU fan unavailable")
        battery = data.get("battery_info") or {}
        pct = battery.get("percentage")
        present = battery.get("present", False)
        self.battery.update(f"{pct:.0f}%" if present and isinstance(pct, (int, float)) else "No battery",
                            " · ".join(str(v) for v in (battery.get("status"), battery.get("time_remaining")) if v) or "No battery detected",
                            pct if present and isinstance(pct, (int, float)) else None)
        now = time.monotonic()
        self.history.append((now, reading(data, "cpu_temp"), reading(data, "gpu_temp")))
        while self.history and now - self.history[0][0] > 120:
            self.history.popleft()
        self.age.set_label("Live readings · updated just now")
        self.chart.queue_draw()

    def _draw(self, area, cr, width, height):
        if width < 80 or height < 60:
            return
        style = area.get_style_context()
        _, fg = style.lookup_color("view_fg_color")
        _, accent = style.lookup_color("accent_color")
        manager = Adw.StyleManager.get_default()
        if manager.get_high_contrast():
            accent = fg
        second = Gdk.RGBA()
        second.parse("#c64600" if not manager.get_dark() else "#ffbe6f")
        if manager.get_high_contrast():
            second = fg
        left, top, w, h = 38, 10, width - 50, height - 36
        layout = area.create_pango_layout("")
        font = layout.get_font_description() or area.get_pango_context().get_font_description().copy()
        font.set_size(10 * Pango.SCALE)
        layout.set_font_description(font)

        def text(value, x, y):
            cr.set_source_rgba(fg.red, fg.green, fg.blue, .85)
            layout.set_text(value, -1)
            cr.move_to(x, y)
            PangoCairo.show_layout(cr, layout)

        for temp in (0, 25, 50, 75, 100):
            y = top + h * (1 - temp / 110)
            cr.set_source_rgba(fg.red, fg.green, fg.blue, .25 if manager.get_high_contrast() else .12)
            cr.set_line_width(1)
            cr.move_to(left, y)
            cr.line_to(left + w, y)
            cr.stroke()
            text(str(temp) + "°", 0, y - 7)
        text("−2 min", left, height - 18)
        text("Now", width - 34, height - 18)
        if not self.history:
            text("Waiting for readings", left + 12, top + h / 2)
            return
        now = time.monotonic()
        cr.save()
        cr.rectangle(left, top, w, h)
        cr.clip()
        for index, color in ((1, accent), (2, second)):
            cr.set_source_rgba(color.red, color.green, color.blue, 1)
            cr.set_line_width(2)
            cr.set_dash([] if index == 1 else [5, 4])
            previous = None
            for point in self.history:
                ts, value = point[0], point[index]
                if value is None:
                    cr.stroke()
                    previous = None
                    continue
                x, y = left + w * (1 - (now - ts) / 120), top + h * (1 - value / 110)
                if previous is None or ts - previous > 6:
                    cr.stroke()
                    cr.move_to(x, y)
                else:
                    cr.line_to(x, y)
                previous = ts
            cr.stroke()
        cr.restore()
