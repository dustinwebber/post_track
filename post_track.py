import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
import requests
import xml.etree.ElementTree as ET
import json
import time
import os
import re
import webbrowser
import threading
import queue
from datetime import datetime
from deep_translator import GoogleTranslator

# 固定工作目录为脚本所在目录
os.chdir(os.path.dirname(os.path.abspath(__file__)))

# ==================== 配置区 ====================

SUBREDDITS_BY_CATEGORY = {
    "🇺🇸 美股综合": [
        'wallstreetbets', 'stocks', 'investing', 'StockMarket', 'SecurityAnalysis', 'ValueInvesting',
    ],
    "📈 期权 & 策略": [
        'options', 'thetagang', 'Daytrading', 'algotrading',
    ],
    "🪙 细分赛道": [
        'pennystocks', 'dividends', 'ETFs', 'Bogleheads', 'Superstonk',
    ],
    "🇨🇦 加拿大": [
        'CanadianInvestor', 'baystreetbets', 'PersonalFinanceCanada', 'canadianfire',
    ],
    "🌍 宏观 & 经济": [
        'Economics', 'worldnews', 'business', 'Finance', 'economy',
    ],
}

RSS_FEEDS = {
    "Reuters Business":    "https://feeds.reuters.com/reuters/businessNews",
    "Reuters Markets":     "https://feeds.reuters.com/reuters/USmarkets",
    "Yahoo Finance":       "https://finance.yahoo.com/rss/topfinstories",
    "MarketWatch Top":     "https://feeds.marketwatch.com/marketwatch/topstories",
    "MarketWatch Markets": "https://feeds.marketwatch.com/marketwatch/marketpulse",
    "Seeking Alpha":       "https://seekingalpha.com/feed.xml",
    "Investopedia News":   "https://www.investopedia.com/feedbuilder/feed/getfeed/?feedName=rss_headline",
    "CNBC Finance":        "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=10000664",
    "CNBC Markets":        "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=15839069",
    "Bloomberg Markets":   "https://feeds.bloomberg.com/markets/news.rss",
    "FT Markets":          "https://www.ft.com/markets?format=rss",
    "Globe & Mail Invest": "https://www.theglobeandmail.com/investing/rss",
    "Financial Post":      "https://financialpost.com/feed",
    "CBC Business":        "https://www.cbc.ca/cmlink/rss-business",
}

LIMIT_PER_SUB  = 25
CHECK_INTERVAL = 600

OUTPUT_DIR = "stock_monitor_outputs"
SEEN_FILE  = "seen_stock_monitor.json"
os.makedirs(OUTPUT_DIR, exist_ok=True)

translator = GoogleTranslator(source='auto', target='zh-CN')

ALL_SUBREDDITS = [sub for subs in SUBREDDITS_BY_CATEGORY.values() for sub in subs]
TOTAL_SOURCES  = len(ALL_SUBREDDITS) + len(RSS_FEEDS)


# ==================== 工具函数 ====================
def needs_translation(text):
    if not text or not text.strip():
        return False
    chinese = len(re.findall(r'[\u4e00-\u9fff]', text))
    return chinese / max(len(text), 1) < 0.15

def safe_translate(text, max_chars=4000):
    if not text or not text.strip() or not needs_translation(text):
        return text or ""
    try:
        return translator.translate(text[:max_chars])
    except Exception:
        return text

def parse_rss_date(date_str):
    if not date_str:
        return datetime.now().strftime("%Y-%m-%d %H:%M")
    for fmt in ("%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S GMT",
                "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return datetime.strptime(date_str.strip(), fmt).strftime("%Y-%m-%d %H:%M")
        except ValueError:
            pass
    return date_str[:16]

def clean_html(text):
    if not text:
        return ""
    text = re.sub(r'<[^>]+>', ' ', text)
    for ent, rep in [('&amp;', '&'), ('&lt;', '<'), ('&gt;', '>'),
                     ('&quot;', '"'), ('&#\d+;', '')]:
        text = re.sub(ent, rep, text)
    return re.sub(r'\s+', ' ', text).strip()


# ==================== 主 GUI ====================
class StockMonitorGUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("财经信息聚合监控器 — Reddit + 财经新闻 RSS")
        self.geometry("1500x920")
        self.minsize(1200, 760)

        self.posts   = {}
        self.seen    = self._load_seen()
        self.running = False
        self.scraper_thread = None
        self.q = queue.Queue()

        self.reddit_enabled = tk.BooleanVar(value=True)
        self.rss_enabled    = tk.BooleanVar(value=True)

        # 单击暂停插入：记录暂停截止时间（0 表示不暂停）
        self.insert_paused_until = 0
        self._pause_timer_id     = None   # after() 句柄，用于取消倒计时
        self._pending_posts      = []     # 暂停期间积压的条目

        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(300, self._drain_queue)

    def _build_ui(self):
        style = ttk.Style()
        style.configure("Treeview",         font=("微软雅黑", 10))
        style.configure("Treeview.Heading", font=("微软雅黑", 11, "bold"))
        style.configure("green.Horizontal.TProgressbar",
                        troughcolor="#d5e8d4", background="#27ae60")
        style.configure("blue.Horizontal.TProgressbar",
                        troughcolor="#d6eaf8", background="#2980b9")

        # 顶部工具栏
        toolbar = tk.Frame(self, bg="#1c2833", pady=8)
        toolbar.pack(fill=tk.X)
        btn_cfg = dict(font=("微软雅黑", 10), padx=8, pady=4)

        self.start_btn = tk.Button(toolbar, text="▶ 开始监控", bg="#27ae60", fg="white",
                                   command=self.start_scraping, **btn_cfg)
        self.start_btn.pack(side=tk.LEFT, padx=(14, 4))

        self.stop_btn = tk.Button(toolbar, text="■ 停止", bg="#c0392b", fg="white",
                                  command=self.stop_scraping, state=tk.DISABLED, **btn_cfg)
        self.stop_btn.pack(side=tk.LEFT, padx=4)

        tk.Button(toolbar, text="🗑 清空列表", bg="#566573", fg="white",
                  command=self._clear_list, **btn_cfg).pack(side=tk.LEFT, padx=4)
        tk.Button(toolbar, text="📂 输出文件夹", bg="#2471a3", fg="white",
                  command=self._open_folder, **btn_cfg).pack(side=tk.LEFT, padx=4)

        ttk.Separator(toolbar, orient="vertical").pack(side=tk.LEFT, fill=tk.Y, padx=12, pady=4)
        tk.Label(toolbar, text="数据源：", bg="#1c2833", fg="#aeb6bf",
                 font=("微软雅黑", 10)).pack(side=tk.LEFT)
        tk.Checkbutton(toolbar, text="Reddit 帖子", variable=self.reddit_enabled,
                       bg="#1c2833", fg="white", selectcolor="#2c3e50",
                       font=("微软雅黑", 10), activebackground="#1c2833").pack(side=tk.LEFT, padx=6)
        tk.Checkbutton(toolbar, text="财经新闻 RSS", variable=self.rss_enabled,
                       bg="#1c2833", fg="white", selectcolor="#2c3e50",
                       font=("微软雅黑", 10), activebackground="#1c2833").pack(side=tk.LEFT, padx=6)

        self.count_var = tk.StringVar(value="已抓取：0 条")
        tk.Label(toolbar, textvariable=self.count_var, bg="#1c2833", fg="#f0b27a",
                 font=("微软雅黑", 11, "bold")).pack(side=tk.RIGHT, padx=20)

        # 筛选栏
        filter_bar = tk.Frame(self, bg="#2c3e50", pady=6)
        filter_bar.pack(fill=tk.X)

        tk.Label(filter_bar, text="筛选类型：", bg="#2c3e50", fg="#aeb6bf",
                 font=("微软雅黑", 10)).pack(side=tk.LEFT, padx=12)
        self.filter_var = tk.StringVar(value="全部")
        for opt in ["全部", "Reddit", "RSS新闻"]:
            tk.Radiobutton(filter_bar, text=opt, variable=self.filter_var, value=opt,
                           bg="#2c3e50", fg="white", selectcolor="#1a5276",
                           activebackground="#2c3e50", font=("微软雅黑", 10),
                           command=self._apply_filter).pack(side=tk.LEFT, padx=8)

        ttk.Separator(filter_bar, orient="vertical").pack(side=tk.LEFT, fill=tk.Y, padx=10, pady=3)
        tk.Label(filter_bar, text="关键词：", bg="#2c3e50", fg="#aeb6bf",
                 font=("微软雅黑", 10)).pack(side=tk.LEFT)
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *_: self._apply_filter())
        tk.Entry(filter_bar, textvariable=self.search_var,
                 font=("微软雅黑", 10), width=26, relief="flat", bd=4).pack(side=tk.LEFT, padx=6)
        tk.Button(filter_bar, text="✕ 清除", bg="#566573", fg="white",
                  font=("微软雅黑", 9), padx=4,
                  command=lambda: self.search_var.set("")).pack(side=tk.LEFT, padx=2)

        # 主列表
        list_frame = tk.Frame(self)
        list_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=(6, 2))

        columns = ("时间", "类型", "来源", "翻译标题")
        self.tree = ttk.Treeview(list_frame, columns=columns,
                                 show="headings", selectmode="browse")
        self.tree.heading("时间",     text="时间",     anchor="center")
        self.tree.heading("类型",     text="类型",     anchor="center")
        self.tree.heading("来源",     text="来源",     anchor="center")
        self.tree.heading("翻译标题", text="翻译标题", anchor="w")
        self.tree.column("时间",     width=148,  anchor="center", stretch=False)
        self.tree.column("类型",     width=100,  anchor="center", stretch=False)
        self.tree.column("来源",     width=180,  anchor="center", stretch=False)
        self.tree.column("翻译标题", width=1000, anchor="w")
        self.tree.tag_configure("reddit", background="#f0f8ff")
        self.tree.tag_configure("rss",    background="#f0fff4")

        vsb = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.bind("<Double-1>",       self._on_double_click)
        self.tree.bind("<ButtonRelease-1>", self._on_single_click)

        # 底部状态栏
        status_bar = tk.Frame(self, bg="#1c2833", height=72)
        status_bar.pack(fill=tk.X, side=tk.BOTTOM)
        status_bar.pack_propagate(False)

        row1 = tk.Frame(status_bar, bg="#1c2833")
        row1.pack(fill=tk.X, padx=14, pady=(8, 2))
        tk.Label(row1, text="扫描进度：", bg="#1c2833", fg="#aeb6bf",
                 font=("微软雅黑", 9, "bold")).pack(side=tk.LEFT)
        self.progress_label = tk.StringVar(value="等待开始")
        tk.Label(row1, textvariable=self.progress_label, bg="#1c2833", fg="#f0b27a",
                 font=("微软雅黑", 9)).pack(side=tk.LEFT, padx=8)
        self.main_bar = ttk.Progressbar(
            row1, orient=tk.HORIZONTAL, length=420,
            mode='determinate', maximum=TOTAL_SOURCES,
            style="green.Horizontal.TProgressbar")
        self.main_bar.pack(side=tk.LEFT, padx=6)
        self.spinner = ttk.Progressbar(row1, mode='indeterminate', length=60,
                                       style="blue.Horizontal.TProgressbar")
        self.spinner.pack(side=tk.LEFT, padx=8)

        row2 = tk.Frame(status_bar, bg="#1c2833")
        row2.pack(fill=tk.X, padx=14, pady=(0, 6))
        self.status_var = tk.StringVar(value="就绪 — 点击「开始监控」抓取 Reddit + RSS 财经信息")
        tk.Label(row2, textvariable=self.status_var, bg="#1c2833", fg="#aed6f1",
                 font=("微软雅黑", 10, "bold"), anchor="w").pack(side=tk.LEFT, fill=tk.X, expand=True)

        # 暂停提示标签（单击选中时显示）
        self.pause_var = tk.StringVar(value="")
        self.pause_label = tk.Label(row2, textvariable=self.pause_var,
                                    bg="#1c2833", fg="#f39c12",
                                    font=("微软雅黑", 10, "bold"), anchor="e")
        self.pause_label.pack(side=tk.RIGHT, padx=14)

    # ── 持久化 ──────────────────────────────────
    def _load_seen(self):
        if os.path.exists(SEEN_FILE):
            with open(SEEN_FILE, 'r', encoding='utf-8') as f:
                return set(json.load(f))
        return set()

    def _save_seen(self):
        with open(SEEN_FILE, 'w', encoding='utf-8') as f:
            json.dump(list(self.seen), f, ensure_ascii=False)

    # ── 抓取控制 ────────────────────────────────
    def start_scraping(self):
        if self.running:
            return
        self.running = True
        self.start_btn.config(state=tk.DISABLED)
        self.stop_btn.config(state=tk.NORMAL)
        self.spinner.start(10)
        self.main_bar['value'] = 0
        self.scraper_thread = threading.Thread(target=self._scraper_loop, daemon=True)
        self.scraper_thread.start()
        self.status_var.set("监控启动中...")

    def stop_scraping(self):
        self.running = False
        self.start_btn.config(state=tk.NORMAL)
        self.stop_btn.config(state=tk.DISABLED)
        self.spinner.stop()
        self.main_bar['value'] = 0
        self.progress_label.set("已停止")
        self.status_var.set("监控已停止")

    def _scraper_loop(self):
        headers = {'User-Agent': 'Mozilla/5.0 (compatible; StockMonitorBot/2.0)'}
        while self.running:
            idx = 0
            if self.reddit_enabled.get():
                for category, subs in SUBREDDITS_BY_CATEGORY.items():
                    for sub in subs:
                        if not self.running:
                            break
                        idx += 1
                        self.q.put(('progress', (idx, f"r/{sub}", category)))
                        self.q.put(('status', f"[Reddit] 扫描 r/{sub}  ({idx}/{TOTAL_SOURCES})"))
                        self._fetch_reddit(sub, headers)
                        time.sleep(2.5)

            if self.rss_enabled.get():
                for feed_name, feed_url in RSS_FEEDS.items():
                    if not self.running:
                        break
                    idx += 1
                    self.q.put(('progress', (idx, feed_name, "📰 财经新闻")))
                    self.q.put(('status', f"[RSS] 抓取 {feed_name}  ({idx}/{TOTAL_SOURCES})"))
                    self._fetch_rss(feed_name, feed_url, headers)
                    time.sleep(1.5)

            self._save_seen()
            next_t = datetime.fromtimestamp(time.time() + CHECK_INTERVAL).strftime("%H:%M")
            self.q.put(('done', next_t))
            time.sleep(CHECK_INTERVAL)

    # ── Reddit ──────────────────────────────────
    def _fetch_reddit(self, sub, headers):
        try:
            resp = requests.get(
                f"https://www.reddit.com/r/{sub}/new.json?limit={LIMIT_PER_SUB}",
                headers=headers, timeout=15)
            if resp.status_code == 200:
                for item in resp.json()['data']['children']:
                    self._process_reddit(item, sub)
            else:
                self.q.put(('status', f"[Reddit] r/{sub} 失败 ({resp.status_code})"))
        except Exception as e:
            self.q.put(('status', f"[Reddit] r/{sub} 出错: {str(e)[:60]}"))

    def _process_reddit(self, post_data, sub_name):
        post    = post_data['data']
        full_id = post['name']
        if full_id in self.seen:
            return
        title    = post.get('title', '(无标题)')
        selftext = post.get('selftext', '') or '(链接帖，无正文)'
        url      = f"https://www.reddit.com{post['permalink']}"
        created  = datetime.fromtimestamp(post['created_utc']).strftime("%Y-%m-%d %H:%M")
        self.q.put(('status', f"[翻译] r/{sub_name}: {title[:50]}..."))
        info = {
            'full_id':     full_id,
            'source_type': 'Reddit',
            'source_name': f"r/{sub_name}",
            'time':        created,
            'title':       title,
            'trans_title': safe_translate(title),
            'body':        selftext,
            'trans_body':  safe_translate(selftext),
            'url':         url,
            'score':       post.get('score', 0),
            'comments':    post.get('num_comments', 0),
            'author':      post.get('author', '[已删除]'),
            'tag':         'reddit',
        }
        self.posts[full_id] = info
        self.seen.add(full_id)
        self.q.put(('add_post', info))
        self._save_md(info)

    # ── RSS ─────────────────────────────────────
    def _fetch_rss(self, feed_name, feed_url, headers):
        try:
            resp = requests.get(feed_url, headers=headers, timeout=15)
            if resp.status_code != 200:
                self.q.put(('status', f"[RSS] {feed_name} 失败 ({resp.status_code})"))
                return
            root  = ET.fromstring(resp.content)
            ns    = {'atom': 'http://www.w3.org/2005/Atom'}
            items = root.findall('.//item') or root.findall('.//atom:entry', ns)
            for item in items[:20]:
                self._process_rss_item(item, feed_name, ns, headers)
        except ET.ParseError:
            self.q.put(('status', f"[RSS] {feed_name} XML 解析失败"))
        except Exception as e:
            self.q.put(('status', f"[RSS] {feed_name} 出错: {str(e)[:60]}"))

    def _process_rss_item(self, item, feed_name, ns, headers):
        def get(tag, alt=None):
            el = item.find(tag)
            if el is None and alt:
                el = item.find(alt, ns)
            return el.text.strip() if el is not None and el.text else ""

        title = get('title', 'atom:title')
        link_el = item.find('link')
        if link_el is not None and link_el.text:
            link = link_el.text.strip()
        else:
            link_el = item.find('atom:link', ns)
            link = link_el.get('href', '') if link_el is not None else ''

        pub_date = get('pubDate') or get('atom:published', 'atom:updated')

        # 先用 RSS 自带的描述字段作为兜底正文
        rss_desc = clean_html(get('description', 'atom:summary') or get('atom:content'))

        full_id = f"rss_{feed_name}_{link[-60:]}"
        if full_id in self.seen:
            return

        # 尝试抓取原文页面提取正文
        body = self._fetch_article_body(link, headers, fallback=rss_desc)

        self.q.put(('status', f"[翻译] RSS {feed_name}: {title[:50]}..."))
        info = {
            'full_id':     full_id,
            'source_type': 'RSS新闻',
            'source_name': feed_name,
            'time':        parse_rss_date(pub_date),
            'title':       title,
            'trans_title': safe_translate(title),
            'body':        body,
            'trans_body':  safe_translate(body),
            'url':         link,
            'score':       '—',
            'comments':    '—',
            'author':      feed_name,
            'tag':         'rss',
        }
        self.posts[full_id] = info
        self.seen.add(full_id)
        self.q.put(('add_post', info))
        self._save_md(info)

    def _fetch_article_body(self, url, headers, fallback='', min_chars=100):
        """
        访问原文 URL，用启发式规则提取正文段落。
        若抓取失败或内容太短，退回到 RSS 自带的 fallback 描述。
        """
        if not url or not url.startswith('http'):
            return fallback
        try:
            resp = requests.get(url, headers=headers, timeout=18, allow_redirects=True)
            if resp.status_code != 200:
                return fallback
            html = resp.text

            # 1. 尝试提取 <article> 标签内容（语义最强）
            article_match = re.search(
                r'<article[^>]*>(.*?)</article>', html, re.DOTALL | re.IGNORECASE)
            if article_match:
                text = clean_html(article_match.group(1))
                if len(text) >= min_chars:
                    return text[:6000]

            # 2. 尝试常见正文容器 class/id
            for pattern in [
                r'<div[^>]+class="[^"]*(?:article|story|content|body|post|entry)[^"]*"[^>]*>(.*?)</div>',
                r'<div[^>]+id="[^"]*(?:article|story|content|body|post|entry)[^"]*"[^>]*>(.*?)</div>',
                r'<section[^>]+class="[^"]*(?:article|story|content|body)[^"]*"[^>]*>(.*?)</section>',
            ]:
                m = re.search(pattern, html, re.DOTALL | re.IGNORECASE)
                if m:
                    text = clean_html(m.group(1))
                    if len(text) >= min_chars:
                        return text[:6000]

            # 3. 提取所有 <p> 段落并拼接（通用兜底）
            paras = re.findall(r'<p[^>]*>(.*?)</p>', html, re.DOTALL | re.IGNORECASE)
            text  = ' '.join(clean_html(p) for p in paras if len(clean_html(p)) > 40)
            if len(text) >= min_chars:
                return text[:6000]

            # 4. 全部失败，返回 RSS 摘要
            return fallback if fallback else '(正文抓取失败，请点击「浏览器打开原文」查看)'

        except Exception:
            return fallback if fallback else '(网络访问超时，请点击「浏览器打开原文」查看)'

    # ── 保存 Markdown ───────────────────────────
    def _save_md(self, info):
        safe_name = re.sub(r'[\\/:*?"<>|]', '_', info['source_name'])
        short_id  = re.sub(r'[^a-zA-Z0-9]', '', info['full_id'])[-20:]
        filepath  = os.path.join(OUTPUT_DIR, f"{short_id}_{safe_name}.md")
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(f"# [{info['source_type']}] {info['source_name']}\n\n")
            f.write(f"**链接**：{info['url']}\n**时间**：{info['time']}\n\n")
            f.write(f"## 原始标题\n{info['title']}\n\n## 翻译标题\n{info['trans_title']}\n\n")
            f.write(f"## 原始正文\n{info['body']}\n\n## 翻译正文\n{info['trans_body']}\n\n")

    # ── 列表 & 筛选 ─────────────────────────────
    def _add_to_tree(self, info):
        """插入新条目；单击暂停期间先缓存，恢复后批量插入"""
        if time.time() < self.insert_paused_until:
            self._pending_posts.append(info)
            return
        self._insert_row(info)

    def _insert_row(self, info):
        """真正执行插入到 Treeview"""
        icon = "🔴 Reddit" if info['source_type'] == 'Reddit' else "📰 RSS"
        try:
            self.tree.insert("", 0, iid=info['full_id'],
                             values=(info['time'], icon, info['source_name'], info['trans_title']),
                             tags=(info['tag'],))
        except tk.TclError:
            pass
        self.count_var.set(f"已抓取：{len(self.posts)} 条")
        self._filter_single(info['full_id'], info)

    def _apply_filter(self):
        ftype = self.filter_var.get()
        kw    = self.search_var.get().strip().lower()
        for fid, info in self.posts.items():
            show = True
            if ftype == "Reddit"  and info['source_type'] != 'Reddit':  show = False
            if ftype == "RSS新闻" and info['source_type'] != 'RSS新闻': show = False
            if kw and kw not in info['trans_title'].lower() \
                   and kw not in info['title'].lower():
                show = False
            try:
                if show:
                    self.tree.reattach(fid, '', 0)
                else:
                    self.tree.detach(fid)
            except tk.TclError:
                pass

    def _filter_single(self, fid, info):
        ftype = self.filter_var.get()
        kw    = self.search_var.get().strip().lower()
        if ftype == "Reddit"  and info['source_type'] != 'Reddit':  self.tree.detach(fid); return
        if ftype == "RSS新闻" and info['source_type'] != 'RSS新闻': self.tree.detach(fid); return
        if kw and kw not in info['trans_title'].lower() \
               and kw not in info['title'].lower():
            self.tree.detach(fid)

    # ── Queue 消费 ──────────────────────────────
    def _drain_queue(self):
        try:
            while True:
                typ, data = self.q.get_nowait()
                if typ == 'add_post':
                    self._add_to_tree(data)
                elif typ == 'status':
                    self.status_var.set(data)
                elif typ == 'progress':
                    idx, label, category = data
                    self.main_bar['value'] = idx
                    self.progress_label.set(
                        f"{label}  [{category}]  ({idx}/{TOTAL_SOURCES})")
                elif typ == 'done':
                    next_t = data
                    self.main_bar['value'] = TOTAL_SOURCES
                    self.progress_label.set(f"本轮完成 ✓  下次：{next_t}")
                    self.status_var.set(
                        f"全部来源扫描完毕 | 累计 {len(self.posts)} 条 | 下一轮约 {next_t}")
        except queue.Empty:
            pass
        self.after(300, self._drain_queue)

    # ── 交互 ────────────────────────────────────
    def _on_single_click(self, event):
        """单击选中某行 → 暂停插入 5 秒，避免列表跳动干扰双击"""
        # 如果点击的是空白区域则忽略
        if not self.tree.identify_row(event.y):
            return
        self._pause_insert(seconds=5)

    def _pause_insert(self, seconds=5):
        """启动/重置暂停计时器"""
        self.insert_paused_until = time.time() + seconds
        # 取消上一个倒计时刷新（如果有）
        if self._pause_timer_id:
            self.after_cancel(self._pause_timer_id)
        self._update_pause_countdown()

    def _update_pause_countdown(self):
        """每秒刷新状态栏右侧的暂停倒计时标签"""
        remaining = self.insert_paused_until - time.time()
        if remaining > 0:
            pending_n = len(self._pending_posts)
            pending_str = f"  ({pending_n} 条待插入)" if pending_n else ""
            self.pause_var.set(f"⏸ 列表已暂停 {int(remaining)+1} 秒{pending_str}  — 双击打开帖子")
            self._pause_timer_id = self.after(500, self._update_pause_countdown)
        else:
            self._resume_insert()

    def _resume_insert(self):
        """暂停结束，批量插入缓存的条目并清空暂停标记"""
        self.insert_paused_until = 0
        self.pause_var.set("")
        self._pause_timer_id = None
        # 批量插入积压条目
        pending = self._pending_posts[:]
        self._pending_posts.clear()
        for info in pending:
            self._insert_row(info)

    def _on_double_click(self, event):
        sel = self.tree.selection()
        if not sel:
            return
        fid = sel[0]
        if fid in self.posts:
            # 双击后立即结束暂停，恢复正常滚动
            self._resume_insert()
            DetailWindow(self, self.posts[fid])

    def _clear_list(self):
        if messagebox.askyesno("清空", "确定清空当前列表？（不会删除已保存的文件）"):
            self.tree.delete(*self.tree.get_children())
            self.posts.clear()
            self.count_var.set("已抓取：0 条")
            self.status_var.set("列表已清空")

    def _open_folder(self):
        path = os.path.abspath(OUTPUT_DIR)
        if os.name == 'nt':
            os.startfile(path)
        else:
            webbrowser.open(f"file://{path}")

    def _on_close(self):
        self.stop_scraping()
        if self.scraper_thread and self.scraper_thread.is_alive():
            self.scraper_thread.join(3)
        self._save_seen()
        self.destroy()


# ==================== 详情窗口（双语对照）====================
class DetailWindow(tk.Toplevel):
    def __init__(self, parent, info):
        super().__init__(parent)
        src_icon = "🔴 Reddit" if info['source_type'] == 'Reddit' else "📰 RSS新闻"
        self.title(f"{src_icon} — {info['source_name']}  |  双语对照详情")
        self.geometry("1440x880")
        self.resizable(True, True)
        self.configure(bg="#f4f6f7")

        # 同步滚动锁，防止相互触发死循环
        self._syncing = False

        # ── 顶部元信息 ──
        top = tk.Frame(self, bg="#1c2833", pady=10)
        top.pack(fill=tk.X)

        score_str = f"⬆ {info['score']} 分" if info['score'] != '—' else ""
        cmts_str  = f"💬 {info['comments']} 评论" if info['comments'] != '—' else ""
        meta = (f"  {src_icon}  📌 {info['source_name']}    🕐 {info['time']}    "
                f"👤 {info['author']}    {score_str}  {cmts_str}")
        tk.Label(top, text=meta, bg="#1c2833", fg="white",
                 font=("微软雅黑", 11)).pack(side=tk.LEFT, padx=10)
        tk.Button(top, text="🔗 浏览器打开原文", bg="#2471a3", fg="white",
                  font=("微软雅黑", 10), padx=6,
                  command=lambda: webbrowser.open(info['url'])).pack(
                      side=tk.RIGHT, padx=16, pady=4)

        # ── 标题对照 ──
        title_area = tk.Frame(self, bg="#d6eaf8", pady=10)
        title_area.pack(fill=tk.X)
        title_area.columnconfigure(0, weight=1)
        title_area.columnconfigure(2, weight=1)

        lf = tk.Frame(title_area, bg="#d6eaf8")
        lf.grid(row=0, column=0, sticky="nsew", padx=(18, 8))
        tk.Label(lf, text="📄 原文标题", font=("微软雅黑", 10, "bold"),
                 bg="#d6eaf8", fg="#1a5276").pack(anchor="w")
        tk.Label(lf, text=info['title'], font=("微软雅黑", 12),
                 bg="#d6eaf8", fg="#1c2833", wraplength=600,
                 justify="left").pack(anchor="w", pady=3)

        ttk.Separator(title_area, orient="vertical").grid(
            row=0, column=1, sticky="ns", pady=4)

        rf = tk.Frame(title_area, bg="#d5f5e3")
        rf.grid(row=0, column=2, sticky="nsew", padx=(8, 18))
        tk.Label(rf, text="🈯 中文翻译", font=("微软雅黑", 10, "bold"),
                 bg="#d5f5e3", fg="#1e8449").pack(anchor="w")
        tk.Label(rf, text=info['trans_title'], font=("微软雅黑", 12),
                 bg="#d5f5e3", fg="#1c2833", wraplength=600,
                 justify="left").pack(anchor="w", pady=3)

        # ── 正文提示 ──
        tk.Label(self,
                 text="正文对照（左：原文  |  右：中文翻译）— 可拖动中间分隔线调整宽度",
                 font=("微软雅黑", 10, "bold"), bg="#f4f6f7", fg="#2c3e50",
                 pady=5).pack(fill=tk.X, padx=16)

        # ── 双栏正文 ──
        paned = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True, padx=10, pady=4)

        lf2 = ttk.LabelFrame(paned, text="📄 原文正文", padding=6)
        paned.add(lf2, weight=1)
        self.left_txt = scrolledtext.ScrolledText(
            lf2, wrap=tk.WORD, font=("Consolas", 11),
            bg="#fdfefe", relief="flat", spacing1=4)
        self.left_txt.pack(fill=tk.BOTH, expand=True)
        self.left_txt.insert(tk.END, info['body'] or "(无正文)")
        self.left_txt.config(state='disabled')

        rf2 = ttk.LabelFrame(paned, text="🈯 中文翻译正文", padding=6)
        paned.add(rf2, weight=1)
        self.right_txt = scrolledtext.ScrolledText(
            rf2, wrap=tk.WORD, font=("微软雅黑", 11),
            bg="#f9fff9", relief="flat", spacing1=4)
        self.right_txt.pack(fill=tk.BOTH, expand=True)
        self.right_txt.insert(tk.END, info['trans_body'] or "(无翻译)")
        self.right_txt.config(state='disabled')

        # ── 同步滚动（修复版）──
        # 用 yscrollcommand 捕获滚动位置，再用 yview_moveto 同步对方
        self.left_txt.configure(yscrollcommand=self._on_left_scroll)
        self.right_txt.configure(yscrollcommand=self._on_right_scroll)
        # 鼠标滚轮同步
        self.left_txt.bind("<MouseWheel>",  self._wheel_left)
        self.right_txt.bind("<MouseWheel>", self._wheel_right)
        # Linux 滚轮
        self.left_txt.bind("<Button-4>",  self._wheel_left)
        self.left_txt.bind("<Button-5>",  self._wheel_left)
        self.right_txt.bind("<Button-4>", self._wheel_right)
        self.right_txt.bind("<Button-5>", self._wheel_right)

        # ── 底部状态栏 ──
        bot = tk.Frame(self, bg="#1c2833", height=40)
        bot.pack(fill=tk.X, side=tk.BOTTOM)
        bot.pack_propagate(False)

        bar = ttk.Progressbar(bot, mode='determinate', length=180, maximum=100)
        bar.pack(side=tk.LEFT, padx=16, pady=8)
        bar['value'] = 100

        stat_text = (f"✅ 加载完成  |  来源：{info['source_name']}  |  "
                     f"原文 {len(info['body'])} 字符  |  "
                     f"翻译 {len(info['trans_body'])} 字符")
        tk.Label(bot, text=stat_text, bg="#1c2833", fg="#aed6f1",
                 font=("微软雅黑", 10)).pack(side=tk.LEFT, padx=8)
        tk.Button(bot, text="关闭", bg="#566573", fg="white",
                  font=("微软雅黑", 10), padx=8,
                  command=self.destroy).pack(side=tk.RIGHT, padx=16, pady=6)

    # ── 同步滚动方法（正确实现）────────────────
    def _on_left_scroll(self, first, last):
        """左侧滚动条移动时：更新左侧滚动条，并同步右侧视图位置"""
        self.left_txt.tk.call(self.left_txt._w, 'yview', 'moveto', first)
        if not self._syncing:
            self._syncing = True
            self.right_txt.yview_moveto(first)
            self._syncing = False

    def _on_right_scroll(self, first, last):
        """右侧滚动条移动时：更新右侧滚动条，并同步左侧视图位置"""
        self.right_txt.tk.call(self.right_txt._w, 'yview', 'moveto', first)
        if not self._syncing:
            self._syncing = True
            self.left_txt.yview_moveto(first)
            self._syncing = False

    def _wheel_left(self, event):
        """鼠标滚轮在左侧时同步右侧"""
        delta = -1 if (getattr(event, 'num', 0) == 5 or
                       getattr(event, 'delta', 1) < 0) else 1
        self.left_txt.yview_scroll(-delta, "units")
        self.right_txt.yview_scroll(-delta, "units")
        return "break"

    def _wheel_right(self, event):
        """鼠标滚轮在右侧时同步左侧"""
        delta = -1 if (getattr(event, 'num', 0) == 5 or
                       getattr(event, 'delta', 1) < 0) else 1
        self.right_txt.yview_scroll(-delta, "units")
        self.left_txt.yview_scroll(-delta, "units")
        return "break"


# ==================== 入口 ====================
if __name__ == "__main__":
    app = StockMonitorGUI()
    app.mainloop()