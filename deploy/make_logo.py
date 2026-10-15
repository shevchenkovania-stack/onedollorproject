# Brand logo system v3 — ONE family, TWO surfaces.
#
# Same mark (house + keyhole: "Găsește Casa" = find your house and the key to it), drawn once and
# reused. The bot and the channel must NOT be the identical square anymore (a founder's-eye tell
# that nobody designed this), but they must read as one brand — so they share the exact geometry
# and differ only by the surface colour: Bot = brand teal (the tool), Channel = ink slate (the feed).
#
# Deterministic PIL, drawn at 4x and LANCZOS-downscaled so every edge is crisp at 100 px. Telegram
# crops both bot avatars and channel photos to a CIRCLE, so everything lives inside the centre safe
# area and nothing important sits near a corner. No text: Telegram renders the name itself, an icon
# must survive on its own.
#
# Output: deploy/logo_bot.png and deploy/logo_channel.png (each 1024x1024, RGB).
from PIL import Image, ImageDraw

S = 4096                      # master canvas, downscaled to 1024 with LANCZOS
WHITE = (255, 255, 255, 255)

# Two surface gradients of the same family. Teal = bot (searching tool), slate = channel (the feed).
TEAL = ((15, 178, 160), (10, 110, 99))     # #0FB2A0 -> #0A6E63
SLATE = ((59, 74, 107), (28, 37, 54))      # #3B4A6B -> #1C2536


def _gradient(top: tuple, bottom: tuple) -> Image.Image:
    img = Image.new("RGBA", (S, S))
    d = ImageDraw.Draw(img)
    for y in range(S):
        t = y / S
        c = tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3))
        d.line([(0, y), (S, y)], fill=c + (255,))
    return img, d


def mark(bg: tuple) -> Image.Image:
    """The house+keyhole icon, identical geometry, only the background gradient changes."""
    img, d = _gradient(*bg)
    mid = tuple((bg[0][i] + bg[1][i]) // 2 for i in range(3)) + (255,)

    # chimney (behind the roof line, on the right slope)
    d.rounded_rectangle((0.615 * S, 0.250 * S, 0.672 * S, 0.360 * S), radius=0.010 * S, fill=WHITE)

    # roof: a clean filled gable (triangle with a slightly flattened foot)
    d.polygon([(0.5 * S, 0.185 * S), (0.805 * S, 0.440 * S), (0.195 * S, 0.440 * S)], fill=WHITE)
    # roof underside trim so the gable reads as a solid, not a floating triangle
    d.rectangle((0.195 * S, 0.425 * S, 0.805 * S, 0.450 * S), fill=WHITE)

    # body: bold rounded square (rounded bottom corners)
    d.rounded_rectangle((0.270 * S, 0.440 * S, 0.730 * S, 0.795 * S),
                        radius=0.030 * S, fill=WHITE)

    # keyhole: circle + wedge, punched through in the mid-gradient colour (negative space)
    cx, cy, r = 0.5 * S, 0.575 * S, 0.058 * S
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=mid)
    d.polygon([(cx, cy), (cx - 0.052 * S, 0.720 * S), (cx + 0.052 * S, 0.720 * S)], fill=mid)

    return img.resize((1024, 1024), Image.LANCZOS).convert("RGB")


if __name__ == "__main__":
    mark(TEAL).save("deploy/logo_bot.png", optimize=True)
    mark(SLATE).save("deploy/logo_channel.png", optimize=True)
    print("wrote deploy/logo_bot.png and deploy/logo_channel.png")
