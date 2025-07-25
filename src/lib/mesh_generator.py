import io
import time

from matplotlib import pyplot as plt
from shapely.geometry.linestring import LineString
import os
import numpy as np
from skimage import measure
from shapely.geometry import Polygon, MultiPolygon
from shapely import affinity
import trimesh
from skimage.color import rgb2lab, deltaE_ciede2000
import geopandas as gpd
from gi.repository import GLib
from descartes import PolygonPatch
from functools import wraps

from trimesh.path.packing import meshes

# Configuration defaults
OUTPUT_DIR = 'meshes'
SIMPLIFY_TOLERANCE = 0.4  # Simplify tolerance for raw polygons
SMOOTHING_WINDOW = 3  # Window size for contour smoothing
MIN_AREA = 1  # Minimum polygon area to keep

def timed(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        t0 = time.perf_counter()
        result = func(*args, **kwargs)
        t1 = time.perf_counter()
        print(f"[TIMING] {func.__name__:25s}: {t1-t0:0.3f}s")
        return result
    return wrapper


def ensure_dir(path):
    if not os.path.exists(path):
        os.makedirs(path)


def extract_color_masks(img_arr, filament_shades):
    """
    img_arr: H×W×4 (RGBA) numpy array
    filament_shades: output of generate_shades()
      a list of lists, so filament_shades[f][s] is an RGB tuple.
    returns: dict keyed by (filament_index, shade_index) → boolean mask
    """
    h, w = img_arr.shape[:2]
    rgb = img_arr[..., :3]
    masks = {}

    used_shades = set() # to track used shades, prevent duplicates
    for fi, shades in enumerate(filament_shades):
        for si, shade in enumerate(shades):
            if shade in used_shades:
                print(f"Skipping duplicate shade {shade} for filament {fi}, shade {si}")
                continue
            # exact match on RGB channels
            m = np.all(rgb == shade, axis=2)
            if m.any():
                masks[(fi, si)] = m
                used_shades.add(shade)
    return masks


@timed
def mask_to_polygons(mask, min_area=100, simplify_tol=1.0):
    # 1️⃣ Clean the raster mask – keep exactly the same pre-processing you had
    # mask = binary_fill_holes(mask)                        # fills boundary-connected zeros :contentReference[oaicite:1]{index=1}
    # mask = binary_closing(mask, structure=np.ones((3, 3)))# closes one-pixel gaps :contentReference[oaicite:2]{index=2}
    padded = np.pad(mask.astype(float), 1, constant_values=0)

    # 2️⃣ Convert every *ring* returned by marching-squares into a LineString
    rings = [
        LineString([(p[1] - 1, p[0] - 1) for p in c])  # shift because of the 1-pixel pad
        for c in measure.find_contours(padded, 0.5)  # skimage marching squares :contentReference[oaicite:3]{index=3}
    ]

    if not rings:
        return []

    # 3️⃣ Build polygons *with holes* at C speed — one line!
    polys = gpd.GeoSeries(rings).build_area()  # GEOS/JTS build-area :contentReference[oaicite:4]{index=4}

    # 4️⃣ Optional smoothing / simplification exactly like before
    polys = polys.buffer(0)  # ensure valid shells after build-area :contentReference[oaicite:5]{index=5}
    polys = polys.simplify(simplify_tol)

    # 5️⃣ Filter out tiny blobs and return plain Shapely objects
    polys = polys[polys.area >= min_area]

    return list(polys.geometry)


def flip_polygons_vertically(polygons, height_px):
    return [affinity.scale(poly, xfact=1, yfact=-1, origin=(0, height_px)) for poly in polygons]

@timed
def generate_layer_mesh(polygons, thickness):
    if not isinstance(polygons, list):
        polygons = [polygons]

    flat_polys = []
    for geom in polygons:
        if isinstance(geom, MultiPolygon):
            flat_polys.extend(geom.geoms)
        else:
            flat_polys.append(geom)

    meshes = []
    for poly in flat_polys:
        if not poly.is_valid or poly.is_empty:
            continue
        m = trimesh.creation.extrude_polygon(poly, thickness)
        meshes.append(m)
    return trimesh.util.concatenate(meshes) if meshes else None

@timed
def merge_layers_downward(meshes_list):
    last = None
    for i, meshes in enumerate(meshes_list[::-1]):
        for j, mesh in enumerate(meshes[::-1]):
            if last is None:
                last = mesh
            else:
                last = trimesh.util.concatenate(last, mesh)
                meshes_list[-(i + 1)][-(j + 1)] = last

from shapely.ops import unary_union
@timed
def merge_polys_downward(polys_list):
    """
    In-place cumulative union of every sub-layer group with all above it.
    Input: polys_list[layer][shade] is a list of Polygons (possibly empty).
    After this runs, every cell polys_list[layer][shade] will be a single
    Shapely geometry representing the union of itself and all groups above it.
    """
    accumulated = None

    # Walk layers from top (last index) down to 0
    for i in range(len(polys_list) - 1, -1, -1):
        layer = polys_list[i]
        # Walk shades from last to first
        for j in range(len(layer) - 1, -1, -1):
            group = layer[j]
            # 1) flatten the small list-of-polygons into one geometry
            if isinstance(group, list):
                if not group:
                    # empty group: nothing to union
                    poly = None
                else:
                    poly = unary_union(group)
            else:
                poly = group

            if poly is None or poly.is_empty:
                continue

            # 2) merge into accumulated
            if accumulated is None:
                accumulated = poly
            else:
                accumulated = accumulated.union(poly)

            # 3) write back the running union as a single geometry
            polys_list[i][j] = accumulated

    return polys_list



def _generate_base_mesh(segmented_image, layer_height=0.2, base_layers=4,
                      target_max_cm=10):
    base_height = layer_height * base_layers
    w_px, h_px = segmented_image.size
    scale_xy = (target_max_cm * 10) / max(w_px, h_px)

    # Base layer
    base_rect = Polygon([(0, 0), (w_px, 0), (w_px, h_px), (0, h_px)])
    base_poly = flip_polygons_vertically([base_rect], h_px)
    base_mesh = generate_layer_mesh(base_poly, base_height)
    if base_mesh:
        base_mesh.apply_scale([scale_xy, scale_xy, 1])
        return base_mesh, base_height

import multiprocessing as mp
from itertools import product


def process_mask(task):
    from shapely.geometry import Polygon  # If needed for serialization safety
    (fi, L), mask, h_px = task
    if not mask.any():
        return (fi, L, [])
    polys = mask_to_polygons(mask, min_area=MIN_AREA, simplify_tol=SIMPLIFY_TOLERANCE)
    flipped = flip_polygons_vertically(polys, h_px)
    return (fi, L, flipped)

@timed
def create_layered_polygons_parallel(
    segmented_image,
    shades,
    progress_cb=None,
):
    """
    :progress_cb: a callable progress_cb(completed: int, total: int) → bool
                  should return False if you want to abort early.
    """

    ensure_dir(OUTPUT_DIR)
    w_px, h_px = segmented_image.size
    seg_arr = np.array(segmented_image.convert("RGBA"))

    # ---- steps 1 & 2 unchanged ----
    masks = extract_color_masks(seg_arr, shades)
    counts_map = {}
    for fi in range(1, len(shades)):
        cnt = np.zeros(seg_arr.shape[:2], dtype=int)
        for si in range(len(shades[fi])):
            m = masks.get((fi, si))
            if m is not None:
                cnt[m] = si + 1
        counts_map[fi] = cnt
        print(f"Layer height {fi}: {np.unique(cnt)}")

    # ---- step 3: build tasks ----
    h_px = seg_arr.shape[0]
    tasks = []
    for fi in range(1, len(shades)):
        cnt = counts_map[fi]
        for L in range(1, len(np.unique(cnt)) + 1):
            mask_L = cnt >= L
            tasks.append(((fi, L), mask_L, h_px))

    total = len(tasks)
    completed = 0
    results = []

    # ---- step 5: run in parallel but iterate for progress ----
    with mp.Pool(processes=mp.cpu_count()) as pool:
        # imap yields one result at a time as soon as it's ready
        for fi, L, polys in pool.imap_unordered(process_mask, tasks):
            results.append((fi, L, polys))
            completed += 1

            if progress_cb:
                # We must call into GTK from the main thread:
                # GLib.idle_add will schedule the callback on the main loop.
                # We pass fraction (0.0–1.0) or raw counts if you prefer.
                def _emit(done, tot):
                    # If callback returns False, that means “please abort”
                    return progress_cb((done/tot) / 2)
                GLib.idle_add(_emit, completed, total)

    # ---- steps 6 & 7 unchanged ----
    polys_map = {}
    for fi, L, polys in results:
        polys_map.setdefault(fi, {})[L] = polys

    polys_list = []
    for fi in range(1, len(shades)):
        poly_list = [polys_map.get(fi, {}).get(L, []) for L in range(1, len(shades[fi]) + 1)]
        if any(poly_list):
            polys_list.append(poly_list)

    base = Polygon([(0, 0), (w_px, 0), (w_px, h_px), (0, h_px)])
    base = flip_polygons_vertically([base], h_px)[0]
    polys_list.insert(0, [[base]])

    return polys_list


def process_generate_layer_mesh(task):
    idx, idy, sublayer, layer_height = task
    try:
        m = generate_layer_mesh(sublayer, layer_height)
        return (idx, idy, m)
    except Exception as e:
        print(f"Error in generate_layer_mesh for layer {idx}, shade {idy}: {e}")
        return (idx, idy, None)


@timed
def polygons_to_meshes_parallel(segmented_image,
                                polys_list,
                                layer_height=0.2,
                                base_layers=4,
                                target_max_cm=10,
                                progress_cb=None):
    # 1) Flatten out all the (layer, shade, sublayer) tasks
    tasks = []
    for idx, polys in enumerate(polys_list):
        for idy, sublayer in enumerate(polys):
            tasks.append((idx, idy, sublayer, layer_height))
    total = len(tasks)
    if total == 0:
        if progress_cb:
            progress_cb(1.0)
        return []

    # 2) Run them in a Pool, reporting progress as each result arrives
    results = []
    with mp.Pool(processes=mp.cpu_count()) as pool:
        for n, triple in enumerate(pool.imap(process_generate_layer_mesh, tasks), start=1):
            results.append(triple)
            if progress_cb:
                progress_cb(n / total)

    # 3) Rebuild into meshes_list[layer][shade]
    meshes_dict = {}
    for idx, idy, mesh in results:
        if mesh:
            meshes_dict.setdefault(idx, {})[idy] = mesh

    meshes_list = []
    for idx in sorted(meshes_dict):
        shade_dict = meshes_dict[idx]
        sublayers = [shade_dict[i] for i in sorted(shade_dict)]
        if sublayers:
            meshes_list.append(sublayers)

    # 4) Merge downward and build the base
    merge_layers_downward(meshes_list)
    base_mesh, base_height = _generate_base_mesh(
        segmented_image, layer_height, base_layers, target_max_cm
    )

    # 5) Scale & stack each layer
    w_px, h_px = segmented_image.size
    scale_xy = (target_max_cm * 10) / max(w_px, h_px)
    meshes = [base_mesh] if base_mesh else []
    current_z0 = base_height
    for layer in meshes_list:
        for m in layer:
            m.apply_translation([0, 0, current_z0])
            if not m.is_empty:
                current_z0 += layer_height

        combined = trimesh.util.concatenate(layer)
        combined.apply_scale([scale_xy, scale_xy, 1])
        meshes.append(combined)

    # final callback = 100%
    if progress_cb:
        progress_cb(1.0)

    return meshes



import numpy as np
import matplotlib
matplotlib.use('Agg')  # non-interactive backend
import matplotlib.pyplot as plt
from shapely.geometry import Polygon, MultiPolygon
from shapely.ops import unary_union
from gi.repository import GdkPixbuf
import io


@timed
def render_polygons_to_pixbuf(
    layered_polygons,
    filament_shades,
    image_size=None,
    width=400,
    height=400,
    bg_color='white',
    progress_cb=None,
):
    """
    Render each polygon in `layered_polygons` using its exact shade color.

    - layered_polygons: list of layers; each layer is a list of sub-layer groups,
      and each sub-layer group is either a Polygon/MultiPolygon or an iterable of them.
    - filament_shades: list of lists of RGB tuples, so filament_shades[layer_idx][shade_idx]
      gives the exact color for that sub-layer.
    - image_size: (w_px, h_px) to match input resolution, else use width/height.
    - bg_color: background fill (name, hex, or 'transparent').
    """
    # 1) Determine output resolution
    if image_size:
        w_px, h_px = image_size
    else:
        w_px, h_px = width, height
    dpi = 100
    fig_w, fig_h = w_px / dpi, h_px / dpi

    # 2) Flatten out all polys & assign each its precise shade color
    flat_polys, flat_colors = [], []
    for layer_idx, layer_groups in enumerate(layered_polygons):
        shades = filament_shades[layer_idx]
        for shade_idx, group in enumerate(layer_groups):
            color = shades[shade_idx] if shade_idx < len(shades) else shades[-1]
            if isinstance(group, (Polygon, MultiPolygon)):
                geoms = [group]
            else:
                geoms = list(group)
            for poly in geoms:
                if not getattr(poly, "is_empty", False):
                    flat_polys.append(poly)
                    flat_colors.append(color)

    # 3) Set up Matplotlib figure
    fig = plt.figure(figsize=(fig_w, fig_h), dpi=dpi)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_aspect('equal')
    ax.axis('off')

    # Background
    if bg_color == 'transparent':
        fig.patch.set_alpha(0.0)
        ax.patch.set_alpha(0.0)
    else:
        fig.patch.set_facecolor(bg_color)
        ax.set_facecolor(bg_color)

    # 4) Auto-zoom to all polygons
    xs, ys = [], []
    for poly in flat_polys:
        minx, miny, maxx, maxy = poly.bounds
        xs.extend([minx, maxx])
        ys.extend([miny, maxy])
    if xs and ys:
        ax.set_xlim(min(xs), max(xs))
        ax.set_ylim(min(ys), max(ys))

    # 5) Draw every shape with its shade (full opacity)
    from matplotlib.path import Path
    from matplotlib.patches import PathPatch

    length = len(flat_polys)
    current = 0
    for poly, rgb in zip(flat_polys, flat_colors):
        geoms = poly.geoms if isinstance(poly, MultiPolygon) else [poly]
        for geom in geoms:
            # Exterior ring
            verts = list(geom.exterior.coords)
            codes = [Path.MOVETO] + [Path.LINETO]*(len(verts)-2) + [Path.CLOSEPOLY]
            # Interior holes
            for interior in geom.interiors:
                icoords = list(interior.coords)
                verts += icoords
                codes += [Path.MOVETO] + [Path.LINETO]*(len(icoords)-2) + [Path.CLOSEPOLY]

            path = Path(verts, codes)
            patch = PathPatch(
                path,
                facecolor=np.array(rgb)/255.0,
                linewidth=0,
                fill=True
            )
            ax.add_patch(patch)
        # 5.1) Progress callback
        current += 1
        if progress_cb and current % 30 == 0:
            # We must call into GTK from the main thread:
            # GLib.idle_add will schedule the callback on the main loop.
            def _emit(done, tot):
                # If callback returns False, that means “please abort”
                return progress_cb((done/tot) / 2 + 0.5)
            GLib.idle_add(_emit, current, length)


    # 6) Export to PNG in memory
    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=dpi, transparent=(bg_color=='transparent'))
    plt.close(fig)
    buf.seek(0)

    # 7) Load into a GdkPixbuf
    loader = GdkPixbuf.PixbufLoader.new_with_type('png')
    loader.write(buf.getvalue())
    loader.close()
    return loader.get_pixbuf()

