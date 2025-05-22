# drucken3d.spec

block_cipher = None
import os
import site

typelib_path = r'D:/a/_temp/msys64/ucrt64/lib/girepository-1.0'

if not os.path.exists(typelib_path):
    raise FileNotFoundError(f"Typelib path not found: {typelib_path}")

# Bundle all .typelib files
binaries = [
    (os.path.join(typelib_path, tl), 'gi_typelibs')
    for tl in os.listdir(typelib_path)
    if tl.endswith('.typelib')
]

libs_path = os.path.join(os.path.dirname(shapely.__file__), ".libs")
binaries += [(os.path.join(libs_path, "geos_c.dll"), "shapely/.libs")]

a = Analysis(
    ['run.py'],
    pathex=['.'],
    binaries=binaries,
    datas=[
        ('build-install/share/*', 'share'),
    ],
    hiddenimports=[
        "gi",
        "gi.repository.GLib",
        "gi.repository.GObject",
        "gi.repository.Gio",
        "gi.repository.Gtk",
        "gi.repository.Gdk",
        "gi.repository.GdkPixbuf",
        "gi.repository.Adw",
        "PIL.Image",
        "numpy",
        "skimage",
        "skimage.color",
        "skimage.measure",
        "matplotlib",
        "matplotlib.pyplot",
        "shapely",
        "shapely.geometry",
        "shapely.affinity",
        "descartes",
        "trimesh",
        "trimesh.path.packing",
        "geopandas",
        "threading",
        "gettext",
        "io",
        "time",
        "functools",
        "os",
        "sys"
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='drucken3d',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='drucken3d'
)
