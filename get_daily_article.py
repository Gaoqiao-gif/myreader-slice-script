import os
import re
import time
import json
import copy
import base64
import random
import sys
import datetime
import hashlib
import urllib.parse
import zipfile
import html
import shutil
import requests
import urllib3
from bs4 import BeautifulSoup
import fitz  # PyMuPDF for PDF extraction

# ==================== 【1. 环境与伪装配置 (B项目专属)】 ====================
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
if sys.version_info >= (3, 7):
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36'
]

HISTORY_FILE = "sync_history_b.txt"
LIT_HISTORY_FILE = "sync_history_lit_final_b.txt"

GLOBAL_EPUB_ARTICLES = []


# ==================== 【全局运行指标统计器】 ====================
class RunMetrics:
    def __init__(self):
        self.start_time = time.time()
        self.stats = {
            "lit_books": 0,
            "lit_chars": 0,
            "lit_book_info": "",
            "cdt_count": 0,
            "wiki_count": 0,
            "custom_feeds": 0,
            "errors": [],
            "module_logs": []
        }

    def __getitem__(self, key):
        if key == "errors":
            return self.stats["errors"]
        return self.stats[key]

    def __setitem__(self, key, value):
        if key == "errors" and isinstance(value, list):
            self.stats["errors"] = value
        else:
            self.stats[key] = value

    def record(self, module, ok, detail=""):
        status = "成功" if ok else "提示/未执行"
        entry = f"[{module}] {status}" + (f": {detail}" if detail else "")
        self.stats["module_logs"].append(entry)

    def log_error(self, source, err_msg):
        self.stats["errors"].append(f"[{source}] {str(err_msg)}")
        print(f"⚠️ [{source}] 抓取异常: {err_msg}", flush=True)


RUN_METRICS = RunMetrics()


def get_headers():
    return {
        'User-Agent': random.choice(USER_AGENTS),
        'Accept-Language': 'zh-CN,zh;q=0.9',
        'Referer': 'https://chinadigitaltimes.net/'
    }


def safe_get(url, max_seconds=60, headers=None):
    """
    流式强杀版 GET 请求：
    - 彻底弃用 Retry 重试机制，避免慢连接倍增卡死
    - 使用 stream=True 分块读取，单次请求总耗时超过 max_seconds 立即关闭连接并抛出 TimeoutError
    """
    start_ts = time.time()
    req_headers = headers if headers is not None else get_headers()
    resp = requests.get(
        url,
        headers=req_headers,
        stream=True,
        timeout=(10, 15),
        verify=False
    )
    chunks = []
    try:
        for chunk in resp.iter_content(chunk_size=8192):
            if (time.time() - start_ts) > max_seconds:
                raise TimeoutError(f"请求总耗时超过 {max_seconds} 秒硬性上限: {url}")
            if chunk:
                chunks.append(chunk)
                if (time.time() - start_ts) > max_seconds:
                    raise TimeoutError(f"请求总耗时超过 {max_seconds} 秒硬性上限: {url}")
    except Exception:
        try:
            resp.close()
        except Exception:
            pass
        raise

    resp._content = b"".join(chunks)
    if not resp.encoding or resp.encoding.lower() == 'iso-8859-1':
        resp.encoding = resp.apparent_encoding or 'utf-8'
    return resp


def get_beijing_time():
    utc_now = datetime.datetime.now(datetime.timezone.utc)
    return utc_now.astimezone(datetime.timezone(datetime.timedelta(hours=8)))


def load_history(filename=HISTORY_FILE):
    if not os.path.exists(filename):
        return set()
    try:
        with open(filename, "r", encoding="utf-8") as f:
            return set(line.strip() for line in f if line.strip())
    except Exception:
        return set()


def save_to_history_safely(identifier, filename=HISTORY_FILE):
    try:
        temp_file = filename + ".tmp"
        existing = load_history(filename)
        existing.add(identifier)
        with open(temp_file, "w", encoding="utf-8") as f:
            for item in existing:
                f.write(f"{item}\n")
        os.replace(temp_file, filename)
    except Exception as e:
        print(f"⚠️ 保存历史记录异常: {e}", flush=True)


def clean_filename(title):
    if not title:
        return "untitled"
    name = html.unescape(str(title))
    name = re.sub(r'[\\/:*?"<>|]', '_', name)
    return name[:60].strip()


def normalize_text(text):
    if not text:
        return ""
    return re.sub(r'[^\w\u4e00-\u9fa5]', '', str(text)).lower()


def generate_article_md5(source_name, title):
    norm_title = normalize_text(title)
    raw_str = f"{source_name}_{norm_title}"
    return hashlib.md5(raw_str.encode('utf-8')).hexdigest()


# 翻译模块保持注销状态
def smart_translate(text):
    pass


def split_long_paragraph(text, target_len=150, window=10):
    if not text:
        return ""
    clean_text = text.strip()
    if not clean_text:
        return ""

    clean_text = re.sub(r'^[\u3000\s]+', '', clean_text)
    split_puncts = ['。', '！', '？', '；', '…', '.', '!', '?']

    if len(clean_text) <= target_len:
        return f"<p>\u3000\u3000{clean_text}</p>"

    parts = []
    p = clean_text

    while len(p) > target_len:
        min_idx = max(0, target_len - window)
        max_idx = min(len(p), target_len + window)
        cut_idx = -1

        search_window = p[min_idx:max_idx]
        for idx in range(len(search_window) - 1, -1, -1):
            if search_window[idx] in split_puncts:
                cut_idx = min_idx + idx + 1
                break

        if cut_idx == -1:
            for idx in range(min_idx - 1, -1, -1):
                if p[idx] in split_puncts:
                    cut_idx = idx + 1
                    break

        if cut_idx == -1 or cut_idx < 30:
            cut_idx = target_len

        chunk = p[:cut_idx].strip()
        p = re.sub(r'^[\u3000\s]+', '', p[cut_idx:].strip())

        if chunk:
            parts.append(f"<p>\u3000\u3000{chunk}</p>")

    if p:
        parts.append(f"<p>\u3000\u3000{p}</p>")

    return "".join(parts)


def optimize_html_for_mobile_soup(soup):
    if not soup.head:
        if soup.html:
            head = soup.new_tag('head')
            soup.html.insert(0, head)
        else:
            new_soup = BeautifulSoup('<html><head></head><body></body></html>', 'html.parser')
            if soup.body:
                new_soup.body.extend(soup.contents)
            else:
                new_soup.body.append(soup)
            soup = new_soup

    viewport_meta = soup.find('meta', attrs={'name': 'viewport'})
    if not viewport_meta:
        viewport_meta = soup.new_tag('meta', attrs={
            'name': 'viewport',
            'content': 'width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no, viewport-fit=cover'
        })
        soup.head.insert(0, viewport_meta)

    mobile_css = """
    <style>
        html, body, section, article, p, table, tr, td, th {
            width: auto !important;
            max-width: 100% !important;
            margin-left: 0 !important;
            margin-right: 0 !important;
            box-sizing: border-box !important;
            overflow-wrap: break-word !important;
            word-break: break-word !important;
        }
        body {
            background-color: transparent !important;
            font-size: 1rem !important;
            padding: 15px 12px !important;
            margin: 0 auto !important;
            max-width: 900px !important;
            font-family: -apple-system, sans-serif !important;
            line-height: 1.6 !important;
            color: #222 !important;
        }
        h1 {
            font-size: 22px !important;
            border-bottom: 2px solid #f0f0f0 !important;
            padding-bottom: 12px !important;
            margin-bottom: 15px !important;
            line-height: 1.4 !important;
        }
        .meta {
            font-size: 13px !important;
            color: #888 !important;
            margin-bottom: 20px !important;
            border-left: 3px solid #eee !important;
            padding-left: 10px !important;
        }
        p {
            margin-top: 0 !important;
            margin-bottom: 0.2em !important;
            text-align: justify !important;
            font-size: 16.5px !important;
            text-indent: 0 !important;
        }
        img {
            max-width: 100% !important;
            height: auto !important;
            display: block;
            margin: 10px auto;
        }
        table {
            width: 100% !important;
            overflow-x: auto;
            display: block;
        }
    </style>
    """
    if not soup.find('style', string=re.compile('box-sizing')):
        css_soup = BeautifulSoup(mobile_css, 'html.parser')
        soup.head.append(css_soup)

    for tag in soup.find_all(style=True):
        styles = tag['style'].lower()
        if any(kw in styles for kw in ['width', 'font-size', 'line-height', 'position', 'left', 'right', 'height']):
            del tag['style']

    return soup


def get_nutstore_auth():
    account = os.environ.get('NUTSTORE_USER') or os.environ.get('DAV_ACCOUNT')
    token = os.environ.get('NUTSTORE_PASSWORD') or os.environ.get('DAV_TOKEN')
    return account, token


def upload_to_memory_queue(file_content, target_filename):
    global GLOBAL_EPUB_ARTICLES
    target_filename = clean_filename(target_filename)
    is_lit = "文学" in target_filename or "SmartBookSplitter" in target_filename

    parts = target_filename.split('_', 2)
    source = parts[1] if len(parts) > 1 else "综合资讯"
    title = parts[2] if len(parts) > 2 else target_filename

    GLOBAL_EPUB_ARTICLES.append({
        "is_lit": is_lit,
        "title": title,
        "source": source,
        "content": file_content,
        "filename": target_filename
    })
    print(f"📥 [B项目内存拦截成功] 已成功归档至待打包队列: {target_filename}", flush=True)
    return True


def upload_to_nutstore(file_content, target_filename):
    return upload_to_memory_queue(file_content, target_filename)


# ==================== 【运行状态报告纯文本输出器 (末尾控制台打印)】 ====================
def print_run_report_plain(date_str, epub_success):
    duration = round(time.time() - RUN_METRICS.start_time, 2)
    stats = RUN_METRICS.stats
    now_bj = get_beijing_time().strftime('%Y-%m-%d %H:%M:%S')
    sync_status = "已成功上传至坚果云" if epub_success else ("未打包或上传失败" if GLOBAL_EPUB_ARTICLES else "无新内容跳过上传")

    lines = [
        "",
        "=" * 56,
        f"📊 今日自动化抓取运行状态报告 ({date_str})",
        "=" * 56,
        f"🕒 执行时间: {now_bj} (北京时间)",
        f"⏱️ 总计耗时: {duration} 秒",
        f"📦 归档篇数: {len(GLOBAL_EPUB_ARTICLES)} 篇",
        f"☁️ 云端同步: {sync_status}",
        "-" * 56,
        "📈 模块统计:",
        f"  • 文学切书: {stats['lit_books']} 本 | 有效切片字数: {stats['lit_chars']} 字"
        + (f" | {stats['lit_book_info']}" if stats.get("lit_book_info") else ""),
        f"  • 资讯抓取: CDT {stats['cdt_count']} 篇 | Wiki特色 {stats['wiki_count']} 篇 | 自定义源 {stats['custom_feeds']} 篇",
    ]

    if stats.get("module_logs"):
        lines.append("-" * 56)
        lines.append("📝 运行摘要:")
        for item in stats["module_logs"]:
            lines.append(f"  • {item}")

    lines.append("-" * 56)
    if stats["errors"]:
        lines.append(f"⚠️ 运行异常记录 ({len(stats['errors'])} 条):")
        for err in stats["errors"]:
            lines.append(f"  • {err}")
    else:
        lines.append("✨ 本次运行全程顺利，无异常错误上报。")

    lines.append("=" * 56)
    print("\n".join(lines), flush=True)


def build_and_upload_daily_epub(date_str):
    global GLOBAL_EPUB_ARTICLES
    if not GLOBAL_EPUB_ARTICLES:
        print("📭 今日没有收集到任何新文章，跳过 EPUB 打包。", flush=True)
        return False

    try:
        from ebooklib import epub
    except ImportError:
        print("❌ 错误: 未安装 ebooklib 库！请在环境中运行 pip install EbookLib six", flush=True)
        return False

    print(f"📚 开始编纂《高桥文学》今日电子报 EPUB，共计 {len(GLOBAL_EPUB_ARTICLES)} 篇内容...", flush=True)

    book = epub.EpubBook()
    epub_filename = f"高桥文学_{date_str}.epub"

    book.set_identifier(f'id_gaoqiao_{date_str}')
    book.set_title(f'高桥文学 ({date_str})')
    book.set_language('zh')
    book.add_author('Python Automation Bot Gaoqiao')

    epub_chapters = []
    for idx, art in enumerate(GLOBAL_EPUB_ARTICLES, 1):
        chapter = epub.EpubHtml(
            title=f"[{art['source']}] {art['title']}"[:50],
            file_name=f'chap_{idx}.xhtml',
            lang='zh'
        )
        chapter.content = art['content']
        book.add_item(chapter)
        epub_chapters.append(chapter)

    book.toc = tuple(epub_chapters)
    nav_html = epub.EpubNav()
    nav_html.title = "目录"
    nav_html.spine_attributes = {'linear': 'no'}
    book.add_item(nav_html)

    book.add_item(epub.EpubItem(
        uid='style_nav',
        file_name='style.css',
        media_type='text/css',
        content='body { font-family: sans-serif; line-height: 1.8; } nav { display: none !important; } h1 { display: none !important; }'
    ))

    book.spine = epub_chapters
    epub_path = f"temp_gaoqiao_{date_str}.epub"
    epub.write_epub(epub_path, book, {})

    account, token = get_nutstore_auth()
    if not account or not token:
        print("❌ 坚果云账号密码未配置，无法上传 EPUB。", flush=True)
        if os.path.exists(epub_path):
            os.remove(epub_path)
        return False

    upload_url = f"https://dav.jianguoyun.com/dav/MyReader/{epub_filename}"
    success = False
    try:
        with open(epub_path, "rb") as f:
            r = requests.put(
                upload_url,
                data=f.read(),
                auth=(account, token),
                timeout=(15, 180),
                verify=False
            )
            if r.status_code in [200, 201, 204]:
                print(f"☁️ 成功推送《高桥文学》至坚果云: {epub_filename}", flush=True)
                success = True
            else:
                RUN_METRICS.log_error("坚果云上传", f"HTTP {r.status_code}")
    except Exception as e:
        RUN_METRICS.log_error("坚果云上传", e)
        print(f"❌ 上传 EPUB 异常: {e}", flush=True)
    finally:
        if os.path.exists(epub_path):
            os.remove(epub_path)

    return success


def wrap_to_rich_html(raw_content, title, link, date_str, source_name="CDT"):
    title = html.unescape(str(title))
    raw_content = re.sub(r'<!\[CDATA\[(.*?)\]\]>', r'\1', str(raw_content), flags=re.DOTALL)
    soup = BeautifulSoup(raw_content, 'html.parser')

    for s in soup(['script', 'style', 'iframe', 'ins', 'button', 'input', 'header', 'footer', 'nav']):
        s.decompose()

    first_header = soup.find(['h1', 'h2', 'h3', 'h4', 'h5', 'h6'])
    if first_header:
        header_text = normalize_text(first_header.get_text())
        clean_title = normalize_text(title)
        if header_text and (header_text in clean_title or clean_title in header_text):
            first_header.decompose()

    for unwanted_box in soup.find_all(class_=re.compile(r'(archive-card|copyright|theme-category|cds-collect)')):
        unwanted_box.decompose()

    noise_keywords = ['cdt 档案卡', 'cdt档案卡', '发表日期', '主题归类', 'cds收藏', '版权说明', '该作品版权归原作者所有', '详细版权说明']
    for tag in soup.find_all(['p', 'div', 'li', 'span', 'blockquote']):
        tag_text = tag.get_text().strip().lower()
        if any(kw in tag_text for kw in noise_keywords):
            tag.decompose()

    for img in soup.find_all('img'):
        img.decompose()

    for caption in soup.find_all(['p', 'figcaption', 'div', 'span']):
        text = caption.get_text().strip()
        if re.search(r'(\|\s*图|图\s*[\n\r]*$|图源|图片来源|数据来源|\[查看文章原图\])', text) or caption.get('class') in ['data-caption', 'image-caption', 'wp-caption-text']:
            caption.decompose()

    for p_tag in soup.find_all(['p', 'div']):
        p_text = p_tag.get_text().strip()
        if p_text and len(p_text) > 20:
            new_html = split_long_paragraph(p_text)
            p_tag.replace_with(BeautifulSoup(new_html, 'html.parser'))

    body_html = str(soup)
    base_html = f"""<!DOCTYPE html><html><head><meta charset="UTF-8"></head>
    <body><h1>{title}</h1><div class="meta">日期: {date_str} | 来源: {source_name}</div>
    <article class="pdf-content-flow">{body_html}</article></body></html>"""

    return str(optimize_html_for_mobile_soup(BeautifulSoup(base_html, 'html.parser')))


def fetch_full_content_if_needed(link, raw_content):
    soup_check = BeautifulSoup(raw_content, 'html.parser')
    clean_text = soup_check.get_text().strip()
    if len(clean_text) < 300 and link and link.startswith('http'):
        try:
            res = safe_get(link, max_seconds=60, headers=get_headers())
            if res.status_code == 200:
                page_soup = BeautifulSoup(res.text, 'html.parser')
                content_div = page_soup.find('div', class_='entry-content') or page_soup.find('article') or page_soup.find('div', class_='post-content')
                if content_div:
                    return str(content_div)
        except Exception:
            pass
    return raw_content


# ==================== 【单书独立切片、“照镜子” 1:1 原貌还原的 SmartBookSplitter】 ====================
class SmartBookSplitter:
    BLOCK_TAGS = ['p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'div', 'blockquote']
    SENTENCE_ENDERS = ['。', '！', '？', '；', '”', '’', '.', '!', '?', ';', '"', "'"]

    def __init__(self, history_file=LIT_HISTORY_FILE, split_size=6000):
        self.history_file = history_file
        self.split_size = split_size
        self.progress_map = self._load_progress()

    def _default_coord(self):
        return {
            "chap_idx": 0,
            "node_idx": 0,
            "char_offset": 0,
            "finished": False
        }

    def _normalize_coord(self, raw_val):
        if isinstance(raw_val, dict):
            return {
                "chap_idx": max(0, int(raw_val.get("chap_idx", 0))),
                "node_idx": max(0, int(raw_val.get("node_idx", 0))),
                "char_offset": max(0, int(raw_val.get("char_offset", 0))),
                "finished": bool(raw_val.get("finished", False))
            }
        if isinstance(raw_val, int) and raw_val == -1:
            return {
                "chap_idx": 0,
                "node_idx": 0,
                "char_offset": 0,
                "finished": True
            }
        try:
            offset_val = max(0, int(raw_val))
        except Exception:
            offset_val = 0
        return {
            "chap_idx": 0,
            "node_idx": 0,
            "char_offset": offset_val,
            "finished": False
        }

    def _load_progress(self):
        progress = {}
        if not os.path.exists(self.history_file):
            return progress
        try:
            with open(self.history_file, "r", encoding="utf-8") as f:
                for line in f:
                    line_s = line.strip()
                    if not line_s or ":" not in line_s:
                        continue
                    key, val_str = line_s.split(":", 1)
                    key = key.strip()
                    val_str = val_str.strip()
                    try:
                        if val_str.startswith("{"):
                            parsed = json.loads(val_str)
                            progress[key] = self._normalize_coord(parsed)
                        else:
                            progress[key] = self._normalize_coord(int(val_str))
                    except Exception:
                        progress[key] = self._default_coord()
        except Exception as e:
            print(f"⚠️ 读取文学切片账本异常: {e}", flush=True)
        return progress

    def _save_progress(self):
        try:
            temp_file = self.history_file + ".tmp"
            with open(temp_file, "w", encoding="utf-8") as f:
                for k, v in self.progress_map.items():
                    coord = self._normalize_coord(v)
                    payload = json.dumps(coord, ensure_ascii=False)
                    f.write(f"{k} : {payload}\n")
            os.replace(temp_file, self.history_file)
        except Exception as e:
            print(f"⚠️ 保存文学切片账本异常: {e}", flush=True)

    def _decode_txt_strict(self, file_path):
        encodings = ['utf-8', 'gb18030', 'gbk', 'utf-16']
        for enc in encodings:
            try:
                with open(file_path, 'r', encoding=enc) as f:
                    return f.read()
            except UnicodeDecodeError:
                continue
            except Exception:
                continue
        raise UnicodeDecodeError("strict_decode", b"", 0, 1, f"无法按 {encodings} 严格解码文件: {file_path}")

    def _is_blank_node(self, node):
        text_no_ws = re.sub(r'[\s\u3000\xa0]+', '', node.get_text())
        if text_no_ws:
            return False
        if node.find(['img', 'svg', 'table']):
            return False
        return True

    def _collect_chapter_block_nodes(self, chap_dom):
        body = chap_dom.find('body') or chap_dom
        direct_blocks = [
            el for el in body.find_all(self.BLOCK_TAGS, recursive=False)
        ]
        if direct_blocks:
            return direct_blocks

        all_blocks = body.find_all(self.BLOCK_TAGS, recursive=True)
        if all_blocks:
            block_ids = set(id(el) for el in all_blocks)
            leaf_blocks = [
                el for el in all_blocks
                if not any(id(desc) in block_ids for desc in el.find_all(self.BLOCK_TAGS, recursive=True))
            ]
            if leaf_blocks:
                return leaf_blocks
            return all_blocks

        return [body]

    def _slice_node_prefix_by_chars(self, node, max_chars):
        """
        在不超过 max_chars 字符的前提下，尽可能保留完整 HTML 结构与句末标点截断。
        返回 (fragment_soup_or_node, actual_char_len)
        """
        if max_chars <= 0:
            return None, 0

        full_text = node.get_text()
        if len(full_text) <= max_chars:
            return copy.deepcopy(node), len(full_text)

        candidate = full_text[:max_chars]
        cut_pos = -1
        for punct in self.SENTENCE_ENDERS:
            idx = candidate.rfind(punct)
            if idx >= 0 and (idx + 1) > cut_pos:
                cut_pos = idx + 1

        target_len = cut_pos if cut_pos > 0 else max_chars
        cloned = copy.deepcopy(node)
        remaining_to_keep = [target_len]

        def _trim_in_place(el):
            for child in list(el.contents):
                if remaining_to_keep[0] <= 0:
                    child.extract()
                elif isinstance(child, str):
                    s = str(child)
                    if len(s) <= remaining_to_keep[0]:
                        remaining_to_keep[0] -= len(s)
                    else:
                        kept = s[:remaining_to_keep[0]]
                        remaining_to_keep[0] = 0
                        child.replace_with(kept)
                else:
                    child_text_len = len(child.get_text())
                    if child_text_len <= remaining_to_keep[0]:
                        remaining_to_keep[0] -= child_text_len
                    else:
                        _trim_in_place(child)

        _trim_in_place(cloned)
        actual_len = len(cloned.get_text())
        return cloned, actual_len

    def _slice_node_suffix_from_offset(self, node, char_offset):
        """
        仅在初始章节与初始段落按 char_offset 跳过已读前缀，保留剩余子结构与样式。
        返回 (fragment_soup_or_node, avail_char_len)
        """
        full_text = node.get_text()
        if char_offset <= 0:
            return copy.deepcopy(node), len(full_text)
        if char_offset >= len(full_text):
            return None, 0

        cloned = copy.deepcopy(node)
        skip_rem = [char_offset]

        def _skip_prefix_in_place(el):
            for child in list(el.contents):
                if skip_rem[0] <= 0:
                    break
                if isinstance(child, str):
                    s = str(child)
                    if len(s) <= skip_rem[0]:
                        skip_rem[0] -= len(s)
                        child.extract()
                    else:
                        kept = s[skip_rem[0]:]
                        skip_rem[0] = 0
                        child.replace_with(kept)
                else:
                    child_len = len(child.get_text())
                    if child_len <= skip_rem[0]:
                        skip_rem[0] -= child_len
                        child.extract()
                    else:
                        _skip_prefix_in_place(child)

        _skip_prefix_in_place(cloned)
        avail_len = len(cloned.get_text())
        return cloned, avail_len

    def _parse_book_to_dom_structure(self, file_path):
        ext = os.path.splitext(file_path)[1].lower()
        styles = []
        chapters = []
        print(f"🔍 [解析器] 开始解析文件: '{file_path}' (类型: {ext})", flush=True)

        if ext == '.epub':
            try:
                with zipfile.ZipFile(file_path, 'r') as z:
                    namelist = z.namelist()
                    opf_path = "content.opf"
                    if "META-INF/container.xml" in namelist:
                        try:
                            c_soup = BeautifulSoup(z.read("META-INF/container.xml"), 'xml')
                            rootfile = c_soup.find('rootfile')
                            if rootfile and rootfile.get('full-path'):
                                opf_path = rootfile.get('full-path')
                        except Exception:
                            pass

                    opf_dir = os.path.dirname(opf_path)
                    if opf_path not in namelist:
                        opf_candidates = [n for n in namelist if n.endswith('.opf')]
                        if opf_candidates:
                            opf_path = opf_candidates[0]
                            opf_dir = os.path.dirname(opf_path)

                    opf_soup = BeautifulSoup(z.read(opf_path), 'xml')
                    manifest = opf_soup.find('manifest')
                    spine = opf_soup.find('spine')

                    id_to_href = {}
                    css_files_ordered = []
                    if manifest:
                        for item in manifest.find_all('item'):
                            item_id = item.get('id')
                            href = item.get('href')
                            media_type = (item.get('media-type') or '').lower()
                            if item_id and href:
                                full_href = urllib.parse.unquote(
                                    os.path.normpath(os.path.join(opf_dir, href)).replace('\\', '/')
                                )
                                id_to_href[item_id] = full_href
                                if 'css' in media_type or href.lower().endswith('.css'):
                                    css_files_ordered.append(full_href)

                    for css_path in css_files_ordered:
                        target_css = css_path if css_path in namelist else next(
                            (n for n in namelist if n.endswith(os.path.basename(css_path))), None
                        )
                        if target_css:
                            try:
                                css_text = z.read(target_css).decode('utf-8')
                            except UnicodeDecodeError:
                                css_text = z.read(target_css).decode('gb18030', errors='replace')
                            if css_text and css_text not in styles:
                                styles.append(css_text)

                    for n in namelist:
                        if n.lower().endswith('.css'):
                            try:
                                css_text = z.read(n).decode('utf-8')
                            except UnicodeDecodeError:
                                css_text = z.read(n).decode('gb18030', errors='replace')
                            if css_text and css_text not in styles:
                                styles.append(css_text)

                    spine_itemrefs = spine.find_all('itemref') if spine else []
                    itemref_ids = [sr.get('idref') for sr in spine_itemrefs if sr.get('idref')]

                    target_files = []
                    if itemref_ids:
                        for i_id in itemref_ids:
                            if i_id in id_to_href:
                                target_files.append(id_to_href[i_id])
                    else:
                        target_files = [n for n in namelist if n.lower().endswith(('.html', '.xhtml', '.htm'))]

                    for idx, chap_file in enumerate(target_files):
                        if chap_file not in namelist:
                            matched = [n for n in namelist if n.endswith(os.path.basename(chap_file))]
                            if matched:
                                chap_file = matched[0]
                            else:
                                continue

                        try:
                            chap_raw = z.read(chap_file)
                            chap_soup = BeautifulSoup(chap_raw, 'html.parser')

                            for style_tag in chap_soup.find_all('style'):
                                s_txt = style_tag.string if style_tag.string else style_tag.get_text()
                                if s_txt and s_txt not in styles:
                                    styles.append(s_txt)

                            chap_dir = os.path.dirname(chap_file)
                            for img in chap_soup.find_all('img'):
                                src = img.get('src') or img.get('xlink:href')
                                if src and not src.startswith('data:'):
                                    clean_src = urllib.parse.unquote(src.split('#')[0].split('?')[0])
                                    img_path = os.path.normpath(os.path.join(chap_dir, clean_src)).replace('\\', '/')
                                    resolved_img = img_path if img_path in namelist else next(
                                        (n for n in namelist if n.endswith(os.path.basename(img_path))), None
                                    )
                                    if resolved_img:
                                        try:
                                            img_data = z.read(resolved_img)
                                            ext_img = os.path.splitext(resolved_img)[1].lower()
                                            mime = "image/jpeg"
                                            if ext_img == '.png':
                                                mime = "image/png"
                                            elif ext_img == '.gif':
                                                mime = "image/gif"
                                            elif ext_img == '.svg':
                                                mime = "image/svg+xml"
                                            elif ext_img == '.webp':
                                                mime = "image/webp"
                                            b64_str = base64.b64encode(img_data).decode('utf-8')
                                            img['src'] = f"data:{mime};base64,{b64_str}"
                                        except Exception:
                                            pass

                            c_title = f"章节 {idx + 1}"
                            h_tag = chap_soup.find(['h1', 'h2', 'h3'])
                            if h_tag and h_tag.get_text().strip():
                                c_title = h_tag.get_text().strip()

                            body_tag = chap_soup.find('body') or chap_soup
                            chapters.append({
                                "chapter_index": idx,
                                "title": c_title,
                                "dom": body_tag
                            })
                        except Exception:
                            pass
                print(f"✅ [EPUB] 解析完成，共成功加载 {len(chapters)} 个章节。", flush=True)
            except Exception as e:
                print(f"⚠️ EPUB 原生 DOM 解析异常: {e}", flush=True)

        elif ext == '.docx':
            try:
                import mammoth
                with open(file_path, "rb") as docx_file:
                    result = mammoth.convert_to_html(docx_file)
                    soup = BeautifulSoup(result.value, 'html.parser')
                    chapters.append({
                        "chapter_index": 0,
                        "title": os.path.basename(file_path),
                        "dom": soup
                    })
                print(f"✅ [DOCX] 解析完成。", flush=True)
            except Exception as e:
                print(f"⚠️ DOCX 解析异常: {e}", flush=True)

        elif ext in ['.html', '.htm', '.md']:
            try:
                raw_text = self._decode_txt_strict(file_path)
                if ext == '.md':
                    import markdown
                    content = markdown.markdown(raw_text)
                else:
                    content = raw_text
                soup = BeautifulSoup(content, 'html.parser')
                for s in soup.find_all('style'):
                    s_txt = s.string if s.string else s.get_text()
                    if s_txt and s_txt not in styles:
                        styles.append(s_txt)
                body = soup.find('body') or soup
                chapters.append({
                    "chapter_index": 0,
                    "title": os.path.basename(file_path),
                    "dom": body
                })
                print(f"✅ [HTML/MD] 解析完成。", flush=True)
            except Exception as e:
                print(f"⚠️ HTML/MD 解析异常: {e}", flush=True)

        if ext == '.txt' or not chapters:
            print(f"📖 [TXT] 正在按严格编码与“照镜子” 1:1 原貌处理...", flush=True)
            text = self._decode_txt_strict(file_path)
            soup = BeautifulSoup("<html><body></body></html>", 'html.parser')
            body = soup.body
            for line in text.splitlines():
                p = soup.new_tag('p', attrs={'class': 'mirror-line'})
                if line == "":
                    p.append(soup.new_tag('br'))
                else:
                    p.string = line
                body.append(p)

            chapters = [{
                "chapter_index": 0,
                "title": os.path.basename(file_path),
                "dom": body
            }]
            print(f"✅ [TXT] “照镜子” 1:1 原貌构建完成（共 {len(text.splitlines())} 行）。", flush=True)

        return {
            "styles": styles,
            "chapters": chapters
        }

    def run_daily_slice(self, date_str):
        global RUN_METRICS

        books = sorted([
            f for f in os.listdir('.')
            if f.lower().endswith(('.txt', '.epub', '.docx', '.html', '.md'))
            and not f.lower().startswith('temp_')
            and "sync_history" not in f.lower()
            and f not in {"requirements.txt", "README.md", "get_daily_article.py"}
        ])

        if not books:
            print("📭 本地未发现受支持的文学书籍文件。", flush=True)
            RUN_METRICS["lit_book_info"] = "本地未发现文学书籍文件"
            RUN_METRICS["errors"].append("本地未发现文学书籍文件")
            return

        print(f"📚 扫描到书架书籍共 {len(books)} 本", flush=True)
        min_target = self.split_size
        max_hard_limit = self.split_size + 1000

        target = None
        for b in books:
            coord = self._normalize_coord(self.progress_map.get(b, self._default_coord()))
            if not coord["finished"]:
                target = b
                break

        if not target:
            print("🎉 本地所有文学书均已阅读完毕！", flush=True)
            RUN_METRICS["lit_book_info"] = "所有文学书均已阅读完毕"
            return

        print(f"📖 锁定当前阅读书籍: 《{target}》", flush=True)
        book_model = self._parse_book_to_dom_structure(target)
        chapters = book_model["chapters"]
        all_styles = book_model["styles"]

        if not chapters:
            done_coord = self._default_coord()
            done_coord["finished"] = True
            self.progress_map[target] = done_coord
            self._save_progress()
            RUN_METRICS["lit_book_info"] = f"书籍章节为空: {target}"
            RUN_METRICS["errors"].append(f"书籍章节为空: {target}")
            return

        start_coord = self._normalize_coord(self.progress_map.get(target, self._default_coord()))
        start_chap_idx = min(start_coord["chap_idx"], len(chapters) - 1)
        start_node_idx = start_coord["node_idx"]
        start_char_offset = start_coord["char_offset"]

        collected_nodes = []
        collected_char_count = 0
        stop_reason_reached_target = False

        next_chap_idx = start_chap_idx
        next_node_idx = start_node_idx
        next_char_offset = start_char_offset
        reached_book_end = True

        for c_idx in range(start_chap_idx, len(chapters)):
            chap = chapters[c_idx]
            block_nodes = self._collect_chapter_block_nodes(chap["dom"])
            n_start = start_node_idx if c_idx == start_chap_idx else 0

            if n_start >= len(block_nodes):
                if c_idx == start_chap_idx:
                    next_chap_idx = c_idx + 1
                    next_node_idx = 0
                    next_char_offset = 0
                continue

            for n_idx in range(n_start, len(block_nodes)):
                node = block_nodes[n_idx]
                is_blank = self._is_blank_node(node)

                if c_idx == start_chap_idx and n_idx == start_node_idx and start_char_offset > 0:
                    if is_blank:
                        collected_nodes.append(copy.deepcopy(node))
                        next_chap_idx = c_idx
                        next_node_idx = n_idx + 1
                        next_char_offset = 0
                        continue

                    sliced_node, avail_len = self._slice_node_suffix_from_offset(node, start_char_offset)
                    if avail_len <= 0 or sliced_node is None:
                        next_chap_idx = c_idx
                        next_node_idx = n_idx + 1
                        next_char_offset = 0
                        continue
                else:
                    if is_blank:
                        if collected_char_count > 0:
                            collected_nodes.append(copy.deepcopy(node))
                        next_chap_idx = c_idx
                        next_node_idx = n_idx + 1
                        next_char_offset = 0
                        continue

                    sliced_node = copy.deepcopy(node)
                    avail_len = len(sliced_node.get_text())

                if avail_len == 0:
                    next_chap_idx = c_idx
                    next_node_idx = n_idx + 1
                    next_char_offset = 0
                    continue

                projected = collected_char_count + avail_len

                if projected <= max_hard_limit:
                    collected_nodes.append(sliced_node)
                    collected_char_count += avail_len

                    # 下一段/下一章严格从第 0 个字符开始读
                    if (n_idx + 1) < len(block_nodes):
                        next_chap_idx = c_idx
                        next_node_idx = n_idx + 1
                        next_char_offset = 0
                    else:
                        next_chap_idx = c_idx + 1
                        next_node_idx = 0
                        next_char_offset = 0

                    if collected_char_count >= min_target:
                        stop_reason_reached_target = True
                        reached_book_end = False
                        break
                else:
                    # 仅当累计字数 < 6000 且加上完整段落超过 7000 硬上限时，在 7000 上限内找句末标点切分
                    remaining_quota = max_hard_limit - collected_char_count
                    if remaining_quota > 0:
                        partial_node, actual_cut_len = self._slice_node_prefix_by_chars(sliced_node, remaining_quota)
                        if partial_node is not None and actual_cut_len > 0:
                            collected_nodes.append(partial_node)
                            collected_char_count += actual_cut_len
                            base_offset = start_char_offset if (c_idx == start_chap_idx and n_idx == start_node_idx) else 0
                            next_chap_idx = c_idx
                            next_node_idx = n_idx
                            next_char_offset = base_offset + actual_cut_len
                        else:
                            base_offset = start_char_offset if (c_idx == start_chap_idx and n_idx == start_node_idx) else 0
                            next_chap_idx = c_idx
                            next_node_idx = n_idx
                            next_char_offset = base_offset
                    else:
                        base_offset = start_char_offset if (c_idx == start_chap_idx and n_idx == start_node_idx) else 0
                        next_chap_idx = c_idx
                        next_node_idx = n_idx
                        next_char_offset = base_offset

                    stop_reason_reached_target = True
                    reached_book_end = False
                    break

            if stop_reason_reached_target:
                break

        if reached_book_end:
            self.progress_map[target] = {
                "chap_idx": len(chapters),
                "node_idx": 0,
                "char_offset": 0,
                "finished": True
            }
            print(f"🎉 书籍《{target}》已全部读完，账本标记 finished=true。", flush=True)
        else:
            if next_chap_idx >= len(chapters):
                self.progress_map[target] = {
                    "chap_idx": len(chapters),
                    "node_idx": 0,
                    "char_offset": 0,
                    "finished": True
                }
            else:
                self.progress_map[target] = {
                    "chap_idx": next_chap_idx,
                    "node_idx": next_node_idx,
                    "char_offset": next_char_offset,
                    "finished": False
                }

        self._save_progress()

        if not collected_nodes or collected_char_count == 0:
            print("📭 今日未收集到任何文学书切片内容。", flush=True)
            RUN_METRICS["lit_book_info"] = "未收集到文学书切片内容"
            RUN_METRICS["errors"].append("未收集到文学书切片内容")
            return

        RUN_METRICS.stats["lit_books"] += 1
        RUN_METRICS.stats["lit_chars"] += collected_char_count
        RUN_METRICS["lit_book_info"] = f"《{target}》({collected_char_count}字)"

        print(f"✨ 单书切片完成: 《{target}》，本次切出字数: {collected_char_count} 字，准备封装上传...", flush=True)

        combined_styles = "\n".join(all_styles)
        mirror_fallback_css = """
p.mirror-line { white-space: pre-wrap; margin: 0; }
img { max-width: 100%; height: auto; }
""".strip()

        body_soup = BeautifulSoup("<div></div>", 'html.parser')
        container_div = body_soup.div
        for node in collected_nodes:
            container_div.append(node)

        display_title_summary = f"《{target}》"
        display_name = f"{date_str}_文学_单书切分_共{collected_char_count}字"

        html_output = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <style>
{combined_styles}
{mirror_fallback_css}
    </style>
</head>
<body>
    <h1>文学联读与切片</h1>
    <div class='meta'>包含来源: {display_title_summary}</div>
    {str(container_div)}
    <div style='color:#999;font-size:13px;margin-top:50px;border-top:1px solid #eee;'>实际切出总字数: {collected_char_count} 字</div>
</body>
</html>"""

        if upload_to_memory_queue(html_output, display_name):
            print(f"✅ 文学书切片拦截成功: {display_name} (总字数: {collected_char_count})", flush=True)


# ==================== 【资讯抓取模块 (60秒硬性超时管控)】 ====================
def get_cdt_all(h_set, date_str):
    print("🌐 正在扫描 CDT 全文...", flush=True)
    count = 0
    for s_name, url in [
        ("中国数字时代", "https://chinadigitaltimes.net/chinese/feed"),
        ("CDT-404文库", "https://chinadigitaltimes.net/chinese/category/404%e6%96%87%e5%ba%93/feed")
    ]:
        try:
            r = safe_get(url, max_seconds=60, headers=get_headers())
            items = BeautifulSoup(r.content, 'xml').find_all('item')
            for item in items[:10]:
                title = html.unescape(item.find('title').text.strip())
                link = item.find('link').text.strip()
                hid = generate_article_md5(s_name, title)
                if hid in h_set:
                    continue
                time.sleep(random.uniform(1.5, 3))
                tag = item.find('content:encoded') or item.find('encoded')
                raw = tag.text if tag else item.find('description').text
                raw = fetch_full_content_if_needed(link, raw)
                if upload_to_memory_queue(wrap_to_rich_html(raw, title, link, date_str, s_name), f"{date_str}_{s_name}_{clean_filename(title)}"):
                    save_to_history_safely(hid, HISTORY_FILE)
                    h_set.add(hid)
                    count += 1
        except Exception as e:
            RUN_METRICS.log_error("CDT抓取", e)
    RUN_METRICS.stats["cdt_count"] = count


def get_wiki_all(date_str):
    print("🌍 抓取 Wiki 今日特色...", flush=True)
    y, m, d = date_str[:4], date_str[4:6], date_str[6:8]
    try:
        resp = safe_get(f"https://zh.wikipedia.org/api/rest_v1/feed/featured/{y}/{m}/{d}", max_seconds=60, headers=get_headers())
        tfa = resp.json().get('tfa')
        if tfa:
            title = html.unescape(tfa.get('title'))
            rich = wrap_to_rich_html(tfa.get('extract_html'), title, tfa['content_urls']['desktop']['page'], date_str, "Wiki今日特色")
            upload_to_memory_queue(rich, f"{date_str}_Wiki_今日特色")
            RUN_METRICS.stats["wiki_count"] = 1
    except Exception as e:
        RUN_METRICS.log_error("Wiki抓取", e)


# Gutenberg 模块保持注销状态
def get_gutenberg_all(h_set, date_str):
    pass


def get_user_custom_feeds(h_set, date_str):
    print("📡 正在抓取全部自定义源...", flush=True)
    feeds_list = [
        ("纽时双语", "https://plink.anyfeeder.com/nytimes/dual"),
        ("田园", "https://feedx.net/rss/tjxz.xml"),
        ("中国日报双语", "https://feedx.net/rss/cddual.xml"),
        ("三联", "https://feedmaker.kindle4rss.com/feeds/lifeweek.weixin.xml"),
        ("今日诗词", "https://v2.jinrishici.com/one.json?client=python-script"),
        ("一言古诗词", "https://v1.hitokoto.cn/?c=i&encode=text"),
    ]
    count = 0
    for s_name, url in feeds_list:
        try:
            r = safe_get(url, max_seconds=60, headers=get_headers())

            if s_name == "今日诗词" and "json" in url:
                data = r.json()
                if data.get("status") == "success":
                    c, o = data['data']['content'], data['data']['origin']
                    title = html.unescape(f"《{o['title']}》-{o['author']}")
                    hid = generate_article_md5(s_name, title)
                    if hid not in h_set and upload_to_memory_queue(wrap_to_rich_html(f"<div style='text-align:center;'><h2>{c}</h2><p>——{o['author']}《{o['title']}》</p></div>", title, "https://www.jinrishici.com/", date_str, s_name), f"{date_str}_{s_name}_{clean_filename(o['title'])}"):
                        save_to_history_safely(hid, HISTORY_FILE)
                        h_set.add(hid)
                        count += 1
                continue

            if s_name == "一言古诗词" and "encode=text" in url:
                title = html.unescape(r.text.strip())
                if title:
                    hid = generate_article_md5(s_name, title)
                    if hid not in h_set and upload_to_memory_queue(wrap_to_rich_html(f"<div style='text-align:center;'><h2>{title}</h2></div>", title, "https://hitokoto.cn/", date_str, s_name), f"{date_str}_{s_name}_{clean_filename(title[:20])}"):
                        save_to_history_safely(hid, HISTORY_FILE)
                        h_set.add(hid)
                        count += 1
                continue

            soup = BeautifulSoup(r.content, 'xml')
            for item in soup.find_all(['item', 'entry'])[:8]:
                title_el = item.find('title')
                if not title_el:
                    continue
                title = html.unescape(title_el.text.strip())
                link_el = item.find('link')
                link = link_el.get('href') if (link_el and link_el.get('href')) else (link_el.text.strip() if link_el else "")
                hid = generate_article_md5(s_name, title)
                if hid in h_set:
                    continue
                tag = item.find(['content:encoded', 'encoded', 'content', 'description', 'summary'])
                raw = tag.text if tag else "（空）"
                if "CDT" in s_name:
                    raw = fetch_full_content_if_needed(link, raw)
                if upload_to_memory_queue(wrap_to_rich_html(raw, title, link, date_str, s_name), f"{date_str}_{s_name}_{clean_filename(title)}"):
                    save_to_history_safely(hid, HISTORY_FILE)
                    h_set.add(hid)
                    count += 1
        except Exception as e:
            RUN_METRICS.log_error(f"自定义源-{s_name}", e)
    RUN_METRICS.stats["custom_feeds"] = count


def main():
    global GLOBAL_EPUB_ARTICLES
    GLOBAL_EPUB_ARTICLES = []

    bj = get_beijing_time()
    today = bj.strftime("%Y%m%d")
    print(f"🚀 任务启动 (高桥文学) | 北京时间: {bj.strftime('%Y-%m-%d %H:%M:%S')}", flush=True)

    h_set = load_history(HISTORY_FILE)

    # 1. 抓取与切分文学书
    SmartBookSplitter().run_daily_slice(today)

    # 2. 抓取资讯与特色
    get_cdt_all(h_set, today)
    get_wiki_all(today)
    get_gutenberg_all(h_set, today)  # 已注销空跑
    get_user_custom_feeds(h_set, today)

    # 3. 编译并推送到坚果云（不含运行状态报告）
    epub_success = build_and_upload_daily_epub(today)
    if epub_success:
        print("🏁 所有任务执行完毕，《高桥文学》电子书已成功同步至坚果云，本地对账本已更新。", flush=True)
    else:
        print("❌ 警告: 《高桥文学》电子书上传坚果云失败！", flush=True)

    # 4. 在脚本最末尾以纯文本打印运行状态报告到 GitHub Actions 日志
    print_run_report_plain(today, epub_success)


if __name__ == "__main__":
    main()
