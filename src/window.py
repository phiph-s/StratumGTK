import gi

# GTK & Libadwaita -----------------------------------------------------------
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Gtk, Gio, Gdk, GObject, GLib, Adw
from gettext import gettext as _
from PIL import Image
import os
from .lib.mask_creation import group_pixels_to_filaments


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
    load_image_button: Gtk.Button = Gtk.Template.Child("load_image_button")
    main_content_area: Gtk.Box = Gtk.Template.Child("main_content_area")
    

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        
        print("UI that GTK loaded says:",
        self.get_template_child(Drucken3dWindow, "add_filament_button")
            .get_template_child.__doc__)

        self._store: Gio.ListStore = Gio.ListStore.new(ColorObject)
        self._selection = Gtk.SingleSelection(model=self._store)
        self.filament_list.set_model(self._selection)

        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", self._on_setup_item)
        factory.connect("bind", self._on_bind_item)
        self.filament_list.set_factory(factory)

        self._edit_index = -1
        self._image: Image.Image = None

        # Main UI area placeholder
        self._main_content: Gtk.Box = self.get_template_child(Drucken3dWindow, "main_content_area")

        
        
        self._image_area = Gtk.Image()
        self._image_area.set_hexpand(True)
        self._image_area.set_vexpand(True)
        self._image_area.set_halign(Gtk.Align.FILL)
        self._image_area.set_valign(Gtk.Align.FILL)
        self._main_content.append(self._image_area)

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
            self._store.append(ColorObject(dialog.get_rgba()))
            self._refresh_list()
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
        dialog = Gtk.FileChooserNative(title="Open image", transient_for=self, action=Gtk.FileChooserAction.OPEN)
        filter_img = Gtk.FileFilter()
        filter_img.set_name("Image files")
        filter_img.add_mime_type("image/png")
        filter_img.add_mime_type("image/jpeg")
        filter_img.add_mime_type("image/bmp")
        dialog.add_filter(filter_img)

        def response_cb(dlg, response):
            if response == Gtk.ResponseType.ACCEPT:
                filename = dlg.get_file().get_path()
                self._image = Image.open(filename)
            dlg.destroy()

        dialog.connect("response", response_cb)
        dialog.show()
    
    @Gtk.Template.Callback()
    def on_redraw_clicked(self, *_):
        if not self._image or self._store.get_n_items() < 2:
            print("Need at least 2 filaments and a loaded image to redraw.")
            return

        colors = []
        for i in range(self._store.get_n_items() - 1, -1, -1):
            rgba = self._store.get_item(i).rgba
            colors.append((int(rgba.red * 255), int(rgba.green * 255), int(rgba.blue * 255)))

        result_image = group_pixels_to_filaments(self._image.copy(), colors)
        # Convert to GdkPixbuf and set in Gtk.Image
        from gi.repository import GdkPixbuf
        import io

        with io.BytesIO() as output:
            result_image.save(output, format="PNG")
            output.seek(0)
            loader = GdkPixbuf.PixbufLoader.new_with_type("png")
            loader.write(output.read())
            loader.close()
            self._image_area.set_from_pixbuf(loader.get_pixbuf())

