pkgdatadir = get_option('prefix') / get_option('datadir') / meson.project_name()
moduledir = pkgdatadir / 'drucken3d'
gnome = import('gnome')

gnome.compile_resources('drucken3d',
  'drucken3d.gresource.xml',
  gresource_bundle: true,
  install: true,
  install_dir: pkgdatadir,
)

python = import('python')

conf = configuration_data()
conf.set('PYTHON', python.find_installation('python3').full_path())
conf.set('VERSION', meson.project_version())
conf.set('localedir', get_option('prefix') / get_option('localedir'))
conf.set('pkgdatadir', pkgdatadir)

configure_file(
  input: 'drucken3d.in',
  output: 'drucken3d',
  configuration: conf,
  install: true,
  install_dir: get_option('bindir'),
  install_mode: 'r-xr-xr-x'
)

drucken3d_sources = [
  '__init__.py',
  'main.py',
  'window.py',
]

install_data(drucken3d_sources, install_dir: moduledir)

install_subdir('lib', install_dir: moduledir)
