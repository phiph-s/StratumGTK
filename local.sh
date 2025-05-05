echo "Cleaning builddir directory"
rm -r builddir

echo "Rebuilding"
meson setup builddir
meson configure builddir -Dprefix="$(pwd)/builddir" -Dbuildtype=debug
ninja -C builddir install

echo "Running"
./builddir/bin/drucken3d