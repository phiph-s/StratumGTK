from PIL import Image

def group_pixels_to_filaments(image: Image, filaments, max_size = None):

    if max_size:
        scale_factor = min(max_size / image.width, max_size / image.height)
        new_width = int(image.width * scale_factor)
        new_height = int(image.height * scale_factor)
        # Resize the image
        image = image.resize((new_width, new_height))

    # Create a new image with the same size and mode as the original image
    new_image = Image.new(image.mode, image.size)

    # Get the pixel data from the original image
    pixels = image.load()

    # Get the pixel data from the new image
    new_pixels = new_image.load()

    # Iterate over each pixel in the original image
    for x in range(image.width):
        for y in range(image.height):
            # Get the color of the current pixel
            color = pixels[x, y]

            # Find the nearest filament color
            nearest_filament = min(filaments,
                                   key=lambda filament: sum((c1 - c2) ** 2 for c1, c2 in zip(color, filament)))

            # Set the pixel in the new image to the nearest filament color
            new_pixels[x, y] = nearest_filament

    return new_image
