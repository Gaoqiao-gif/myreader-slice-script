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
import requests
import urllib3
from bs4 import BeautifulSoup
from deep_translator import GoogleTranslator
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

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

# B项目独立账本文件名，直接留在本地仓库中管理
HISTORY_FILE = "sync_history_b.txt"
LIT_HISTORY_FILE = "sync_history_lit_final_b.txt"

# 全局内存文章暂存队列，用于最后合并打包成 EPUB
GLOBAL_EPUB_ARTICLES = []

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

# ==================== 【本地账本与历史记录模块 (原子写入加固)】 ====================
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
        print(f"⚠️ 保存历史记录异常: {e}")

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

# ==================== 【智能翻译模块】 ====================
def smart_translate(text):
    if not text.strip(): return ""
    for attempt in range(3):
        try:
            res = GoogleTranslator(source='auto', target='zh-CN').translate(text)
            if res: return res
        except Exception:
            time.sleep(1.5)
    return "（翻译暂时不可用）"

# ==================== 【全局 150 字长段落掐断与缩进辅助函数】 ====================
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

# ==================== 【移动端“铁腕”适配核心处理器】 ====================
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
        html, body, div, section, article, p, table, tr, td, th {
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

# ==================== 【坚果云 WebDAV 核心模块 & 内存拦截改造】 ====================
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
    print(f"📥 [B项目内存拦截成功] 已成功归档至待打包队列: {target_filename}")
    return True

# ==================== 【EPUB 电子书编纂核心函数 (B项目专属)】 ====================
def build_and_upload_daily_epub(date_str):
    global GLOBAL_EPUB_ARTICLES
    if not GLOBAL_EPUB_ARTICLES:
        print("📭 今日没有收集到任何新文章，跳过 EPUB 打包。")
        return False

    try:
        from ebooklib import epub
    except ImportError:
        print("❌ 错误: 未安装 ebooklib 库！请在环境中运行 pip install EbookLib six")
        return False

    print(f"📚 开始编纂《高桥文学》今日电子报 EPUB，共计 {len(GLOBAL_EPUB_ARTICLES)} 篇内容...")
    
    book = epub.EpubBook()
    epub_filename = f"高桥文学_{date_str}.epub"
    
    book.set_identifier(f'id_gaoqiao_{date_str}')
    book.set_title(f'高桥文学 ({date_str})')
    book.set_language('zh')
    book.add_author('Python Automation Bot Gaoqiao')

    GLOBAL_EPUB_ARTICLES.sort(key=lambda x: (0 if x['is_lit'] else 1))

    epub_chapters = []
    for idx, art in enumerate(GLOBAL_EPUB_ARTICLES, 1):
        c_title = art['title']
        c_source = art['source']
        c_html = art['content']
        
        chapter = epub.EpubHtml(
            title=f"[{c_source}] {c_title}"[:50],
            file_name=f'chap_{idx}.xhtml',
            lang='zh'
        )
        chapter.content = c_html
        
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
        print("❌ 坚果云账号密码未配置，无法上传 EPUB。")
        return False

    base_url = "https://dav.jianguoyun.com/dav/MyReader"
    upload_url = f"{base_url}/{epub_filename}"
    
    success = False
    try:
        with open(epub_path, "rb") as f:
            r = http_session.put(upload_url, data=f.read(), auth=(account, token), timeout=60, verify=False)
            if r.status_code in [200, 201, 204]:
                print(f"☁️ 成功推送《高桥文学》至坚果云: {epub_filename}")
                success = True
            else:
                print(f"❌ 坚果云响应异常，状态码: {r.status_code}")
    except Exception as e:
        print(f"❌ 上传 EPUB 异常: {e}")
    finally:
        if os.path.exists(epub_path):
            os.remove(epub_path)

    return success

# ==================== 【三个项目通用的终极 HTML 包装器】 ====================
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
    
    base_html = f"""
    <!DOCTYPE html><html><head><meta charset="UTF-8">
    </head>
    <body><h1>{title}</h1><div class="meta">日期: {date_str} | 来源: {source_name}</div>
    <article class="pdf-content-flow">{body_html}</article></body></html>
    """
    
    final_soup = BeautifulSoup(base_html, 'html.parser')
    optimized_soup = optimize_html_for_mobile_soup(final_soup)
    
    return str(optimized_soup)

# ==================== 【通用全文抓取增强函数】 ====================
def fetch_full_content_if_needed(link, raw_content):
    soup_check = BeautifulSoup(raw_content, 'html.parser')
    clean_text = soup_check.get_text().strip()
    
    if len(clean_text) < 300 and link and link.startswith('http'):
        print(f"🔗 检测到内容较短，正在通过原文链接获取完整正文: {link}")
        try:
            res = http_session.get(link, headers=get_headers(), timeout=15, verify=False)
            if res.status_code == 200:
                page_soup = BeautifulSoup(res.text, 'html.parser')
                content_div = (
                    page_soup.find('div', class_='entry-content') or 
                    page_soup.find('article') or 
                    page_soup.find('div', class_='post-content')
                )
                if content_div:
                    return str(content_div)
        except Exception as e:
            print(f"⚠️ 抓取原文链接失败: {e}")
            
    return raw_content

# ==================== 【文学书分段逻辑 (编码加固版)】 ====================
class SmartBookSplitter:
    def __init__(self, history_file=LIT_HISTORY_FILE, split_size=6000):
        self.history_file = history_file
        self.split_size = split_size
        self.progress_map = self._load_progress()
        self.punctuations = {'。', '！', '？', '；', '”', '’', '…', '\n'}

    def _load_progress(self):
        progress = {}
        if os.path.exists(self.history_file):
            with open(self.history_file, "r", encoding="utf-8") as f:
                for line in f:
                    if ":" in line:
                        parts = line.strip().split(":", 1)
                        try:
                            progress[parts[0].strip()] = int(parts[1].strip())
                        except: pass
        return progress

    def _save_progress(self):
        content = "".join([f"{k} : {v}\n" for k, v in self.progress_map.items()])
        with open(self.history_file, "w", encoding="utf-8") as f:
            f.write(content)

    def _extract_text(self, file_path):
        ext = os.path.splitext(file_path)[1].lower()
        
        # 1. 处理老旧 .txt 文件（严谨的编码探测：utf-8 -> gb18030(兼容gbk/gb2312) -> utf-16）
        if ext == '.txt':
            encodings_to_try = ['utf-8', 'gb18030', 'utf-16']
            for enc in encodings_to_try:
                try:
                    # 先用 strict 模式严格测试是否能正确解码
                    with open(file_path, 'r', encoding=enc, errors='strict') as f:
                        text = f.read()
                        print(f"📖 成功以 [{enc}] 编码读取文本文件: {file_path}")
                        return text
                except (UnicodeDecodeError, Exception):
                    continue
            
            # 如果所有标准编码都严格失败，最后用 gb18030 配合 replace 兜底（确保不崩）
            try:
                with open(file_path, 'r', encoding='gb18030', errors='replace') as f:
                    print(f"⚠️ 警告: 文本文件编码复杂，已强制以 [gb18030] 兜底读取: {file_path}")
                    return f.read()
            except Exception as e:
                print(f"❌ 终极读取文本失败: {e}")
                return ""

        # 2. 处理 .epub 文件（防止内部 html 带有老旧编码）
        elif ext == '.epub':
            try:
                text_content = []
                with zipfile.ZipFile(file_path, 'r') as z:
                    for name in sorted(z.namelist()):
                        if name.endswith(('.html', '.xhtml', '.htm')):
                            raw_bytes = z.read(name)
                            # 尝试对 epub 内页字节进行多编码解码
                            html_text = ""
                            for enc in ['utf-8', 'gb18030']:
                                try:
                                    html_text = raw_bytes.decode(enc)
                                    break
                                except UnicodeDecodeError:
                                    continue
                            if not html_text:
                                html_text = raw_bytes.decode('gb18030', errors='replace')
                                
                            soup = BeautifulSoup(html_text, 'html.parser')
                            for el in soup.find_all(['p', 'h1', 'h2', 'div']):
                                t = el.get_text().strip()
                                if t: text_content.append(t)
                print(f"📖 成功解析 EPUB 电子书: {file_path}")
                return "\n\n".join(text_content)
            except Exception as e:
                print(f"❌ 解析 EPUB 失败: {e}")
                pass
                
        return ""

    def run_daily_slice(self, date_str):
        books = sorted([f for f in os.listdir('.') if f.lower().endswith(('.txt', '.epub')) and "sync_history" not in f.lower() and f not in {"requirements.txt", "README.md", "get_daily_article.py"}])
        target = next((b for b in books if self.progress_map.get(b, 0) != -1), None)
        if not target:
            print("📚 没有发现待切分的新文学书。")
            return

        print(f"📖 正在分发文学书版头: 《{target}》")
        full_text = self._extract_text(target).replace('\r', '')
        total = len(full_text)
        start = self.progress_map.get(target, 0)

        if start >= total:
            self.progress_map[target] = -1
            self._save_progress()
            return

        end = min(start + self.split_size, total)
        
        if end < total:
            found_punc = False
            for i in range(end, min(end + 300, total)):
                if full_text[i] in self.punctuations:
                    end = i + 1
                    found_punc = True
                    break
            
            if not found_punc:
                for i in range(end, max(start, end - 300), -1):
                    if full_text[i] in self.punctuations:
                        end = i + 1
                        break

        slice_text = full_text[start:end]
        
        body_html = "".join([split_long_paragraph(p) for p in slice_text.split('\n') if p.strip()])
        pure_name = clean_filename(target.split('.')[0])
        display_name = f"{date_str}_文学_{pure_name}_第{start+1}字"
        
        raw_output = f"<h1>《{target}》 (今日版头第1章)</h1>{body_html}<div style='color:#999;font-size:13px;margin-top:50px;border-top:1px solid #eee;'>进度: {start+1}-{end} | 总计: {total}</div>"
        
        lit_soup = BeautifulSoup(f"<!DOCTYPE html><html><head><meta charset='UTF-8'></head><body>{raw_output}</body></html>", 'html.parser')
        optimized_lit_soup = optimize_html_for_mobile_soup(lit_soup)
        html_output = str(optimized_lit_soup)

        if upload_to_nutstore(html_output, display_name):
            print(f"✅ 高桥文学书切片拦截成功: {display_name} (字数: {len(slice_text)})")
            self.progress_map[target] = end if end < total else -1
            self._save_progress()
            
# ==================== 【资讯抓取模块】 ====================
def get_cdt_all(h_set, date_str):
    print("🌐 正在扫描 CDT 全文...")
    for s_name, url in [("中国数字时代", "https://chinadigitaltimes.net/chinese/feed"), ("CDT-404文库", "https://chinadigitaltimes.net/chinese/category/404%e6%96%87%e5%ba%93/feed")]:
        try:
            r = http_session.get(url, headers=get_headers(), timeout=30, verify=False)
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
                
                filename_with_date = f"{date_str}_{s_name}_{clean_filename(title)}"
                if upload_to_nutstore(wrap_to_rich_html(raw, title, link, date_str, s_name), filename_with_date):
                    save_to_history_safely(hid, HISTORY_FILE)
                    h_set.add(hid)
        except Exception as e:
            print(f"⚠️ CDT 抓取异常: {e}")

def get_wiki_all(date_str):
    print("🌍 抓取 Wiki 今日特色...")
    y, m, d = date_str[:4], date_str[4:6], date_str[6:8]
    url = f"https://zh.wikipedia.org/api/rest_v1/feed/featured/{y}/{m}/{d}"
    try:
        resp = http_session.get(url, headers=get_headers(), timeout=20, verify=False)
        tfa = resp.json().get('tfa')
        if tfa:
            title = html.unescape(tfa.get('title'))
            rich = wrap_to_rich_html(tfa.get('extract_html'), title, tfa['content_urls']['desktop']['page'], date_str, "Wiki今日特色")
            upload_to_nutstore(rich, f"{date_str}_Wiki_今日特色")
    except Exception as e:
        print(f"⚠️ Wiki 抓取异常: {e}")

def get_gutenberg_all(h_set, date_str):
    print("📚 检查 Gutenberg (中英对照精读版)...")
    try:
        r = http_session.get("https://www.gutenberg.org/cache/epub/feeds/today.rss", headers=get_headers(), timeout=30, verify=False)
        items = re.findall(r'<item>(.*?)</item>', r.text, re.DOTALL)
        for it in items[:2]:
            title_match = re.search(r'<title>(.*?)</title>', it, re.DOTALL)
            link_match = re.search(r'<link>(.*?)</link>', it, re.DOTALL)
            if not title_match or not link_match: continue
            title = html.unescape(title_match.group(1).strip())
            link = link_match.group(1).strip()
            
            hid = generate_article_md5("Guten", title)
            if hid in h_set: continue
            
            bid_match = re.search(r'/ebooks/(\d+)', link)
            if not bid_match: continue
            bid = bid_match.group(1)
            
            tr = http_session.get(f"https://www.gutenberg.org/cache/epub/{bid}/pg{bid}.txt", headers=get_headers(), timeout=20, verify=False)
            if tr.status_code != 200:
                tr = http_session.get(f"https://www.gutenberg.org/files/{bid}/{bid}-0.txt", headers=get_headers(), timeout=20, verify=False)
                
            if tr.status_code == 200:
                paras = [p.strip() for p in tr.text[:15000].split('\n\n') if len(p.strip()) > 30]
                bil_list = []
                for p in paras[:10]:
                    clean_p = p.replace('\n', ' ')
                    if not clean_p: continue
                    if len(clean_p) > 150:
                        sub_text = clean_p[:150]
                        last_punct = max(sub_text.rfind('.'), sub_text.rfind('?'), sub_text.rfind('!'))
                        if last_punct > 50:
                            clean_p = sub_text[:last_punct + 1]
                        else:
                            clean_p = sub_text + "..."
                    
                    zh_trans = smart_translate(clean_p)
                    
                    formatted_en = split_long_paragraph(clean_p)
                    formatted_zh = split_long_paragraph(zh_trans)
                    
                    bil_list.append(
                        f"<div style='margin-bottom: 25px;'>"
                        f"<div style='font-size: 17.5px; line-height: 1.8; color: #2c3e50;'>{formatted_en}</div>"
                        f"<div style='font-size: 16.5px; line-height: 1.8; color: #7f8c8d; margin-top: 8px;'>{formatted_zh}</div>"
                        f"</div>"
                    )
                    time.sleep(1)
                
                bil = "".join(bil_list)
                filename_with_date = f"{date_str}_Guten_精读_{clean_filename(title)}"
                if upload_to_nutstore(wrap_to_rich_html(bil, f"[英语精读] {title}", link, date_str, "Gutenberg"), filename_with_date):
                    save_to_history_safely(hid, HISTORY_FILE)
                    h_set.add(hid)
                    print(f"✅ Gutenberg 英语精读版拦截成功: {title}")
    except Exception as e:
        print(f"⚠️ Gutenberg 抓取异常: {e}")

def get_user_custom_feeds(h_set, date_str):
    print("📡 正在抓取全部自定义源...")
    feeds_list = [
        ("纽时双语", "https://plink.anyfeeder.com/nytimes/dual"),
        ("田园", "https://feedx.net/rss/tjxz.xml"),
        ("中国日报双语", "https://feedx.net/rss/cddual.xml"),
        ("三联", "https://feedmaker.kindle4rss.com/feeds/lifeweek.weixin.xml"),
        ("今日诗词", "https://v2.jinrishici.com/one.json?client=python-script"),
        ("一言古诗词", "https://v1.hitokoto.cn/?c=i&encode=text"),
    ]
    
    for s_name, url in feeds_list:
        try:
            r = http_session.get(url, headers=get_headers(), timeout=30, verify=False)
            if not r.encoding or r.encoding.lower() == 'iso-8859-1': r.encoding = r.apparent_encoding
            
            if s_name == "今日诗词" and "json" in url:
                data = r.json()
                if data.get("status") == "success":
                    c, o = data['data']['content'], data['data']['origin']
                    title = html.unescape(f"《{o['title']}》-{o['author']}")
                    hid = generate_article_md5(s_name, title)
                    if hid not in h_set:
                        raw = f"<div style='text-align:center;'><h2>{c}</h2><p>——{o['author']}《{o['title']}》</p></div>"
                        filename_with_date = f"{date_str}_{s_name}_{clean_filename(o['title'])}"
                        if upload_to_nutstore(wrap_to_rich_html(raw, title, "https://www.jinrishici.com/", date_str, s_name), filename_with_date):
                            save_to_history_safely(hid, HISTORY_FILE)
                            h_set.add(hid)
                continue

            if s_name == "一言古诗词" and "encode=text" in url:
                title = html.unescape(r.text.strip())
                if title:
                    hid = generate_article_md5(s_name, title)
                    if hid not in h_set:
                        raw = f"<div style='text-align:center;'><h2>{title}</h2></div>"
                        filename_with_date = f"{date_str}_{s_name}_{clean_filename(title[:20])}"
                        if upload_to_nutstore(wrap_to_rich_html(raw, title, "https://hitokoto.cn/", date_str, s_name), filename_with_date):
                            save_to_history_safely(hid, HISTORY_FILE)
                            h_set.add(hid)
                continue

            soup = BeautifulSoup(r.content, 'xml')
            for item in soup.find_all(['item', 'entry'])[:8]:
                title_el = item.find('title')
                if not title_el: continue
                title = html.unescape(title_el.text.strip())
                
                link_el = item.find('link')
                link = ""
                if link_el:
                    link = link_el.get('href') if link_el.get('href') else link_el.text.strip()
                
                hid = generate_article_md5(s_name, title)
                if hid in h_set: continue
                
                tag = item.find(['content:encoded', 'encoded', 'content', 'description', 'summary'])
                raw = tag.text if tag else "（空）"
                
                if "CDT" in s_name:
                    raw = fetch_full_content_if_needed(link, raw)
                
                filename_with_date = f"{date_str}_{s_name}_{clean_filename(title)}"
                if upload_to_nutstore(wrap_to_rich_html(raw, title, link, date_str, s_name), filename_with_date):
                    save_to_history_safely(hid, HISTORY_FILE)
                    h_set.add(hid)
        except Exception as e:
            print(f"⚠️ 自定义源 [{s_name}] 抓取或解析失败: {e}")
            continue

# ==================== 【主控调度模块】 ====================
def main():
    bj = get_beijing_time()
    today = bj.strftime("%Y%m%d")
    print(f"🚀 任务启动 (高桥文学) | 北京时间: {bj.strftime('%Y-%m-%d %H:%M:%S')}")
    
    h_set = load_history(HISTORY_FILE)
    
    SmartBookSplitter().run_daily_slice(today)
    get_cdt_all(h_set, today)
    get_wiki_all(today)
    get_gutenberg_all(h_set, today)
    get_user_custom_feeds(h_set, today)
    
    epub_success = build_and_upload_daily_epub(today)
    
    if epub_success:
        print("🏁 所有任务执行完毕，《高桥文学》电子书已成功同步至坚果云，本地对账本已更新。")
    else:
        print("❌ 警告: 《高桥文学》电子书上传坚果云失败！")

if __name__ == "__main__": 
    main()
