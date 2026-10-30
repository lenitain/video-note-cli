"""把 Markdown 笔记导成单文件 HTML。

浏览器不渲染 Markdown：双击 .md 看到的是纯文本，Markdown 预览扩展默认也不允许
读 ``file://`` 下的相对图片。想在浏览器里看图文笔记，只能给它一份 HTML——图片
直接内联成 data URI，单个文件复制到哪都能看。

也可以脱离主流程单独转换已有笔记：

    uv run python -m video_note.html_export ~/notes/video/某个笔记.md
"""

from __future__ import annotations

import argparse
import base64
import mimetypes
import re
import sys
from pathlib import Path
from urllib.parse import unquote

from markdown_it import MarkdownIt

# 内联图片时的体积上限：超过它就不再转 base64，留着相对路径（HTML 与图片同目录时
# 仍然能显示）。正常一帧 640x360 的 JPEG 是 20-60 KB，碰不到这道线。
MAX_INLINE_BYTES = 4 * 1024 * 1024
# Markdown 渲染出的 <img>：把 src 换成 data URI，并把 alt 变成图注。
IMAGE_TAG_RE = re.compile(r"<img\b[^>]*?src=\"([^\"]+)\"[^>]*?>", re.IGNORECASE)
ALT_ATTR_RE = re.compile(r"\balt=\"([^\"]*)\"", re.IGNORECASE)
TITLE_RE = re.compile(r"^#\s+(.+)$", re.MULTILINE)

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{
    margin: 0 auto; padding: 3rem 1.25rem 6rem; max-width: 54rem;
    font: 16px/1.75 -apple-system, "Noto Sans CJK SC", "Source Han Sans SC",
          "PingFang SC", "Microsoft YaHei", sans-serif;
    color: #1f2328; background: #ffffff;
    word-break: break-word;
  }}
  h1 {{ font-size: 1.9rem; line-height: 1.3; margin: 0 0 1rem; }}
  h2 {{ font-size: 1.35rem; margin: 2.6rem 0 .8rem; padding-top: .9rem;
        border-top: 1px solid #e5e7eb; }}
  h3 {{ font-size: 1.12rem; margin: 1.8rem 0 .5rem; }}
  p, ul, ol {{ margin: .8rem 0; }}
  li {{ margin: .3rem 0; }}
  a {{ color: #0b62c4; }}
  code {{ background: #f2f4f7; border-radius: 4px; padding: .1em .35em;
          font: .9em/1.5 "JetBrains Mono", "Fira Code", ui-monospace, monospace; }}
  pre {{ background: #f6f8fa; border-radius: 8px; padding: .9rem 1rem; overflow-x: auto; }}
  pre code {{ background: none; padding: 0; }}
  blockquote {{ margin: 1.2rem 0; padding: .5rem 1rem; color: #57606a;
                border-left: 3px solid #d0d7de; background: #f9fafb; }}
  hr {{ border: 0; border-top: 1px solid #e5e7eb; margin: 2.5rem 0; }}
  figure {{ margin: 1.4rem 0; }}
  img {{ display: block; max-width: 100%; height: auto; border-radius: 8px;
         border: 1px solid #e5e7eb; background: #0d1117; }}
  figcaption {{ margin-top: .5rem; font-size: .86rem; color: #6e7681; text-align: center; }}
  @media (prefers-color-scheme: dark) {{
    body {{ color: #e6edf3; background: #0d1117; }}
    h2 {{ border-color: #21262d; }}
    code, pre {{ background: #161b22; }}
    blockquote {{ color: #9198a1; background: #161b22; border-color: #30363d; }}
    img {{ border-color: #30363d; }}
    a {{ color: #4493f8; }}
  }}
</style>
{math_script}
</head>
<body>
<article>
{body}
</article>
</body>
</html>
"""

# 公式：笔记里用 $...$ 与 $$...$$。联网时交给 MathJax 排版，离线就保持原样文本。
MATH_SCRIPT = """<script>
window.MathJax = {
  tex: { inlineMath: [['$', '$']], displayMath: [['$$', '$$']] },
  options: { skipHtmlTags: ['script', 'noscript', 'style', 'textarea', 'pre', 'code'] }
};
</script>
<script async src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-mml-chtml.js"></script>"""


def render_note_html(markdown_text: str, base_dir: Path, title: str = "") -> str:
    """Markdown → 单文件 HTML；正文图片就地内联成 data URI。"""
    renderer = MarkdownIt("commonmark", {"html": False}).enable("table")
    body = renderer.render(markdown_text)
    body = IMAGE_TAG_RE.sub(lambda match: _figure(match, base_dir), body)
    heading = TITLE_RE.search(markdown_text)
    page_title = title or (heading.group(1).strip() if heading else "视频笔记")
    return HTML_TEMPLATE.format(
        title=_escape(page_title), body=body, math_script=MATH_SCRIPT
    )


def write_note_html(html_path: Path, markdown_text: str | None = None) -> Path:
    """把 Markdown 写成单文件 HTML，落在 ``html_path`` 并返回它。

    ``markdown_text`` 用来喂“带图版本”：落盘的 Markdown 可能是去掉插图的纯文字版，
    而 HTML 需要带图片链接的那份；省略时读同主干的 ``.md``。
    """
    text = (
        markdown_text
        if markdown_text is not None
        else html_path.with_suffix(".md").read_text(encoding="utf-8")
    )
    html_path.write_text(
        render_note_html(text, html_path.parent), encoding="utf-8"
    )
    return html_path


def _figure(match: re.Match[str], base_dir: Path) -> str:
    tag = match.group(0)
    alt = ALT_ATTR_RE.search(tag)
    caption = alt.group(1) if alt else ""
    source = _inline_source(match.group(1), base_dir)
    figure = f'<img src="{source}" alt="{caption}">'
    if caption:
        figure = f"<figure>{figure}<figcaption>{caption}</figcaption></figure>"
    return figure


def _inline_source(src: str, base_dir: Path) -> str:
    """本地相对路径转 data URI；外链与读不到的文件原样返回。"""
    if src.startswith(("http://", "https://", "data:")):
        return src
    path = (base_dir / unquote(src)).resolve()
    try:
        if not path.is_file() or path.stat().st_size > MAX_INLINE_BYTES:
            return src
        payload = base64.b64encode(path.read_bytes()).decode("ascii")
    except OSError:
        return src
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return f"data:{mime};base64,{payload}"


def _escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m video_note.html_export",
        description="把 Markdown 笔记转成单文件 HTML（图片内联，浏览器直接看）",
    )
    parser.add_argument("notes", nargs="+", metavar="NOTE.md")
    args = parser.parse_args(argv)
    failed = 0
    for value in args.notes:
        path = Path(value).expanduser()
        if not path.is_file():
            print(f"找不到文件：{path}", file=sys.stderr)
            failed += 1
            continue
        print(write_note_html(path.with_suffix(".html")))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
