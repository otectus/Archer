"""Native settings groups with guarded loading, drafts and transactional feedback."""

import threading

from gi.repository import Adw, Gdk, GLib, GObject, Gtk, Pango


def label(text, css=None):
    widget = Gtk.Label(label=text, xalign=0, wrap=True)
    widget.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
    if css:
        widget.add_css_class(css)
    return widget


def confirm(parent, title, body, action, callback, destructive=False):
    dialog = Adw.AlertDialog(heading=title, body=body)
    dialog.add_response("cancel", "Cancel")
    dialog.add_response("apply", action)
    dialog.set_default_response("cancel")
    dialog.set_close_response("cancel")
    dialog.set_response_appearance("apply", Adw.ResponseAppearance.DESTRUCTIVE
                                   if destructive else Adw.ResponseAppearance.SUGGESTED)
    dialog.connect("response", lambda _dialog, response: callback() if response == "apply" else None)
    dialog.present(parent)
    return dialog


class Page(Gtk.ScrolledWindow):
    def __init__(self, title, description, state, wide=False):
        super().__init__(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.state = state
        self.forms = []
        self._style_handlers = []
        self.content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24)
        self.content.add_css_class("page-content")
        clamp = Adw.Clamp(maximum_size=1100 if wide else 800)
        clamp.set_child(self.content)
        self.set_child(clamp)
        self.heading = label(title, "title-1")
        self.heading.set_focusable(True)
        intro = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        intro.append(self.heading)
        intro.append(label(description, "dim-label"))
        self.content.append(intro)
        self.unavailable = Adw.StatusPage(
            title="Waiting for your device", description="Hardware controls appear when Archer connects.",
            icon_name="computer-symbolic")
        self.content.append(self.unavailable)
        self.missing = label("", "dim-label")
        self.missing.set_visible(False)
        self.content.append(self.missing)

    def availability(self, available, detail="These controls are not supported on this device."):
        self.unavailable.set_visible(not available)
        self.unavailable.set_title("Controls unavailable")
        self.unavailable.set_description(detail)

    def form(self, *args, **kwargs):
        form = Form(self.state, *args, **kwargs)
        self.forms.append(form)
        self.content.append(form)
        return form

    @property
    def dirty(self):
        return any(form.dirty for form in self.forms)

    def watch_style(self, callback):
        manager = Adw.StyleManager.get_default()
        for name in ("dark", "high-contrast", "accent-color"):
            handler = manager.connect(f"notify::{name}", lambda *_: callback())
            self._style_handlers.append((manager, handler))

    def shutdown(self):
        for manager, handler in self._style_handlers:
            manager.disconnect(handler)
        self._style_handlers.clear()


class Form(Gtk.Box):
    __gsignals__ = {
        "completed": (GObject.SignalFlags.RUN_FIRST, None, (bool,)),
    }

    def __init__(self, state, title, description="", save=None, immediate=False,
                 action="Apply", confirmation=None, destructive=False):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.state = state
        self.save = save
        self.immediate = immediate
        self.confirmation = confirmation
        self.destructive = destructive
        self.fields = {}
        self.confirmed = {}
        self.loading = False
        self.pending = False
        self.allowed = True
        self.dirty = False
        self.baseline_unknown = False
        self.restart_delay = 0
        self._awaiting_restart = False
        self.group = Adw.PreferencesGroup(title=title, description=description)
        self.append(self.group)
        self.feedback = Gtk.Label(label="", xalign=0, wrap=True, accessible_role=Gtk.AccessibleRole.STATUS)
        self.feedback.set_visible(False)
        self.append(self.feedback)
        self.actions = Gtk.Box(spacing=8, halign=Gtk.Align.END)
        self.spinner = Gtk.Spinner(visible=False)
        self.actions.append(self.spinner)
        self.reset_button = Gtk.Button(label="Reset")
        self.reset_button.get_child().set_wrap(True)
        self.reset_button.connect("clicked", lambda *_: self.reset())
        self.actions.append(self.reset_button)
        self.apply_button = Gtk.Button(label=action)
        self.apply_button.get_child().set_wrap(True)
        self.apply_button.add_css_class("destructive-action" if destructive else "suggested-action")
        self.apply_button.connect("clicked", lambda *_: self.submit())
        self.actions.append(self.apply_button)
        self.append(self.actions)
        self.actions.set_visible(save is not None and not immediate)
        state.connect("connection-changed", self._connection_changed)
        self._sensitivity()

    def _connection_changed(self, _state, status):
        self._sensitivity()
        if self._awaiting_restart and status == "Connected":
            self._awaiting_restart = False
            self.feedback.set_label("Service connected.")

    def _register(self, key, widget, get, set_value, signal):
        self.fields[key] = (get, set_value)
        widget.connect(signal, lambda *_: self.changed())
        self.group.add(widget)
        return widget

    def switch(self, key, title, subtitle=""):
        row = Adw.SwitchRow(title=title, subtitle=subtitle)
        return self._register(key, row, row.get_active, row.set_active, "notify::active")

    def combo(self, key, title, choices, subtitle=""):
        row = Adw.ComboRow(title=title, subtitle=subtitle, model=Gtk.StringList.new(choices))
        return self._register(key, row, row.get_selected, row.set_selected, "notify::selected")

    def spin(self, key, title, minimum, maximum, step=1):
        row = Adw.SpinRow.new_with_range(minimum, maximum, step)
        row.set_title(title)
        return self._register(key, row, lambda: int(row.get_value()), row.set_value, "notify::value")

    def radio(self, key, choices):
        buttons = []
        rows = []
        for title, description in choices:
            row = Adw.ActionRow(title=title, subtitle=description, activatable=True)
            button = Gtk.CheckButton(valign=Gtk.Align.CENTER)
            if buttons:
                button.set_group(buttons[0])
            buttons.append(button)
            button.update_property([Gtk.AccessibleProperty.LABEL], [title])
            button.connect("toggled", lambda btn: self.changed() if btn.get_active() else None)
            row.add_prefix(button)
            row.set_activatable_widget(button)
            self.group.add(row)
            rows.append(row)

        def select(index):
            for i, button in enumerate(buttons):
                button.set_active(i == index)

        self.fields[key] = (lambda: next((i for i, button in enumerate(buttons) if button.get_active()), 0), select)
        return rows

    def color(self, key, title):
        row = Adw.EntryRow(title=title)
        row.set_input_purpose(Gtk.InputPurpose.FREE_FORM)
        swatch = Gtk.ColorDialogButton(dialog=Gtk.ColorDialog(with_alpha=False), valign=Gtk.Align.CENTER)
        row.add_suffix(swatch)

        def set_color(text):
            rgba = Gdk.RGBA()
            if rgba.parse(text):
                swatch.set_rgba(rgba)
            row.set_text(text)

        def picked(*_):
            rgba = swatch.get_rgba()
            text = "#{:02x}{:02x}{:02x}".format(*(round(c * 255) for c in (rgba.red, rgba.green, rgba.blue)))
            if row.get_text().lower() != text:
                row.set_text(text)

        swatch.connect("notify::rgba", picked)
        return self._register(key, row, row.get_text, set_color, "changed")

    def info(self, title, subtitle="—", copyable=False):
        row = Adw.ActionRow(title=title, subtitle=subtitle)
        if copyable:
            copy = Gtk.Button(icon_name="edit-copy-symbolic", valign=Gtk.Align.CENTER,
                              tooltip_text=f"Copy {title.lower()}")
            copy.add_css_class("flat")
            copy.connect("clicked", lambda *_: row.get_clipboard().set(row.get_subtitle()))
            row.add_suffix(copy)
        self.group.add(row)
        return row

    def values(self):
        return {key: get() for key, (get, _) in self.fields.items()}

    def load(self, values, force=False):
        if not force and self.pending:
            return
        if not force and self.dirty:
            self.confirmed = dict(values)
            self.dirty = self.values() != self.confirmed
            self._sensitivity()
            return
        self.confirmed = dict(values)
        self.loading = True
        try:
            for key, value in values.items():
                if key in self.fields:
                    self.fields[key][1](value)
        finally:
            self.loading = False
        self.dirty = False
        self._sensitivity()

    def reset(self):
        self.load(self.confirmed, force=True)
        self.feedback.set_visible(False)

    def changed(self):
        if self.loading or self.pending:
            return
        self.dirty = self.values() != self.confirmed
        self._sensitivity()
        if self.immediate and self.dirty and self.state.writable:
            self.submit()

    def _sensitivity(self):
        enabled = self.allowed and not self.pending and (self.save is None or self.state.writable)
        self.group.set_sensitive(enabled)
        self.apply_button.set_sensitive(enabled and (self.dirty or not self.fields or self.baseline_unknown))
        self.reset_button.set_sensitive(not self.pending and self.dirty)
        self.reset_button.set_visible(bool(self.fields))

    def submit(self):
        if self.pending or not self.state.writable or not self.allowed:
            return
        if self.confirmation:
            return confirm(self.get_root(), self.group.get_title(), self.confirmation,
                           self.apply_button.get_label(), self._run, self.destructive)
        else:
            self._run()

    def _run(self):
        if self.pending or not self.state.writable:
            return
        values = self.values()
        self.pending = True
        self.state.invalidate()
        self.feedback.set_label("Saving…")
        self.feedback.remove_css_class("error")
        self.feedback.set_visible(True)
        self.spinner.start()
        self.spinner.set_visible(True)
        self._sensitivity()

        def work():
            try:
                response = self.save(values)
                if not isinstance(response, dict):
                    response = {"success": False, "error": "Unexpected service response."}
            except Exception as error:
                response = {"success": False, "error": str(error)}
            GLib.idle_add(self._done, response, values)
        threading.Thread(target=work, daemon=True).start()

    def _done(self, response, values):
        if self.state.closed:
            return False
        self.pending = False
        self.spinner.stop()
        self.spinner.set_visible(False)
        if response.get("success"):
            self.baseline_unknown = False
            self.load(values, force=True)
            self.feedback.set_label("Saved" if self.fields else "Request accepted. Refreshing status…")
            if self.restart_delay:
                self._awaiting_restart = True
                self.feedback.set_label("Restart requested. Waiting for the service…")
                self.state.expect_restart(self.restart_delay)
        else:
            error = response.get("error") or "The service could not complete this change."
            if self.immediate:
                self.load(self.confirmed, force=True)
            uncertain = "respond" in error.lower() or "timeout" in error.lower() or "timed out" in error.lower()
            if uncertain:
                self.state._status("Reconciling")
            suffix = " Checking the current state before another change." if uncertain else " Try the change again when ready."
            self.feedback.set_label(error + suffix)
            self.feedback.add_css_class("error")
        self._sensitivity()
        if not self._awaiting_restart:
            self.state.refresh()
        self.emit("completed", bool(response.get("success")))
        return False
