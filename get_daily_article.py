import os
import re
import time
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
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
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
            "cdt_count": 0,
            "wiki_count": 0,
            "custom_feeds": 0,
            "errors": [],
            "module_logs": []
        }

    def record(self, module, ok, detail=""):
        status = "成功" if ok else "提示/未执行"
        entry = f"[{module}] {status}" + (f": {detail}" if detail else "")
        self.stats["module_logs"].append(entry)

    def log_error(self, source, err_msg):
        self.stats["errors"].append(f"[{source}] {str(err_msg)}")
        print(f"⚠️ [{source}] 抓取异常: {err_msg}", flush=True)

RUN_METRICS = RunMetrics()

def get_robust_session():
    session = requests.Session()
    retries = Retry(total=3, backoff_factor=1, status_forcelist=[500, 502, 503, 504])
    session.mount('https://', HTTPAdapter(max_retries=retries))
    session.mount('http://', HTTPAdapter(max_retries=retries))
    return session

http_session = get_robust_session()

def get_headers():
    return {
        'User-Agent': random.choice(USER_AGENTS),
        'Accept-Language': 'zh-CN,zh;q=0.9',
        'Referer': 'https://chinadigitaltimes.net/'
    }

def get_beijing_time():
    utc_now = datetime.datetime.now(datetime.timezone.utc)
    return utc_now.astimezone(datetime.timezone(datetime.timedelta(hours=8)))

def load_history(filename=HISTORY_FILE):
    if not os.path.exists(filename): return set()
    try:
        with open(filename, "r", encoding="utf-8") as f:
            return set(line.strip() for line in f if line.strip())
    except: return set()

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
    if not title: return "untitled"
    name = html.unescape(str(title))
    name = re.sub(r'[\\/:*?"<>|]', '_', name)
    return name[:60].strip()

def normalize_text(text):
    if not text: return ""
    return re.sub(r'[^\w\u4e00-\u9fa5]', '', str(text)).lower()

def generate_article_md5(source_name, title):
    norm_title = normalize_text(title)
    raw_str = f"{source_name}_{norm_title}"
    return hashlib.md5(raw_str.encode('utf-8')).hexdigest()

# 翻译模块保持注销状态
def smart_translate(text):
    return "（翻译模块已按规则注销）"

def split_long_paragraph(text, target_len=150, window=10):
    if not text: return ""
    clean_text = text.strip()
    if not clean_text: return ""

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

def upload_to_nutstore(file_content, target_filename):
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
        f"  • 文学切书: {stats['lit_books']} 本 | 有效切片字数: {stats['lit_chars']} 字",
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
        return False

    upload_url = f"https://dav.jianguoyun.com/dav/MyReader/{epub_filename}"
    success = False
    try:
        with open(epub_path, "rb") as f:
            # 严格限制 60 秒网络超时
            r = http_session.put(upload_url, data=f.read(), auth=(account, token), timeout=60, verify=False)
            if r.status_code in [200, 201, 204]:
                print(f"☁️ 成功推送《高桥文学》至坚果云: {epub_filename}", flush=True)
                success = True
    except Exception as e:
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
            # 严格限制 60 秒网络超时
            res = http_session.get(link, headers=get_headers(), timeout=60, verify=False)
            if res.status_code == 200:
                page_soup = BeautifulSoup(res.text, 'html.parser')
                content_div = page_soup.find('div', class_='entry-content') or page_soup.find('article') or page_soup.find('div', class_='post-content')
                if content_div: return str(content_div)
        except: pass
    return raw_content

# ==================== 【单书独立切片、绝不跨书拼凑的 SmartBookSplitter】 ====================
class SmartBookSplitter:
    def __init__(self, history_file=LIT_HISTORY_FILE, split_size=6000):
        self.history_file = history_file
        self.split_size = split_size
        self.progress_map = self._load_progress()

    def _load_progress(self):
        progress = {}
        if os.path.exists(self.history_file):
            with open(self.history_file, "r", encoding="utf-8") as f:
                for line in f:
                    if ":" in line:
                        parts = line.strip().split(":", 1)
                        key = parts[0].strip()
                        val_str = parts[1].strip()
                        try:
                            if "{" in val_str:
                                import json
                                progress[key] = json.loads(val_str)
                            else:
                                val_int = int(val_str)
                                if val_int == -1:
                                    progress[key] = -1
                                else:
                                    progress[key] = {"chap_idx": 0, "node_idx": 0, "char_offset": val_int}
                        except: 
                            progress[key] = {"chap_idx": 0, "node_idx": 0, "char_offset": 0}
        return progress

    def _save_progress(self):
        import json
        content = "".join([f"{k} : {json.dumps(v) if isinstance(v, dict) else v}\n" for k, v in self.progress_map.items()])
        with open(self.history_file, "w", encoding="utf-8") as f:
            f.write(content)

    def _parse_book_to_dom_structure(self, file_path):
        ext = os.path.splitext(file_path)[1].lower()
        styles = []
        chapters = []
        print(f"🔍 [解析器] 开始解析文件: '{file_path}' (类型: {ext})", flush=True)

        if ext == '.epub':
            try:
                import base64
                with zipfile.ZipFile(file_path, 'r') as z:
                    namelist = z.namelist()
                    opf_path = "content.opf"
                    if "META-INF/container.xml" in namelist:
                        try:
                            c_soup = BeautifulSoup(z.read("META-INF/container.xml"), 'xml')
                            rootfile = c_soup.find('rootfile')
                            if rootfile and rootfile.get('full-path'):
                                opf_path = rootfile.get('full-path')
                        except: pass

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
                    if manifest:
                        for item in manifest.find_all('item'):
                            item_id = item.get('id')
                            href = item.get('href')
                            media_type = item.get('media-type', '')
                            if item_id and href:
                                full_href = os.path.normpath(os.path.join(opf_dir, href)).replace('\\', '/')
                                id_to_href[item_id] = full_href
                                if 'css' in media_type or href.endswith('.css'):
                                    try:
                                        css_text = z.read(full_href).decode('utf-8', errors='ignore')
                                        styles.append(css_text)
                                    except: pass

                    spine_itemrefs = spine.find_all('itemref') if spine else []
                    itemref_ids = [sr.get('idref') for sr in spine_itemrefs if sr.get('idref')]
                    
                    target_files = []
                    if itemref_ids:
                        for i_id in itemref_ids:
                            if i_id in id_to_href:
                                target_files.append(id_to_href[i_id])
                    else:
                        target_files = [n for n in namelist if n.endswith(('.html', '.xhtml', '.htm'))]

                    for idx, chap_file in enumerate(target_files):
                        if chap_file not in namelist:
                            matched = [n for n in namelist if n.endswith(os.path.basename(chap_file))]
                            if matched: chap_file = matched[0]
                            else: continue
                        
                        try:
                            chap_raw = z.read(chap_file)
                            chap_soup = BeautifulSoup(chap_raw, 'html.parser')

                            for style_tag in chap_soup.find_all('style'):
                                if style_tag.string:
                                    styles.append(style_tag.string)

                            chap_dir = os.path.dirname(chap_file)
                            for img in chap_soup.find_all('img'):
                                src = img.get('src') or img.get('xlink:href')
                                if src and not src.startswith('data:'):
                                    img_path = os.path.normpath(os.path.join(chap_dir, src)).replace('\\', '/')
                                    if img_path in namelist:
                                        try:
                                            img_data = z.read(img_path)
                                            mime = "image/jpeg"
                                            if img_path.lower().endswith('.png'): mime = "image/png"
                                            elif img_path.lower().endswith('.gif'): mime = "image/gif"
                                            elif img_path.lower().endswith('.svg'): mime = "image/svg+xml"
                                            b64_str = base64.b64encode(img_data).decode('utf-8')
                                            img['src'] = f"data:{mime};base64,{b64_str}"
                                        except: pass

                            c_title = f"章节 {idx+1}"
                            h_tag = chap_soup.find(['h1', 'h2', 'h3'])
                            if h_tag: c_title = h_tag.get_text().strip()

                            body_tag = chap_soup.find('body') or chap_soup
                            chapters.append({
                                "chapter_index": idx,
                                "title": c_title,
                                "dom": body_tag
                            })
                        except: pass
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
                with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()
                    if ext == '.md':
                        import markdown
                        content = markdown.markdown(content)
                    soup = BeautifulSoup(content, 'html.parser')
                    for s in soup.find_all('style'):
                        if s.string: styles.append(s.string)
                    body = soup.find('body') or soup
                    chapters.append({
                        "chapter_index": 0,
                        "title": os.path.basename(file_path),
                        "dom": body
                    })
                print(f"✅ [HTML/MD] 解析完成。", flush=True)
            except Exception as e:
                print(f"⚠️ HTML/MD 解析异常: {e}", flush=True)

        if not chapters:
            print(f"📖 [TXT] 正在按纯文本处理并自动识别章节...", flush=True)
            text = ""
            for enc in ['utf-8', 'gbk', 'utf-16']:
                try:
                    with open(file_path, 'r', encoding=enc, errors='ignore') as f:
                        text = f.read()
                        break
                except: continue
            
            if text:
                soup = BeautifulSoup("<html><body><div></div></body></html>", 'html.parser')
                body = soup.body
                current_div = body.div
                
                lines = text.split('\n')
                for line in lines:
                    line_str = line.strip()
                    if not line_str: continue
                    if re.match(r'^\s*(第[一二三四五六七八九十百千万0-9]+[章节回卷集部篇].*)', line_str):
                        current_div = soup.new_tag('div')
                        body.append(current_div)
                        h2 = soup.new_tag('h2')
                        h2.string = line_str
                        current_div.append(h2)
                    else:
                        p = soup.new_tag('p')
                        p.string = f"\u3000\u3000{line_str}"
                        current_div.append(p)
                
                chapters.append({
                    "chapter_index": 0,
                    "title": os.path.basename(file_path),
                    "dom": body
                })
            print(f"✅ [TXT] 结构化构建完成。", flush=True)

        return {
            "styles": list(set(styles)),
            "chapters": chapters
        }

    def run_daily_slice(self, date_str):
        books = sorted([
            f for f in os.listdir('.') 
            if f.lower().endswith(('.epub', '.docx', '.txt', '.html', '.md')) 
            and not f.lower().startswith('temp_')
            and "sync_history" not in f.lower() 
            and f not in {"requirements.txt", "README.md", "get_daily_article.py"}
        ])

        if not books:
            print("📭 本地未发现受支持的文学书籍文件。", flush=True)
            RUN_METRICS.record("book_slice", False, "本地未发现文学书籍文件")
            return

        print(f"📚 扫描到书架书籍共 {len(books)} 本", flush=True)
        target_len = self.split_size

        target = next((b for b in books if self.progress_map.get(b, 0) != -1), None)
        if not target:
            print("🎉 本地所有文学书均已阅读完毕！", flush=True)
            RUN_METRICS.record("book_slice", True, "所有文学书均已阅读完毕")
            return

        print(f"📖 锁定当前阅读书籍: 《{target}》", flush=True)
        book_model = self._parse_book_to_dom_structure(target)
        chapters = book_model["chapters"]
        all_styles = book_model["styles"]

        if not chapters:
            self.progress_map[target] = -1
            self._save_progress()
            RUN_METRICS.record("book_slice", False, f"书籍章节为空: {target}")
            return

        book_prog = self.progress_map.get(target, 0)
        if isinstance(book_prog, dict):
            curr_chap_idx = book_prog.get("chap_idx", 0)
            start_node_idx = book_prog.get("node_idx", 0)
            start_offset = book_prog.get("char_offset", 0)
        else:
            curr_chap_idx = 0
            start_node_idx = 0
            start_offset = 0

        accumulated_dom_nodes = []
        collected_char_count = 0
        book_finished = False

        while curr_chap_idx < len(chapters):
            chapter = chapters[curr_chap_idx]
            chap_dom = chapter["dom"]
            
            block_elements = chap_dom.find_all(['p', 'div', 'h1', 'h2', 'h3', 'h4', 'li', 'blockquote'], recursive=False)
            if not block_elements:
                block_elements = chap_dom.find_all(['p', 'div', 'h1', 'h2', 'h3', 'h4', 'li', 'blockquote'], recursive=True)
            if not block_elements:
                block_elements = [chap_dom]

            n_idx = start_node_idx if curr_chap_idx == start_node_idx else 0
            
            while n_idx < len(block_elements):
                node = block_elements[n_idx]
                node_text = node.get_text()
                node_offset = start_offset if (curr_chap_idx == curr_chap_idx and n_idx == n_idx) else 0
                
                text_chunk = node_text[node_offset:]
                chunk_len = len(text_chunk)
                
                if chunk_len == 0:
                    n_idx += 1
                    start_offset = 0
                    continue

                if collected_char_count < target_len:
                    accumulated_dom_nodes.append((all_styles, node))
                    collected_char_count += chunk_len
                    n_idx += 1
                    start_offset = 0
                elif target_len <= collected_char_count < target_len + 1000:
                    accumulated_dom_nodes.append((all_styles, node))
                    collected_char_count += chunk_len
                    n_idx += 1
                    start_offset = 0
                    book_finished = True
                    break
                else:
                    remaining_needed = (target_len + 500) - collected_char_count
                    sub_text = text_chunk[:max(100, remaining_needed)]
                    
                    cut_pos = -1
                    for punct in ['。', '.', '！', '？', '；']:
                        pos = sub_text.rfind(punct)
                        if pos > 20:
                            cut_pos = pos + 1
                            break
                    
                    if cut_pos == -1 or cut_pos == 0:
                        cut_pos = min(len(text_chunk), max(1, remaining_needed))

                    import copy
                    node_copy = copy.copy(node)
                    node_copy.string = text_chunk[:cut_pos]

                    accumulated_dom_nodes.append((all_styles, node_copy))
                    collected_char_count += cut_pos

                    start_offset = node_offset + cut_pos
                    book_finished = True
                    break

            if n_idx >= len(block_elements):
                curr_chap_idx += 1
                start_node_idx = 0
                start_offset = 0
                if curr_chap_idx >= len(chapters):
                    self.progress_map[target] = -1
                    print(f"🎉 书籍《{target}》已全部读完，标记为 -1。", flush=True)
                    break
            else:
                self.progress_map[target] = {
                    "chap_idx": curr_chap_idx,
                    "node_idx": n_idx,
                    "char_offset": start_offset
                }

            if book_finished or collected_char_count >= target_len:
                break

        if curr_chap_idx >= len(chapters):
            self.progress_map[target] = -1

        self._save_progress()

        if not accumulated_dom_nodes:
            print("📭 今日未收集到任何文学书切片内容。", flush=True)
            RUN_METRICS.record("book_slice", False, "未收集到文学书切片内容")
            return

        print(f"✨ 单书切片完成: 《{target}》，本次切出字数: {collected_char_count} 字，准备封装上传...", flush=True)

        combined_styles = "".join(list(set([s for styles, _ in accumulated_dom_nodes for s in styles])))
        body_content_soup = BeautifulSoup("<div></div>", 'html.parser')
        container_div = body_content_soup.div

        for styles, node in accumulated_dom_nodes:
            container_div.append(node)

        display_title_summary = f"《{target}》"
        display_name = f"{date_str}_文学_单书切分_共{collected_char_count}字"

        raw_output_html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <style>{combined_styles}</style>
        </head>
        <body>
            <h1>文学联读与切片</h1>
            <div class='meta'>包含来源: {display_title_summary}</div>
            {str(container_div)}
            <div style='color:#999;font-size:13px;margin-top:50px;border-top:1px solid #eee;'>实际切出总字数: {collected_char_count} 字</div>
        </body>
        </html>
        """

        lit_soup = BeautifulSoup(raw_output_html, 'html.parser')
        optimized_lit_soup = optimize_html_for_mobile_soup(lit_soup)
        html_output = str(optimized_lit_soup)

        if upload_to_nutstore(html_output, display_name):
            print(f"✅ 文学书切片拦截成功: {display_name} (总字数: {collected_char_count})", flush=True)
            RUN_METRICS.stats["lit_books"] += 1
            RUN_METRICS.stats["lit_chars"] += collected_char_count
            RUN_METRICS.record("book_slice", True, f"成功切片书籍《{target}》共 {collected_char_count} 字")

# ==================== 【资讯抓取模块 (60秒超时管控)】 ====================
def get_cdt_all(h_set, date_str):
    print("🌐 正在扫描 CDT 全文...", flush=True)
    count = 0
    for s_name, url in [("中国数字时代", "https://chinadigitaltimes.net/chinese/feed"), ("CDT-404文库", "https://chinadigitaltimes.net/chinese/category/404%e6%96%87%e5%ba%93/feed")]:
        try:
            # 严格限制 60 秒网络超时
            r = http_session.get(url, headers=get_headers(), timeout=60, verify=False)
            if not r.encoding or r.encoding.lower() == 'iso-8859-1': r.encoding = r.apparent_encoding
            items = BeautifulSoup(r.content, 'xml').find_all('item')
            for item in items[:10]:
                title = html.unescape(item.find('title').text.strip())
                link = item.find('link').text.strip()
                hid = generate_article_md5(s_name, title)
                if hid in h_set: continue
                time.sleep(random.uniform(1.5, 3))
                tag = item.find('content:encoded') or item.find('encoded')
                raw = tag.text if tag else item.find('description').text
                raw = fetch_full_content_if_needed(link, raw)
                if upload_to_nutstore(wrap_to_rich_html(raw, title, link, date_str, s_name), f"{date_str}_{s_name}_{clean_filename(title)}"):
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
        # 严格限制 60 秒网络超时
        resp = http_session.get(f"https://zh.wikipedia.org/api/rest_v1/feed/featured/{y}/{m}/{d}", headers=get_headers(), timeout=60, verify=False)
        tfa = resp.json().get('tfa')
        if tfa:
            title = html.unescape(tfa.get('title'))
            rich = wrap_to_rich_html(tfa.get('extract_html'), title, tfa['content_urls']['desktop']['page'], date_str, "Wiki今日特色")
            upload_to_nutstore(rich, f"{date_str}_Wiki_今日特色")
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
            # 严格限制 60 秒网络超时
            r = http_session.get(url, headers=get_headers(), timeout=60, verify=False)
            if not r.encoding or r.encoding.lower() == 'iso-8859-1': r.encoding = r.apparent_encoding
            
            if s_name == "今日诗词" and "json" in url:
                data = r.json()
                if data.get("status") == "success":
                    c, o = data['data']['content'], data['data']['origin']
                    title = html.unescape(f"《{o['title']}》-{o['author']}")
                    hid = generate_article_md5(s_name, title)
                    if hid not in h_set and upload_to_nutstore(wrap_to_rich_html(f"<div style='text-align:center;'><h2>{c}</h2><p>——{o['author']}《{o['title']}》</p></div>", title, "https://www.jinrishici.com/", date_str, s_name), f"{date_str}_{s_name}_{clean_filename(o['title'])}"):
                        save_to_history_safely(hid, HISTORY_FILE); h_set.add(hid)
                        count += 1
                continue

            if s_name == "一言古诗词" and "encode=text" in url:
                title = html.unescape(r.text.strip())
                if title:
                    hid = generate_article_md5(s_name, title)
                    if hid not in h_set and upload_to_nutstore(wrap_to_rich_html(f"<div style='text-align:center;'><h2>{title}</h2></div>", title, "https://hitokoto.cn/", date_str, s_name), f"{date_str}_{s_name}_{clean_filename(title[:20])}"):
                        save_to_history_safely(hid, HISTORY_FILE); h_set.add(hid)
                        count += 1
                continue

            soup = BeautifulSoup(r.content, 'xml')
            for item in soup.find_all(['item', 'entry'])[:8]:
                title_el = item.find('title')
                if not title_el: continue
                title = html.unescape(title_el.text.strip())
                link_el = item.find('link')
                link = link_el.get('href') if (link_el and link_el.get('href')) else (link_el.text.strip() if link_el else "")
                hid = generate_article_md5(s_name, title)
                if hid in h_set: continue
                tag = item.find(['content:encoded', 'encoded', 'content', 'description', 'summary'])
                raw = tag.text if tag else "（空）"
                if "CDT" in s_name: raw = fetch_full_content_if_needed(link, raw)
                if upload_to_nutstore(wrap_to_rich_html(raw, title, link, date_str, s_name), f"{date_str}_{s_name}_{clean_filename(title)}"):
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
    get_gutenberg_all(h_set, today) # 已注销空跑
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
