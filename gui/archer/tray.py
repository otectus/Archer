"""
System tray icon via D-Bus StatusNotifierItem protocol.

Uses GIO D-Bus directly — no pystray, no GTK3 dependency.
Works on Wayland compositors (Hyprland, KDE, Sway) that run
org.kde.StatusNotifierWatcher.
"""

import os
import struct

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib


# ---------------------------------------------------------------------------
# D-Bus interface XML
# ---------------------------------------------------------------------------

SNI_XML = """
<node>
  <interface name="org.kde.StatusNotifierItem">
    <method name="Activate">
      <arg direction="in" name="x" type="i"/>
      <arg direction="in" name="y" type="i"/>
    </method>
    <method name="SecondaryActivate">
      <arg direction="in" name="x" type="i"/>
      <arg direction="in" name="y" type="i"/>
    </method>
    <method name="ContextMenu">
      <arg direction="in" name="x" type="i"/>
      <arg direction="in" name="y" type="i"/>
    </method>
    <method name="Scroll">
      <arg direction="in" name="delta" type="i"/>
      <arg direction="in" name="orientation" type="s"/>
    </method>
    <method name="ProvideXdgActivationToken">
      <arg direction="in" name="token" type="s"/>
    </method>
    <signal name="NewIcon"/>
    <signal name="NewTitle"/>
    <signal name="NewStatus">
      <arg type="s" name="status"/>
    </signal>
    <property name="Category" type="s" access="read"/>
    <property name="Id" type="s" access="read"/>
    <property name="Title" type="s" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="WindowId" type="i" access="read"/>
    <property name="IconName" type="s" access="read"/>
    <property name="IconPixmap" type="a(iiay)" access="read"/>
    <property name="OverlayIconName" type="s" access="read"/>
    <property name="OverlayIconPixmap" type="a(iiay)" access="read"/>
    <property name="AttentionIconName" type="s" access="read"/>
    <property name="AttentionIconPixmap" type="a(iiay)" access="read"/>
    <property name="AttentionMovieName" type="s" access="read"/>
    <property name="ToolTip" type="(sa(iiay)ss)" access="read"/>
    <property name="Menu" type="o" access="read"/>
    <property name="ItemIsMenu" type="b" access="read"/>
    <property name="IconThemePath" type="s" access="read"/>
  </interface>
</node>
"""

DBUSMENU_XML = """
<node>
  <interface name="com.canonical.dbusmenu">
    <method name="GetLayout">
      <arg direction="in" name="parentId" type="i"/>
      <arg direction="in" name="recursionDepth" type="i"/>
      <arg direction="in" name="propertyNames" type="as"/>
      <arg direction="out" name="revision" type="u"/>
      <arg direction="out" name="layout" type="(ia{sv}av)"/>
    </method>
    <method name="GetGroupProperties">
      <arg direction="in" name="ids" type="ai"/>
      <arg direction="in" name="propertyNames" type="as"/>
      <arg direction="out" name="properties" type="a(ia{sv})"/>
    </method>
    <method name="GetProperty">
      <arg direction="in" name="id" type="i"/>
      <arg direction="in" name="name" type="s"/>
      <arg direction="out" name="value" type="v"/>
    </method>
    <method name="Event">
      <arg direction="in" name="id" type="i"/>
      <arg direction="in" name="eventId" type="s"/>
      <arg direction="in" name="data" type="v"/>
      <arg direction="in" name="timestamp" type="u"/>
    </method>
    <method name="EventGroup">
      <arg direction="in" name="events" type="a(isvu)"/>
      <arg direction="out" name="idErrors" type="ai"/>
    </method>
    <method name="AboutToShow">
      <arg direction="in" name="id" type="i"/>
      <arg direction="out" name="needUpdate" type="b"/>
    </method>
    <method name="AboutToShowGroup">
      <arg direction="in" name="ids" type="ai"/>
      <arg direction="out" name="updatesNeeded" type="ai"/>
      <arg direction="out" name="idErrors" type="ai"/>
    </method>
    <signal name="ItemsPropertiesUpdated">
      <arg type="a(ia{sv})" name="updatedProps"/>
      <arg type="a(ias)" name="removedProps"/>
    </signal>
    <signal name="LayoutUpdated">
      <arg type="u" name="revision"/>
      <arg type="i" name="parent"/>
    </signal>
    <signal name="ItemActivationRequested">
      <arg type="i" name="id"/>
      <arg type="u" name="timestamp"/>
    </signal>
    <property name="Version" type="u" access="read"/>
    <property name="TextDirection" type="s" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="IconThemePath" type="as" access="read"/>
  </interface>
</node>
"""


def _load_icon_pixmap():
    """Load archer-tray.png as ARGB32 big-endian pixel data for SNI."""
    try:
        from PIL import Image
        icon_path = os.path.join(
            os.path.dirname(__file__), "..", "assets", "archer-tray.png"
        )
        if not os.path.exists(icon_path):
            return []
        img = Image.open(icon_path).convert("RGBA")
        w, h = img.size
        pixels = img.load()
        data = bytearray()
        for y in range(h):
            for x in range(w):
                r, g, b, a = pixels[x, y]
                # ARGB32, network byte order (big-endian)
                data.extend(struct.pack(">BBBB", a, r, g, b))
        return [(w, h, bytes(data))]
    except Exception:
        return []


class StatusNotifierItem:
    """D-Bus StatusNotifierItem tray icon using GIO."""

    def __init__(self, on_activate, on_quit):
        self._on_activate = on_activate
        self._on_quit = on_quit
        self._bus = None
        self._sni_reg_id = 0
        self._menu_reg_id = 0
        self._bus_name_id = 0
        self._service_name = f"org.freedesktop.StatusNotifierItem-{os.getpid()}-1"
        self._icon_pixmap = _load_icon_pixmap()
        self._revision = 1

        # Menu items: id -> {label, action}
        self._menu_items = {
            1: {"label": "Open", "action": self._on_activate},
            2: {"label": "Exit", "action": self._on_quit},
        }

    def start(self):
        """Register tray icon on the session bus."""
        self._bus = Gio.bus_get_sync(Gio.BusType.SESSION)

        # Own a well-known bus name
        self._bus_name_id = Gio.bus_own_name(
            Gio.BusType.SESSION,
            self._service_name,
            Gio.BusNameOwnerFlags.NONE,
            None, None, None,
        )

        # Register SNI interface at /StatusNotifierItem
        sni_info = Gio.DBusNodeInfo.new_for_xml(SNI_XML)
        self._sni_reg_id = self._bus.register_object(
            "/StatusNotifierItem",
            sni_info.interfaces[0],
            self._sni_method_call,
            self._sni_get_property,
            None,
        )

        # Register DBusMenu interface at /Menu
        menu_info = Gio.DBusNodeInfo.new_for_xml(DBUSMENU_XML)
        self._menu_reg_id = self._bus.register_object(
            "/Menu",
            menu_info.interfaces[0],
            self._menu_method_call,
            self._menu_get_property,
            None,
        )

        # Register with the StatusNotifierWatcher
        self._bus.call_sync(
            "org.kde.StatusNotifierWatcher",
            "/StatusNotifierWatcher",
            "org.kde.StatusNotifierWatcher",
            "RegisterStatusNotifierItem",
            GLib.Variant("(s)", (self._service_name,)),
            None,
            Gio.DBusCallFlags.NONE,
            -1,
            None,
        )

    def stop(self):
        """Unregister from D-Bus."""
        if self._bus:
            if self._sni_reg_id:
                self._bus.unregister_object(self._sni_reg_id)
                self._sni_reg_id = 0
            if self._menu_reg_id:
                self._bus.unregister_object(self._menu_reg_id)
                self._menu_reg_id = 0
        if self._bus_name_id:
            Gio.bus_unown_name(self._bus_name_id)
            self._bus_name_id = 0

    # -------------------------------------------------------------------
    # StatusNotifierItem method handler
    # -------------------------------------------------------------------
    def _sni_method_call(self, conn, sender, path, iface, method, params, invocation):
        if method == "Activate":
            self._on_activate()
        elif method == "ContextMenu":
            pass  # Menu is handled via DBusMenu
        elif method == "SecondaryActivate":
            self._on_activate()
        invocation.return_value(None)

    def _sni_get_property(self, conn, sender, path, iface, prop):
        props = {
            "Category": GLib.Variant("s", "ApplicationStatus"),
            "Id": GLib.Variant("s", "archer"),
            "Title": GLib.Variant("s", "Archer"),
            "Status": GLib.Variant("s", "Active"),
            "WindowId": GLib.Variant("i", 0),
            "IconName": GLib.Variant("s", ""),
            "IconPixmap": GLib.Variant("a(iiay)", self._icon_pixmap),
            "OverlayIconName": GLib.Variant("s", ""),
            "OverlayIconPixmap": GLib.Variant("a(iiay)", []),
            "AttentionIconName": GLib.Variant("s", ""),
            "AttentionIconPixmap": GLib.Variant("a(iiay)", []),
            "AttentionMovieName": GLib.Variant("s", ""),
            "ToolTip": GLib.Variant("(sa(iiay)ss)", ("", [], "Archer", "")),
            "Menu": GLib.Variant("o", "/Menu"),
            "ItemIsMenu": GLib.Variant("b", False),
            "IconThemePath": GLib.Variant("s", ""),
        }
        return props.get(prop)

    # -------------------------------------------------------------------
    # DBusMenu method handler
    # -------------------------------------------------------------------
    def _menu_method_call(self, conn, sender, path, iface, method, params, invocation):
        if method == "GetLayout":
            layout = self._build_layout()
            invocation.return_value(GLib.Variant("(u(ia{sv}av))", (self._revision, layout)))
        elif method == "GetGroupProperties":
            ids = params.get_child_value(0)
            result = []
            for i in range(ids.n_children()):
                item_id = ids.get_child_value(i).get_int32()
                props = self._get_item_properties(item_id)
                result.append((item_id, props))
            invocation.return_value(GLib.Variant("(a(ia{sv}))", (result,)))
        elif method == "GetProperty":
            item_id = params.get_child_value(0).get_int32()
            prop_name = params.get_child_value(1).get_string()
            props = self._get_item_properties(item_id)
            if prop_name in props:
                invocation.return_value(GLib.Variant("(v)", (props[prop_name],)))
            else:
                invocation.return_value(GLib.Variant("(v)", (GLib.Variant("s", ""),)))
        elif method == "Event":
            item_id = params.get_child_value(0).get_int32()
            event_id = params.get_child_value(1).get_string()
            if event_id == "clicked" and item_id in self._menu_items:
                action = self._menu_items[item_id]["action"]
                GLib.idle_add(action)
            invocation.return_value(None)
        elif method == "EventGroup":
            events = params.get_child_value(0)
            for i in range(events.n_children()):
                event = events.get_child_value(i)
                item_id = event.get_child_value(0).get_int32()
                event_id = event.get_child_value(1).get_string()
                if event_id == "clicked" and item_id in self._menu_items:
                    action = self._menu_items[item_id]["action"]
                    GLib.idle_add(action)
            invocation.return_value(GLib.Variant("(ai)", ([],)))
        elif method == "AboutToShow":
            invocation.return_value(GLib.Variant("(b)", (False,)))
        elif method == "AboutToShowGroup":
            invocation.return_value(GLib.Variant("(aiai)", ([], [])))
        else:
            invocation.return_value(None)

    def _menu_get_property(self, conn, sender, path, iface, prop):
        props = {
            "Version": GLib.Variant("u", 3),
            "TextDirection": GLib.Variant("s", "ltr"),
            "Status": GLib.Variant("s", "normal"),
            "IconThemePath": GLib.Variant("as", []),
        }
        return props.get(prop)

    def _build_layout(self):
        """Build the menu layout as a nested GVariant structure."""
        children = []
        for item_id, item in self._menu_items.items():
            child_props = {
                "label": GLib.Variant("s", item["label"]),
                "enabled": GLib.Variant("b", True),
                "visible": GLib.Variant("b", True),
            }
            child = GLib.Variant("(ia{sv}av)", (item_id, child_props, []))
            children.append(GLib.Variant("v", child))

        root_props = {
            "children-display": GLib.Variant("s", "submenu"),
        }
        return (0, root_props, children)

    def _get_item_properties(self, item_id):
        """Get properties dict for a menu item."""
        if item_id == 0:
            return {"children-display": GLib.Variant("s", "submenu")}
        item = self._menu_items.get(item_id)
        if not item:
            return {}
        return {
            "label": GLib.Variant("s", item["label"]),
            "enabled": GLib.Variant("b", True),
            "visible": GLib.Variant("b", True),
        }
