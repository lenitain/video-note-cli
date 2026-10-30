# video-note-cli

把视频变成**带时间轴、正文内嵌画面**的 Markdown 笔记的 Linux 命令行工具。

只保留「取字幕 / 本地转写 → 视觉大模型看图写笔记」功能。

核心代码取自 [VideoToNo](https://github.com/like-attract/video-to-note)（MIT），去掉了 FastAPI、MCP、托盘、扫码登录、HTML/PNG 导出和密钥加密，改成适合终端与脚本调用的形式。

## 能做什么

- 输入：B 站、YouTube、其他 `yt-dlp` 能解析的链接，本地音视频，`.srt` / `.vtt` / `.json3` 字幕文件。
- 取字顺序：平台字幕 → B 站 AI 字幕（需 Cookie）→ 本地语音转写（faster-whisper 或 paraformer）。
- 图文：按画面切换切分视频，模型决定每张画面插在哪段正文之后。
- 输出：Markdown 笔记（纯文字 / 带图两种）、单文件 HTML、带时间轴的转录稿。
- 联网核验：详细风格的点评允许模型联网检索，核验事实并标注来源；检索不到的才标明“模型补充，未联网核验”。
- 复用：同一输入重复运行会复用已缓存的转录；也可以直接从已有转录稿重新生成笔记。
- 无需 Key：`--transcript-only` 只取字幕/转写，全程不调用大模型。

不包含：Web UI、HTTP API、MCP、任务队列、托盘通知、自动更新、HTML/PNG 导出、扫码登录、密钥加密。

## 环境要求

- Python 3.11+（项目固定使用 3.12，见 `.python-version`）
- [ffmpeg](https://ffmpeg.org/)（在 PATH 里）
- [uv](https://github.com/astral-sh/uv)（推荐）

## 安装

```bash
git clone git@github.com:lenitain/video-note-cli
cd video-note-cli
uv sync
```

安装后可以直接跑：

```bash
uv run video-note --help
```

想在任何目录都用 `video-note`，把虚拟环境里的入口软链到 PATH：

```bash
cd <项目目录>              # 例如 ~/video-note-cli
mkdir -p ~/.local/bin
ln -sf "$PWD/.venv/bin/video-note" ~/.local/bin/video-note
```

确认 `~/.local/bin` 在 `PATH` 里后，就可以：

```bash
video-note --version
```

## 构建

```bash
uv build          # 产物在 dist/：sdist + wheel
uv tool install dist/video_note_cli-*.whl   # 装成全局命令
```

`pyproject.toml` 用 hatchling 构建，包目录是 `src/video_note/`，入口点是 `video-note`。

## 快速开始

```bash
# 1. 配置模型（默认 DeepSeek；Key 建议走环境变量）
mkdir -p ~/.config/video-note
cp config.example.toml ~/.config/video-note/config.toml
$EDITOR ~/.config/video-note/config.toml

# 2. 只要转录稿，不需要 API Key
video-note 'https://www.bilibili.com/video/BVxxxx' --transcript-only

# 3. 生成图文笔记
video-note 'https://www.bilibili.com/video/BVxxxx'
```

产物默认写到 `~/notes/video`，每次运行一个目录 `视频_20251030-143012/`：里面是 `视频.html`（图文，单文件）、`视频.md`（纯文字）、`视频.frames/`（截图）、`视频.transcript.*`（转录稿）。

配置里还能固定这些长期偏好：笔记风格与推理强度（`[note]`）、点评联网核验（`[web]`，默认开启，`--no-web` 关）、
复用与清缓存（`[run]`）、出哪几件成品（`[output] formats`）、
B 站 cookies.txt 路径（`[bilibili] cookie_file`）。命令行同名参数临时盖过它们。

完整参数、配置项、产物布局与故障排查见 **[SPEC.md](SPEC.md)**。

## 文档

| 文件                                       | 内容                                                      |
| ------------------------------------------ | --------------------------------------------------------- |
| [SPEC.md](SPEC.md)                         | 命令行用法、配置项、产物与缓存布局、模型接口、故障排查    |
| [config.example.toml](config.example.toml) | 配置文件模板（复制到 `~/.config/video-note/config.toml`） |

## 开发

```bash
uv sync                        # 安装依赖
uv run video-note --help       # 运行
uv run python -m video_note --help
uv build                       # 构建产物
ruff check src && ruff format src   # 静态检查与格式化（ruff 未列入依赖，需自行安装）
```

源码结构：

```text
src/video_note/
  cli.py               命令行入口
  pipeline.py          主流程
  settings.py          TOML/环境变量配置
  bili_cookies.py      cookies.txt 读取
  transcript_files.py  转录稿读写（JSON/Markdown/SRT）
  render.py            笔记头尾、插图占位符解析、文件名、错误文案
  html_export.py       笔记转单文件 HTML（图片内联），也可单独调用
  screenshots.py       PyAV 逐帧扫描、按画面变化切段、JPEG 输出
  video_frames.py      截图模型（含覆盖区间）、按区间选取、抽稀、视觉接口编码
  web_search.py        联网检索后端（baidu/bing/duckduckgo/tavily），供点评核验
  transcript.py        时间轴、切块、质量检查（上游代码）
  video_processor.py   下载、平台字幕、B 站 AI 字幕（上游代码）
  whisper_asr.py       faster-whisper 转写（上游代码）
  paraformer_asr.py    Paraformer 转写（上游代码）
  llm_summarizer.py    长转录分段与笔记生成（上游代码）
  download.py          模型下载断点续传（上游代码）
```

## 许可

MIT。`src/video_note/` 中的 `transcript.py`、`download.py`、`whisper_asr.py`、`paraformer_asr.py`、`video_processor.py`、`llm_summarizer.py` 取自 [VideoToNo](https://github.com/like-attract/video-to-note)，Copyright (c) 2026 like-attract，MIT License。详见 `LICENSE` 与 `NOTICE`。
