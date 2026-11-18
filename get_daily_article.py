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
            "errors": []
        }

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

# ==================== 【运行状态报告生成器 (置顶首章)】 ====================
def generate_run_report_html(date_str):
    duration = round(time.time() - RUN_METRICS.start_time, 2)
    stats = RUN_METRICS.stats
    
    error_html = ""
    if stats["errors"]:
        items_str = "".join([f"<li>{html.escape(err)}</li>" for err in stats['errors']])
        error_html = f"<div style='margin-top:20px;color:#c0392b;'><h3>⚠️ 运行异常记录：</h3><ul>{items_str}</ul></div>"
    else:
        error_html = "<p style='color:#27ae60;margin-top:20px;'>✨ 本次运行全程顺利，无异常错误上报。</p>"

    report_content = f"""<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"></head>
<body>
    <h1>📊 今日自动化抓取运行状态报告</h1>
    <div class="meta">日期: {date_str} | 耗时: {duration} 秒</div>
    <article class="pdf-content-flow">
        <div style="background:#f8f9fa;padding:15px;border-radius:8px;border-left:4px solid #3498db;margin-bottom:20px;">
            <p><strong>🕒 任务执行时间：</strong> {get_beijing_time().strftime('%Y-%m-%d %H:%M:%S')}</p>
            <p><strong>📚 文学切书统计：</strong> 共处理 {stats['lit_books']} 本书，有效切片字数：{stats['lit_chars']} 字</p>
            <p><strong>🌐 资讯抓取统计：</strong> CDT 文章 {stats['cdt_count']} 篇 | Wiki特色 1 篇 | 自定义源文章 {stats['custom_feeds']} 篇</p>
            <p><strong>📦 待打包总文章数：</strong> {len(GLOBAL_EPUB_ARTICLES)} 篇</p>
        </div>
        {error_html}
    </article>
</body>
</html>"""
    
    optimized_report = optimize_html_for_mobile_soup(BeautifulSoup(report_content, 'html.parser'))
    
    # 强行插入队列首位作为电子书第一章
    GLOBAL_EPUB_ARTICLES.insert(0, {
        "is_lit": False,
        "title": f"【运行状态报告】{date_str}",
        "source": "系统运维",
        "content": str(optimized_report),
        "filename": f"{date_str}_系统运维_运行状态报告"
    })
    print(f"📊 [运行状态报告] 已成功生成并强行置顶插入队列首位。", flush=True)

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

    # 保证报告（无is_lit或特定排布）在最前，其余按原本排序
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

def run_daily_slice(self, date_str):
        books = sorted([
            f for f in os.listdir('.') 
            if f.lower().endswith(('.txt', '.epub', '.mobi', '.azw3', '.pdf')) 
            and not f.lower().startswith('temp_')
            and "sync_history" not in f.lower() 
            and f not in {"requirements.txt", "README.md", "get_daily_article.py"}
        ])
        
        if not books:
            print("📚 已经没有找到任何文学书籍文件。", flush=True)
            return

        # 严格只找当前需要读的唯一一本书
        target = next((b for b in books if self.progress_map.get(b, 0) != -1), None)
        if not target:
            print("📚 本地所有文学书均已阅读完毕，重置进度循环阅读。", flush=True)
            for b in books:
                self.progress_map[b] = 0
            self._save_progress()
            target = books[0]

        print(f"📖 正在独立读取文学书: 《{target}》", flush=True)
        full_text = self._extract_text(target).replace('\r', '')
        total_len = len(full_text)
        start = self.progress_map.get(target, 0)

        if total_len == 0 or start >= total_len:
            self.progress_map[target] = -1
            self._save_progress()
            print(f"📚 《{target}》 已读完或内容为空，自动跳过。", flush=True)
            return self.run_daily_slice(date_str) # 递归处理下一本

        remaining_in_book = total_len - start
        target_len = self.split_size # 6000字

        # 【核心逻辑】：如果这本书剩下的字数本身就不够 6000 字，有多少切多少，切完直接标 -1！绝对不跨书拼凑
        if remaining_in_book <= target_len:
            end = total_len
            self.progress_map[target] = -1
            print(f"🎉 提示：《{target}》 剩余字数不足 {target_len} 字（剩余 {remaining_in_book} 字），今天全部切完并标记为 -1！", flush=True)
        else:
            # 正常字数充足的情况，按设定的 6000 字切片并寻找标点断句
            end = start + target_len
            found_punc = False
            for i in range(end, min(end + 500, total_len)):
                if full_text[i] == self.primary_punc:
                    end = i + 1
                    found_punc = True
                    break
            if not found_punc:
                for i in range(end, min(end + 500, total_len)):
                    if full_text[i] in self.other_puncts:
                        end = i + 1
                        found_punc = True
                        break
            if not found_punc:
                for i in range(end, max(start, end - 300), -1):
                    if full_text[i] == self.primary_punc or full_text[i] in self.other_puncts:
                        end = i + 1
                        break

            self.progress_map[target] = end
            print(f"✅ 已从《{target}》切分 {end - start} 字 | 最新断点位置: {end}", flush=True)

        self._save_progress()

        slice_text = full_text[start:end]
        total_collected = len(slice_text)

        if total_collected == 0:
            print("📚 今日没有收集到任何文学内容。", flush=True)
            return

        RUN_METRICS.stats["lit_books"] = 1
        RUN_METRICS.stats["lit_chars"] = total_collected

        pure_name = clean_filename(target.split('.')[0])
        display_name = f"{date_str}_文学_{pure_name}_第{start+1}字"

        chunk_html = "".join([split_long_paragraph(p) for p in slice_text.split('\n') if p.strip()])
        raw_output = f"<h1>《{target}》</h1><div style='margin-bottom:30px;'>{chunk_html}</div><div style='color:#999;font-size:13px;margin-top:50px;border-top:1px solid #eee;'>包含书目: 《{target}》(进度:{start+1}-{end}) | 今日总字数: {total_collected}</div>"
        
        optimized_lit_soup = optimize_html_for_mobile_soup(BeautifulSoup(f"<!DOCTYPE html><html><head><meta charset='UTF-8'></head><body>{raw_output}</body></html>", 'html.parser'))
        
        if upload_to_nutstore(str(optimized_lit_soup), display_name):
            print(f"🎉 文学书单书独立切片成功: {display_name} (总字数: {total_collected})", flush=True)

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
    
    # 3. 生成运行状态报告并强行置顶插入队列首位
    generate_run_report_html(today)
    
    # 4. 编译并推送到坚果云
    epub_success = build_and_upload_daily_epub(today)
    if epub_success:
        print("🏁 所有任务执行完毕，《高桥文学》电子书已成功同步至坚果云，本地对账本已更新。", flush=True)
    else:
        print("❌ 警告: 《高桥文学》电子书上传坚果云失败！", flush=True)

if __name__ == "__main__": 
    main()
