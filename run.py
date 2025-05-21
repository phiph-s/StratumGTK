import os
import sys
import locale
import gettext
import signal
from gi.repository import Gio

VERSION = "0.2.0"

# Paths relative to PyInstaller bundle
base = os.path.abspath(os.path.dirname(sys.argv[0]))
share_dir = os.path.join(base, "_internal", "share")
resource_path = os.path.join(share_dir, "drucken3d.gresource")
pkgdatadir = share_dir  # drucken3d Python package is assumed to be here
localedir = os.path.join(share_dir, "locale")

sys.path.insert(1, pkgdatadir)

# Signals
signal.signal(signal.SIGINT, signal.SIG_DFL)

# i18n
try:
    locale.bindtextdomain("drucken3d", localedir)
    locale.textdomain("drucken3d")
except:
    pass
gettext.install("drucken3d", localedir)

# Load GResource
resource = Gio.Resource.load(resource_path)
resource._register()

# Run main app
from drucken3d import main
sys.exit(main.main(VERSION))
