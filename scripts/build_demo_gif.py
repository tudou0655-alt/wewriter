"""Build a fictional-data GIF tour; no customer screenshots or API calls."""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs/showcase/wewriter-demo.gif"
SIZE = (1200, 720)
INK, GREEN, MUTED = "#18352e", "#146c53", "#5e7069"
LIME, LAVENDER, PAPER = "#d8f181", "#e6daf9", "#f6f6f0"


def font(size, bold=False):
    file = Path("C:/Windows/Fonts") / ("msyhbd.ttc" if bold else "msyh.ttc")
    return ImageFont.truetype(str(file), size) if file.exists() else ImageFont.load_default()


def label(draw, pos, value, size=20, fill=INK, bold=False):
    draw.text(pos, value, font=font(size, bold), fill=fill)


def card(draw, rect, fill="white"):
    draw.rounded_rectangle(rect, 18, fill=fill, outline="#d9e1d7", width=2)


def frame(page, step, heading, subheading):
    image = Image.new("RGB", SIZE, PAPER)
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 182, 720), fill="white")
    card(draw, (17, 25, 54, 62), LIME)
    label(draw, (27, 29), "W", 22, GREEN, True)
    label(draw, (66, 26), "WeWriter", 22, INK, True)
    label(draw, (20, 77), "演示数据 · 免 Key", 14, MUTED)
    for i, name in enumerate(("工作台", "公众号", "文章库", "稿件库", "设置")):
        y = 142 + i * 58
        if name == page:
            card(draw, (14, y - 8, 167, y + 39), LIME)
        label(draw, (30, y), name, 19, INK, name == page)
    label(draw, (18, 624), "功能示意 · 虚构素材", 13, MUTED)
    label(draw, (18, 651), "非真实客户任务", 13, MUTED)
    label(draw, (219, 27), "WORKSPACE / " + page, 15, GREEN, True)
    label(draw, (219, 59), "从授权素材到可审核稿件", 32, INK, True)
    card(draw, (1000, 27, 1163, 70), "#e5f4eb")
    label(draw, (1020, 35), "● 演示模式", 17, GREEN, True)
    label(draw, (219, 129), f"{step:02d}  {heading}", 27, INK, True)
    label(draw, (220, 176), subheading, 17, MUTED)
    draw.rounded_rectangle((220, 688, 1162, 697), 4, fill="#dbe4dc")
    draw.rounded_rectangle((220, 688, 220 + int(942 * step / 6), 697), 4, fill=GREEN)
    return image, draw


def row(draw, y, title, detail, status):
    card(draw, (220, y, 1162, y + 110))
    label(draw, (246, y + 19), title, 21, INK, True)
    label(draw, (246, y + 62), detail, 16, MUTED)
    card(draw, (995, y + 32, 1130, y + 76), "#eaf4e4")
    label(draw, (1012, y + 41), status, 16, GREEN, True)


def workbench():
    im, d = frame("工作台", 1, "打开免 Key 演示", "独立样例库，不采集真实公众号，也不调用模型。")
    card(d, (220, 227, 775, 494), LIME)
    label(d, (251, 261), "今天，让内容更进一步。", 31, INK, True)
    label(d, (251, 331), "2 个示例公众号 · 2 篇示例文章", 19)
    card(d, (250, 400, 491, 456), INK)
    label(d, (270, 412), "查看演示素材 →", 18, "white", True)
    card(d, (797, 227, 1162, 494), LAVENDER)
    label(d, (827, 270), "本次稿件", 22, INK, True)
    label(d, (827, 337), "1 篇演示稿件", 28, INK, True)
    label(d, (827, 405), "待人工审核", 19, "#a55234", True)
    row(d, 518, "从素材到可审核稿件", "按状态推进，来源可追溯。", "仅本地样例")
    return im


def accounts():
    im, d = frame("公众号", 2, "管理公众号", "账号名称和标识均来自软件自带的虚构演示用例。")
    row(d, 232, "示例 · 科技观察", "演示账号：demo_technology", "已启用")
    row(d, 359, "示例 · 产品笔记", "演示账号：demo_product", "已启用")
    card(d, (220, 518, 1162, 635), "#eaf4ec")
    label(d, (248, 540), "正式使用时可同步已授权公众号；演示模式不触发登录或网络请求。", 20)
    return im


def articles():
    im, d = frame("文章库", 3, "查看来源文章", "标题、正文均为本地样例，阅读量未知，不参与真实热度排名。")
    row(d, 232, "从信息收集到内容创作：让资料真正用起来", "示例 · 科技观察 · 演示素材 · 阅读量未知", "查看文章")
    row(d, 359, "一个好的写作工具，应该保留哪些人工判断", "示例 · 产品笔记 · 演示素材 · 阅读量未知", "查看文章")
    card(d, (220, 518, 1162, 635), "#fff8df")
    label(d, (247, 539), "示例正文", 21, INK, True)
    label(d, (247, 577), "整理信息时，应先记录来源，再提炼事实与观点。", 19)
    return im


def drafts():
    im, d = frame("稿件库", 4, "稿件需要人工审核", "演示稿件已预置在独立样例库，并非实际模型生成结果。")
    row(d, 232, "演示稿件｜让每日阅读变成可追溯的创作素材", "来源：2 篇虚构文章 · 可查看素材报告", "待审核")
    card(d, (220, 382, 1162, 625), LAVENDER)
    label(d, (247, 416), "审核前重点检查", 23, INK, True)
    label(d, (247, 473), "① 事实与来源是否对应", 19)
    label(d, (247, 521), "② 标题和表达是否适合读者", 19)
    label(d, (247, 569), "③ 确认后再复制或导出", 19)
    return im


def detail():
    im, d = frame("稿件库", 5, "查看正文与来源", "正文、素材报告与审核状态分开展示。")
    card(d, (220, 219, 1162, 640))
    label(d, (249, 248), "演示稿件｜让每日阅读变成可追溯的创作素材", 25, INK, True)
    d.line((249, 305, 1132, 305), fill="#d9e1d7", width=2)
    label(d, (249, 337), "从收集开始，保留来源", 22, INK, True)
    label(d, (249, 388), "每天的信息很多。把文章标题与原文链接一起保存，", 19)
    label(d, (249, 426), "后续写作才有可靠的材料入口。", 19)
    card(d, (248, 501, 768, 606), "#eaf4ec")
    label(d, (270, 516), "素材报告", 19, GREEN, True)
    label(d, (270, 557), "[S1] 示例 · 科技观察", 17)
    card(d, (786, 501, 1132, 606), "#fff3e4")
    label(d, (808, 524), "● 待人工审核", 19, "#a55234", True)
    label(d, (808, 561), "演示稿不可直接发布", 16, MUTED)
    return im


def finish():
    im, d = frame("工作台", 6, "演示结束", "正式任务需要授权素材、模型配置与人工审核。")
    card(d, (220, 228, 1162, 635), LIME)
    label(d, (264, 275), "采集 → 整理 → 写作 → 审核 → 导出", 34, INK, True)
    label(d, (265, 364), "任务状态与来源关系保存在本地，失败后可按阶段继续。", 20)
    label(d, (265, 434), "本 GIF 为功能示意；全部名称与素材均为虚构用例。", 20)
    label(d, (265, 483), "未展示客户数据，也未调用任何 API。", 20)
    card(d, (263, 559, 577, 611), INK)
    label(d, (286, 570), "WeWriter · 免 Key Demo", 18, "white", True)
    return im


def main():
    images = [fn() for fn in (workbench, accounts, articles, drafts, detail, finish)]
    frames = [im.quantize(colors=128, method=Image.Quantize.MEDIANCUT) for im in images]
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    frames[0].save(OUTPUT, save_all=True, append_images=frames[1:],
                   duration=4000, loop=0, optimize=True, disposal=2)
    print(f"Wrote {OUTPUT} ({OUTPUT.stat().st_size} bytes, 24 seconds)")


if __name__ == "__main__":
    main()
