from PIL import Image
import numpy as np
from skimage.color import rgb2lab


def segment_to_shades(source_image: Image, filament_shades):
    # 1) load & normalize to [0,1]
    rgb = np.asarray(source_image.convert('RGB'), dtype=float) / 255.0
    lab = rgb2lab(rgb)  # (H, W, 3)

    h, w, _ = lab.shape
    lab_flat = lab.reshape(-1, 3)  # (H*W, 3)

    # 2) flatten your shades into one array
    flat_shades = [shade for shade_list in filament_shades for shade in shade_list]
    shade_rgb = np.array(flat_shades, dtype=float)  # (N, 3), still 0–255

    # normalize & convert to Lab
    shade_rgb_norm = shade_rgb / 255.0
    shade_lab = rgb2lab(shade_rgb_norm.reshape(1, -1, 3)).reshape(-1, 3)  # (N, 3)

    # 3) compute all distances: each pixel vs. each shade
    #    result shape: (H*W, N)
    dists = np.linalg.norm(lab_flat[:, None, :] - shade_lab[None, :, :], axis=2)

    # 4) pick the nearest shade index for each pixel
    nearest = np.argmin(dists, axis=1)  # (H*W,)

    # 5) build segmented array & back to image
    seg_flat_rgb = shade_rgb[nearest].astype(np.uint8)  # (H*W, 3)
    seg_rgb = seg_flat_rgb.reshape(h, w, 3)

    return Image.fromarray(seg_rgb, mode='RGB')

def generate_shades(filament_order, overlay_factor=0.3, max_layers=3):
    """
    From a flat list of base RGB filaments produce, for each filament,
    a list of 'max_layers' blended shades against the previous filament.
    The very first filament just has itself as a single shade.
    """
    all_shades = []
    for i, cur in enumerate(filament_order):
        if i == 0:
            # first filament: no blending underneath
            all_shades.append([tuple(cur)])
        else:
            prev = filament_order[i-1]
            shades = []
            for L in range(1, max_layers+1):
                blend = min(overlay_factor * L, 1.0)
                # linear interpolate:  prev*(1−blend) + cur*blend
                shade = tuple(
                    int(round(prev[c] * (1 - blend) + cur[c] * blend))
                    for c in range(3)
                )
                shades.append(shade)
            all_shades.append(shades)
    return all_shades