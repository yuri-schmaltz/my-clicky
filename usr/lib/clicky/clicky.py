#!/usr/bin/python3
import gettext
import gi
import locale
import os
import setproctitle
import subprocess
import warnings
import sys
import traceback

# Suppress GTK deprecation warnings
warnings.filterwarnings("ignore")

gi.require_version("Gtk", "3.0")
gi.require_version('XApp', '1.0')
from gi.repository import Gtk, Gdk, Gio, XApp, GLib

import utils
from common import *

setproctitle.setproctitle("clicky")

# i18n
APP = 'clicky'

# Dynamic path resolution
script_dir = os.path.dirname(os.path.abspath(__file__))
# Check if running locally or installed
if os.path.exists(os.path.join(script_dir, "../../share/clicky")):
     # Local development structure (usr/lib/clicky -> usr/share/clicky)
     root_dir = os.path.abspath(os.path.join(script_dir, "../../.."))
     LOCALE_DIR = os.path.join(root_dir, "usr/share/locale")
     SHARE_DIR = os.path.join(root_dir, "usr/share/clicky")
else:
     # Installed system-wide
     LOCALE_DIR = "/usr/share/locale"
     SHARE_DIR = "/usr/share/clicky"

locale.bindtextdomain(APP, LOCALE_DIR)
gettext.bindtextdomain(APP, LOCALE_DIR)
gettext.textdomain(APP)
_ = gettext.gettext


class MyApplication(Gtk.Application):

    def __init__(self, application_id, flags):
        Gtk.Application.__init__(self, application_id=application_id, flags=flags)
        self.connect("activate", self.activate)
        
        # Add command line parsing
        self.add_main_option("area", ord('a'), GLib.OptionFlags.NONE, GLib.OptionArg.NONE, "Capture area", None)
        self.add_main_option("screen", ord('s'), GLib.OptionFlags.NONE, GLib.OptionArg.NONE, "Capture screen", None)
        self.add_main_option("window", ord('w'), GLib.OptionFlags.NONE, GLib.OptionArg.NONE, "Capture window", None)

    def do_command_line(self, command_line):
        options = command_line.get_options_dict()
        self.cli_mode = None
        
        if options.contains("area"):
            self.cli_mode = "area"
        elif options.contains("screen"):
            self.cli_mode = "screen"
        elif options.contains("window"):
            self.cli_mode = "window"
            
        self.activate()
        return 0

    def activate(self, application=None):
        windows = self.get_windows()
        if (len(windows) > 0):
            window = windows[0]
            window.present()
            window.show()
        else:
            # Check if we have a CLI mode set
            cli_mode = getattr(self, 'cli_mode', None)
            
            window = MainWindow(self)
            self.add_window(window.window)
            
            if cli_mode:
                # Direct capture mode
                window.set_mode_and_capture(cli_mode)
            else:
                window.window.show()

class MainWindow():

    def __init__(self, application):

        self.application = application
        self.settings = Gio.Settings(schema_id="org.x.clicky")

        # Main UI
        gladefile = os.path.join(SHARE_DIR, "clicky.ui")
        self.builder = Gtk.Builder()
        self.builder.set_translation_domain(APP)
        self.builder.add_from_file(gladefile)
        self.window = self.builder.get_object("main_window")
        self.window.set_title(_("Screenshot"))
        self.window.set_icon_name("clicky")
        self.window.set_resizable(False)
        self.stack = self.builder.get_object("stack")
        self.stack.set_homogeneous(True)
        self.stack.set_vhomogeneous(True)
        self.main_box = self.builder.get_object("main_box")
        self.screenshot_box = self.builder.get_object("screenshot_box")
        self.radio_mode_screen = self.builder.get_object("radio_mode_screen")
        self.radio_mode_window = self.builder.get_object("radio_mode_window")
        self.radio_mode_area = self.builder.get_object("radio_mode_area")
        self.checkbox_pointer = self.builder.get_object("checkbox_pointer")
        self.checkbox_shadow = self.builder.get_object("checkbox_shadow")
        self.spin_delay = self.builder.get_object("spin_delay")

        # CSS
        provider = Gtk.CssProvider()
        provider.load_from_path(os.path.join(SHARE_DIR, "clicky.css"))
        screen = Gdk.Display.get_default_screen(Gdk.Display.get_default())
        Gtk.StyleContext.add_provider_for_screen(screen, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        # Settings
        prefer_dark_mode = self.settings.get_boolean("prefer-dark-mode")
        Gtk.Settings.get_default().set_property("gtk-application-prefer-dark-theme", prefer_dark_mode)

        mode = self.settings.get_string("capture-mode")
        self.builder.get_object(f"radio_mode_{mode}").set_active(True)

        self.settings.bind("include-pointer", self.checkbox_pointer, "active", Gio.SettingsBindFlags.DEFAULT)
        self.settings.bind("add-shadow", self.checkbox_shadow, "active", Gio.SettingsBindFlags.DEFAULT)
        self.settings.bind("delay", self.spin_delay, "value", Gio.SettingsBindFlags.DEFAULT)

        # import xapp.SettingsWidgets
        # spin = xapp.SettingsWidgets.SpinButton(_("Delay"), units="seconds")
        # self.builder.get_object("box_options").pack_start(spin, False, False, 0)

        self.window.show()

        # Store initial window size to keep UI stable across captures
        self.fixed_size = None
        def store_initial_size():
            self.fixed_size = self.window.get_size()
            self.apply_fixed_layout()
            return False
        GLib.idle_add(store_initial_size)

        self.builder.get_object("go_back_button").hide()

        # Widget signals
        self.window.connect("key-press-event",self.on_key_press_event)
        self.builder.get_object("go_back_button").connect("clicked", self.go_back)
        self.builder.get_object("button_take_screenshot").connect("clicked", self.start_screenshot)
        self.radio_mode_screen.connect("toggled", self.on_capture_mode_toggled)
        self.radio_mode_window.connect("toggled", self.on_capture_mode_toggled)
        self.radio_mode_area.connect("toggled", self.on_capture_mode_toggled)

    def get_capture_mode(self):
        if self.radio_mode_screen.get_active():
            mode = CAPTURE_MODE_SCREEN
        elif self.radio_mode_window.get_active():
            mode = CAPTURE_MODE_WINDOW
        else:
            mode = CAPTURE_MODE_AREA
        return mode

    def on_capture_mode_toggled(self, widget):
        self.settings.set_string("capture-mode", self.get_capture_mode())

    def start_screenshot(self, widget):
        self.hide_window()
        # Increased to 500ms to ensure compositor animations (fade-out) are complete
        delay_seconds = self.settings.get_int("delay")
        delay_ms = max(0, int(delay_seconds)) * 1000
        GLib.timeout_add(500 + delay_ms, self.take_screenshot)

    def hide_window(self):
        self.window.hide()
        self.window.set_opacity(0)
        self.window.set_skip_pager_hint(True)
        self.window.set_skip_taskbar_hint(True)

    def show_window(self):
        self.window.show()
        self.window.set_opacity(1)
        self.window.set_skip_pager_hint(False)
        self.window.set_skip_taskbar_hint(False)

    def show_error_dialog(self, message):
        dialog = Gtk.MessageDialog(
            transient_for=self.window,
            flags=0,
            message_type=Gtk.MessageType.ERROR,
            buttons=Gtk.ButtonsType.OK,
            text=_("Screenshot Failed"),
        )
        dialog.format_secondary_text(message)
        dialog.run()
        dialog.destroy()

    def set_mode_and_capture(self, mode):
        # Map string mode to radio button or constant
        if mode == "screen":
            self.radio_mode_screen.set_active(True)
        elif mode == "window":
            self.radio_mode_window.set_active(True)
        elif mode == "area":
            self.radio_mode_area.set_active(True)
            
        # Start capture immediately
        self.start_screenshot(None)

    def set_canvas_mode(self, widget, mode):
        self.canvas.current_tool = mode

    def setup_canvas_ui(self):
        # Get the container box
        box = self.builder.get_object("screenshot_box")
        
        # Add Toolbar
        toolbar = Gtk.Toolbar()
        toolbar.set_style(Gtk.ToolbarStyle.ICONS)
        
        # Pen Tool
        btn_pen = Gtk.ToolButton()
        btn_pen.set_icon_name("draw-freehand-symbolic")
        btn_pen.set_tooltip_text(_("Pen"))
        btn_pen.connect("clicked", self.set_canvas_mode, "pen")
        toolbar.insert(btn_pen, -1)
        
        # Highlighter Tool
        btn_high = Gtk.ToolButton()
        btn_high.set_icon_name("format-text-highlight-symbolic")
        btn_high.set_tooltip_text(_("Highlighter"))
        btn_high.connect("clicked", self.set_canvas_mode, "highlighter")
        toolbar.insert(btn_high, -1)
        
        # Shapes Separator
        toolbar.insert(Gtk.SeparatorToolItem(), -1)

        # Rectangle Tool
        btn_rect = Gtk.ToolButton()
        btn_rect.set_icon_name("draw-rectangle-symbolic")
        btn_rect.set_tooltip_text(_("Rectangle"))
        btn_rect.connect("clicked", self.set_canvas_mode, "rectangle")
        toolbar.insert(btn_rect, -1)

        # Circle Tool
        btn_circ = Gtk.ToolButton()
        btn_circ.set_icon_name("draw-ellipse-symbolic")
        btn_circ.set_tooltip_text(_("Circle"))
        btn_circ.connect("clicked", self.set_canvas_mode, "circle")
        toolbar.insert(btn_circ, -1)
        
        # Crop Tool
        btn_crop = Gtk.ToolButton()
        btn_crop.set_icon_name("edit-cut-symbolic")
        btn_crop.set_tooltip_text(_("Crop Image"))
        btn_crop.connect("clicked", self.set_canvas_mode, "crop")
        toolbar.insert(btn_crop, -1)
        
        toolbar.insert(Gtk.SeparatorToolItem(), -1)

        # Line Tool
        btn_line = Gtk.ToolButton()
        btn_line.set_icon_name("draw-line-symbolic")
        btn_line.set_tooltip_text(_("Line"))
        btn_line.connect("clicked", self.set_canvas_mode, "line")
        toolbar.insert(btn_line, -1)

        # Arrow Tool
        btn_arrow = Gtk.ToolButton()
        btn_arrow.set_icon_name("go-next-symbolic")
        btn_arrow.set_tooltip_text(_("Arrow"))
        btn_arrow.connect("clicked", self.set_canvas_mode, "arrow")
        toolbar.insert(btn_arrow, -1)

        toolbar.insert(Gtk.SeparatorToolItem(), -1)

        # --- Properties Bar ---
        prop_box = Gtk.Box(spacing=10)
        prop_box.set_margin_start(10)
        prop_box.set_margin_end(10)
        
        # Color
        color_btn = Gtk.ColorButton.new_with_rgba(Gdk.RGBA(1, 0, 0, 1))
        color_btn.set_tooltip_text(_("Stroke Color"))
        color_btn.connect("color-set", lambda b: self.canvas.set_stroke_color(b.get_rgba()))
        prop_box.pack_start(color_btn, False, False, 0)
        
        # Line Width
        adj = Gtk.Adjustment(3, 1, 50, 1, 5, 0)
        width_spin = Gtk.SpinButton.new(adj, 1, 0)
        width_spin.set_tooltip_text(_("Line Width"))
        width_spin.connect("value-changed", lambda s: self.canvas.set_line_width(s.get_value()))
        prop_box.pack_start(width_spin, False, False, 0)
        
        # Fill Toggle
        fill_check = Gtk.CheckButton.new_with_label(_("Fill"))
        fill_check.connect("toggled", lambda c: self.canvas.set_fill_active(c.get_active()))
        prop_box.pack_start(fill_check, False, False, 0)
        
        # Opacity
        prop_box.pack_start(Gtk.Label(label=_("Opacity:")), False, False, 0)
        opacity_scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0.1, 1.0, 0.1)
        opacity_scale.set_value(1.0)
        opacity_scale.set_size_request(100, -1)
        opacity_scale.connect("value-changed", lambda s: self.canvas.set_opacity(s.get_value()))
        prop_box.pack_start(opacity_scale, False, False, 0)

        prop_box.show_all()
        prop_item = Gtk.ToolItem()
        prop_item.set_expand(True)
        prop_item.add(prop_box)
        toolbar.insert(prop_item, -1)

        # Blue/Eraser (Placeholder for now)
        btn_blue = Gtk.ToolButton()
        btn_blue.set_icon_name("edit-clear-symbolic")
        btn_blue.set_tooltip_text(_("Blue Pen"))
        btn_blue.connect("clicked", self.set_canvas_mode, "eraser")
        toolbar.insert(btn_blue, -1)
        
        toolbar.show_all()
        box.pack_start(toolbar, False, False, 0)
        box.reorder_child(toolbar, 0)
        
        # Replace Image with Canvas
        self.old_image_widget = self.builder.get_object("screenshot_image")
        self.old_image_widget.hide()
        # We keep it in the hierarchy or remove it? Removing cleanly is better.
        # But for safety, let's just create Canvas and pack it.
        
        from canvas import CanvasWidget
        self.canvas = CanvasWidget()
        self.canvas.show()

        self.scroller = Gtk.ScrolledWindow()
        self.scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        self.scroller.set_hexpand(True)
        self.scroller.set_vexpand(True)
        self.scroller.add(self.canvas)
        self.scroller.show()

        box.pack_start(self.scroller, True, True, 0)
        
        # Add Save Button explicitly here if needed, or reuse existing flow if there's a save button.
        # There isn't a save button in the UI XML shown (only placeholders or hidden).
        # We need a Save button in the toolbar.
        
        btn_save = Gtk.ToolButton()
        btn_save.set_icon_name("document-save-symbolic")
        btn_save.set_tooltip_text(_("Save"))
        btn_save.connect("clicked", self.save_canvas)
        toolbar.insert(btn_save, -1)

        if self.fixed_size:
            self.apply_fixed_layout()

    def save_canvas(self, widget):
        if not self.canvas: return
        
        pixbuf = self.canvas.get_result_pixbuf()
        if pixbuf:
            pictures_dir = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_PICTURES)
            if not pictures_dir:
                pictures_dir = os.path.expanduser("~")
            captures_dir_name = _("Captures")
            captures_dir = os.path.join(pictures_dir, captures_dir_name)
            os.makedirs(captures_dir, exist_ok=True)

            dialog = Gtk.FileChooserDialog(
                title=_("Save Screenshot"),
                parent=self.window,
                action=Gtk.FileChooserAction.SAVE
            )
            dialog.add_buttons(
                Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                Gtk.STOCK_SAVE, Gtk.ResponseType.OK
            )
            dialog.set_do_overwrite_confirmation(True)
            dialog.set_current_folder(captures_dir)
            dialog.set_current_name(_("Screenshot.png"))
            
            response = dialog.run()
            if response == Gtk.ResponseType.OK:
                filename = dialog.get_filename()
                pixbuf.savev(filename, "png", [], [])
            
            dialog.destroy()


    def go_back(self, widget):
        self.navigate_to("main_page")

    def copy_to_clipboard(self, pixbuf):
        clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        clipboard.set_image(pixbuf)
        clipboard.store()

    def show_notification(self):
        notification = Gio.Notification.new(_("Screenshot Saved"))
        notification.set_body(_("Image copied to clipboard. Click to edit."))
        notification.set_icon(Gio.ThemedIcon.new("clicky"))
        
        # Action to open window (default action)
        # For simplicity, since the app is already running (hidden or showing), 
        # clicking usually activates the app. We just need to ensure the window is shown.
        
        self.application.send_notification("screenshot-taken", notification)

    def apply_fixed_layout(self):
        if not self.fixed_size:
            return
        width, height = self.fixed_size
        # Keep stack/pages fixed to initial size to avoid jumps
        self.stack.set_size_request(width, height)
        if self.main_box is not None:
            self.main_box.set_size_request(width, height)
        if self.screenshot_box is not None:
            self.screenshot_box.set_size_request(width, height)
        if hasattr(self, 'scroller') and self.scroller is not None:
            self.scroller.set_size_request(width, height)

        geometry = Gdk.Geometry()
        geometry.min_width = width
        geometry.max_width = width
        geometry.min_height = height
        geometry.max_height = height
        self.window.set_geometry_hints(None, geometry, Gdk.WindowHints.MIN_SIZE | Gdk.WindowHints.MAX_SIZE)

    def take_screenshot(self):
        try:
            options = Options(self.settings)
            pixbuf = utils.capture_pixbuf(options)
            
            # Post-capture actions (Win 11 style)
            if pixbuf:
                self.copy_to_clipboard(pixbuf)
                self.show_notification()
                
                # Setup Canvas Logic
                if not hasattr(self, 'canvas'):
                    self.setup_canvas_ui()
                
                self.canvas.set_pixbuf(pixbuf)
                
                # Keep main window size stable (no resize to image)
                self.canvas.set_size_request(400, 300)
                if self.fixed_size:
                    self.apply_fixed_layout()
                
                self.navigate_to("screenshot_page")
                self.show_window()
            else:
                self.show_error_dialog(_("Screenshot canceled or failed."))
                self.navigate_to("main_page")
                self.show_window()
        except Exception as e:
            print(traceback.format_exc())
            self.show_error_dialog(_("An error occurred during the screenshot:\n\n") + str(e))
            self.show_window()

    @idle_function
    def navigate_to(self, page, name=""):
        if page == "main_page":
            self.builder.get_object("go_back_button").hide()
        else:
            self.builder.get_object("go_back_button").show()
        self.stack.set_visible_child_name(page)

    def open_about(self, widget):
        dlg = Gtk.AboutDialog()
        dlg.set_transient_for(self.window)
        dlg.set_title(_("About"))
        dlg.set_program_name(_("Screenshot"))
        dlg.set_comments(_("Save images of your screen or individual windows"))
        try:
            h = open('/usr/share/common-licenses/GPL', encoding="utf-8")
            s = h.readlines()
            gpl = ""
            for line in s:
                gpl += line
            h.close()
            dlg.set_license(gpl)
        except Exception as e:
            print (e)

        dlg.set_version("__DEB_VERSION__")
        dlg.set_icon_name("clicky")
        dlg.set_logo_icon_name("clicky")
        dlg.set_website("https://www.github.com/linuxmint/clicky")
        def close(w, res):
            if res == Gtk.ResponseType.CANCEL or res == Gtk.ResponseType.DELETE_EVENT:
                w.destroy()
        dlg.connect("response", close)
        dlg.show()

    def open_keyboard_shortcuts(self, widget):
        gladefile = os.path.join(SHARE_DIR, "shortcuts.ui")
        builder = Gtk.Builder()
        builder.set_translation_domain(APP)
        builder.add_from_file(gladefile)
        window = builder.get_object("shortcuts")
        window.set_title(_("Screenshot"))
        window.show()

    def on_menu_quit(self, widget):
        self.application.quit()

    def on_key_press_event(self, widget, event):
        persistant_modifiers = Gtk.accelerator_get_default_mod_mask()
        modifier = event.get_state() & persistant_modifiers
        ctrl = modifier == Gdk.ModifierType.CONTROL_MASK
        shift = modifier == Gdk.ModifierType.SHIFT_MASK

        if ctrl and event.keyval == Gdk.KEY_r:
            # Ctrl + R
            pass
        elif ctrl and event.keyval == Gdk.KEY_f:
            # Ctrl + F
            pass
        elif event.keyval == Gdk.KEY_F11:
             # F11..
             pass

if __name__ == "__main__":
    application = MyApplication("org.x.clicky", Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
    application.run(sys.argv)

