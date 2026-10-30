"""联网检索后端：给点评阶段的模型提供 web_search 工具的实际检索能力。

后端都是"请求一次搜索结果页 / 接口"级别的轻实现，不引第三方搜索 SDK：

- baidu：抓 baidu.com 的结果页，免 Key，中文查询最准，链接是跳转地址（自动解出真实地址）。
- bing：抓 bing.com 的结果页，免 Key，英文查询兜底（中文长查询实测经常只按首词匹配）。
- duckduckgo：抓 html.duckduckgo.com 的结果页，免 Key（国内网络常不通，留给有代理的环境）。
- tavily：官方搜索 API，需要 Key，结果最稳、摘要最好。

auto 档按有没有 Key 选：有 Key 用 tavily；没有就 baidu 优先、结果为空再试 bing。
"""

from __future__ import annotations

import base64
import html
import json
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlparse

WEB_SEARCH_TOOL_NAME = "web_search"

# 给模型的工具定义：每次一个查询，返回标题、链接、摘要的清单。
WEB_SEARCH_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": WEB_SEARCH_TOOL_NAME,
        "description": (
            "搜索网页，返回结果的标题、链接与摘要。用于核验点评中涉及的事实"
            "（数字、日期、人物、产品规格、标准、结论的现实依据）。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "检索关键词，尽量具体，一次只问一件事",
                }
            },
            "required": ["query"],
        },
    },
}

WEB_SEARCH_BACKENDS = ("auto", "baidu", "bing", "duckduckgo", "tavily")

# 搜索站点对默认 UA 会直接给空结果页，必须像正常浏览器一样访问。
_SEARCH_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

# 摘要进提示词的预算：再长就是原文搬运，对核验没有增量。
_SNIPPET_CHARACTERS = 220


@dataclass(frozen=True)
class WebSearchResult:
    title: str
    url: str
    snippet: str


def search_query_from_arguments(arguments: str) -> str:
    """从模型给出的工具参数 JSON 里取 query；解析不了就当原始查询串用。"""
    try:
        value = json.loads(arguments or "{}")
    except ValueError:
        return (arguments or "").strip()
    if isinstance(value, dict):
        return str(value.get("query") or "").strip()
    return ""


class WebSearcher:
    """一次查询一份结果清单；状态只存配置，可以反复调用。"""

    def __init__(
        self,
        backend: str = "auto",
        api_key: str = "",
        timeout: float = 15.0,
        max_results: int = 5,
        max_searches: int = 4,
    ) -> None:
        if backend not in WEB_SEARCH_BACKENDS:
            raise ValueError(
                f"未知搜索后端：{backend}；可选 {' / '.join(WEB_SEARCH_BACKENDS)}"
            )
        self.backend = backend
        self.api_key = api_key.strip()
        self.timeout = timeout
        self.max_results = max_results
        # 点评阶段的检索预算：一次点评最多搜几次，由调用方按次扣减。
        self.max_searches = max_searches

    @property
    def backend_chain(self) -> tuple[str, ...]:
        """实际会用的后端顺序：auto 先免 Key 的中文首选，结果空再换下一个。"""
        if self.backend == "auto":
            return ("tavily",) if self.api_key else ("baidu", "bing")
        return (self.backend,)

    async def search(self, query: str) -> tuple[str, list[WebSearchResult]]:
        """按后端顺序搜到结果就停，返回 (实际用的后端, 结果)；全空就用最后一个的名字。

        单个后端报错（网络不通、页面改版）换下一个；全部失败时抛最后一个错误，
        让调用方分得清“没有结果”和“根本没搜成”。
        """
        import httpx  # 延迟导入：只要转录稿的运行不需要 HTTP 客户端

        async with httpx.AsyncClient(
            timeout=self.timeout,
            follow_redirects=True,
            headers=_SEARCH_HEADERS,
        ) as client:
            last = self.backend_chain[-1]
            failure: Exception | None = None
            searched = False
            for backend in self.backend_chain:
                last = backend
                try:
                    results = await self._search_once(client, backend, query)
                except Exception as exc:
                    failure = exc
                    continue
                searched = True
                if results:
                    return backend, results
            if not searched and failure is not None:
                raise failure
            return last, []

    async def _search_once(
        self, client: Any, backend: str, query: str
    ) -> list[WebSearchResult]:
        if backend == "tavily":
            return await _search_tavily(client, query, self)
        if backend == "duckduckgo":
            return await _search_duckduckgo(client, query, self)
        if backend == "baidu":
            return await _search_baidu(client, query, self)
        return await _search_bing(client, query, self)

    async def search_report(self, query: str) -> str:
        """搜一次并把结果排版成给模型看的文本；失败也返回文字而不是抛异常。

        点评只是可选增强，检索挂了（网络不通、页面改版、限流）不该把整条任务
        拖死——返回一句失败说明，模型会按"未联网核验"继续写。
        """
        query = query.strip()
        if not query:
            return "缺少检索关键词。"
        try:
            backend, results = await self.search(query)
        except Exception as exc:
            return (
                f"联网检索「{query}」失败（{type(exc).__name__}: {exc}），"
                "请按未联网核验处理。"
            )
        if not results:
            return f"没有找到「{query}」的可用结果，请按未联网核验处理。"
        lines = [f"检索「{query}」（{backend}）共 {len(results)} 条："]
        for index, result in enumerate(results, 1):
            lines.append(f"{index}. {result.title}")
            lines.append(f"   {result.url}")
            if result.snippet:
                lines.append(f"   {result.snippet}")
        return "\n".join(lines)


async def _search_baidu(
    client: Any, query: str, searcher: WebSearcher
) -> list[WebSearchResult]:
    """抓 baidu.com 结果页：标题链接在 <h3><a href>，摘要取该条目块的剩余文字。

    结果链接是 baidu.com/link?url= 跳转地址，逐条解出真实地址让引用可以直接点开；
    解不开就保留跳转地址（仍可访问）。带 baidu.php 的条目是广告，直接丢掉。
    """
    response = await client.get(
        "https://www.baidu.com/s", params={"wd": query, "rn": searcher.max_results}
    )
    response.raise_for_status()
    page = html.unescape(response.text)
    entries = re.split(r"<h3[^>]*>", page)[1:]
    results: list[WebSearchResult] = []
    for entry in entries:
        match = re.search(r'<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', entry, re.S)
        if match is None:
            continue
        url = match.group(1)
        if "baidu.php" in url or not url.startswith("http"):
            continue
        title = _strip_html(match.group(2))
        snippet = _strip_html(entry[match.end() :])
        results.append(
            WebSearchResult(title=title, url=await _resolve_link(client, url), snippet=snippet)
        )
        if len(results) >= searcher.max_results:
            break
    return results


async def _search_bing(
    client: Any, query: str, searcher: WebSearcher
) -> list[WebSearchResult]:
    """抓 bing.com 结果页：条目在 class="b_algo" 块里，摘要取标题后的第一个 <p>。

    个别结果的真实链接藏在 bing.com/ck/a 跳转地址的 u 参数里（base64），解开再用。
    """
    response = await client.get("https://www.bing.com/search", params={"q": query})
    response.raise_for_status()
    page = html.unescape(response.text)
    results: list[WebSearchResult] = []
    for block in page.split('class="b_algo"')[1:]:
        match = re.search(
            r'<h2[^>]*>\s*<a[^>]*href="(https?://[^"]+)"[^>]*>(.*?)</a>', block, re.S
        )
        if match is None:
            continue
        url = _unwrap_bing_link(match.group(1))
        if _is_search_engine_link(url):
            continue
        title = _strip_html(match.group(2))
        snippet = ""
        paragraph = re.search(r"<p[^>]*>(.*?)</p>", block[match.end() :], re.S)
        if paragraph:
            snippet = _strip_html(paragraph.group(1))
        results.append(WebSearchResult(title=title, url=url, snippet=snippet))
        if len(results) >= searcher.max_results:
            break
    return results


async def _search_duckduckgo(
    client: Any, query: str, searcher: WebSearcher
) -> list[WebSearchResult]:
    """抓 html.duckduckgo.com 结果页；真实链接藏在跳转地址的 uddg 参数里。"""
    response = await client.get(
        "https://html.duckduckgo.com/html/", params={"q": query}
    )
    response.raise_for_status()
    page = html.unescape(response.text)
    results: list[WebSearchResult] = []
    for match in re.finditer(
        r'<a[^>]*class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>'
        r'(.*?)(?=<a[^>]*class="result__a"|$)',
        page,
        re.S,
    ):
        url = _unwrap_duckduckgo_link(match.group(1))
        title = _strip_html(match.group(2))
        snippet = ""
        block = re.search(
            r'class="result__snippet"[^>]*>(.*?)</(?:a|div|span)>', match.group(3), re.S
        )
        if block:
            snippet = _strip_html(block.group(1))
        results.append(WebSearchResult(title=title, url=url, snippet=snippet))
        if len(results) >= searcher.max_results:
            break
    return results


async def _search_tavily(
    client: Any, query: str, searcher: WebSearcher
) -> list[WebSearchResult]:
    """Tavily 搜索 API：JSON 直出，字段就是标题 / 链接 / 摘要。"""
    response = await client.post(
        "https://api.tavily.com/search",
        json={
            "api_key": searcher.api_key,
            "query": query,
            "max_results": searcher.max_results,
        },
    )
    response.raise_for_status()
    payload = response.json()
    return [
        WebSearchResult(
            title=str(item.get("title") or "").strip(),
            url=str(item.get("url") or "").strip(),
            snippet=_clip_snippet(str(item.get("content") or "")),
        )
        for item in payload.get("results", [])
        if item.get("url")
    ]


async def _resolve_link(client: Any, url: str) -> str:
    """跟一次跳转拿到真实地址；失败就保留原跳转地址。"""
    if not url.startswith("http"):
        return url
    try:
        response = await client.get(url)
        resolved = str(response.url)
    except Exception:
        return url
    return resolved if resolved.startswith("http") else url


def _is_search_engine_link(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return any(
        marker in host for marker in ("bing.com", "microsoft.com", "msn.com")
    )


def _unwrap_bing_link(url: str) -> str:
    """解 bing.com/ck/a 跳转：u=a1<base64 编码的真实地址>。"""
    parsed = urlparse(url)
    if "/ck/a" not in (parsed.path or ""):
        return url
    encoded = parse_qs(parsed.query).get("u", [""])[0]
    if not encoded.startswith("a1"):
        return url
    try:
        padded = encoded[2:] + "=" * (-len(encoded[2:]) % 4)
        return base64.urlsafe_b64decode(padded).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return url


def _unwrap_duckduckgo_link(url: str) -> str:
    parsed = urlparse(url)
    if "duckduckgo.com" in (parsed.hostname or ""):
        target = parse_qs(parsed.query).get("uddg", [""])[0]
        return target or url
    return url


def _strip_html(fragment: str) -> str:
    return _clip_snippet(re.sub(r"<[^>]+>", "", fragment))


def _clip_snippet(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= _SNIPPET_CHARACTERS:
        return text
    return text[:_SNIPPET_CHARACTERS].rstrip() + "…"
