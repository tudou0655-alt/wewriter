"""Build the README's offline product-tour GIF from redacted screenshots."""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps


ROOT = Path(__file__).resolve().parents[1]
SCREENSHOTS = ROOT / "docs" / "showcase" / "screenshots"
OUTPUT = ROOT / "docs" / "showcase" / "wewriter-demo.gif"
SIZE = (1200, 760)
GREEN = "#0d745e"
INK = "#18352e"
MUTED = "#61726d"
PAPER = "#f2f6f4"


def font(size, bold=False):
    name = "msyhbd.ttc" if bold else "msyh.ttc"
    path = Path("C:/Windows/Fonts") / name
    return ImageFont.truetype(str(path), size) if path.exists() else ImageFont.load_default()


def base():
    return Image.new("RGB", SIZE, PAPER)


def centered(draw, text, y, text_font, fill=INK):
    box = draw.textbbox((0, 0), text, font=text_font)
    draw.text(((SIZE[0] - box[2]) / 2, y), text, font=text_font, fill=fill)


def title_frame(title, subtitle):
    image = base()
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((120, 135, 1080, 625), 36, fill="white", outline="#d9e6e1", width=3)
    centered(draw, title, 255, font(48, True))
    centered(draw, subtitle, 340, font(26), MUTED)
    centered(draw, "虚构 / 脱敏数据 · 不调用 API · 不需要 Key", 445, font(22), GREEN)
    return image


def screenshot_frame(filename, title, detail, step, total):
    image = base()
    draw = ImageDraw.Draw(image)
    draw.text((42, 24), title, font=font(31, True), fill=INK)
    draw.text((42, 66), detail, font=font(20), fill=MUTED)
    shot = Image.open(SCREENSHOTS / filename).convert("RGB")
    shot = ImageOps.contain(shot, (1116, 620), Image.Resampling.LANCZOS)
    x = (SIZE[0] - shot.width) // 2
    image.paste(shot, (x, 110))
    draw.rounded_rectangle((x - 2, 108, x + shot.width + 2, 112 + shot.height), 8,
                           outline="#c9d9d3", width=2)
    bar_width = int((SIZE[0] - 84) * step / total)
    draw.rounded_rectangle((42, 738, SIZE[0] - 42, 746), 4, fill="#d8e4e0")
    draw.rounded_rectangle((42, 738, 42 + bar_width, 746), 4, fill=GREEN)
    return image


def main():
    tour = [
        title_frame("WeWriter", "可恢复、可追溯的 Windows AI 内容工作台"),
        screenshot_frame("workbench.png", "1. 启动演示工作台", "查看当前任务、阶段进度与待审核稿件", 1, 8),
        screenshot_frame("task-detail.png", "2. 采集与筛选漏斗", "每个公众号的返回、排除、候选和失败原因可解释", 2, 8),
        screenshot_frame("articles.png", "3. 来源文章库", "按阅读量筛选，0 与未知分开，正文状态一目了然", 3, 8),
        screenshot_frame("article-detail.png", "4. 来源追溯", "保留原文、指标来源、采集时间和处理状态", 4, 8),
        screenshot_frame("drafts.png", "5. 稿件进入人工审核", "AI 生成不等于自动发布，待审核状态明确可见", 5, 8),
        screenshot_frame("draft-view.png", "6. 二审、人工修改与导出", "正文保持干净；审核后复制全文或导出 Word / Markdown / TXT", 6, 8),
        screenshot_frame("settings.png", "7. 本地配置与维护", "模型、Key、定时、备份恢复都在客户电脑完成", 7, 8),
        screenshot_frame("workbench.png", "8. 失败后继续", "SQLite 缓存中间结果，只续跑未完成阶段", 8, 8),
        title_frame("从素材到可审核稿件", "按状态推进 · 缓存复用 · 失败恢复 · 来源可追溯"),
    ]
    frames = [frame.quantize(colors=128, method=Image.Quantize.MEDIANCUT) for frame in tour]
    frames[0].save(OUTPUT, save_all=True, append_images=frames[1:], duration=6000,
                   loop=0, optimize=True, disposal=2)
    print(f"Wrote {OUTPUT} ({OUTPUT.stat().st_size} bytes, 60 seconds)")


if __name__ == "__main__":
    main()
