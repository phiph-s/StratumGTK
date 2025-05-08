import threading

import gi

# GTK & Libadwaita -----------------------------------------------------------
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Gtk, Gio, Gdk, GObject, GLib, Adw, GdkPixbuf
from gettext import gettext as _
from PIL import Image
import numpy as np
from .lib.mask_creation import generate_shades, segment_to_shades
from .lib.mesh_generator import create_layered_polygons_parallel, render_polygons_to_pixbuf, polygons_to_meshes_parallel

class ColorObject(GObject.Object):
    rgba = GObject.Property(type=Gdk.RGBA)

    def __init__(self, rgba):
        super().__init__()
        self.rgba = rgba


@Gtk.Template(resource_path="/dev/seelos/drucken3d/window.ui")
class Drucken3dWindow(Adw.ApplicationWindow):
    __gtype_name__ = "Drucken3dWindow"

    filament_list: Gtk.ListView = Gtk.Template.Child("filament_list")
    add_filament_button: Gtk.Button = Gtk.Template.Child("add_filament_button")
    remove_filament_button: Gtk.Button = Gtk.Template.Child("remove_filament_button")
    redraw_button: Gtk.Button = Gtk.Template.Child("redraw_button")
    export_button: Gtk.Button = Gtk.Template.Child("export_button")
    load_image_button: Gtk.Button = Gtk.Template.Child("load_image_button")
    mesh_view_container: Gtk.Image = Gtk.Template.Child("mesh_view_container")
    main_content_stack = Gtk.Template.Child("main_content_stack")
    loader_spinner = Gtk.Template.Child("loader_spinner")

    layer_height_spin: Gtk.SpinButton = Gtk.Template.Child("layer_height_spin")
    base_layers_spin: Gtk.SpinButton = Gtk.Template.Child("base_layers_spin")
    max_size_spin: Gtk.SpinButton = Gtk.Template.Child("max_size_spin")
    redraw_banner = Gtk.Template.Child("redraw_banner")
    progress = Gtk.Template.Child("progress")


    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        self._store: Gio.ListStore = Gio.ListStore.new(ColorObject)
        self._selection = Gtk.SingleSelection(model=self._store)
        self.filament_list.set_model(self._selection)

        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", self._on_setup_item)
        factory.connect("bind", self._on_bind_item)
        self.filament_list.set_factory(factory)

        self._edit_index = -1
        self._image: Image.Image = None

        # refresh the list
        self._refresh_list()

        self.export_button.set_sensitive(False)
        self.segmented_image = None
        self.shades = None
        self.polygons = []

    def _on_filament_change(self, reason=None):
        self.redraw_banner.set_revealed(True)
        if reason is not None:
            self.redraw_banner.set_title(reason)
        else: self.redraw_banner.set_title("Filament list changed. Redraw required.")

    def _on_setup_item(self, _factory, list_item):
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)

        swatch = Gtk.Box()
        swatch.set_size_request(32, 32)
        swatch.add_css_class("color-swatch")

        up_button = Gtk.Button(icon_name="go-up-symbolic")
        up_button.connect("clicked", self._on_move_up_clicked, list_item)

        down_button = Gtk.Button(icon_name="go-down-symbolic")
        down_button.connect("clicked", self._on_move_down_clicked, list_item)

        label = Gtk.Label(xalign=0)
        label.set_hexpand(True)

        row.append(swatch)
        row.append(down_button)
        row.append(up_button)
        row.append(label)

        list_item.set_child(row)
        list_item.swatch = swatch
        list_item.label = label

    def _on_bind_item(self, _factory, list_item):
        color_obj: ColorObject = list_item.get_item()
        swatch: Gtk.Widget = list_item.swatch
        label: Gtk.Label = list_item.label

        index = list_item.get_position()
        total = self._store.get_n_items()

        if index == total - 1:
            label.set_text("Base")
        elif index == 0:
            label.set_text("Top")
        else:
            label.set_text("")

        rgba = color_obj.rgba
        css = Gtk.CssProvider()
        css.load_from_data(
            f".color-swatch {{ background-color: {rgba.to_string()}; border-radius: 4px; }}".encode()
        )
        swatch.get_style_context().add_provider(css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    def _refresh_list(self):
        self.filament_list.set_factory(None)
        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", self._on_setup_item)
        factory.connect("bind", self._on_bind_item)
        self.filament_list.set_factory(factory)

    def _on_move_up_clicked(self, _button, list_item):
        index = list_item.get_position()
        if index > 0:
            item = self._store.get_item(index)
            self._store.remove(index)
            self._store.insert(index - 1, item)
            self._selection.set_selected(index - 1)
            self._refresh_list()
            self._on_filament_change()

    def _on_move_down_clicked(self, _button, list_item):
        index = list_item.get_position()
        if index < self._store.get_n_items() - 1:
            item = self._store.get_item(index)
            self._store.remove(index)
            self._store.insert(index + 1, item)
            self._selection.set_selected(index + 1)
            self._refresh_list()
            self._on_filament_change()

    @Gtk.Template.Callback()
    def on_add_filament_clicked(self, *_):
        dialog = Gtk.ColorChooserDialog(title="Choose filament colour", transient_for=self, modal=True)
        dialog.connect("response", self._on_add_color_response)
        dialog.present()

    def _on_add_color_response(self, dialog, response_id):
        if response_id == Gtk.ResponseType.OK:
            print ("Adding filament color", dialog.get_rgba())
            self._store.insert(0,ColorObject(dialog.get_rgba()))
            self._refresh_list()
            self._on_filament_change()
        dialog.destroy()

    @Gtk.Template.Callback()
    def on_remove_filament_clicked(self, *_):
        index = self._selection.get_selected()
        if index != -1:
            self._store.remove(index)
            self._refresh_list()
            self._on_filament_change()

    @Gtk.Template.Callback()
    def on_filament_row_activate(self, _view, position):
        self._edit_index = position
        color_obj: ColorObject = self._store.get_item(self._edit_index)
        dialog = Gtk.ColorChooserDialog(title="Change filament colour", transient_for=self, modal=True)
        dialog.set_rgba(color_obj.rgba)
        dialog.connect("response", self._on_edit_color_response)
        dialog.present()

    def _on_edit_color_response(self, dialog, response_id):
        if response_id == Gtk.ResponseType.OK:
            new = dialog.get_rgba()
            self._store.splice(self._edit_index, 1, [ColorObject(new)])
            self._refresh_list()
            self._on_filament_change()
        dialog.destroy()

    @Gtk.Template.Callback()
    def on_load_image_clicked(self, *_):
        dialog = Gtk.FileChooserNative(
            title="Open Image",
            transient_for=self,
            action=Gtk.FileChooserAction.OPEN,
            accept_label="_Open",
            cancel_label="_Cancel",
        )

        filter_img = Gtk.FileFilter()
        filter_img.set_name("Image files")
        filter_img.add_mime_type("image/png")
        filter_img.add_mime_type("image/jpeg")
        filter_img.add_mime_type("image/bmp")
        dialog.add_filter(filter_img)

        def response_handler(dialog, response):
            if response == Gtk.ResponseType.ACCEPT:
                file = dialog.get_file()
                if file:
                    filename = file.get_path()
                    self._image = Image.open(filename)
                    print(f"Loaded image: {filename}, size: {self._image.size}")
                    self.mesh_view_container.set_from_file(filename)
                    # switch back to image page
                    self.main_content_stack.set_visible_child_name("image")
                    self._on_filament_change("Input image loaded. Redraw required.")
            dialog.destroy()

        dialog.connect("response", response_handler)
        dialog.show()


    @Gtk.Template.Callback()
    def on_redraw_clicked(self, *_):
        if not self._image or self._store.get_n_items() < 2:
            print("Need at least 2 filaments and a loaded image to redraw.")
            return

        self.redraw_banner.set_revealed(False)
        self.progress.set_fraction(0.05)
        self.progress.set_visible(True)

        # ➊ switch to loader page & start spinner
        self.main_content_stack.set_visible_child_name("loader")
        self.loader_spinner.start()
        self.export_button.set_sensitive(False)
        self.redraw_button.set_sensitive(False)

        # gather colors (unchanged)…
        colors = []
        for i in range(self._store.get_n_items() - 1, -1, -1):
            rgba = self._store.get_item(i).rgba
            colors.append((
                int(rgba.red * 255),
                int(rgba.green * 255),
                int(rgba.blue * 255),
            ))

        # kick off background thread
        thread = threading.Thread(
            target=self._background_redraw,
            args=(colors,),
            daemon=True
        )
        thread.start()

    def _background_redraw(self, colors):
        # heavy work off the UI thread
        self.shades = generate_shades(colors)
        self.segmented_image = segment_to_shades(self._image, self.shades)
        self.polygons = create_layered_polygons_parallel(self.segmented_image, self.shades, progress_cb=self.progress.set_fraction)
        pixbuf = render_polygons_to_pixbuf(self.polygons, self.shades, self.segmented_image.size, progress_cb=self.progress.set_fraction)

        # schedule back on main loop
        GLib.idle_add(self._finish_redraw, pixbuf)

    def _finish_redraw(self, pixbuf):
        # runs in GTK’s thread
        self.mesh_view_container.set_from_pixbuf(pixbuf)
        self.loader_spinner.stop()
        # switch back to image page
        self.main_content_stack.set_visible_child_name("image")
        self.export_button.set_sensitive(True)
        self.redraw_button.set_sensitive(True)
        self.progress.set_visible(False)
        return False  # remove this idle callback

    @Gtk.Template.Callback()
    def on_export_clicked(self, *_):
        if not self.polygons:
            return

        # 1️⃣ Create a FileChooserNative for SAVE, with a .zip filter
        chooser = Gtk.FileChooserNative(
                title="Save Mesh",
                transient_for=self,
                action=Gtk.FileChooserAction.SAVE,
                accept_label="_Save",
                cancel_label="_Cancel",
            )
        # Keep it referenced; GTK does not own it :contentReference[oaicite:9]{index=9}

        chooser.set_current_name("meshes.zip")
        zip_filter = Gtk.FileFilter()
        zip_filter.set_name("ZIP archives")
        zip_filter.add_pattern("*.zip")
        chooser.add_filter(zip_filter)

        def _on_choice(dialog, response):
            if response == Gtk.ResponseType.ACCEPT:
                gfile = dialog.get_file()
                if gfile:
                    path = gfile.get_path()
                    # ➡️ Launch the progress dialog + thread
                    self._start_export_thread(path)
            dialog.destroy()

        chooser.connect("response", _on_choice)
        chooser.show()

    def _background_export(self, zip_path, progress_bar, dialog):
        import zipfile
        from io import BytesIO

        # Helper to update UI safely
        def _report(frac: float):
            progress_bar.set_fraction(frac)
            progress_bar.set_text(f"{int(frac * 100)}%")
            return False  # one-shot callback

        # 2️⃣ Create ZIP in write mode with DEFLATE compression :contentReference[oaicite:11]{index=11}
        with zipfile.ZipFile(zip_path, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            # Generate meshes; report progress via GLib.idle_add :contentReference[oaicite:12]{index=12}
            meshes = polygons_to_meshes_parallel(
                self.segmented_image,
                self.polygons[1:],
                layer_height=self.layer_height_spin.get_value(),
                target_max_cm=self.max_size_spin.get_value(),
                base_layers=self.base_layers_spin.get_value(),
                progress_cb=lambda f: GLib.idle_add(_report, f)
            )
            # 3️⃣ Write each mesh into the zip using an in-memory buffer :contentReference[oaicite:13]{index=13}
            for idx, mesh in enumerate(meshes):
                buf = BytesIO()
                mesh.export(file_obj=buf, file_type='stl')
                archive.writestr(f"mesh_{idx}.stl",
                                 buf.getvalue())  # grab bytes via getvalue() :contentReference[oaicite:14]{index=14}

        # When done, schedule the finish callback on the GTK thread
        GLib.idle_add(self._finish_export, len(meshes), dialog)  # :contentReference[oaicite:15]{index=15}

    def _start_export_thread(self, zip_path):
        # Build a modal dialog with NO close button
        dlg = Gtk.Dialog(transient_for=self, modal=True, use_header_bar=True)
        dlg.set_title("Exporting…")
        dlg.set_deletable(False)  # remove “×” from headerbar :contentReference[oaicite:10]{index=10}

        # Progress bar with padding
        progress = Gtk.ProgressBar(show_text=True)
        progress.set_margin_top(20)
        progress.set_margin_bottom(20)
        progress.set_margin_start(20)
        progress.set_margin_end(20)

        dlg.get_content_area().append(progress)
        dlg.show()

        # Spawn worker thread
        thread = threading.Thread(
            target=self._background_export,
            args=(zip_path, progress, dlg),
            daemon=True
        )
        thread.start()

    def _finish_export(self, mesh_count, dialog):
        dialog.destroy()
        msg = Gtk.MessageDialog(
            transient_for=self, modal=True,
            message_type=Gtk.MessageType.INFO,
            buttons=Gtk.ButtonsType.OK,
            text=f"Exported {mesh_count} meshes into ZIP archive."
        )
        msg.connect("response", lambda d, r: d.destroy())
        msg.show()
        return False


