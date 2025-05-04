import io

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
from gi.repository import Gtk, GdkPixbuf

# Configuration defaults
OUTPUT_DIR = 'meshes'
SIMPLIFY_TOLERANCE = 0.5  # Simplify tolerance for raw polygons
SMOOTHING_WINDOW = 3  # Window size for contour smoothing
MIN_AREA = 0.25  # Minimum polygon area to keep


def ensure_dir(path):
    if not os.path.exists(path):
        os.makedirs(path)


def extract_color_masks(img_arr):
    h, w = img_arr.shape[:2]
    flat = img_arr.reshape(-1, img_arr.shape[2])
    colors, inv = np.unique(flat, axis=0, return_inverse=True)
    masks = {}
    for idx, color in enumerate(map(tuple, colors)):
        mask = (inv.reshape(h, w) == idx)
        if mask.sum() and len(color) >= 3:
            masks[color[:3]] = mask
    return masks


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


def generate_layer_mesh(polygons, thickness, engine='triangle'):
    meshes = []
    for poly in polygons:
        m = trimesh.creation.extrude_polygon(poly, thickness,
                                             triangulate_kwargs={'engine': engine})
        meshes.append(m)
    return trimesh.util.concatenate(meshes) if meshes else None


def merge_layers_downward(meshes_list):
    last = None
    for i, meshes in enumerate(meshes_list[::-1]):
        for j, mesh in enumerate(meshes[::-1]):
            if last is None:
                last = mesh
            else:
                last = trimesh.util.concatenate(last, mesh)
                meshes_list[-(i + 1)][-(j + 1)] = last

def merge_polys_downward(polys_list):
    last = None
    for i, layer in enumerate(reversed(polys_list)):

        for j, poly in enumerate(reversed(layer)):
            if last is None:
                last = poly
            else:
                last += poly
            polys_list[-(i + 1)][-(j + 1)] = last[:]

    return polys_list

def create_layered_meshes(segmented_image, source_image, filament_order,
                      layer_height=0.2, base_layers=4,
                      min_layers=1, max_layers=3,
                      target_max_cm=10, engine='triangle'):
    ensure_dir(OUTPUT_DIR)
    base_height = layer_height * base_layers

    w_px, h_px = segmented_image.size
    scale_xy = (target_max_cm * 10) / max(w_px, h_px)

    src_arr = np.array(source_image.convert('RGBA'))

    seg_arr = np.array(segmented_image.convert('RGBA'))
    masks = extract_color_masks(seg_arr)

    # Base layer
    base_rect = Polygon([(0, 0), (w_px, 0), (w_px, h_px), (0, h_px)])
    base_poly = flip_polygons_vertically([base_rect], h_px)
    base_mesh = generate_layer_mesh(base_poly, base_height, engine)
    if base_mesh:
        base_mesh.apply_scale([scale_xy, scale_xy, 1])
        r, g, b = filament_order[0][:3]
        base_mesh.export(os.path.join(OUTPUT_DIR, f'layer_0_{r}_{g}_{b}.stl'))

    # Compute counts_map
    counts_map = {}
    rgb_np = np.asarray(source_image.convert("RGB")).astype(float) / 255.0
    # Convert RGB image to LAB
    lab_target = rgb2lab(rgb_np)

    for idx in range(1, len(filament_order)):
        prev = filament_order[idx - 1]
        # Convert this filament’s color to LAB
        prev_lab = rgb2lab(np.array(prev)[np.newaxis, np.newaxis, :] / 255.)  # shape: (1, 1, 3)

        # Create region mask
        region = np.zeros((h_px, w_px), dtype=bool)
        for c in filament_order[idx:]:
            if c in masks:
                region |= masks[c]
        if not region.any():
            continue

        # Compute perceptual color difference (ΔE) only in the region
        prev_lab_img = np.tile(prev_lab, (h_px, w_px, 1))  # shape: (h_px, w_px, 3)
        deltaE = deltaE_ciede2000(prev_lab_img, lab_target) * region

        # Normalize ΔE and map to layer count
        norm = deltaE / 100  # ΔE2000 is roughly 0–100
        cnt = np.rint(min_layers + norm * (max_layers - min_layers)).astype(int)
        cnt = np.clip(cnt, min_layers, max_layers) * region.astype(int)

        counts_map[idx] = cnt

    # Build meshes_map
    meshes_list = []
    for idx in range(1, len(filament_order)):
        cnt = counts_map.get(idx)
        if cnt is None: continue
        mesh_list = []
        for L in range(1, max_layers + 1):
            mask_L = cnt >= L
            if not mask_L.any(): continue
            polys = mask_to_polygons(mask_L)
            if not polys: continue
            polys = flip_polygons_vertically(polys, h_px)
            m = generate_layer_mesh(polys, layer_height, engine)
            if m: mesh_list.append(m)
        if mesh_list:
            meshes_list.append(mesh_list)

    # Merge downward without deleting layers
    merge_layers_downward(meshes_list)
    meshes = [base_mesh] if base_mesh else []

    current_z0 = base_height
    for idx, mesh_list in enumerate(meshes_list):
        for L, m in enumerate(mesh_list):
            m.apply_translation([0, 0, current_z0])
            if not m.is_empty:
                current_z0 += layer_height
        combined = trimesh.util.concatenate(mesh_list)
        combined.apply_scale([scale_xy, scale_xy, 1])
        meshes.append(combined)

    return meshes

def _generate_base_mesh(segmented_image, layer_height=0.2, base_layers=4,
                      target_max_cm=10, engine='triangle'):
    base_height = layer_height * base_layers
    w_px, h_px = segmented_image.size
    scale_xy = (target_max_cm * 10) / max(w_px, h_px)

    # Base layer
    base_rect = Polygon([(0, 0), (w_px, 0), (w_px, h_px), (0, h_px)])
    base_poly = flip_polygons_vertically([base_rect], h_px)
    base_mesh = generate_layer_mesh(base_poly, base_height, engine)
    if base_mesh:
        base_mesh.apply_scale([scale_xy, scale_xy, 1])
        return base_mesh, base_height

def create_layered_polygons(segmented_image, source_image, filament_order,
                      min_layers=1, max_layers=3,
                      target_max_cm=10, engine='triangle'):
    ensure_dir(OUTPUT_DIR)

    w_px, h_px = segmented_image.size
    seg_arr = np.array(segmented_image.convert('RGBA'))
    masks = extract_color_masks(seg_arr)

    base_rect = Polygon([(0, 0), (w_px, 0), (w_px, h_px), (0, h_px)])
    base_poly = flip_polygons_vertically([base_rect], h_px)

    # Compute counts_map
    counts_map = {}
    rgb_np = np.asarray(source_image.convert("RGB")).astype(float) / 255.0
    # Convert RGB image to LAB
    lab_target = rgb2lab(rgb_np)

    for idx in range(1, len(filament_order)):
        prev = filament_order[idx - 1]
        # Convert this filament’s color to LAB
        prev_lab = rgb2lab(np.array(prev)[np.newaxis, np.newaxis, :] / 255.)  # shape: (1, 1, 3)

        # Create region mask
        region = np.zeros((h_px, w_px), dtype=bool)
        for c in filament_order[idx:]:
            if c in masks:
                region |= masks[c]
        if not region.any():
            continue

        # Compute perceptual color difference (ΔE) only in the region
        prev_lab_img = np.tile(prev_lab, (h_px, w_px, 1))  # shape: (h_px, w_px, 3)
        deltaE = deltaE_ciede2000(prev_lab_img, lab_target) * region

        # Normalize ΔE and map to layer count
        norm = deltaE / 100  # ΔE2000 is roughly 0–100
        cnt = np.rint(min_layers + norm * (max_layers - min_layers)).astype(int)
        cnt = np.clip(cnt, min_layers, max_layers) * region.astype(int)

        counts_map[idx] = cnt

    # Build meshes_map
    polys_list = []
    for idx in range(1, len(filament_order)):
        cnt = counts_map.get(idx)
        if cnt is None: continue
        poly_list = []
        for L in range(1, max_layers + 1):
            mask_L = cnt >= L
            if not mask_L.any(): continue
            polys = mask_to_polygons(mask_L)
            if not polys: continue
            polys = flip_polygons_vertically(polys, h_px)
            poly_list.append(polys)
        if poly_list:
            polys_list.append(poly_list)

    # Merge downward without deleting layers
    merge_polys_downward(polys_list)
    polys_list.insert(0, base_poly)
    return polys_list

def polygons_to_meshes(segmented_image, polys_list, layer_height=0.2, base_layers=4, target_max_cm=10, engine='triangle'):
    meshes_list = []
    for idx, polys in enumerate(polys_list):
        if not polys: continue
        m = generate_layer_mesh(polys, layer_height, engine)
        if m:
            meshes_list.append(m)

    base_mesh, base_height = _generate_base_mesh(segmented_image, layer_height, base_layers, target_max_cm, engine)
    w_px, h_px = segmented_image.size
    scale_xy = (target_max_cm * 10) / max(w_px, h_px)
    meshes = [base_mesh] if base_mesh else []

    current_z0 = base_height
    for idx, mesh_list in enumerate(meshes_list):
        for L, m in enumerate(mesh_list):
            m.apply_translation([0, 0, current_z0])
            if not m.is_empty:
                current_z0 += layer_height
        combined = trimesh.util.concatenate(mesh_list)
        combined.apply_scale([scale_xy, scale_xy, 1])
        meshes.append(combined)

    return meshes


def render_polygons_to_pixbuf(layered_polygons, filament_colors, width=400, height=400, bg_color='white'):
    """
    Render nested layers of polygons (list of lists/groups) with matching colors.
    layered_polygons: [layer0, layer1, ...], each layer is a list of Polygon/MultiPolygon or groups thereof.
    filament_colors: list of RGB tuples per layer.
    """
    # Flatten nested layers into parallel lists of geometries and colors
    flat_polys = []
    flat_colors = []
    for layer_idx, layer_groups in enumerate(layered_polygons):
        color = filament_colors[layer_idx]
        for group in layer_groups:
            # group may be a single geometry or an iterable of geometries
            if isinstance(group, (Polygon, MultiPolygon)):
                geom_list = [group]
            else:
                geom_list = list(group)
            for poly in geom_list:
                flat_polys.append(poly)
                flat_colors.append(color)

    # Create matplotlib figure
    fig = plt.figure(figsize=(width/100, height/100), dpi=100)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_aspect('equal')
    ax.axis('off')
    fig.patch.set_facecolor(bg_color)
    ax.set_facecolor(bg_color)

    # Draw each polygon with corresponding color
    first = True
    for poly, rgb in zip(flat_polys, flat_colors):
        if not hasattr(poly, 'is_empty') or poly.is_empty:
            continue
        # handle MultiPolygon
        geoms = poly.geoms if isinstance(poly, MultiPolygon) else [poly]
        for geom in geoms:
            xs, ys = geom.exterior.xy
            alpha = 0.3
            if first:
                # First polygon is fully opaque
                alpha = 1.0
                first = False
            ax.fill(xs, ys, facecolor=np.array(rgb)/255.0, edgecolor='black', linewidth=0.5, alpha=alpha)
            for interior in geom.interiors:
                ix, iy = interior.xy
                ax.fill(ix, iy, facecolor=bg_color, edgecolor='black', linewidth=0.5)

    # Render to PNG buffer
    buf = io.BytesIO()
    fig.canvas.print_png(buf)
    plt.close(fig)
    buf.seek(0)

    # Load into GdkPixbuf
    loader = GdkPixbuf.PixbufLoader.new_with_type('png')
    loader.write(buf.getvalue())
    loader.close()
    pixbuf = loader.get_pixbuf()
    return pixbuf


def store_meshes(meshes, output_dir=OUTPUT_DIR, filament_order=None):
    ensure_dir(output_dir)
    for idx, mesh in enumerate(meshes):
        r, g, b = filament_order[idx + 1][:3]
        mesh.export(os.path.join(output_dir, f'layer_{idx + 1}_{r}_{g}_{b}.stl'))
        print(f"Exported layer {idx} with {len(mesh)} meshes")