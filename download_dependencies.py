import subprocess, os, json, hashlib, re
from pathlib import Path

# === CONFIG ===
REQUIREMENTS_FILE = "requirements.txt"
DOWNLOAD_DIR      = "data/flatpak-sources"
PYTHON_VERSION    = "312"                   # for --python-version
ABI               = "cp312"
PLATFORM          = "manylinux2014_x86_64"

# Any wheel whose filename starts with these (case-insensitive) will be dropped
EXCLUDED_PACKAGES = {"pip", "setuptools", "wheel", "packaging", "meson"}

os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# 1) Download everything pip can for our reqs (wheels only)
print("📦 downloading wheels…")
subprocess.run([
    "pip", "download",
    "--python-version", PYTHON_VERSION,
    "--platform", PLATFORM,
    "--implementation", "cp",
    "--abi", ABI,
    "--only-binary", ":all:",
    "--dest", DOWNLOAD_DIR,
    "-r", REQUIREMENTS_FILE
], check=True)

# 2) Gather and filter wheel files
all_wheels = sorted(Path(DOWNLOAD_DIR).glob("*.whl"))
filtered = []
for whl in all_wheels:
    base = whl.name.split("-", 1)[0].lower()
    if base in EXCLUDED_PACKAGES:
        print(f"  ↳ skipping {whl.name}")
        continue
    filtered.append(whl)

# 3) Group by name-version and pick the one matching PLATFORM if there's a choice
groups = {}
for whl in filtered:
    key = "-".join(whl.name.split("-", 2)[:2])  # e.g. "pillow-11.2.1"
    groups.setdefault(key, []).append(whl)

selected = []
for key, group in groups.items():
    # prefer the wheel whose filename contains our PLATFORM tag
    matches = [w for w in group if PLATFORM in w.name]
    chosen = matches[0] if matches else group[0]
    selected.append(chosen)

# 4) Emit Flatpak 'sources' JSON
sources = []
for whl in sorted(selected, key=lambda w: w.name.lower()):
    sha256 = hashlib.sha256(whl.read_bytes()).hexdigest()
    sources.append({
        "type":   "file",
        "path":   str(whl),
        "sha256": sha256
    })

# 6) Emit Flatpak JSON sources—but wrapped and saved
manifest = {
  "name": "python-deps",
  "buildsystem": "simple",
  "build-commands": [
    "pip3 install --no-index --find-links=. --prefix=/app *.whl"
  ],
  "sources": sources
}

with open("flatpak-whls.json", "w") as f:
    json.dump(manifest, f, indent=2)

print("✅ Written flatpak-whls.json")

