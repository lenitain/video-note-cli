# video-note-cli 使用规格（SPEC）

本文件是 `video-note` 命令行工具的完整参考：命令行参数、配置项、产物布局、模型接口与故障排查。
只想快速跑起来的话，看 [README.md](README.md) 就够了。

## 1. 处理流程

一次运行的顺序固定：

1. **解析输入**：视频链接（`yt-dlp` 能解析的站点）、本地音视频文件、或字幕文件（`.srt` / `.vtt` / `.json3`）。
2. **取字**：平台字幕 → B 站 AI 字幕（需 Cookie）→ 本地语音转写（faster-whisper 或 paraformer）。
3. **切画面**：按画面变化把视频切成片段，每段留一张代表帧（URL 输入会额外下载一份低清预览视频）。
4. **写笔记**：把转录稿（按时间切段）与该段的画面一起发给视觉模型，产出带时间轴、图文内嵌的 Markdown。
   终稿装不进一次成稿额度、或视频超过 10 分钟时自动分段直写（按时间顺序写完整，不预先压成摘要，每段都有尾部核对）。
   详细风格末尾追加点评与分析；开了联网核验（`[web]`，默认开）时模型可以联网检索来核验
   点评里的事实断言并标注来源，检索不到的才标明“模型补充，未联网核验”。
5. **出成品**：按开关写 Markdown / HTML / 转录稿到本次运行的目录（`<标题>_<开始时间>/`）。

每一步都可以单独跳过：`--transcript-only` 只要转录稿，`--no-screenshots` 不要画面，
`--from-transcript` 直接从已有转录稿开始。

## 2. 命令行

### 2.1 基本形式

```bash
video-note [输入] [选项]
```

- 输入是位置参数，可省略；省略时必须给 `--from-transcript`。
- `--transcript-only` 与 `--md-noimage` / `--md-image` / `--html` 互斥（同用会报错退出）。

### 2.2 输入类型

| 输入 | 说明 |
|---|---|
| `https://www.bilibili.com/video/BVxxxx` | B 站链接，支持 `b23.tv` 短链、`?p=` 指定分 P |
| `https://www.youtube.com/watch?v=xxxx` | 其他 `yt-dlp` 能解析的站点 |
| `./lecture.mp4` | 本地音视频文件，没有平台字幕时走本地转写 |
| `./lecture.srt` | 字幕文件（`.srt` / `.vtt` / `.json3`），跳过下载与转写 |
| `--from-transcript ./lecture.transcript.json` | 从已有转录稿（`.json` / `.srt` / `.vtt` / `.md` / `.txt`）开始 |

### 2.3 例子

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

# 生成笔记时顺便把转录稿也放到这次的运行目录
video-note 'https://www.bilibili.com/video/BVxxxx' --save-transcript

# 改成每 10 秒抽一帧，画面更密（抽帧开关与间隔各管各的）
video-note ./lecture.mp4 --screenshots --screenshot-interval 10

# 同一份正文出两件 Markdown：一件带图、一件纯文字
video-note 'https://www.bilibili.com/video/BVxxxx' --md-image --md-noimage

# 纯文本模型：关掉抽帧，只写文字笔记
video-note 'https://www.bilibili.com/video/BVxxxx' --no-screenshots

# 完成后删除缓存里的音频和预览视频，只保留转录
video-note 'https://www.bilibili.com/video/BVxxxx' --clean
```

### 2.4 参数参考

| 参数 | 说明 |
|---|---|
| `input` | 视频链接、本地音视频文件，或 `.srt`/`.vtt`/`.json` 字幕文件 |
| `-o, --out DIR` | 输出根目录（默认读取配置，初始为 `~/notes/video`）；每次运行在下面建 `<标题>_<开始时间>/` |
| `--style detailed\|faithful\|concise` | 笔记风格（默认读取配置，初始为 `detailed`）；详细+点评 / 忠实复原 / 精简 |
| `--effort auto\|off\|high\|max` | 模型推理强度（默认读取配置，初始为 `auto`）；`max` 按 `high` 处理 |
| `--web / --no-web` | 点评阶段允许模型联网检索、核验事实并标注来源（默认读取配置，初始为开启） |
| `--web-backend auto\|baidu\|bing\|duckduckgo\|tavily` | 联网检索后端（默认读取配置）；`auto` 有 Key 用 tavily，否则 baidu 优先、bing 兜底 |
| `-t, --transcript-only` | 只生成带时间轴的转录稿，不调用大模型、不需要 API Key |
| `--save-transcript / --no-save-transcript` | 生成笔记时同时把转录稿写到本次运行的目录（默认读取配置，初始为关闭） |
| `--from-transcript FILE` | 跳过下载和转写，直接从转录稿生成笔记 |
| `--subtitles / --no-subtitles` | 是否优先用平台字幕（默认读取配置，初始为开启）；`--no-subtitles` 强制使用本地语音转写 |
| `--pages N[,N-M]` | B 站分 P 选择，例如 `1,3-5`（默认跟随链接 `?p=`，否则全部） |
| `--asr MODEL` | 转写模型，见 §7 |
| `--device cpu\|cuda` | 转写设备（GPU 不可用时自动退回 CPU） |
| `--provider NAME` | 模型供应商标识，见 §8 |
| `--base-url URL` | OpenAI 兼容接口地址（`custom` 必填） |
| `--model NAME` | 模型名（留空使用 provider 默认值） |
| `--api-key KEY` | API Key；建议写在配置的 `[llm] api_key` 里 |
| `--api-key-env VAR` | 从哪个环境变量读 API Key；写 `provider` 则用该 provider 的惯用变量名 |
| `--token-plan-key KEY` | Token Plan 订阅 Key；与按量计费 Key 是同一 provider 的两种计费方式 |
| `--token-plan-key-env VAR` | 从哪个环境变量读 Token Plan 订阅 Key |
| `--cookie-file FILE` | Netscape 格式 cookies.txt，用于 B 站 AI 字幕（默认读取配置 `[bilibili] cookie_file`） |
| `--config FILE` | 指定配置文件路径 |
| `--reuse / --no-reuse` | 复用同一输入的已有转录（默认读取配置，初始为开启） |
| `--clean / --no-clean` | 完成后删除缓存里的音频、预览视频与旧版截图缓存，只保留转录（默认读取配置，初始为关闭） |
| `--screenshots / --no-screenshots` | 是否抽帧并把画面插进笔记正文（默认读取配置，初始为开启） |
| `--screenshot-interval SECONDS` | 画面切分的最小间隔，5-300 秒（默认读取配置）；只设间隔，抽帧开关看 `--screenshots` |
| `-v, --verbose` | 失败时打印完整堆栈 |
| `--version` | 打印版本号 |

### 2.5 成品开关

三件成品各管各的，可以任意组合；**都不指定时用配置 `[output] formats` 里的清单（初始为 `--md-noimage --html`）**。

| 参数 | 产物 |
|---|---|
| `--md-noimage` | 纯文字 Markdown（去掉插图行）：`<标题>.md` |
| `--md-image` | 带插图的 Markdown（相对路径引用 `<标题>.frames/`）：`<标题>.images.md` |
| `--html` | 单文件 HTML（图片内联成 data URI）：`<标题>.html` |

两个 Markdown 开关同时给，就各出一份，名字互不覆盖。

**命令行与配置的关系**：命令行点名了任一成品开关，就**整份替换**配置里的清单（不给的就是不要），
这样才能只写配置里某一件的反面；一个都没点名时才用配置里的清单。

`-t/--transcript-only` 只写转录稿，不写笔记；配置里的 `formats` 在这个模式下不生效。

## 3. 配置

### 3.1 创建配置文件

```bash
mkdir -p ~/.config/video-note
cp config.example.toml ~/.config/video-note/config.toml
$EDITOR ~/.config/video-note/config.toml
```

配置是明文 TOML，自用不需要加密。`config.toml` 已在 `.gitignore` 里，不会被提交。
用 `--config FILE` 可以临时指定别的配置文件。

配置装的是**长期偏好**（写什么风格、思考多少、出哪几件成品、要不要复用与清缓存），命令行管**本次运行的决定**；
同名时命令行盖过配置。哪些参数能进配置见 §3.3。

### 3.2 完整示例

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
cookie_file = ""           # 浏览器导出的 Netscape cookies.txt 路径（留空就不用）

[note]
style = "detailed"         # detailed 详细+点评 / faithful 忠实复原 / concise 精简
effort = "auto"            # auto / off / high / max（max 按 high 处理）

[web]
enabled = true             # 点评阶段联网检索、核验事实并标注来源
backend = "auto"           # auto / baidu / bing / duckduckgo / tavily
api_key = ""               # tavily 后端的 Key（其他后端不需要）
api_key_env = ""           # 从环境变量读 Key（如 TAVILY_API_KEY）
max_searches = 4           # 一次点评最多检索几次
timeout = 15               # 单次检索超时（秒）

[run]
reuse = true               # 复用同一输入的已有转录
save_transcript = false    # 生成笔记时顺带存转录稿
prefer_subtitles = true    # 优先用平台字幕（false = 强制本地转写）
clean = false              # 跑完删掉缓存里的音频、预览视频与旧版截图缓存

[output]
dir = "~/notes/video"        # 产物根目录；每次运行在它下面建 <标题>_<开始时间>/
formats = ["md-noimage", "html"]  # 要写哪几件成品：md-noimage / md-image / html

[screenshots]
enabled = true         # 抽帧并把画面插进正文（需要支持图片输入的模型）
interval = 30          # 画面切分的最小间隔（秒），5-300
```

### 3.3 哪些参数能写进配置

配置装的是**稳定的个人偏好**，命令行管**本次运行的决定**。两边同名时命令行盖过配置：

| 命令行 | 配置项 | 环境变量 |
|---|---|---|
| `-o, --out` | `[output] dir` | `VIDEO_NOTE_OUTPUT_DIR` |
| `--md-noimage` / `--md-image` / `--html` | `[output] formats` | `VIDEO_NOTE_OUTPUT_FORMATS` |
| `--asr` | `[asr] model` | `VIDEO_NOTE_ASR_MODEL` |
| `--device` | `[asr] device` | `VIDEO_NOTE_DEVICE` |
| `--provider` | `[llm] provider` | `VIDEO_NOTE_PROVIDER` |
| `--base-url` | `[llm] base_url` | `VIDEO_NOTE_BASE_URL` |
| `--model` | `[llm] model` | `VIDEO_NOTE_MODEL` |
| `--api-key` | `[llm] api_key` | —（靠 `api_key_env` 指名） |
| `--api-key-env` | `[llm] api_key_env` | — |
| `--token-plan-key` | `[llm] token_plan_key` | — |
| `--token-plan-key-env` | `[llm] token_plan_key_env` | — |
| `--style` | `[note] style` | `VIDEO_NOTE_STYLE` |
| `--effort` | `[note] effort` | `VIDEO_NOTE_EFFORT` |
| `--web / --no-web` | `[web] enabled` | `VIDEO_NOTE_WEB` |
| `--web-backend` | `[web] backend` | `VIDEO_NOTE_WEB_BACKEND` |
| — | `[web] api_key` / `api_key_env` | `VIDEO_NOTE_WEB_API_KEY` |
| — | `[web] max_searches` | `VIDEO_NOTE_WEB_MAX_SEARCHES` |
| `--reuse / --no-reuse` | `[run] reuse` | `VIDEO_NOTE_REUSE` |
| `--save-transcript / --no-save-transcript` | `[run] save_transcript` | `VIDEO_NOTE_SAVE_TRANSCRIPT` |
| `--subtitles / --no-subtitles` | `[run] prefer_subtitles` | `VIDEO_NOTE_PREFER_SUBTITLES` |
| `--clean / --no-clean` | `[run] clean` | `VIDEO_NOTE_CLEAN` |
| `--cookie-file` | `[bilibili] cookie_file` | `VIDEO_NOTE_COOKIE_FILE` |
| `--screenshots / --no-screenshots` | `[screenshots] enabled` | `VIDEO_NOTE_SCREENSHOTS` |
| `--screenshot-interval` | `[screenshots] interval` | `VIDEO_NOTE_SCREENSHOT_INTERVAL` |

留在命令行、**不进配置**的：

| 参数 | 为什么 |
|---|---|
| `input` / `--from-transcript` / `--pages` | 本次处理哪个对象是这次任务的一部分，不是长期偏好 |
| `-t, --transcript-only` | 另一种运行模式（只出转录稿），且与成品开关互斥，放进配置会让两者的冲突变含糊 |
| `-v, --verbose` | 诊断开关，看堆栈是临时的需求 |
| `--config` | 元选项：用哪个配置文件 |

### 3.4 API Key 从哪来

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

### 3.5 环境变量

除 Key 之外，其余设置都能用环境变量覆盖配置文件：

| 变量 | 含义 |
|---|---|
| `VIDEO_NOTE_PROVIDER` | provider |
| `VIDEO_NOTE_BASE_URL` | base_url |
| `VIDEO_NOTE_MODEL` | model |
| `VIDEO_NOTE_ASR_MODEL` | 转写模型 |
| `VIDEO_NOTE_DEVICE` | `cpu` / `cuda` |
| `VIDEO_NOTE_OUTPUT_DIR` | 产物根目录 |
| `VIDEO_NOTE_OUTPUT_FORMATS` | 成品清单，逗号分隔：`md-noimage,md-image,html` |
| `VIDEO_NOTE_STYLE` | 笔记风格：`detailed` / `faithful` / `concise` |
| `VIDEO_NOTE_EFFORT` | 推理强度：`auto` / `off` / `high` / `max` |
| `VIDEO_NOTE_REUSE` | 复用已有转录：`1` / `0` |
| `VIDEO_NOTE_SAVE_TRANSCRIPT` | 顺带存转录稿：`1` / `0` |
| `VIDEO_NOTE_PREFER_SUBTITLES` | 优先用平台字幕：`1` / `0` |
| `VIDEO_NOTE_CLEAN` | 跑完清缓存：`1` / `0` |
| `VIDEO_NOTE_COOKIE_FILE` | Netscape cookies.txt 路径 |
| `VIDEO_NOTE_SCREENSHOTS` | `1` / `0`，是否抽帧 |
| `VIDEO_NOTE_SCREENSHOT_INTERVAL` | 画面切分的最小间隔（秒） |
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

# 产物根目录也可以在配置文件里改，或用环境变量/命令行临时覆盖
VIDEO_NOTE_OUTPUT_DIR=/data/notes video-note 'https://www.bilibili.com/video/BVxxxx'
video-note 'https://www.bilibili.com/video/BVxxxx' --out /data/notes
```

### 3.6 优先级

从低到高：内置默认值 → `~/.config/video-note/config.toml` → 环境变量 → 命令行参数。

Key 例外：它的来源由 `api_key` / `api_key_env` 显式声明，不靠“配置里是空的”去猜。

## 4. 输出

输出根目录默认是 `~/notes/video`，可在配置文件的 `[output] dir` 修改，也可以用 `VIDEO_NOTE_OUTPUT_DIR` 或 `--out` 临时覆盖。

**一次运行一个目录**：根目录下面每次运行建一个子目录 `<标题>_<开始提取时间>/`，例如
`深入理解索引_20251030-143012/`。时间戳是这次**开始提取**的时刻（不是落盘时刻——下载、转写、写笔记都要花时间），
同一秒里重跑同一支视频时目录名后面加 `-2`、`-3`。

### 4.1 产物

一次运行的全部产物都在那个目录里：

- 网页版（看图的那份）：`<标题>.html`，图片内联成 data URI，单文件、浏览器双击就能看
- 笔记（文字的那份）：`<标题>.md`，不含插图
- 笔记（带图的那份）：`<标题>.images.md`，用相对路径引用 `<标题>.frames/` 里的图片
- 原始截图：`<标题>.frames/`，抽出的每一段画面都在这里，含区间清单 `frames.json`；缓存里不再放图片
- 转录：`<标题>.transcript.md`、`<标题>.transcript.json`、`<标题>.transcript.srt`
- B 站分 P：`<标题>-P1,3.md`（转录稿同理）

文件名只由标题和固定后缀决定；每次运行落在新的时间戳目录里，同一个目录里不会撞名。

### 4.2 缓存

缓存在 `~/.cache/video-note/`：

- `runs/<hash>/audio.*`：下载的音频
- `runs/<hash>/preview.*`：抽帧用的低清预览视频（分 P 视频是 `preview_p<n>.*`，每 P 一份）
- `runs/<hash>/transcript.json`：可复用的转录
- `models/`：faster-whisper / paraformer 模型

同一链接（且分 P、转写模型一致）再次运行会直接复用 `transcript.json`。换模型或想强制重跑时加 `--no-reuse`。
缓存可以随时手动删：

```bash
rm -rf ~/.cache/video-note/runs
```

## 5. 图文笔记

开启后（默认 `[screenshots] enabled = true`，命令行 `--screenshots` / `--no-screenshots` 可覆盖）：

- URL：额外下载一份低清预览视频，不下载完整高清视频；B 站分 P 逐 P 下载，帧时间戳按各 P 时长换算到合并时间轴
- 本地视频：直接从本地文件抽帧
- 按画面变化切分视频，一段画面留一张代表帧（最晚的一帧，动画/逐行代码收尾后内容最完整），并记下它持续多长时间
- 切换要连续两次采样都确认，字幕换行、光标移动、一次按键高亮不会把一张幻灯片拆成几张；两段画面间隔小于 `--screenshot-interval` 时并进下一段
- 画面被切成时间段，不再是一个孤立时间点：幻灯片在小节开始前就出现、一直讲到段中，也会配给这一节
- 按段直写时每段只带该时间段的画面，候选最多 16 张；提示词允许的插图数按这段时长给（约 4 分钟一张，单段最多 8 张），避免长段被压成零星两三张
- 截图直接抽到本次运行目录的 `<标题>.frames/`（不再进缓存）：HTML 里内联它们，Markdown 默认不带图
- 模型在正文里写 `[[图片 0003：说明文字]]`，程序换成 `![说明文字](./标题-frames/frame_0002_60s.jpg)`；两件 Markdown 成品里，`<标题>.md` 去掉插图行，`<标题>.images.md` 保留
- 插图目录名不含空格与括号，图片链接因此不需要 `<>` 包裹——终端预览、编辑器、浏览器都能直接解析
- 插图会按所在小节的时间区间核对：引用了不存在的编号、同一张图重复引用、图不在本节区间内，都会在日志里明确报出来；越界的图先换成本节里第一张没用过的画面（原说明一并弃用，改用这张图自己的时间段），换不到才删除
- 有带时间区间的小节模型没写插图时，程序会自动补上该段第一张画面（日志里会说明补了哪几节），不靠模型自觉
- 模型一张都没引用时，退化成笔记末尾的 `## 视频截图` 图库（最多 12 张），保证画面不丢
- 字幕文件 / `--from-transcript` 输入没有视频画面，会跳过抽帧
- `--transcript-only` 只输出转录，也会跳过抽帧
- `--clean`（或配置 `[run] clean = true`）会清理缓存里的音频与预览视频（顺带清掉旧版本留下的 `frames_*` 目录），运行目录里的 `.html` 与 `.frames/` 一律保留

截图缓存也支持复用：同一输入、同一间隔再次运行，不会重新下载预览或重新切帧；换间隔会按新间隔重新切。
没有区间清单的旧缓存会重新切一次（否则配图只能按时间点猜）。`--no-reuse` 可强制重跑。
截图目录和笔记在同一个运行目录里：移动时把整个 `<标题>_<开始时间>/` 目录一起搬走，图片链接就不会失效。

**在浏览器里看**：浏览器不渲染 Markdown，双击 `.md` 只会显示纯文本；Markdown 预览扩展默认也不允许读 `file://` 下的相对图片。
所以默认同时写一份同名 `.html`（图片内联成 data URI，单文件可随意复制），直接 `xdg-open <标题>.html` 就能看到图文
（只要 HTML：`--html`；只要文字：`--md-noimage`）；已有笔记可以单独转换：

```bash
uv run python -m video_note.html_export ~/notes/video/某个笔记_20251030-143012/某个笔记.md
```

**需要视觉模型。** 画面以 base64 图片随请求发送，模型必须支持图片输入（默认的 `deepseek-flash`、`gpt-4o`、`qwen-vl`、`glm-4v`、Kimi 视觉版等）。
模型拒绝图片时程序会在日志里明确报错并提示换模型；只想要文字笔记就加 `--no-screenshots`，纯文本模型照样可用。

## 6. B 站 AI 字幕

公共字幕不需要登录。B 站 AI 字幕需要 `SESSDATA`：

1. 在浏览器登录 B 站；
2. 用浏览器扩展导出 `bilibili.com` 的 Netscape `cookies.txt`；
3. `video-note ... --cookie-file ~/cookies.txt`。

或者把 `SESSDATA`、`bili_jct`、`buvid3` 填到配置的 `[bilibili]` 段；cookies.txt 的路径也能写进配置
（`[bilibili] cookie_file = "~/cookies.txt"`），之后直接 `video-note ...` 即可。没有有效凭据时会自动改用本地语音转写，不会卡住。

多分 P 视频默认跟随链接里的 `?p=`；没有 `?p=` 时处理全部分 P。分 P 很多时建议显式 `--pages`。

## 7. 转写模型

`base` 不是“基础功能”，而是 **faster-whisper 的模型档位名**：它大约 74M 参数、模型文件约 140MB，多语言，速度较快，准确率中等。
默认用它是因为下载小、CPU 能跑；中文识别质量一般，想要更准可以换更大的档位。

| 模型 | 说明 |
|---|---|
| `tiny` | 最小最快，准确率最低；适合先快速看一遍 |
| `base` | 默认；约 140MB，速度和准确率折中 |
| `small` | 约 460MB，中文准确率明显好于 `base`，CPU 上更慢 |
| `medium` / `large-v3` | 更准、更慢、模型更大；CPU 上可能需要较长等待 |
| `turbo` / `belle-turbo-zh` | Whisper 的加速/中文微调变体；`belle-turbo-zh` 偏中文 |
| `paraformer-zh` | sherpa-onnx Paraformer，仅中文，CPU 上通常明显快于 Whisper；中文视频首选 |

模型档位可以在配置文件的 `[asr] model` 固定，也可以每次用 `--asr` 覆盖。首次使用会下载模型到 `~/.cache/video-note/models`。

默认走 `hf-mirror.com`；网络环境不同可以：

```bash
HF_ENDPOINT=https://huggingface.co video-note ...
```

有 CUDA 运行库时可以用 `--device cuda`；GPU 不可用会自动退回 CPU。

## 8. 模型接口

内置 OpenAI 兼容 provider：

| provider | 地址 | 默认模型 |
|---|---|---|
| `deepseek` | `https://api.deepseek.com` | `deepseek-flash`（原生支持图片输入） |
| `openai` | `https://api.openai.com/v1` | `gpt-5.6-terra` |
| `openai_gpt4` | `https://api.openai.com/v1` | `gpt-4o` |
| `openai_gpt35` | `https://api.openai.com/v1` | `gpt-4o-mini` |
| `qwen` | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `qwen3.7-plus` |
| `glm` | `https://open.bigmodel.cn/api/paas/v4` | `glm-4.7-flash` |
| `moonshot` | `https://api.moonshot.cn/v1` | `kimi-k3` |
| `minimax` | `https://api.minimax.io/v1`（境外站） | `MiniMax-M3.1-Flash` |
| `minimax_cn` | `https://api.minimaxi.com/v1`（国内站） | `MiniMax-M3.1-Flash` |

自定义网关或本地模型：

```bash
video-note ... \
  --provider custom \
  --base-url http://127.0.0.1:11434/v1 \
  --model qwen2.5:14b
```

本地端点（`localhost` / `127.0.0.1`）不填 Key 时会自动使用占位 Key `local`。远程接口需要 `api_key`；
只要转录稿时用 `--transcript-only`，完全不需要配置模型。

图文笔记要求模型能读图。默认的 `deepseek-flash`（V4.1 Flash）原生支持图片输入，`deepseek-v4-pro` 只支持文字；
接本地视觉模型（如 vLLM + Qwen2.5-VL）同样走 `custom`。截图以 base64 `image_url` 随请求发送，模型不支持图片时会直接报错。

### 8.1 MiniMax

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
- M3.1 系列强制 adaptive thinking：关思考的 `reasoning_effort=none` 与 `thinking.type=disabled` 都会被接口 400 拒绝，
  所以 `--effort off` 会降成最低思考档 `low` 并给出提示，不会把任务打失败；`MiniMax-M3` 接受 `none`，`--effort off` 能真关掉思考。
- 不指定档位（默认 `auto`）时成稿用 `high`、长视频逐段直写用 `off`，而不是模型默认的 `max`——
  M3.1 省略该参数时默认思考最长的一档，容易把输出额度吃满、正文写不出来。

## 9. 故障排查

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

- 图文笔记需要支持图片输入的模型：默认的 `deepseek-flash` 就支持，也可换 `MiniMax-M3.1-Flash` / `qwen-vl` / `glm-4v` / `gpt-4o`；
  `deepseek-v4-pro` 只支持文字，或加 `--no-screenshots` 只写文字
- `--transcript-only`、字幕文件、`--from-transcript` 没有画面，不会插图
- 模型认为画面没有信息时可以不插；一张都没插时会在笔记末尾附图，越界或引用不存在的编号会在日志里报「提示：…」
- 觉得插图太稀：先看日志里「以下小节有可用截图但正文没用上」——那是模型没插，可以调小 `--screenshot-interval`（如 10 秒）给它更密的候选画面，或改用 `--style faithful`
- 画面切分的最小间隔必须在 5-300 秒之间
- 想看图文：开同名 `.html`（浏览器、qutebrowser 都行）。浏览器不渲染 `.md`，而且默认的 Markdown 已按你的要求去掉了插图
- 想让 Markdown 也带图（给 VS Code / Obsidian 用）：加 `--md-image`，会多出 `<标题>.images.md`，纯文字那份不受影响
- 分 P 视频的日志若提示「分 P 时长未知」，说明缓存里没有各 P 时长，截图只能覆盖第 1 个分 P；加 `--no-reuse` 重跑即可
- 预览视频下载失败时日志会说明，笔记仍会正常生成；可以稍后重试或改用本地视频

**抖音**

只保留 `yt-dlp` / 公开分享页解析。需要浏览器登录验证的抖音链接不支持；需要时请先用其他工具下载成本地文件再跑。

## 10. 不做什么

没有 Web 界面、没有服务端、没有打包分发：不包含 Web UI、HTTP API、MCP、任务队列、托盘通知、自动更新、
HTML/PNG 导出、扫码登录、密钥加密。
