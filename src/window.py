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
    main_content_stack = Gtk.Template.Child()
    loader_spinner = Gtk.Template.Child()
    

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

    def _on_move_down_clicked(self, _button, list_item):
        index = list_item.get_position()
        if index < self._store.get_n_items() - 1:
            item = self._store.get_item(index)
            self._store.remove(index)
            self._store.insert(index + 1, item)
            self._selection.set_selected(index + 1)
            self._refresh_list()

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
            # print all colors
            for i in range(self._store.get_n_items()):
                color_obj: ColorObject = self._store.get_item(i)
                print(f"Color {i}: {color_obj.rgba.to_string()}")
        dialog.destroy()

    @Gtk.Template.Callback()
    def on_remove_filament_clicked(self, *_):
        index = self._selection.get_selected()
        if index != -1:
            self._store.remove(index)
            self._refresh_list()

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
            dialog.destroy()

        dialog.connect("response", response_handler)
        dialog.show()

    @Gtk.Template.Callback()
    def on_redraw_clicked(self, *_):
        if not self._image or self._store.get_n_items() < 2:
            print("Need at least 2 filaments and a loaded image to redraw.")
            return

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

    @Gtk.Template.Callback()
    def on_export_clicked(self, *_):
        if self.polygons:
            # open file dialog
            dialog = Gtk.FileChooserNative(
                title="Save Mesh",
                transient_for=self,
                action=Gtk.FileChooserAction.SAVE,
                accept_label="_Save",
                cancel_label="_Cancel",
            )
            #save stl files
            filter_stl = Gtk.FileFilter()
            filter_stl.set_name("STL files")
            filter_stl.add_mime_type("application/sla")
            filter_stl.add_mime_type("application/sla")
            filter_stl.add_pattern("*.stl")
            dialog.add_filter(filter_stl)
            dialog.set_current_name("exported_mesh.stl")

            def response_handler(dialog, response):
                if response == Gtk.ResponseType.ACCEPT:
                    file = dialog.get_file()
                    if file:
                        filename = file.get_path()
                        print(f"Exporting to: {filename}")
                        # save stl
                        meshes = polygons_to_meshes_parallel(self.segmented_image, self.polygons[1:], layer_height=0.08, target_max_cm=25)
                        # list of trimesh meshes
                        for i, mesh in enumerate(meshes):
                            # save each mesh as a separate file
                            mesh.export(f"{filename}_{i}.stl")
                            print(f"Exported {filename}_{i}.stl")

                        # show success message
                        success_dialog = Gtk.MessageDialog(
                            transient_for=self,
                            modal=True,
                            message_type=Gtk.MessageType.INFO,
                            buttons=Gtk.ButtonsType.OK,
                            text=f"Exported {len(meshes)} meshes to {filename}_<index>.stl",
                        )
                        success_dialog.connect("response", lambda d, r: d.destroy())
                        success_dialog.show()
                dialog.destroy()

            dialog.connect("response", response_handler)
            dialog.show()

    def _background_redraw(self, colors):
        # heavy work off the UI thread
        self.shades = generate_shades(colors)
        self.segmented_image = segment_to_shades(self._image, self.shades)
        self.polygons = create_layered_polygons_parallel(self.segmented_image, self.shades)
        pixbuf = render_polygons_to_pixbuf(self.polygons, self.shades, self.segmented_image.size)

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
        return False  # remove this idle callback
