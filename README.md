# video-note-cli

把视频变成**带时间轴、正文内嵌画面**的 Markdown 笔记的 Linux 命令行工具。自用取向：没有 Web 界面、没有服务端、没有打包，只保留「取字幕 / 本地转写 → 视觉大模型看图写笔记」这条主链路。

核心代码取自 [VideoToNo](https://github.com/like-attract/video-to-note)（MIT），去掉了 FastAPI、MCP、托盘、扫码登录、HTML/PNG 导出和密钥加密，改成适合终端与脚本调用的形式。

## 能做什么

- 输入：B 站、YouTube、其他 `yt-dlp` 能解析的链接，本地音视频，`.srt` / `.vtt` / `.json3` 字幕文件。
- 取字顺序：平台字幕 → B 站 AI 字幕（需 Cookie）→ 本地语音转写。
- 本地转写：faster-whisper（默认 `base`），也支持中文 CPU 更快的 `paraformer-zh`。
- B 站：AI 字幕、多分 P 合并、`b23.tv` 短链、视频页 412 时回退开放接口。
- 笔记：详细 / 忠实复原 / 精简三种风格；长转录按时间顺序分段写完整，不预先压成摘要。
- 图文：默认按间隔抽帧，把画面和时间点一起发给视觉模型，由模型决定插在哪段正文之后；幻灯片、图表、代码、演示操作会成为正文里的图片，而不是全部堆在末尾。
- 输出：Markdown 笔记，以及带时间轴的 Markdown / JSON / SRT 转录稿。
- 复用：同一输入重复运行会复用已缓存的转录；也可以直接从已有转录稿重新生成笔记。
- 无需 Key：`--transcript-only` 只取字幕/转写，全程不调用大模型。

不包含：Web UI、HTTP API、MCP、任务队列、托盘通知、自动更新、HTML/PNG 导出、扫码登录、密钥加密、打包分发。

## 安装

需要 Python 3.11+、`ffmpeg`，以及 [uv](https://github.com/astral-sh/uv)（推荐）。项目固定使用 Python 3.12。

```bash
cd ~/.projects/video-note-cli
uv sync
```

安装后可以直接跑：

```bash
uv run video-note --help
```

想在任何目录都用 `video-note`，把虚拟环境里的入口软链到 PATH：

```bash
mkdir -p ~/.local/bin
ln -sf "$PWD/.venv/bin/video-note" ~/.local/bin/video-note
```

确认 `~/.local/bin` 在 `PATH` 里后，就可以：

```bash
video-note --version
```

## 配置

```bash
mkdir -p ~/.config/video-note
cp config.example.toml ~/.config/video-note/config.toml
$EDITOR ~/.config/video-note/config.toml
```

配置是明文 TOML，自用不需要加密。`config.toml` 已在 `.gitignore` 里，不会被提交。

```toml
[llm]
provider = "deepseek"      # deepseek/openai/openai_gpt4/openai_gpt35/qwen/glm/moonshot/minimax/minimax_cn/custom
base_url = ""              # custom 必填；内置 provider 留空用默认地址
model = ""                 # 留空用 provider 默认模型
api_key = "sk-..."         # 按量计费 API Key（置空就是置空）
api_key_env = "provider"   # 从环境变量读 Key；写 "provider" 用该 provider 的惯用变量名
token_plan_key = ""        # Token Plan 订阅 Key（同一 provider 的另一种计费方式）
token_plan_key_env = ""    # 从环境变量读订阅 Key

[asr]
model = "base"             # tiny/base/small/medium/large-v3/turbo/belle-turbo-zh/paraformer-zh
device = "cpu"             # cpu / cuda

[bilibili]
sessdata = ""              # 读取 B 站 AI 字幕时需要
bili_jct = ""
buvid3 = ""

[output]
dir = "~/notes/video"

[screenshots]
enabled = true         # 抽帧并把画面插进正文（需要支持图片输入的模型）
interval = 30          # 抽帧间隔（秒），5-300
```

### API Key 从哪来

**置空就是置空**：`api_key = ""` 就是没有 Key，不会再去别处找。要用环境变量里的 Key，
得显式写出来：

```toml
[llm]
api_key = ""              # 直接写值，或留空
api_key_env = "provider"  # 环境变量名；"provider" = 该 provider 的惯用变量名
```

| 写法 | 含义 |
|---|---|
| `api_key = "sk-..."` | 直接用这个 Key |
| `api_key_env = "MY_KEY"` | 读环境变量 `MY_KEY`（没设就是没有 Key） |
| `api_key_env = "provider"` | 读该 provider 的惯用变量名（`deepseek` → `DEEPSEEK_API_KEY`，`minimax_cn` → `MINIMAX_CN_API_KEY`…） |
| `token_plan_key` / `token_plan_key_env` | 同上，Token Plan 订阅 Key 那把 |

两个都给时直接写的值优先。真正生效的是哪一把，每次跑都会打出来：

```
[video-note] 调用模型：provider=minimax_cn，model=MiniMax-M3.1-Flash，base_url=https://api.minimaxi.com/v1，Key 来自环境变量 MINIMAX_CN_API_KEY
```

命令行同义：`--api-key`、`--api-key-env`、`--token-plan-key`、`--token-plan-key-env`。

其他设置仍然是环境变量覆盖配置文件，命令行参数再覆盖环境变量：

| 变量 | 含义 |
|---|---|
| `VIDEO_NOTE_PROVIDER` | `provider` |
| `VIDEO_NOTE_BASE_URL` | `base_url` |
| `VIDEO_NOTE_MODEL` | `model` |
| `VIDEO_NOTE_ASR_MODEL` | 转写模型 |
| `VIDEO_NOTE_DEVICE` | `cpu` / `cuda` |
| `VIDEO_NOTE_OUTPUT_DIR` | 输出目录 |
| `VIDEO_NOTE_SCREENSHOTS` | `1` / `0`，是否抽帧 |
| `VIDEO_NOTE_SCREENSHOT_INTERVAL` | 抽帧间隔（秒） |
| `BILI_SESSDATA` / `BILI_JCT` / `BILI_BUVID3` | B 站 Cookie |
| `WHISPER_CACHE_DIR` | 模型缓存目录 |
| `HF_ENDPOINT` | 模型下载源，默认 `https://hf-mirror.com` |

Key 类变量的常用名字（要显式指名的那些）：`DEEPSEEK_API_KEY`、`OPENAI_API_KEY`、`DASHSCOPE_API_KEY`、`ZHIPUAI_API_KEY`、`MOONSHOT_API_KEY`、`MINIMAX_API_KEY`、`MINIMAX_CN_API_KEY`（按量计费），`MINIMAX_TOKEN_PLAN_API_KEY`、`MINIMAX_CN_TOKEN_PLAN_API_KEY`（Token Plan 订阅）。

不写配置文件也能用环境变量临时跑（Key 类变量要指明名字，其余设置直接生效）：

```bash
# 通用变量：配置文件里写 api_key_env = "VIDEO_NOTE_API_KEY" 后可用
VIDEO_NOTE_API_KEY=sk-xxx video-note 'https://www.bilibili.com/video/BVxxxx'

# DeepSeek 的惯用变量名：配置文件里写 api_key_env = "provider" 后可用
DEEPSEEK_API_KEY=sk-xxx video-note 'https://www.bilibili.com/video/BVxxxx'

# MiniMax 国内站：按量计费 Key
MINIMAX_CN_API_KEY=sk-xxx video-note 'https://www.bilibili.com/video/BVxxxx'

# MiniMax 国内站：Token Plan 订阅 Key（同一 provider，换把 Key 而已）
MINIMAX_CN_TOKEN_PLAN_API_KEY=sk-cp-xxx video-note 'https://www.bilibili.com/video/BVxxxx'

# 临时换一把：命令行直接指名环境变量
VIDEO_NOTE_API_KEY=sk-xxx video-note --api-key-env VIDEO_NOTE_API_KEY 'https://www.bilibili.com/video/BVxxxx'

# 输出目录也可以在配置文件里改，或用环境变量/命令行临时覆盖
VIDEO_NOTE_OUTPUT_DIR=/data/notes video-note 'https://www.bilibili.com/video/BVxxxx'
video-note 'https://www.bilibili.com/video/BVxxxx' --out /data/notes
```

优先级从低到高：内置默认值 → `~/.config/video-note/config.toml` → 环境变量 → 命令行参数。
Key 例外：它的来源由 `api_key` / `api_key_env` 显式声明，不靠“配置里是空的”去猜。

## 用法

```bash
# 生成图文笔记（默认抽帧，画面按内容插进正文）
video-note 'https://www.bilibili.com/video/BVxxxx'

# 只要带时间轴的转录稿，不需要 API Key
video-note 'https://www.youtube.com/watch?v=xxxx' --transcript-only

# 本地视频；没有平台字幕，走本地转写
video-note ./lecture.mp4 --asr small

# 已经有字幕，直接整理成笔记
video-note ./lecture.srt --style concise

# 从已有转录稿重新生成笔记，不重新下载/转写
video-note --from-transcript ./lecture.transcript.json --style detailed

# B 站多分 P 只处理 1、3、4、5
video-note 'https://www.bilibili.com/video/BVxxxx' --pages 1,3-5

# 用浏览器导出的 cookies.txt 读取 B 站 AI 字幕
video-note 'https://www.bilibili.com/video/BVxxxx' --cookie-file ~/cookies.txt

# 生成笔记时顺便把转录稿也放到输出目录
video-note 'https://www.bilibili.com/video/BVxxxx' --save-transcript

# 纯文本模型：关掉抽帧，只写文字笔记
video-note 'https://www.bilibili.com/video/BVxxxx' --no-screenshots

# 改成每 10 秒抽一帧，画面更密
video-note ./lecture.mp4 --screenshot-interval 10

# 完成后删除缓存里的音频和截图缓存，只保留转录
video-note 'https://www.bilibili.com/video/BVxxxx' --clean
```

### 常用参数

| 参数 | 说明 |
|---|---|
| `-o, --out DIR` | 输出目录 |
| `--style detailed\|faithful\|concise` | 笔记风格，默认 `detailed` |
| `--effort auto\|off\|high\|max` | 推理强度，默认 `auto`；`max` 按 `high` 处理 |
| `-t, --transcript-only` | 只做转录，不调用大模型 |
| `--save-transcript` | 生成笔记时同时保存转录稿 |
| `--from-transcript FILE` | 从 `.json/.srt/.vtt/.md/.txt` 转录稿开始 |
| `--no-subtitles` | 不读平台字幕，强制语音转写 |
| `--pages N[,N-M]` | B 站分 P 选择 |
| `--asr MODEL` | 转写模型 |
| `--device cpu\|cuda` | 转写设备 |
| `--provider/--base-url/--model/--api-key/--token-plan-key` | 临时覆盖模型配置 |
| `--cookie-file FILE` | Netscape 格式 cookies.txt |
| `--screenshots / --no-screenshots` | 是否抽帧并把画面插进正文（默认开启） |
| `--screenshot-interval SECONDS` | 抽帧间隔，5-300 秒，默认 30；指定即启用抽帧 |
| `--reuse / --no-reuse` | 复用已有转录，默认开启 |
| `--clean` | 完成后清理缓存音频、预览视频和截图缓存 |
| `--config FILE` | 指定配置文件 |
| `-v, --verbose` | 失败时打印完整堆栈 |

## 输出

默认写到 `~/notes/video`，可在配置文件的 `[output] dir` 修改，也可以用 `VIDEO_NOTE_OUTPUT_DIR` 或 `--out` 临时覆盖：

- 笔记：`<标题>.md`
- 转录：`<标题>.transcript.md`、`<标题>.transcript.json`、`<标题>.transcript.srt`
- 截图：`<标题>.frames/frame_0001_30s.jpg`，正文内嵌引用；模型一张都没引用时追加到末尾的 `## 视频截图`
- B 站分 P：`<标题>-P1,3.md`（转录稿同理）
- 重名时自动加 `-2`、`-3`

缓存在 `~/.cache/video-note/`：

- `runs/<hash>/audio.*`：下载的音频
- `runs/<hash>/preview.*`：抽帧用的低清预览视频
- `runs/<hash>/frames_<间隔>/`：可复用的截图缓存
- `runs/<hash>/transcript.json`：可复用的转录
- `models/`：faster-whisper / paraformer 模型

同一链接（且分 P、转写模型一致）再次运行会直接复用 `transcript.json`。换模型或想强制重跑时加 `--no-reuse`。缓存可以随时手动删：

```bash
rm -rf ~/.cache/video-note/runs
```

## 图文笔记

开启后（默认 `[screenshots] enabled = true`，命令行 `--screenshots` / `--no-screenshots` 可覆盖）：

- URL：额外下载一份低清预览视频，不下载完整高清视频
- 本地视频：直接从本地文件抽帧
- 按 `interval` 秒抽一帧，存到输出目录的 `<标题>.frames/`；本地大分辨率视频会缩到最大宽度 1280
- 相邻或近期几乎相同的画面只留一张：静态幻灯片不会在正文里重复出现
- 长视频按段直写，每段只带该时间段的画面，单次请求最多 16 张
- 模型在正文里写 `[[图片 0003：说明文字]]`，程序换成 `![说明文字](<./标题.frames/frame_0002_60s.jpg>)`，并且只把正文真正引用的帧复制到 `.frames/`
- 模型一张都没引用时，退化成笔记末尾的 `## 视频截图` 图库（最多 12 张），保证画面不丢
- 字幕文件 / `--from-transcript` 输入没有视频画面，会跳过抽帧
- `--transcript-only` 只输出转录，也会跳过抽帧
- `--clean` 会清理缓存里的预览视频和截图缓存，但输出目录里已经发布的 `.frames/` 会保留

截图缓存也支持复用：同一输入、同一间隔再次运行，不会重新下载预览或重新抽帧；换间隔会按新间隔重新抽。`--no-reuse` 可强制重跑。截图目录必须和笔记放在一起，移动笔记时请连同 `.frames/` 一起移动，否则图片链接会失效。

**需要视觉模型。** 画面以 base64 图片随请求发送，模型必须支持图片输入（默认的 `deepseek-flash`、`gpt-4o`、`qwen-vl`、`glm-4v`、Kimi 视觉版等）。模型拒绝图片时程序会在日志里明确报错并提示换模型；只想要文字笔记就加 `--no-screenshots`，纯文本模型照样可用。

## B 站 AI 字幕

公共字幕不需要登录。B 站 AI 字幕需要 `SESSDATA`：

1. 在浏览器登录 B 站；
2. 用浏览器扩展导出 `bilibili.com` 的 Netscape `cookies.txt`；
3. `video-note ... --cookie-file ~/cookies.txt`。

或者把 `SESSDATA`、`bili_jct`、`buvid3` 填到配置的 `[bilibili]` 段。没有有效凭据时会自动改用本地语音转写，不会卡住。

多分 P 视频默认跟随链接里的 `?p=`；没有 `?p=` 时处理全部分 P。分 P 很多时建议显式 `--pages`。

## 转写模型

`base` 不是“基础功能”，而是 **faster-whisper 的模型档位名**：它大约 74M 参数、模型文件约 140MB，多语言，速度较快，准确率中等。默认用它是因为下载小、CPU 能跑；中文识别质量一般，想要更准可以换更大的档位。

| 模型 | 说明 |
|---|---|
| `tiny` | 最小最快，准确率最低；适合先快速看一遍 |
| `base` | 默认；约 140MB，速度和准确率折中 |
| `small` | 约 460MB，中文准确率明显好于 `base`，CPU 上更慢 |
| `medium` / `large-v3` | 更准、更慢、模型更大；CPU 上可能需要较长等待 |
| `turbo` / `belle-turbo-zh` | Whisper 的加速/中文微调变体；`belle-turbo-zh` 偏中文 |
| `paraformer-zh` | sherpa-onnx Paraformer，仅中文，CPU 上通常明显快于 Whisper；中文视频首选 |

模型档位可以在配置文件的 `[asr] model` 固定，也可以每次用 `--asr` 覆盖。首次使用会下载到 `~/.cache/video-note/models`。

首次使用会下载模型到 `~/.cache/video-note/models`。默认走 `hf-mirror.com`；网络环境不同可以：

```bash
HF_ENDPOINT=https://huggingface.co video-note ...
```

有 CUDA 运行库时可以用 `--device cuda`；GPU 不可用会自动退回 CPU。

## 模型接口

内置 OpenAI 兼容 provider：DeepSeek、OpenAI、通义、智谱、Kimi、MiniMax。自定义网关或本地模型：

```bash
video-note ... \
  --provider custom \
  --base-url http://127.0.0.1:11434/v1 \
  --model qwen2.5:14b
```

本地端点（`localhost` / `127.0.0.1`）不填 Key 时会自动使用占位 Key `local`。远程接口需要 `api_key`；只要转录稿时用 `--transcript-only`，完全不需要配置模型。

图文笔记要求模型能读图。默认的 `deepseek-flash`（V4.1 Flash）原生支持图片输入，`deepseek-v4-pro` 只支持文字；接本地视觉模型（如 vLLM + Qwen2.5-VL）同样走 `custom`。截图以 base64 `image_url` 随请求发送，模型不支持图片时会直接报错。

### MiniMax

| provider | 地址 | 默认模型 |
|---|---|---|
| `minimax` | `https://api.minimax.io/v1`（境外站） | `MiniMax-M3.1-Flash` |
| `minimax_cn` | `https://api.minimaxi.com/v1`（国内站） | `MiniMax-M3.1-Flash` |

按量计费和订阅套餐是同一 provider 的两种计费方式，端点与模型一样，Key 不互通：

```toml
# 配置文件的 [llm] 段
provider = "minimax_cn"
api_key_env = "provider"           # → 读 MINIMAX_CN_API_KEY（按量计费）
token_plan_key_env = "provider"    # → 读 MINIMAX_CN_TOKEN_PLAN_API_KEY（订阅）
```

```bash
# 临时换一把：命令行指名环境变量
MINIMAX_CN_API_KEY=sk-xxx video-note --api-key-env MINIMAX_CN_API_KEY 'URL'
MINIMAX_CN_TOKEN_PLAN_API_KEY=sk-cp-xxx video-note --token-plan-key-env MINIMAX_CN_TOKEN_PLAN_API_KEY 'URL'
```

两把 Key 各占一个槽位：按量计费槽与订阅槽，谁填了用谁；两个都填时按量计费优先。真正生效的是哪一把，每次跑都会打出来：

```
[video-note] 调用模型：provider=minimax_cn，model=MiniMax-M3.1-Flash，base_url=https://api.minimaxi.com/v1，Key 来自环境变量 MINIMAX_CN_API_KEY
```

其他订阅制网关（如 TokenPlan 聚合网关）不是 provider，用 `custom` 指定端点即可：

```bash
video-note 'https://www.bilibili.com/video/BVxxxx' \
  --provider custom \
  --base-url https://discovery-api.intern-ai.org.cn/v1 \
  --model minimax-m3 \
  --api-key "$TOKENPLAN_API_KEY"
```

行为要点（均为实测）：

- `MiniMax-M3.1-Flash` 原生多模态，能读 base64 `image_url`，图文笔记可以直接用；`MiniMax-M3` 及 M2.x 只支持文字。
- M3.1 系列强制 adaptive thinking：关思考的 `reasoning_effort=none` 与 `thinking.type=disabled` 都会被接口 400 拒绝，所以 `--effort off` 会降成最低思考档 `low` 并给出提示，不会把任务打失败；`MiniMax-M3` 接受 `none`，`--effort off` 能真关掉思考。
- 不指定档位（默认 `auto`）时成稿用 `high`、长视频逐段直写用 `off`，而不是模型默认的 `max`——M3.1 省略该参数时默认思考最长的一档，容易把输出额度吃满、正文写不出来。

## 常见问题

**模型下载失败 / 网络不可达**

检查 `HF_ENDPOINT`、代理和磁盘空间。也可以手动把模型文件放进 `~/.cache/video-note/models/manual/<模型名>/`。

**B 站 412 / 403**

配置 `[bilibili] sessdata` 或 `--cookie-file`。程序在视频页被风控时会尝试回退到 `api.bilibili.com` 开放接口。

**笔记生成 401 / 404 / 429 / 上下文不够**

错误信息会给出对应提示：检查 Key、`base_url`、模型名；限流时稍后重试；上下文不够时换更大上下文的模型或用 `--style concise`。

**想先看转录，再决定怎么整理**

```bash
video-note 'URL' --transcript-only
# 然后让 agent/自己读 transcript.md（或 json）整理
```

**笔记里没有插图 / 报错说模型不接受图片**

- 图文笔记需要支持图片输入的模型：默认的 `deepseek-flash` 就支持，也可换 `MiniMax-M3.1-Flash` / `qwen-vl` / `glm-4v` / `gpt-4o`；`deepseek-v4-pro` 只支持文字，或加 `--no-screenshots` 只写文字
- `--transcript-only`、字幕文件、`--from-transcript` 没有画面，不会插图
- 模型认为画面没有信息时可以不插；一张都没插时会在笔记末尾附图
- 觉得插图太稀：把 `--screenshot-interval` 调小（如 10 秒），让画面更密
- 抽帧间隔必须在 5-300 秒之间
- 预览视频下载失败时日志会说明，笔记仍会正常生成；可以稍后重试或改用本地视频

**抖音**

只保留 `yt-dlp` / 公开分享页解析。需要浏览器登录验证的抖音链接不支持；需要时请先用其他工具下载成本地文件再跑。

## 开发

```bash
uv sync
uv run video-note --help
uv run python -m video_note --help
```

核心目录：

```text
src/video_note/
  cli.py               命令行入口
  pipeline.py          主流程
  settings.py          TOML/环境变量配置
  bili_cookies.py      cookies.txt 读取
  transcript_files.py  转录稿读写（JSON/Markdown/SRT）
  render.py            笔记头尾、插图占位符解析、文件名、错误文案
  screenshots.py       PyAV 抽帧与 JPEG 输出
  video_frames.py      截图模型、按时间段选取、抽稀、去重、视觉接口编码
  transcript.py        时间轴、切块、质量检查（上游代码）
  video_processor.py   下载、平台字幕、B 站 AI 字幕（上游代码）
  whisper_asr.py       faster-whisper 转写（上游代码）
  paraformer_asr.py    Paraformer 转写（上游代码）
  llm_summarizer.py    长转录分段与笔记生成（上游代码）
  download.py          模型下载断点续传（上游代码）
```

## 许可

MIT。`src/video_note/` 中的 `transcript.py`、`download.py`、`whisper_asr.py`、`paraformer_asr.py`、`video_processor.py`、`llm_summarizer.py` 取自 [VideoToNo](https://github.com/like-attract/video-to-note)，Copyright (c) 2026 like-attract，MIT License。详见 `LICENSE` 与 `NOTICE`。
