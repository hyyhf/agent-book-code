"""Update the curated README gallery without touching video or book sections."""
from pathlib import Path
import json

ROOT = Path(__file__).resolve().parents[1]
GROUPS = [
    ("概念工坊", [
        ("concept-desktop.png", "概念桌面", "学习、探索与归档，从同一张桌面出发"),
        ("concept-growth.png", "概念生长", "沿学习路径推进，把理解落实到每个节点"),
        ("concepts.png", "认知冲刺", "用环形跑道呈现学习进度与检查点"),
        ("concept-universe.png", "概念宇宙", "展开概念关系，探索关联网络"),
    ]),
    ("心智庭院", [
        ("garden-today.png", "此刻对话", "从一件真实的事出发，记录当下"),
        ("garden-photo.png", "照片故事", "在可探索的粒子画面中重访记忆"),
        ("garden.png", "我的星图", "把经历与观察连接成个人星图"),
        ("garden-philosophy.png", "我的哲学", "让日常反思沉淀成可回读的笔记"),
    ]),
    ("更多工作台界面", [
        ("chat.png", "Agent 对话", "查看思考过程、工具调用与任务结果"),
        ("teams.png", "多 Agent 协作", "查看角色分工与任务执行状态"),
        ("study.png", "学习实验室", "连接文档阅读、概念图谱与主动学习"),
        ("replay.png", "会话演绎", "回放执行路径，检查工具输入与返回"),
    ]),
]


def table(entries):
    lines = ["<table>"]
    for offset in range(0, len(entries), 2):
        lines.append("  <tr>")
        for file, title, caption in entries[offset:offset + 2]:
            lines.extend([
                '    <td width="50%" align="center">',
                f'      <a href="assets/studio/{file}"><img src="assets/studio/{file}" alt="FunHarness Studio {title}" width="100%" /></a>',
                f'      <br/><b>{title}</b><br/><sub>{caption}</sub>',
                '    </td>',
            ])
        lines.append("  </tr>")
    return "\n".join([*lines, "</table>"])


def main():
    readme = ROOT / "README.md"
    text = readme.read_text(encoding="utf-8")
    start = text.index("<table>", text.index("## FunHarness Studio"))
    end = text.index("### 动态演示")
    # First run replaces the former six-image grid; subsequent runs use markers.
    if "<!-- STUDIO_POSTER_START -->" not in text:
        text = text[:start] + ('<!-- STUDIO_POSTER_START -->\n\n'
            '[![FunHarness Studio：概念工坊、心智庭院与智能工作台](assets/studio/studio-poster.png)](assets/studio/studio-poster.png)\n\n'
            '<sub>功能拼贴海报 · [查看下方原始界面截图](#界面图集)</sub>\n\n'
            '<!-- STUDIO_POSTER_END -->\n\n') + text[end:]
    text = text.replace("12 秒快速预览。点击上方截图可查看清晰大图。", "12 秒快速预览。更多清晰截图见下方界面图集。")
    gallery = ["<!-- STUDIO_GALLERY_START -->", "", "### 界面图集", "", "以下均为实际界面截图，点击可查看原图。", ""]
    for title, entries in GROUPS:
        gallery.extend([f"#### {title}", "", table(entries), ""])
    gallery.append("<!-- STUDIO_GALLERY_END -->")
    block = "\n".join(gallery) + "\n\n"
    if "<!-- STUDIO_GALLERY_START -->" in text:
        left = text.index("<!-- STUDIO_GALLERY_START -->")
        right = text.index("<!-- STUDIO_GALLERY_END -->", left) + len("<!-- STUDIO_GALLERY_END -->\n\n")
        text = text[:left] + block + text[right:]
    else:
        text = text.replace("<details>\n<summary>查看 FunHarness TUI 终端演示</summary>", block + "<details>\n<summary>查看 FunHarness TUI 终端演示</summary>", 1)
    readme.write_text(text, encoding="utf-8")
    manifest = {"poster": {"file": "studio-poster.png", "method": "HTML/CSS original screenshot collage, rendered with Electron", "source": "studio-poster.html", "screenshot_count": 20, "note": "20 张原始截图按比例拼贴，无 AI 重绘。"}, "gallery": [{"section": title, "files": [item[0] for item in entries]} for title, entries in GROUPS]}
    (ROOT / "assets/studio/gallery.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

