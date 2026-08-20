# ──────────────────────────────────────────────────────────────
# Native Tool: Web Fetcher (网页内容抓取器)
# 从 official_plugins/web_fetcher.py 迁移而来
# ──────────────────────────────────────────────────────────────

import ipaddress
import os
import re
import socket
import sys
from typing import Optional, Tuple
from urllib.parse import urlparse, urljoin

# ═══════════════════════════════════════════════════════════════
#  Lazy imports – 优雅降级
# ═══════════════════════════════════════════════════════════════

_REQUESTS_AVAILABLE: Optional[bool] = None
_BS4_AVAILABLE: Optional[bool] = None


def _check_requests() -> bool:
    global _REQUESTS_AVAILABLE
    if _REQUESTS_AVAILABLE is None:
        try:
            import requests  # noqa: F401
            _REQUESTS_AVAILABLE = True
        except ImportError:
            _REQUESTS_AVAILABLE = False
    return _REQUESTS_AVAILABLE


def _check_bs4() -> bool:
    global _BS4_AVAILABLE
    if _BS4_AVAILABLE is None:
        try:
            import bs4  # noqa: F401
            _BS4_AVAILABLE = True
        except ImportError:
            _BS4_AVAILABLE = False
    return _BS4_AVAILABLE


# ═══════════════════════════════════════════════════════════════
#  SSRF 防护
# ═══════════════════════════════════════════════════════════════

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


def _is_private_url(url: str) -> Tuple[bool, str]:
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
        ip = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC,
                                socket.SOCK_STREAM)
        addr_str = ip[0][4][0] if ip else hostname
    except socket.gaierror:
        return True, f"无法解析主机名: {hostname}"
    except Exception as e:
        return True, f"DNS 解析失败: {e}"

    try:
        addr = ipaddress.ip_address(addr_str)
    except ValueError:
        return True, f"无法解析为 IP 地址: {addr_str}"

    for net in _BLOCKED_NETWORKS:
        if addr in net:
            return True, (
                f"安全限制：禁止访问内网地址 {addr} "
                f"（属于 {net}）"
            )

    return False, ""


# ═══════════════════════════════════════════════════════════════
#  HTTP 请求
# ═══════════════════════════════════════════════════════════════

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0.0.0 Safari/537.36"
)

_MAX_RESPONSE_BYTES = 10 * 1024 * 1024


def _fetch_with_requests(url: str, timeout: int) -> Tuple[int, str, Optional[str]]:
    import requests

    headers = {
        "User-Agent": _USER_AGENT,
        "Accept": (
            "text/html,application/xhtml+xml,"
            "application/xml;q=0.9,text/plain;q=0.8,*/*;q=0.5"
        ),
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate",
    }

    try:
        resp = requests.get(
            url, headers=headers, timeout=timeout,
            allow_redirects=True, stream=True,
        )

        content_type = resp.headers.get("Content-Type", "").lower()
        if content_type and not any(
            t in content_type
            for t in ("text/", "application/json", "application/xml",
                       "application/xhtml", "application/javascript")
        ):
            resp.close()
            return (
                resp.status_code, "",
                f"不支持的内容类型: {content_type}。仅支持文本类网页。",
            )

        chunks = []
        total = 0
        for chunk in resp.iter_content(chunk_size=8192, decode_unicode=False):
            if chunk:
                total += len(chunk)
                if total > _MAX_RESPONSE_BYTES:
                    chunks.append(chunk[:_MAX_RESPONSE_BYTES - (total - len(chunk))])
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
    except requests.exceptions.ConnectionError as e:
        return 0, "", f"连接失败: {e}"
    except requests.exceptions.TooManyRedirects:
        return 0, "", "重定向次数过多，无法完成请求。"
    except requests.exceptions.RequestException as e:
        return 0, "", f"请求失败: {e}"
    except Exception as e:
        return 0, "", f"未知错误: {e}"


def _fetch_with_urllib(url: str, timeout: int) -> Tuple[int, str, Optional[str]]:
    import gzip
    import io
    from urllib.request import Request, urlopen
    from urllib.error import URLError, HTTPError

    headers = {
        "User-Agent": _USER_AGENT,
        "Accept": (
            "text/html,application/xhtml+xml,"
            "application/xml;q=0.9,text/plain;q=0.8,*/*;q=0.5"
        ),
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip",
    }

    try:
        req = Request(url, headers=headers)
        resp = urlopen(req, timeout=timeout)

        content_type = resp.headers.get("Content-Type", "").lower()
        if content_type and not any(
            t in content_type
            for t in ("text/", "application/json", "application/xml",
                       "application/xhtml", "application/javascript")
        ):
            return (
                resp.status, "",
                f"不支持的内容类型: {content_type}。仅支持文本类网页。",
            )

        raw = resp.read(_MAX_RESPONSE_BYTES + 1)
        if len(raw) > _MAX_RESPONSE_BYTES:
            raw = raw[:_MAX_RESPONSE_BYTES]

        if resp.headers.get("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)

        charset = "utf-8"
        ct = resp.headers.get("Content-Type", "")
        if "charset=" in ct:
            charset = ct.split("charset=")[-1].split(";")[0].strip()
        try:
            text = raw.decode(charset, errors="replace")
        except (LookupError, UnicodeDecodeError):
            text = raw.decode("utf-8", errors="replace")

        return resp.status, text, None

    except HTTPError as e:
        return e.code, "", f"HTTP {e.code}: {e.reason}"
    except URLError as e:
        return 0, "", f"无法连接到 {url}: {e.reason}"
    except socket.timeout:
        return 0, "", f"请求超时（{timeout}s）。请增加 timeout 或稍后重试。"
    except Exception as e:
        return 0, "", f"未知错误: {e}"


def _fetch_url(url: str, timeout: int) -> Tuple[int, str, Optional[str]]:
    if _check_requests():
        return _fetch_with_requests(url, timeout)
    else:
        return _fetch_with_urllib(url, timeout)


# ═══════════════════════════════════════════════════════════════
#  HTML → 纯文本提取
# ═══════════════════════════════════════════════════════════════

def _extract_text_bs4(html: str) -> str:
    import bs4 as _bs4

    try:
        soup = _bs4.BeautifulSoup(html, "html.parser")
    except Exception:
        return _extract_text_regex(html)

    for tag_name in ("script", "style", "noscript", "meta", "link",
                      "head", "iframe", "svg", "canvas", "nav",
                      "footer", "header"):
        for tag in soup.find_all(tag_name):
            tag.decompose()

    body = None
    for selector in ("article", "main", '[role="main"]',
                      "#content", ".content", "#main", ".main",
                      "#article", ".article", ".post", ".post-content",
                      "#post", ".entry-content", ".markdown-body"):
        el = soup.select_one(selector)
        if el:
            body = el
            break

    if body is None:
        body = soup.body or soup

    text = body.get_text(separator="\n", strip=True)
    return _clean_text(text)


def _extract_text_regex(html: str) -> str:
    text = re.sub(
        r'<(script|style|noscript|iframe|svg|canvas|head|meta|link)[^>]*>.*?</\1>',
        '', html, flags=re.DOTALL | re.IGNORECASE,
    )
    text = re.sub(r'<(script|style|noscript|iframe|svg|canvas|head|meta|link)[^>]*/>',
                  '', text, flags=re.IGNORECASE)
    text = re.sub(r'<!--.*?-->', '', text, flags=re.DOTALL)
    text = re.sub(
        r'</?(?:div|p|h[1-6]|li|tr|article|section|header|footer|nav|aside|'
        r'main|blockquote|pre|table|ul|ol|dl|hr|br|figure|figcaption|'
        r'address|fieldset|form|details|summary)[^>]*>',
        '\n', text, flags=re.IGNORECASE,
    )
    text = re.sub(r'<[^>]+>', '', text)
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    text = text.replace("&quot;", '"').replace("&#39;", "'").replace("&apos;", "'")
    text = text.replace("&nbsp;", " ").replace("&#160;", " ")
    text = re.sub(r'&#(\d+);', lambda m: chr(int(m.group(1))), text)
    text = re.sub(r'&#x([0-9a-fA-F]+);',
                  lambda m: chr(int(m.group(1), 16)), text)

    return _clean_text(text)


def _clean_text(text: str) -> str:
    lines = text.split("\n")
    cleaned = []
    for line in lines:
        stripped = re.sub(r'[ \t\r]+', ' ', line).strip()
        cleaned.append(stripped)

    result = []
    prev_empty = False
    for line in cleaned:
        is_empty = (line == "")
        if is_empty and prev_empty:
            continue
        result.append(line)
        prev_empty = is_empty

    return "\n".join(result).strip()


def _extract_text(html: str) -> str:
    if _check_bs4():
        return _extract_text_bs4(html)
    else:
        return _extract_text_regex(html)


# ═══════════════════════════════════════════════════════════════
#  链接提取
# ═══════════════════════════════════════════════════════════════

def _score_link_text(text: str) -> float:
    score = len(text)
    if re.match(r'^[\d\s./\\\-_#]+$', text):
        score *= 0.2
    if re.match(r'^(点击|更多|详情|查看|阅读|进入|跳转|返回|下一页|上一页|here|more|click|read|next|prev|back)\b', text, re.IGNORECASE):
        score *= 0.3
    if len(text) <= 2:
        score *= 0.4
    if re.search(r'[\u4e00-\u9fff]', text):
        score *= 1.5
    if re.search(r'[a-zA-Z]{3,}', text):
        score *= 1.2
    return score


def _extract_links_bs4(html: str, base_url: str) -> list:
    import bs4 as _bs4

    try:
        soup = _bs4.BeautifulSoup(html, "html.parser")
    except Exception:
        return _extract_links_regex(html, base_url)

    base_domain = urlparse(base_url).netloc.lower()
    links = []
    seen = set()

    for a in soup.find_all("a", href=True):
        href = a.get("href", "").strip()
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue

        absolute = urljoin(base_url, href)
        parsed = urlparse(absolute)

        if parsed.scheme not in ("http", "https"):
            continue

        clean_url = parsed._replace(fragment="").geturl()

        if clean_url in seen:
            continue
        seen.add(clean_url)

        link_domain = parsed.netloc.lower()
        is_internal = (link_domain == base_domain)

        text = a.get_text(separator=" ", strip=True)
        if not text:
            text = a.get("title") or a.get("aria-label") or ""
        if not text:
            text = parsed.path.rstrip("/") or "/"

        links.append((clean_url, text, is_internal))

    return links


def _extract_links_regex(html: str, base_url: str) -> list:
    base_domain = urlparse(base_url).netloc.lower()
    links = []
    seen = set()

    pattern = re.compile(
        r'<a\s[^>]*?href\s*=\s*["\']([^"\']+)["\'][^>]*>'
        r'(.*?)'
        r'</a>',
        re.IGNORECASE | re.DOTALL,
    )

    for match in pattern.finditer(html):
        href = match.group(1).strip()
        raw_text = match.group(2)

        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue

        absolute = urljoin(base_url, href)
        parsed = urlparse(absolute)

        if parsed.scheme not in ("http", "https"):
            continue

        clean_url = parsed._replace(fragment="").geturl()

        if clean_url in seen:
            continue
        seen.add(clean_url)

        link_domain = parsed.netloc.lower()
        is_internal = (link_domain == base_domain)

        text = re.sub(r'<[^>]+>', ' ', raw_text)
        text = re.sub(r'\s+', ' ', text).strip()
        if not text:
            text = parsed.path.rstrip("/") or "/"

        links.append((clean_url, text, is_internal))

    return links


def _extract_links(html: str, base_url: str) -> list:
    if _check_bs4():
        return _extract_links_bs4(html, base_url)
    else:
        return _extract_links_regex(html, base_url)


# ═══════════════════════════════════════════════════════════════
#  主处理函数
# ═══════════════════════════════════════════════════════════════

def handle_web_fetch(url: str, max_chars: int = 8000, timeout: int = 15) -> str:
    if not url:
        return "❌ 请提供要抓取的 URL。"

    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    max_chars = max(500, min(max_chars, 50000))
    timeout = max(5, min(timeout, 60))

    is_blocked, reason = _is_private_url(url)
    if is_blocked:
        return f"❌ 安全限制：{reason}"

    status, html, error = _fetch_url(url, timeout)
    if error:
        if status > 0:
            return (
                f"⚠️ HTTP {status}\n"
                f"URL: {url}\n"
                f"服务器返回了错误状态码，没有可提取的正文内容。"
            )
        return f"❌ 抓取失败：{error}"

    if not html or not html.strip():
        return f"⚠️ 网页内容为空（HTTP {status}）。URL: {url}"

    text = _extract_text(html)

    if not text or not text.strip():
        return (
            f"⚠️ 网页无有效文本内容（HTTP {status}）。"
            f"可能是纯 JavaScript 渲染页面或空白页。\nURL: {url}"
        )

    original_len = len(text)
    if original_len > max_chars:
        text = text[:max_chars]
        last_period = max(text.rfind("。"), text.rfind(". "),
                          text.rfind("\n"), text.rfind(" "))
        if last_period > max_chars * 0.7:
            text = text[:last_period + 1]

    bs4_ok = _check_bs4()
    req_ok = _check_requests()
    impl_note = []
    impl_note.append("requests" if req_ok else "urllib (标准库)")
    impl_note.append("BeautifulSoup" if bs4_ok else "正则表达式 (标准库)")

    lines = [
        f"🌐 **网页抓取结果**",
        f"",
        f"📌 **URL**: {url}",
        f"📊 **状态码**: HTTP {status}",
        f"📏 **原始大小**: {original_len:,} 字符",
        f"🔧 **引擎**: {' + '.join(impl_note)}",
    ]

    if original_len > max_chars:
        lines.append(f"✂️ **已截断**: 仅显示前 {len(text):,} 字符")

    lines.append("")
    lines.append("─" * 60)
    lines.append("")
    lines.append(text)

    return "\n".join(lines)


def handle_web_extract_links(url: str, same_domain_only: bool = False,
                              max_links: int = 50, timeout: int = 15) -> str:
    if not url:
        return "❌ 请提供要提取链接的 URL。"

    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    max_links = max(10, min(max_links, 200))
    timeout = max(5, min(timeout, 60))

    is_blocked, reason = _is_private_url(url)
    if is_blocked:
        return f"❌ 安全限制：{reason}"

    status, html, error = _fetch_url(url, timeout)
    if error:
        if status > 0:
            return (
                f"⚠️ HTTP {status}\n"
                f"URL: {url}\n"
                f"服务器返回了错误状态码，无法提取链接。"
            )
        return f"❌ 抓取失败：{error}"

    if not html or not html.strip():
        return f"⚠️ 网页内容为空（HTTP {status}），没有可提取的链接。"

    all_links = _extract_links(html, url)

    if not all_links:
        return (
            f"🔗 **链接提取结果**\n\n"
            f"📌 **URL**: {url}\n"
            f"📊 **状态码**: HTTP {status}\n"
            f"🔍 **找到链接**: 0\n\n"
            f"该页面没有可提取的超链接（可能是纯 JavaScript 渲染页面）。"
        )

    internal_links = []
    external_links = []

    for href, text, is_internal in all_links:
        score = _score_link_text(text)
        entry = (href, text, score)
        if is_internal:
            internal_links.append(entry)
        else:
            external_links.append(entry)

    internal_links.sort(key=lambda x: x[2], reverse=True)
    external_links.sort(key=lambda x: x[2], reverse=True)

    if same_domain_only:
        internal_links = internal_links[:max_links]
        external_links = []
    else:
        if len(internal_links) >= max_links:
            internal_links = internal_links[:max_links]
            external_links = []
        else:
            remaining = max_links - len(internal_links)
            external_links = external_links[:remaining]

    base_domain = urlparse(url).netloc.lower()

    lines = [
        f"🔗 **链接提取结果**",
        f"",
        f"📌 **来源 URL**: {url}",
        f"🏷️ **域名**: {base_domain}",
        f"📊 **HTTP**: {status}",
        f"🔍 **原始链接总数**: {len(all_links)}",
    ]
    if same_domain_only:
        lines.append(f"🎯 **过滤**: 仅显示同域链接")
    lines.append(f"📋 **显示**: {len(internal_links) + len(external_links)} 条（上限 {max_links}）")
    lines.append("")

    if internal_links:
        lines.append(f"## 📂 同域内链（{len(internal_links)} 条）")
        lines.append("")
        for i, (href, text, score) in enumerate(internal_links, 1):
            display_text = text if len(text) <= 80 else text[:77] + "..."
            lines.append(f"  {i}. [{display_text}]({href})")

    if external_links:
        lines.append("")
        lines.append(f"## 🌍 外部链接（{len(external_links)} 条）")
        lines.append("")
        for i, (href, text, score) in enumerate(external_links, 1):
            ext_domain = urlparse(href).netloc.lower()
            display_text = text if len(text) <= 60 else text[:57] + "..."
            lines.append(f"  {i}. [{display_text}]({href})  `[{ext_domain}]`")

    lines.append("")
    lines.append("─" * 60)
    lines.append(
        "💡 **提示**: 对以上任意 URL 调用 `web_fetch` 即可抓取对应页面的正文内容。"
    )

    return "\n".join(lines)
