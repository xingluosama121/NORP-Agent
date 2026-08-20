# Copyright (c) 2026 xingluosama121, MIT Licensed
"""联网检索工具：web_search / web_fetch / web_extract_links。

迁移自现有应用的 web_fetcher_native，特性保持一致：
- SSRF 防护：仅允许 http/https，DNS 解析后拒绝内网 / 回环 / 链路本地地址；
- 双引擎优雅降级：requests 优先（norpagent[web]），urllib 标准库兜底；
- 文本提取：BeautifulSoup 优先，正则表达式标准库兜底。

零第三方依赖时全部功能可用；安装 norpagent[web] 获得更好的
抓取与解析质量。
"""

from __future__ import annotations

import ipaddress
import re
import socket
from html import unescape
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urljoin, urlparse

from norpagent.protocols.tool import Tool, ToolResult

# ── SSRF 防护 ────────────────────────────────────────────────

_BLOCKED_NETWORKS = [
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("224.0.0.0/4"),
    ipaddress.ip_network("240.0.0.0/4"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
]


def is_private_url(url: str) -> Tuple[bool, str]:
    """返回 (是否受限, 原因)。受限 = 禁止访问。"""
    try:
        parsed = urlparse(url)
    except Exception:
        return True, f"无法解析 URL: {url}"

    if parsed.scheme not in ("http", "https"):
        return True, f"不支持的协议: {parsed.scheme}（仅允许 http/https）"

    hostname = parsed.hostname
    if not hostname:
        return True, "URL 中没有可解析的主机名"

    if hostname.lower() in ("localhost", "127.0.0.1", "::1", "0.0.0.0"):
        return True, "禁止访问 localhost 地址"

    try:
        infos = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
        addr_str = infos[0][4][0] if infos else hostname
    except socket.gaierror:
        return True, f"无法解析主机名: {hostname}"
    except Exception as exc:  # noqa: BLE001
        return True, f"DNS 解析失败: {exc}"

    try:
        addr = ipaddress.ip_address(addr_str)
    except ValueError:
        return True, f"无法解析为 IP 地址: {addr_str}"

    for net in _BLOCKED_NETWORKS:
        if addr in net:
            return True, f"安全限制：禁止访问内网地址 {addr}（属于 {net}）"
    return False, ""


# ── HTTP 抓取（requests 优先，urllib 兜底）────────────────────

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0.0.0 Safari/537.36"
)

_ACCEPT = (
    "text/html,application/xhtml+xml,"
    "application/xml;q=0.9,text/plain;q=0.8,*/*;q=0.5"
)

_MAX_RESPONSE_BYTES = 10 * 1024 * 1024

_TEXT_TYPES = (
    "text/", "application/json", "application/xml",
    "application/xhtml", "application/javascript",
)


def _requests_available() -> bool:
    try:
        import requests  # noqa: F401

        return True
    except ImportError:
        return False


def _bs4_available() -> bool:
    try:
        import bs4  # noqa: F401

        return True
    except ImportError:
        return False


def _fetch_url(url: str, timeout: int) -> Tuple[int, str, Optional[str]]:
    """返回 (HTTP 状态码, 响应文本, 错误信息)。错误信息非 None 表示失败。"""
    if _requests_available():
        return _fetch_with_requests(url, timeout)
    return _fetch_with_urllib(url, timeout)


def _check_content_type(content_type: str) -> Optional[str]:
    ctype = (content_type or "").lower()
    if ctype and not any(t in ctype for t in _TEXT_TYPES):
        return f"不支持的内容类型: {ctype}。仅支持文本类网页。"
    return None


def _fetch_with_requests(url: str, timeout: int) -> Tuple[int, str, Optional[str]]:
    import requests

    headers = {
        "User-Agent": _USER_AGENT,
        "Accept": _ACCEPT,
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate",
    }
    try:
        resp = requests.get(
            url, headers=headers, timeout=timeout,
            allow_redirects=True, stream=True,
        )
        ctype_err = _check_content_type(resp.headers.get("Content-Type", ""))
        if ctype_err:
            resp.close()
            return resp.status_code, "", ctype_err

        chunks: List[bytes] = []
        total = 0
        for chunk in resp.iter_content(chunk_size=8192, decode_unicode=False):
            if chunk:
                total += len(chunk)
                if total > _MAX_RESPONSE_BYTES:
                    chunks.append(chunk[: _MAX_RESPONSE_BYTES - (total - len(chunk))])
                    break
                chunks.append(chunk)
        body = b"".join(chunks)
        resp.encoding = resp.apparent_encoding or "utf-8"
        try:
            text = body.decode(resp.encoding, errors="replace")
        except (LookupError, UnicodeDecodeError):
            text = body.decode("utf-8", errors="replace")
        return resp.status_code, text, None
    except requests.exceptions.Timeout:
        return 0, "", f"请求超时（{timeout}s）。请增加 timeout 或稍后重试。"
    except requests.exceptions.ConnectionError as exc:
        return 0, "", f"连接失败: {exc}"
    except requests.exceptions.TooManyRedirects:
        return 0, "", "重定向次数过多，无法完成请求。"
    except requests.exceptions.RequestException as exc:
        return 0, "", f"请求失败: {exc}"
    except Exception as exc:  # noqa: BLE001
        return 0, "", f"未知错误: {exc}"


def _fetch_with_urllib(url: str, timeout: int) -> Tuple[int, str, Optional[str]]:
    import gzip
    from urllib.error import HTTPError, URLError
    from urllib.request import Request, urlopen

    headers = {
        "User-Agent": _USER_AGENT,
        "Accept": _ACCEPT,
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip",
    }
    try:
        req = Request(url, headers=headers)
        resp = urlopen(req, timeout=timeout)
        status = getattr(resp, "status", 200)
        content_type = (resp.headers.get("Content-Type") or "").lower()
        ctype_err = _check_content_type(content_type)
        if ctype_err:
            return status, "", ctype_err

        data = resp.read(_MAX_RESPONSE_BYTES + 1)
        if len(data) > _MAX_RESPONSE_BYTES:
            data = data[:_MAX_RESPONSE_BYTES]
        if resp.headers.get("Content-Encoding", "").lower() == "gzip":
            try:
                data = gzip.decompress(data)
            except OSError:
                pass
        charset = "utf-8"
        if "charset=" in content_type:
            try:
                charset = content_type.split("charset=")[1].split(";")[0].strip()
            except IndexError:
                pass
        try:
            text = data.decode(charset, errors="replace")
        except LookupError:
            text = data.decode("utf-8", errors="replace")
        return status, text, None
    except HTTPError as exc:
        return exc.code, "", f"HTTP {exc.code}: {exc.reason}"
    except URLError as exc:
        return 0, "", f"连接失败: {exc.reason}"
    except socket.timeout:
        return 0, "", f"请求超时（{timeout}s）。请增加 timeout 或稍后重试。"
    except Exception as exc:  # noqa: BLE001
        return 0, "", f"未知错误: {exc}"


# ── HTML 文本提取（bs4 优先，正则兜底）────────────────────────

def _extract_text(html: str) -> str:
    if _bs4_available():
        return _extract_text_bs4(html)
    return _extract_text_regex(html)


def _extract_text_bs4(html: str) -> str:
    import bs4

    try:
        soup = bs4.BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "noscript", "svg", "iframe", "head"]):
            tag.decompose()
        lines: List[str] = []
        for text in soup.stripped_strings:
            text = " ".join(text.split())
            if text and text not in lines:
                lines.append(text)
        return "\n".join(lines)
    except Exception:
        return _extract_text_regex(html)


def _extract_text_regex(html: str) -> str:
    body_match = re.search(r"<body[^>]*>(.*?)</body>", html, re.S | re.I)
    html = body_match.group(1) if body_match else html
    html = re.sub(r"<(script|style|noscript|svg|iframe)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)
    html = re.sub(r"<br\s*/?>", "\n", html, flags=re.I)
    html = re.sub(r"</(p|div|h[1-6]|li|tr|section|article)>", "\n", html, flags=re.I)
    html = re.sub(r"<[^>]+>", " ", html)
    text = unescape(html)
    lines = [" ".join(ln.split()) for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln)


# ── 链接提取 ─────────────────────────────────────────────────

def _extract_links(html: str, base_url: str) -> List[Tuple[str, str, bool]]:
    """返回 [(绝对 URL, 文本, 是否同域)]。"""
    if _bs4_available():
        return _extract_links_bs4(html, base_url)
    return _extract_links_regex(html, base_url)


def _extract_links_bs4(html: str, base_url: str) -> List[Tuple[str, str, bool]]:
    import bs4

    try:
        soup = bs4.BeautifulSoup(html, "html.parser")
        base_domain = urlparse(base_url).netloc.lower()
        result: List[Tuple[str, str, bool]] = []
        for a in soup.find_all("a", href=True):
            href = urljoin(base_url, a.get("href", ""))
            if not href.startswith(("http://", "https://")):
                continue
            text = " ".join(a.get_text(" ", strip=True).split())[:120]
            result.append((href, text, urlparse(href).netloc.lower() == base_domain))
        return result
    except Exception:
        return _extract_links_regex(html, base_url)


def _extract_links_regex(html: str, base_url: str) -> List[Tuple[str, str, bool]]:
    base_domain = urlparse(base_url).netloc.lower()
    result: List[Tuple[str, str, bool]] = []
    for m in re.finditer(r"<a\s+[^>]*href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", html, re.S | re.I):
        href = urljoin(base_url, unescape(m.group(1)).strip())
        if not href.startswith(("http://", "https://")):
            continue
        text = " ".join(unescape(re.sub(r"<[^>]+>", " ", m.group(2))).split())[:120]
        result.append((href, text, urlparse(href).netloc.lower() == base_domain))
    return result


def _score_link_text(text: str) -> int:
    if not text:
        return 0
    score = min(len(text), 60)
    if re.search(r"[一-龥]", text):
        score += 10
    if re.search(r"[a-zA-Z]{2,}", text):
        score += 5
    return score


# ── 工具 ─────────────────────────────────────────────────────

def _ensure_https(url: str) -> str:
    if not url.startswith(("http://", "https://")):
        return "https://" + url
    return url


class WebFetchTool:
    name = "web_fetch"

    def schema(self) -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": (
                    "抓取指定 URL 的网页正文（自动去除 HTML 标签/脚本/样式）。"
                    "内置 SSRF 防护，仅允许公网 http/https 地址。"
                    "返回截断后的纯文本，适合阅读在线文档、博客、API 响应等。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "description": "要抓取的完整 URL"},
                        "max_chars": {"type": "integer", "description": "最大返回字符数（默认 8000，范围 500-50000）"},
                        "timeout": {"type": "integer", "description": "请求超时秒数（默认 15，范围 5-60）"},
                    },
                    "required": ["url"],
                    "additionalProperties": False,
                },
            },
        }

    def run(self, args: Dict[str, Any], ctx: Any) -> ToolResult:
        url = _ensure_https(str(args.get("url") or "").strip())
        if not url or url == "https://":
            return ToolResult(output="请提供要抓取的 URL。", success=False, error="missing_url")
        max_chars = max(500, min(int(args.get("max_chars") or 8000), 50000))
        timeout = max(5, min(int(args.get("timeout") or 15), 60))

        blocked, reason = is_private_url(url)
        if blocked:
            return ToolResult(output=f"安全限制：{reason}", success=False, error=reason)

        status, html, error = _fetch_url(url, timeout)
        if error:
            if status > 0:
                return ToolResult(
                    output=f"HTTP {status}\nURL: {url}\n服务器返回了错误状态码，没有可提取的正文内容。",
                    success=False,
                    error=f"HTTP {status}",
                )
            return ToolResult(output=f"抓取失败：{error}", success=False, error=error)

        if not html or not html.strip():
            return ToolResult(output=f"网页内容为空（HTTP {status}）。URL: {url}", success=False, error="empty")

        text = _extract_text(html)
        if not text or not text.strip():
            return ToolResult(
                output=(
                    f"网页无有效文本内容（HTTP {status}）。"
                    f"可能是纯 JavaScript 渲染页面或空白页。URL: {url}"
                ),
                success=False,
                error="no_text",
            )

        original_len = len(text)
        if original_len > max_chars:
            text = text[:max_chars]
            last_period = max(text.rfind("。"), text.rfind(". "),
                              text.rfind("\n"), text.rfind(" "))
            if last_period > max_chars * 0.7:
                text = text[: last_period + 1]

        engines = " + ".join(
            [("BeautifulSoup" if _bs4_available() else "正则 (标准库)"),
             ("requests" if _requests_available() else "urllib (标准库)")]
        )
        lines = [
            "[网页抓取结果]",
            "",
            f"URL: {url}",
            f"状态码: HTTP {status}",
            f"原始大小: {original_len:,} 字符",
            f"引擎: {engines}",
        ]
        if original_len > max_chars:
            lines.append(f"已截断: 仅显示前 {len(text):,} 字符")
        lines += ["", "-" * 60, "", text]
        return ToolResult(output="\n".join(lines))


class WebExtractLinksTool:
    name = "web_extract_links"

    def schema(self) -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": (
                    "提取指定网页中的所有超链接，按同域内链 / 外部链接分组返回。"
                    "适用于先浏览目录页再挑感兴趣的链接深入抓取。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "description": "要提取链接的网页 URL"},
                        "same_domain_only": {"type": "boolean", "description": "是否只返回同域链接（默认 false）"},
                        "max_links": {"type": "integer", "description": "最大返回链接数（默认 50，范围 10-200）"},
                        "timeout": {"type": "integer", "description": "请求超时秒数（默认 15，范围 5-60）"},
                    },
                    "required": ["url"],
                    "additionalProperties": False,
                },
            },
        }

    def run(self, args: Dict[str, Any], ctx: Any) -> ToolResult:
        url = _ensure_https(str(args.get("url") or "").strip())
        if not url or url == "https://":
            return ToolResult(output="请提供要提取链接的 URL。", success=False, error="missing_url")
        same_domain_only = bool(args.get("same_domain_only", False))
        max_links = max(10, min(int(args.get("max_links") or 50), 200))
        timeout = max(5, min(int(args.get("timeout") or 15), 60))

        blocked, reason = is_private_url(url)
        if blocked:
            return ToolResult(output=f"安全限制：{reason}", success=False, error=reason)

        status, html, error = _fetch_url(url, timeout)
        if error:
            if status > 0:
                return ToolResult(output=f"HTTP {status}，无法提取链接。", success=False, error=f"HTTP {status}")
            return ToolResult(output=f"抓取失败：{error}", success=False, error=error)
        if not html or not html.strip():
            return ToolResult(output=f"网页内容为空（HTTP {status}），没有可提取的链接。", success=False, error="empty")

        all_links = _extract_links(html, url)
        if not all_links:
            return ToolResult(
                output=(
                    f"[链接提取结果]\n\nURL: {url}\n状态码: HTTP {status}\n"
                    f"找到链接: 0\n\n该页面没有可提取的超链接（可能是纯 JavaScript 渲染页面）。"
                )
            )

        internal = sorted(
            [l for l in all_links if l[2]], key=lambda x: _score_link_text(x[1]), reverse=True
        )
        external = sorted(
            [l for l in all_links if not l[2]], key=lambda x: _score_link_text(x[1]), reverse=True
        )
        if same_domain_only:
            internal = internal[:max_links]
            external = []
        elif len(internal) >= max_links:
            internal = internal[:max_links]
            external = []
        else:
            external = external[: max_links - len(internal)]

        base_domain = urlparse(url).netloc.lower()
        lines = [
            "[链接提取结果]",
            "",
            f"来源 URL: {url}",
            f"域名: {base_domain}",
            f"HTTP: {status}",
            f"原始链接总数: {len(all_links)}",
        ]
        if same_domain_only:
            lines.append("过滤: 仅显示同域链接")
        lines.append(f"显示: {len(internal) + len(external)} 条（上限 {max_links}）")
        lines.append("")
        if internal:
            lines.append(f"## 同域内链（{len(internal)} 条）")
            lines.append("")
            for i, (href, text, _) in enumerate(internal, 1):
                disp = text if len(text) <= 80 else text[:77] + "..."
                lines.append(f"  {i}. [{disp}]({href})")
        if external:
            lines.append("")
            lines.append(f"## 外部链接（{len(external)} 条）")
            lines.append("")
            for i, (href, text, _) in enumerate(external, 1):
                disp = text if len(text) <= 60 else text[:57] + "..."
                lines.append(f"  {i}. [{disp}]({href})")
        lines += ["", "-" * 60, "提示: 对以上任意 URL 调用 web_fetch 即可抓取对应页面的正文内容。"]
        return ToolResult(output="\n".join(lines))


class WebSearchTool:
    name = "web_search"

    _ENDPOINT = "https://html.duckduckgo.com/html/"
    _FALLBACK = "https://lite.duckduckgo.com/lite/"

    def schema(self) -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": (
                    "联网搜索关键词，返回结果标题、链接与摘要（默认 DuckDuckGo）。"
                    "适合查最新资料、API 文档、报错信息等。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "搜索关键词或问题"},
                        "max_results": {"type": "integer", "description": "最多返回结果数（默认 5，最大 10）"},
                        "timeout": {"type": "integer", "description": "请求超时秒数（默认 15，范围 5-60）"},
                    },
                    "required": ["query"],
                    "additionalProperties": False,
                },
            },
        }

    def run(self, args: Dict[str, Any], ctx: Any) -> ToolResult:
        query = str(args.get("query") or "").strip()
        if not query:
            return ToolResult(output="请提供搜索关键词。", success=False, error="missing_query")
        max_results = max(1, min(int(args.get("max_results") or 5), 10))
        timeout = max(5, min(int(args.get("timeout") or 15), 60))

        blocked, reason = is_private_url(self._ENDPOINT)
        if blocked:
            return ToolResult(output=f"安全限制：{reason}", success=False, error=reason)

        results = self._search(self._ENDPOINT, query, timeout)
        if not results:
            results = self._search(self._FALLBACK, query, timeout)
        if not results:
            return ToolResult(
                output=f"搜索失败：无结果返回。请稍后重试或更换关键词。",
                success=False,
                error="no_results",
            )

        lines = ["[搜索结果]", "", f"关键词: {query}", f"结果数: {len(results)}", ""]
        for i, (title, href, snippet) in enumerate(results[:max_results], 1):
            lines.append(f"{i}. {title}")
            lines.append(f"   {href}")
            if snippet:
                lines.append(f"   {snippet}")
            lines.append("")
        return ToolResult(output="\n".join(lines).rstrip())

    def _search(self, endpoint: str, query: str, timeout: int) -> List[Tuple[str, str, str]]:
        """抓取搜索页面并解析结果。"""
        try:
            if _requests_available():
                return self._search_requests(endpoint, query, timeout)
            return self._search_urllib(endpoint, query, timeout)
        except Exception:
            return []

    def _search_requests(self, endpoint: str, query: str, timeout: int) -> List[Tuple[str, str, str]]:
        import requests

        resp = requests.post(
            endpoint,
            data={"q": query},
            headers={"User-Agent": _USER_AGENT, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"},
            timeout=timeout,
            allow_redirects=True,
        )
        if resp.status_code != 200:
            return []
        return self._parse_results(resp.text)

    def _search_urllib(self, endpoint: str, query: str, timeout: int) -> List[Tuple[str, str, str]]:
        from urllib.parse import urlencode
        from urllib.request import Request, urlopen

        data = urlencode({"q": query}).encode("utf-8")
        req = Request(
            endpoint,
            data=data,
            headers={
                "User-Agent": _USER_AGENT,
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            },
        )
        resp = urlopen(req, timeout=timeout)
        body = resp.read(2 * 1024 * 1024)
        return self._parse_results(body.decode("utf-8", errors="replace"))

    def _parse_results(self, html: str) -> List[Tuple[str, str, str]]:
        results: List[Tuple[str, str, str]] = []
        for m in re.finditer(
            r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
            html, re.S | re.I,
        ):
            href = unescape(m.group(1)).strip()
            title = unescape(re.sub(r"<[^>]+>", "", m.group(2))).strip()
            href = _unwrap_ddg_redirect(href)
            if not href.startswith(("http://", "https://")):
                continue
            snippet = ""
            sm = re.search(
                r'<a[^>]+class="result__snippet"[^>]*>(.*?)</a>',
                html[m.end(): m.end() + 4000], re.S | re.I,
            )
            if sm:
                snippet = " ".join(unescape(re.sub(r"<[^>]+>", "", sm.group(1))).split())
            results.append((title or href, href, snippet[:300]))
        return results


def _unwrap_ddg_redirect(href: str) -> str:
    """DuckDuckGo 结果链接是重定向 URL，解出真实目标。"""
    try:
        parsed = urlparse(href)
        if "duckduckgo.com" in (parsed.netloc or ""):
            qs = parse_qs(parsed.query)
            uddg = qs.get("uddg", [])
            if uddg and uddg[0].startswith(("http://", "https://")):
                return uddg[0]
    except Exception:
        pass
    return href
