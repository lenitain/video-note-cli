# 上游基线与分歧记录

本仓的核心代码派生自 [VideoToNo](https://github.com/like-attract/video-to-note)（MIT，见 `NOTICE` 与 `LICENSE`）。`NOTICE` 只承担署名，不回答同步问题；本文件是同步契约：**上游停在哪个提交、哪几个文件是派生的、每处改写为什么存在、下次同步怎么合**。

## 上游基线

| 项 | 值 |
| --- | --- |
| 上游仓库 | https://github.com/like-attract/video-to-note |
| 固定版本 | `v1.4.8` |
| 提交 | `4fa35b87fc003b3a6f55a12976b24fc1cfdb2de6` |
| 快照目录 | `upstream/backend/` |

`upstream/` 保存该提交下派生模块的原样副本。它只读、不参与打包、不参与运行（`uv_build` 只收 `src/video_note`），存在的唯一理由是让下一次同步能跑三方合并：**本仓改动（local）、旧快照（base）、上游新版本（theirs）**。没有 base 的同步只能靠重读几百行 diff 手工判断，`cbb9f21` 那次 413 行的搬运就是这么来的。

快照被改动等于改写了历史，因此每条基线的 sha256 都记在下面的表里，由守门脚本核对。

## 模块映射

状态取值：

- **verbatim** — 与基线逐字节相同，同步 = 直接覆盖。守门脚本断言这条性质。
- **adapted** — 在基线之上就地改写，同步 = 三方合并 + 逐条复核分歧日志。
- **local-only** — 上游没有对应物（本仓新写的 CLI、渲染、流水线），不受同步影响。

| 本仓文件 | 上游文件 | 状态 | 分歧 | 上游基线 sha256 |
| --- | --- | --- | --- | --- |
| `src/video_note/transcript.py` | `backend/transcript.py` | verbatim | — | `6e0292cc2edd6a2373b1882a9fb730679f104c44b061011701937d4617ec348d` |
| `src/video_note/download.py` | `backend/download.py` | adapted | D1 | `e09ffd57bd64a9768c736fb17a392f80727c31fbf6641708a38e4d05b201c345` |
| `src/video_note/whisper_asr.py` | `backend/whisper_asr.py` | adapted | D2 | `357661bc154210434bc3480dd8ebceba3f2ad3778985b4e270eda081e6a0c6fb` |
| `src/video_note/paraformer_asr.py` | `backend/paraformer_asr.py` | adapted | D3 | `71c3feed862658f1bc37277b23123f3e5efbefd3f46393ef1b8e74f25a021683` |
| `src/video_note/video_processor.py` | `backend/video_processor.py` | adapted | D4 | `a2bf2cac520622369ed1bc0c91832e91fc2e475b31c2f79aa8c3f21196d278ce` |
| `src/video_note/llm_summarizer.py` | `backend/llm_summarizer.py` | adapted | D5 | `c9324a6d5924434a82eca6c2920547b37cb09b4943837072ee12fe7d662656f0` |
| `src/video_note/screenshots.py` | `backend/video_processor.py` | adapted | D6 | `a2bf2cac520622369ed1bc0c91832e91fc2e475b31c2f79aa8c3f21196d278ce` |
| `src/video_note/timestamps.py` | — | local-only | — | — |
| `src/video_note/video_frames.py` | — | local-only | — | — |
| `src/video_note/pipeline.py` | — | local-only | — | — |
| `src/video_note/cli.py` | — | local-only | — | — |
| `src/video_note/__main__.py` | — | local-only | — | — |
| `src/video_note/__init__.py` | — | local-only | — | — |
| `src/video_note/settings.py` | — | local-only | — | — |
| `src/video_note/render.py` | — | local-only | — | — |
| `src/video_note/html_export.py` | — | local-only | — | — |
| `src/video_note/transcript_files.py` | — | local-only | — | — |
| `src/video_note/web_search.py` | — | local-only | — | — |
| `src/video_note/bili_cookies.py` | — | local-only | — | — |

`pipeline.py` 的取字幕 → 转写 → 抽帧 → 生成串联与上游 `backend/main.py` 等价，但代码是重写的：上游那份是 FastAPI 路由与应用状态，本仓没有 Web 层，逐行派生没有意义。所以它记 `local-only`。

## 分歧日志

每条是「上游代码在本仓被改成了什么」，不是改动历史。编号稳定：**不复用、不重编号**，分歧消失就删掉条目；下一个新编号是 **D7**。每条都点名覆盖它的测试。

**D1 · `download.py`** — 移除 `progress_callback` 参数与 0.4 秒节流逻辑：回调的唯一消费者是前端任务状态，CLI 没有进度条。断点续传、404 立即返回、416 丢弃 `.part`、`Content-Length` 完整性校验原样保留。覆盖：`tests/test_download.py`。

**D2 · `whisper_asr.py`** — 删除 `download_model()` 公共入口与 `_download_model_files*` 的逐文件进度回调（模型随转写按需下载，没有独立下载任务）；`User-Agent` 改 `video-note-cli/0.1`；面向前端的提示语改成命令行措辞（「重启程序」→「重新运行命令」，「手动导入模型」按钮 → 把文件放进缓存目录）。覆盖：`tests/test_whisper_asr.py`。

**D3 · `paraformer_asr.py`** — 删除 `download_models()` / `core_ready()`，改为按组件懒加载 `_download_component(component, cancel_event)`；默认镜像失败后追加官方源 `https://huggingface.co`，仅当 endpoint 仍是默认镜像时追加（`HF_ENDPOINT` 指定自建端点时不改变用户意图）。覆盖：`tests/test_paraformer_asr.py`。

**D4 · `video_processor.py`** —

- 新增 `canonical_bilibili_url()`，`normalize_video_input()` 用它收敛分享链接：去掉 `spm_id_from` / `vd_source` 等追踪参数，保留 `?p=N`，于是同一视频的不同分享链接落到同一个缓存键；
- `_resolve_bili_short_url()` 改成公开的 `resolve_bilibili_short_url()`：`pipeline.py` 也要展开 `b23.tv` 短链；
- 删除 `FRAMES_DIR_NAME`、`extract_audio_track()`、`copy_local_media()`、`extract_frames()`、`_extract_frames_sync()`、`cleanup()`：抽帧在 `screenshots.py`，工作目录按次运行，「清理任务」没有对应动作；
- 预览流与音频流的目标文件名由固定 `preview` 改为 `media_name` 参数；
- yt-dlp 失败后对 B 站一律回退 playurl 直连，不再只在识别为风控时回退（连接被掐断同样会让 yt-dlp 报 `Downloaded N bytes, expected M bytes`）。

覆盖：`tests/test_video_processor.py`。

**D5 · `llm_summarizer.py`** — 最大的一处，按行为分组：

- **Provider 归一**：`normalize_provider()`（`minimax-cn` 与 `minimax_cn` 等价）、`default_base_url()`、`normalize_endpoint_host()`；已保存的 API Key 以 host+path 为身份，只在同一端点复用；
- **参数兼容**：`_unsupported_param_names()` / `_rejected_tool_params()` 从报错里取出被点名的参数并降级重投，`looks_like_vision_rejection()` 识别「当前模型不支持图片」并改走纯文字；
- **路由**：`_is_long_transcript()` 换成 `_needs_section_pipeline(segments, style)`，按成稿输出额度与时长双判据决定是否逐段直写；
- **插图**：`inline_frame_limit()` 按时长分配张数（固定 4 张对 20 分钟的一段等于没插）、`_frames_prompt_block()` 生成画面清单与插图规则、`_frame_data_url()` 让图片以 data URL 进请求；
- **联网核验**：`_web_search_handler()` 与 `_analysis_prompt(web_enabled=...)` 接入 `web_search`，检索不到的事实明确标注未经核验；
- **思考强度**：`_apply_effort_reasoning()` / `_always_thinks()`，并删除了 `_parse_clock()`；
- **尾部截断修补**：按时间戳落后程度与内容重合度判定真截断，缺口小一次补完、缺口大按段续写。

覆盖：`tests/test_llm_summarizer.py`。

**D6 · `screenshots.py`** — 上游 `VideoProcessor.extract_frames()` 的抽帧逻辑（连续两次采样确认切镜、短于最短间隔的画面并入下一段、取段尾帧、写 `frames.json` 与 `.complete`）在本仓重写成独立模块：输入改成 `FrameSource` 序列以支持 B 站分 P 的合并时间轴偏移，去掉任务目录与清理耦合。基线取 `backend/video_processor.py` 的抽帧部分，因此共享同一条 sha256。覆盖：`tests/test_screenshots.py`。

## 同步流程

1. 在本地上游 clone 里取到目标 tag，确认 `git rev-parse <tag>^{commit}`：

   ```sh
   git -C ~/.cloned/video-to-note fetch --tags
   git -C ~/.cloned/video-to-note rev-parse v1.4.8^{commit}
   ```

2. 生成三方合并结果（不碰工作区）：

   ```sh
   uv run python scripts/sync-upstream.py --ref v1.4.8
   ```

   每个派生模块在 `build/sync/` 下产出一个合并文件，并打印「上游未变 / 自动合并 / 冲突 N 处」。上游没碰过的模块不需要复核。

3. 逐个把合并结果写回 `src/video_note/`，按冲突标记和分歧日志复核：**上游改的行为要吸收，本仓的 D 条目要保留**。合并后 `verbatim` 文件必须仍与快照逐字节相同。

4. 跑测试：

   ```sh
   uv run pytest
   ```

5. 确认新快照（只改基线，不改分歧日志）：

   ```sh
   uv run python scripts/sync-upstream.py --ref v1.4.8 --accept
   ```

   它把新版本写进 `upstream/backend/`，并更新本文件里的固定版本、提交、每条基线 sha256。分歧日志由人复核：新版本让某条分歧消失时删掉那条，而不是留着过期描述。

6. 提交。钩子会检查 `UPSTREAM.md` 与派生模块是否在同一个提交里（见下）。

## 守门

`scripts/check_upstream_manifest.py` 机械校验三件事，任何一条不成立就失败：

| 规则 | 防的是 |
| --- | --- |
| 暂存了派生模块（状态非 `local-only`）或 `upstream/` 快照，却没有暂存 `UPSTREAM.md` | 就地改写不记录，下次同步分不清谁改了什么 |
| 快照的 sha256 与表里记录的不一致 | 改写 base，让三方合并悄悄变成错的 |
| 标 `verbatim` 的文件与快照不一致 | 以为可以覆盖，实际会丢一行本地代码 |

```sh
uv run python scripts/check_upstream_manifest.py            # 校验整份工作区
uv run python scripts/check_upstream_manifest.py --staged   # 只校验暂存区（钩子用）
scripts/install-hooks.sh                                   # 装 pre-commit 钩子
```

`tests/test_upstream_manifest.py` 让 `uv run pytest` 也能发现同样的漂移，不装钩子的环境不会就此失守。

`upstream/` 与 `scripts/` 不参与打包，也不进 ruff 的检查范围（上游代码风格不是本仓风格）。
