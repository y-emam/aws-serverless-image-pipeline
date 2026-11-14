"""Regenerate the small test images in this folder.

    python tests/images/make_images.py
"""

from pathlib import Path

from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent


def gradient(size):
    w, h = size
    img = Image.new("RGB", size)
    px = img.load()
    for x in range(w):
        for y in range(h):
            px[x, y] = (x * 255 // w, y * 255 // h, 140)
    ImageDraw.Draw(img).ellipse((w // 4, h // 4, 3 * w // 4, 3 * h // 4), fill=(240, 200, 40))
    return img


def main():
    # Landscape JPEG with EXIF orientation 6 ("rotate 90 CW to display").
    photo = gradient((800, 600))
    exif = Image.Exif()
    exif[0x0112] = 6
    photo.save(HERE / "photo_rotated.jpg", "JPEG", quality=85, exif=exif)

    # PNG with transparency (must be flattened to white for JPEG output).
    rgba = Image.new("RGBA", (400, 300), (0, 0, 0, 0))
    ImageDraw.Draw(rgba).rectangle((50, 50, 350, 250), fill=(30, 120, 220, 255))
    rgba.save(HERE / "transparent.png")

    # Small WebP: smaller than the thumbnail size, must not be upscaled.
    gradient((200, 150)).save(HERE / "small.webp", "WEBP")

    # GIF: a real image, but not an allowed format.
    gradient((64, 64)).save(HERE / "anim.gif", "GIF")

    # Text file with a .jpg name: must be rejected by validate.
    (HERE / "not_an_image.jpg").write_bytes(b"this is not an image\n" * 10)

    print("Test images written to", HERE)


if __name__ == "__main__":
    main()
