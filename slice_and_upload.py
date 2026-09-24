import os
import json
import re
import subprocess
from datetime import datetime, timezone, timedelta
from webdav3.client import Client
from ebooklib import epub

# ==================== 配置与常量 ====================
# 【已修正】坚果云专用的 WebDAV 服务器地址
JIANGUOYUN_SERVER = 'https://dav.jianguoyun.com/dav'
ROOT_DIR = '/MyReaderBooks'
PROGRESS_FILE = 'progress.json'
EXCLUDE_FILES = {'README.md', 'requirements.txt', 'slice_and_upload.py', 'progress.json', '.gitignore'}

TARGET_LEN = 6000
SUB_PARAGRAPH_MIN = 100
SUB_PARAGRAPH_MAX = 150
PUNCTUATION_END = {'。', '！', '？', '\n'}


# ==================== 1. 账本与文件扫描 ====================
def load_progress():
    if os.path.exists(PROGRESS_FILE):
        try:
            with open(PROGRESS_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return {
                    "current_book": data.get("current_book"),
                    "char_pointer": data.get("char_pointer", 0),
                    "chapter_num": data.get("chapter_num", 1)
                }
        except Exception:
            pass
    return {"current_book": None, "char_pointer": 0, "chapter_num": 1}


def save_progress(progress):
    with open(PROGRESS_FILE, 'w', encoding='utf-8') as f:
        json.dump(progress, f, ensure_ascii=False, indent=4)


def get_txt_books():
    files = os.listdir('.')
    books = [f for f in files if f.endswith('.txt') and f not in EXCLUDE_FILES]
    return sorted(books)


# ==================== 2. 文本切片与智能分段 ====================
def find_smart_end_point(text, start):
    n = len(text)
    if start >= n:
        return -1  
    
    target_pos = start + TARGET_LEN
    if target_pos >= n:
        return n  
    
    search_limit = min(n, target_pos + 1000)
    best_pos = -1
    for i in range(target_pos, search_limit):
        if text[i] in PUNCTUATION_END:
            best_pos = i + 1
            break
            
    if best_pos == -1:
        for i in range(target_pos, max(start, target_pos - 1000), -1):
            if text[i] in PUNCTUATION_END:
                best_pos = i + 1
                break
                
    if best_pos == -1:
        best_pos = target_pos
        
    return best_pos


def smart_segment_paragraphs(raw_chunk):
    paragraphs = []
    raw_paragraphs = raw_chunk.split('\n')
    
    for raw_p in raw_paragraphs:
        raw_p = raw_p.strip()
        if not raw_p:
            continue
            
        if len(raw_p) <= SUB_PARAGRAPH_MAX:
            paragraphs.append(raw_p)
        else:
            current_sub = ""
            for char in raw_p:
                current_sub += char
                if len(current_sub) >= SUB_PARAGRAPH_MIN and char in PUNCTUATION_END:
                    paragraphs.append(current_sub.strip())
                    current_sub = ""
            if current_sub.strip():
                paragraphs.append(current_sub.strip())
                
    return paragraphs


# ==================== 3. 生成 EPUB ====================
def create_epub(book_title, chapter_num, paragraphs):
    book = epub.EpubBook()
    
    book.set_identifier(f'id_{book_title}_{chapter_num}')
    book.set_title(f"{book_title} - 第{chapter_num:03d}部分")
    book.set_language('zh')
    
    # 获取北京时间作为元数据出版日期
    bj_tz = timezone(timedelta(hours=8))
    current_date_str = datetime.now(bj_tz).strftime('%Y-%m-%d')
    book.add_metadata('DC', 'date', current_date_str)
    
    html_content = [f'<html><head><title>{book_title} 第{chapter_num:03d}部分</title></head><body>']
    html_content.append(f'<h2>第{chapter_num:03d}部分</h2>')
    for p in paragraphs:
        html_content.append(f'<p style="text-indent: 2em;">{p}</p>')
    html_content.append('</body></html>')
    
    chapter = epub.EpubHtml(
        title=f'第{chapter_num:03d}部分',
        file_name='chapter_today.xhtml',
        content=''.join(html_content)
    )
    
    book.add_item(chapter)
    book.toc = (epub.Link('chapter_today.xhtml', f'第{chapter_num:03d}部分', 'chapter_1'),)
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    
    style = 'body { font-family: sans-serif; } p { margin: 0 0 0.5em 0; }'
    nav_css = epub.EpubItem(uid="style_nav", file_name="style/nav.css", media_type="text/css", content=style)
    book.add_item(nav_css)
    
    book.spine = ['nav', chapter]
    
    epub_filename = f"高桥文学_《{book_title}》_第{chapter_num:03d}部分.epub"
    epub.write_epub(epub_filename, book, {})
    return epub_filename


# ==================== 4. WebDAV 上传（绝对不覆盖） ====================
def upload_to_jianguoyun(local_file, remote_filename):
    user = os.environ.get('JIANGUOYUN_USER')
    password = os.environ.get('JIANGUOYUN_PASS')
    
    if not user or not password:
        raise ValueError("环境变量 JIANGUOYUN_USER 或 JIANGUOYUN_PASS 未设置！")
        
    options = {
        'webdav_conn_str': JIANGUOYUN_SERVER,
        'webdav_login': user,
        'webdav_password': password
    }
    
    client = Client(options)
    client.verify = True
    
    if not client.check(ROOT_DIR):
        client.mkdir(ROOT_DIR)
        
    remote_path = f"{ROOT_DIR}/{remote_filename}"
    
    client.upload_sync(remote_path=remote_path, local_path=local_file)
    print(f"成功上传独立章节文件到云端: {remote_path}")


# ==================== 5. Git 自动记账与推送 ====================
def git_commit_and_push():
    try:
        subprocess.run(['git', 'config', '--global', 'user.name', 'github-actions[bot]'], check=True)
        subprocess.run(['git', 'config', '--global', 'user.email', 'github-actions[bot]@users.noreply.github.com'], check=True)
        subprocess.run(['git', 'add', PROGRESS_FILE], check=True)
        
        status_result = subprocess.run(['git', 'status', '--porcelain'], capture_output=True, text=True, check=True)
        if status_result.stdout.strip():
            subprocess.run(['git', 'commit', '-m', '🔄 自动记账 [skip ci]'], check=True)
            subprocess.run(['git', 'push'], check=True)
            print("Git 账本推送成功。")
        else:
            print("没有检测到账本变动，跳过 Git 提交。")
    except subprocess.CalledProcessError as e:
        print(f"Git 操作执行失败: {e}")


# ==================== 主执行流程 ====================
def main():
    books = get_txt_books()
    if not books:
        print("仓库中未发现任何 TXT 小说文件！")
        return
        
    progress = load_progress()
    current_book = progress.get("current_book")
    char_pointer = progress.get("char_pointer", 0)
    chapter_num = progress.get("chapter_num", 1)
    
    if not current_book or current_book not in books:
        current_book = books[0]
        char_pointer = 0
        chapter_num = 1
        print(f"锁定新小说: {current_book}，指针重置为 0，章节序号重置为 1")
    else:
        print(f"当前正在追更小说: {current_book}，当前指针: {char_pointer}，当前章节: {chapter_num}")
        
    with open(current_book, 'r', encoding='utf-8', errors='ignore') as f:
        content = f.read()
        
    end_pointer = find_smart_end_point(content, char_pointer)
    
    if end_pointer == -1 or char_pointer >= len(content):
        book_index = books.index(current_book)
        if book_index + 1 < len(books):
            current_book = books[book_index + 1]
            char_pointer = 0
            chapter_num = 1
            print(f"上一本书已全书完结！自动切换到下一本: {current_book}")
            end_pointer = find_smart_end_point(content, char_pointer)
        else:
            print("所有小说均已完结，暂无新书可读！")
            return

    raw_chunk = content[char_pointer:end_pointer]
    paragraphs = smart_segment_paragraphs(raw_chunk)
    
    book_title = os.path.splitext(current_book)[0]
    epub_filename = create_epub(book_title, chapter_num, paragraphs)
    print(f"成功封装单章 EPUB: {epub_filename} (字数: {len(raw_chunk)})")
    
    remote_filename = f"高桥文学_《{book_title}》_第{chapter_num:03d}部分.epub"
    
    try:
        upload_to_jianguoyun(epub_filename, remote_filename)
    finally:
        if os.path.exists(epub_filename):
            os.remove(epub_filename)
            
    # 更新账本
    progress["current_book"] = current_book
    progress["char_pointer"] = end_pointer
    progress["chapter_num"] = chapter_num + 1
    save_progress(progress)
    print(f"账本更新完毕，下次阅读起点: {end_pointer}，下次章节序号: {chapter_num + 1}")
    
    git_commit_and_push()


if __name__ == '__main__':
    main()
