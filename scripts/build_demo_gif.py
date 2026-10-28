"""Combine screenshots captured from the running WeWriter --demo UI.

The source PNGs are actual application pages with the built-in fictional
accounts, articles and draft. No drawing or title replacement is performed.
"""

from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SCREENSHOTS = ROOT / "docs" / "showcase" / "screenshots"
OUTPUT = ROOT / "docs" / "showcase" / "wewriter-demo.gif"
PAGES = (
    "workbench.png",
    "accounts.png",
    "articles.png",
    "article-detail.png",
    "drafts.png",
    "draft-view.png",
)


def main():
    frames = []
    size = None
    for name in PAGES:
        with Image.open(SCREENSHOTS / name) as source:
            image = source.convert("RGB")
        if size is None:
            size = image.size
        elif image.size != size:
            raise ValueError(f"{name}: screenshot dimensions differ from {size}")
        frames.append(image.quantize(colors=256, method=Image.Quantize.MEDIANCUT))
    frames[0].save(
        OUTPUT, save_all=True, append_images=frames[1:], duration=4000,
        loop=0, optimize=True, disposal=2,
    )
    print(f"Wrote {OUTPUT}: {len(frames)} actual UI frames, {len(frames) * 4} seconds")


if __name__ == "__main__":
    main()
