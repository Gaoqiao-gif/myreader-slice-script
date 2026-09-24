import os
import re
import sys
import datetime
import zipfile
import requests
import urllib3
from bs4 import BeautifulSoup

# ==================== 【1. 环境配置】 ====================
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
if sys.version_info >= (3, 7):
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

LIT_HISTORY_FILE = "sync_history_lit_final.txt"
GLOBAL_EPUB_ARTICLES = []

def get_beijing_time():
    utc_now = datetime.datetime.now(datetime.timezone.utc)
    return utc_now.astimezone(datetime.timezone(datetime.timedelta(hours=8)))

# ==================== 【坚果云 WebDAV 模块】 ====================
def get_nutstore_auth():
    # 精准对齐你的 GitHub Secrets 名字
    account = os.environ.get('JIANGUOYUN_USER')
    token = os.environ.get('JIANGUOYUN_PASS')
    return account, token

def upload_to_memory_queue(file_content, target_filename):
    global GLOBAL_EPUB_ARTICLES
    
    parts = target_filename.split('_', 2)
    source = parts[1] if len(parts) > 1 else "文学切片"
    title = parts[2] if len(parts) > 2 else target_filename

    GLOBAL_EPUB_ARTICLES.append({
        "is_lit": True,
        "title": title,
        "source": source,
        "content": file_content,
        "filename": target_filename
    })
    print(f" [内存拦截成功] 已成功归档至待打包队列: {target_filename}")
    return True

# ==================== 【EPUB 电子书编纂核心函数】 ====================
def build_and_upload_daily_epub(date_str):
    global GLOBAL_EPUB_ARTICLES
    if not GLOBAL_EPUB_ARTICLES:
        print(" 今日没有切片内容，跳过 EPUB 打包。")
        return False

    try:
        from ebooklib import epub
    except ImportError:
        print("❌ 错误: 未安装 ebooklib 库！请运行 pip install EbookLib")
        return False

    print(f" 开始编纂今日电子书 EPUB，共计 {len(GLOBAL_EPUB_ARTICLES)} 个章节...")
    
    book = epub.EpubBook()
    epub_filename = f"每日文学精选_{date_str}.epub"
    
    book.set_identifier(f'id_{date_str}')
    book.set_title(f'每日文学精选 ({date_str})')
    book.set_language('zh')
    book.add_author('Python Automation Bot')

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
        content='body { font-family: -apple-system, sans-serif; line-height: 1.8; } nav { display: none !important; } h1 { display: none !important; }'
    ))
    
    book.spine = epub_chapters

    epub_path = f"temp_{date_str}.epub"
    epub.write_epub(epub_path, book, {})

    account, token = get_nutstore_auth()
    if not account or not token:
        print("❌ 坚果云账号密码未配置，无法上传 EPUB。")
        return False

    base_url = "https://dav.jianguoyun.com/dav/MyReader"
    upload_url = f"{base_url}/{epub_filename}"
    
    success = False
    try:
        session = requests.Session()
        with open(epub_path, "rb") as f:
            r = session.put(upload_url, data=f.read(), auth=(account, token), timeout=60, verify=False)
            if r.status_code in [200, 201, 204]:
                print(f"☁️ 成功推送电子书至坚果云: {epub_filename}")
                success = True
            else:
                print(f"❌ 坚果云响应异常，状态码: {r.status_code}")
    except Exception as e:
        print(f"❌ 上传 EPUB 异常: {e}")
    finally:
        if os.path.exists(epub_path):
            os.remove(epub_path)

    return success

# ==================== 【文本格式化与长段落切分】 ====================
def split_long_paragraph(text, max_len=150, window=10):
    if not text:
        return ""
    clean_text = text.strip()
    if not clean_text:
        return ""

    clean_text = re.sub(r'^[\u3000\s]+', '', clean_text)
    split_puncts = ['。', '！', '？', '；', '…', '.', '!', '?']

    if len(clean_text) <= max_len:
        return f"<p>\u3000\u3000{clean_text}</p>"

    chunks = []
    p = clean_text

    while len(p) > max_len:
        min_idx = max(0, max_len - window)
        max_idx = min(len(p), max_len + window)
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
            cut_idx = max_len

        chunk = p[:cut_idx].strip()
        p = re.sub(r'^[\u3000\s]+', '', p[cut_idx:].strip())

        if chunk:
            chunks.append(f"<p>\u3000\u3000{chunk}</p>")

    if p:
        chunks.append(f"<p>\u3000\u3000{p}</p>")

    return "".join(chunks)

# ==================== 【移动端排版适配器】 ====================
def optimize_html_for_mobile_soup(soup):
    if not soup.head:
        head = soup.new_tag('head')
        soup.html.insert(0, head)

    mobile_css = """
    <style>
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
        }
        p {
            margin-top: 0 !important;
            margin-bottom: 0.2em !important;
            text-align: justify !important;
            font-size: 16.5px !important;
            text-text-indent: 2em !important;
        }
    </style>
    """
    if not soup.find('style'):
        css_soup = BeautifulSoup(mobile_css, 'html.parser')
        soup.head.append(css_soup)

    return soup

# ==================== 【文学书分段核心类】 ====================
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
                    if " : " in line:
                        parts = line.strip().split(" : ", 1)
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
        if ext == '.txt':
            for enc in ['utf-8', 'gbk', 'utf-16']:
                try:
                    with open(file_path, 'r', encoding=enc, errors='ignore') as f: return f.read()
                except: continue
        elif ext == '.epub':
            try:
                text_content = []
                with zipfile.ZipFile(file_path, 'r') as z:
                    for name in sorted(z.namelist()):
                        if name.endswith(('.html', '.xhtml', '.htm')):
                            soup = BeautifulSoup(z.read(name), 'html.parser')
                            for el in soup.find_all(['p', 'h1', 'h2', 'div']):
                                t = el.get_text().strip()
                                if t: text_content.append(t)
                return "\n\n".join(text_content)
            except: pass
        return ""

    def run_daily_slice(self, date_str):
        books = sorted([f for f in os.listdir('.') if f.lower().endswith(('.txt', '.epub')) and "sync_history" not in f.lower() and f not in {"requirements.txt", "README.md"}])
        target = next((b for b in books if self.progress_map.get(b, 0) != -1), None)
        if not target:
            print(" 没有发现待切分的新文学书（请在同目录下放入 .txt 或 .epub 文件）。")
            return

        print(f" 正在切分文学书: 《{target}》")
        full_text = self._extract_text(target).replace('\r', '')
        total = len(full_text)
        start = self.progress_map.get(target, 0)

        if start >= total:
            self.progress_map[target] = -1
            self._save_progress()
            print(f"书本《{target}》已全部切分完毕！")
            return

        end = min(start + self.split_size, total)
        slice_text = full_text[start:end]
        
        body_html = "".join([split_long_paragraph(p, 100) for p in slice_text.split('\n') if p.strip()])
        pure_name = re.sub(r'[^\w\u4e00-\u9fa5]', '', target.split('.')[0])
        display_name = f"{date_str}_文学_{pure_name}_第{start+1}字"
        
        raw_output = f"<h1>《{target}》</h1>{body_html}<div style='color:#999;font-size:13px;margin-top:50px;border-top:1px solid #eee;'>进度: {start+1}-{end} | 总计: {total}</div>"
        
        lit_soup = BeautifulSoup(f"<!DOCTYPE html><html><head><meta charset='UTF-8'></head><body>{raw_output}</body></html>", 'html.parser')
        optimized_lit_soup = optimize_html_for_mobile_soup(lit_soup)
        html_output = str(optimized_lit_soup)

        if upload_to_memory_queue(html_output, display_name):
            print(f"✅ 文学书切片成功: {display_name} (字数: {len(slice_text)})")
            self.progress_map[target] = end if end < total else -1
            self._save_progress()

# ==================== 【主控调度】 ====================
def main():
    bj = get_beijing_time()
    today = bj.strftime("%Y%m%d")
    print(f" 任务启动 | 北京时间: {bj.strftime('%Y-%m-%d %H:%M:%S')}")
    
    # 1. 执行长文/文学书切片
    SmartBookSplitter().run_daily_slice(today)
    
    # 2. 将切片打包成 EPUB 并上传坚果云
    epub_success = build_and_upload_daily_epub(today)
    
    if epub_success:
        print(" 所有切片与上传任务执行完毕！")
    else:
        print("❌ 本次没有生成新的 EPUB 或上传失败。")

if __name__ == "__main__": 
    main()
