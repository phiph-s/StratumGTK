# drucken3d.spec

block_cipher = None
import os
import site

typelib_path = r'C:\msys64\ucrt64\lib\girepository-1.0'
if not os.path.exists(typelib_path):
    raise FileNotFoundError(f"Typelib path not found: {typelib_path}")

# Include all .typelib files
binaries = [
    (os.path.join(typelib_path, tl), 'gi_typelibs') 
    for tl in os.listdir(typelib_path) if tl.endswith('.typelib')
]

a = Analysis(
    ['run.py'],
    pathex=['.'],
    binaries=binaries,
    datas=[
        ('build-install/share/*', 'share'),
    ],
    hiddenimports=[],
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
