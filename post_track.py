import streamlit as st
import requests
import xml.etree.ElementTree as ET
import json
import time
import re
import webbrowser
from datetime import datetime
from deep_translator import GoogleTranslator
from bs4 import BeautifulSoup

# ==================== 配置区 ====================
st.set_page_config(page_title="财经信息聚合监控", layout="wide", page_icon="📈")

SUBREDDITS_BY_CATEGORY = {
    "🇺🇸 美股综合": ['wallstreetbets', 'stocks', 'investing', 'StockMarket'],
    "📈 期权 & 策略": ['options', 'thetagang', 'Daytrading'],
    "🌍 宏观 & 经济": ['Economics', 'business', 'Finance'],
}

RSS_FEEDS = {
    "Yahoo Finance": "https://finance.yahoo.com/rss/topfinstories",
    "MarketWatch": "https://feeds.marketwatch.com/marketwatch/topstories",
    "Seeking Alpha": "https://seekingalpha.com/feed.xml",
}

translator = GoogleTranslator(source='auto', target='zh-CN')

# ==================== 工具函数 ====================
def safe_translate(text, max_chars=3000):
    if not text or not text.strip():
        return ""
    try:
        # 简单判断是否包含中文，避免重复翻译
        if len(re.findall(r'[\u4e00-\u9fff]', text)) / max(len(text), 1) > 0.15:
            return text
        return translator.translate(text[:max_chars])
    except:
        return text

def clean_html(text):
    if not text: return ""
    return BeautifulSoup(text, "html.parser").get_text(separator=' ').strip()

def fetch_article_body(url):
    """尝试抓取网页正文"""
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        resp = requests.get(url, headers=headers, timeout=5)
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.content, "html.parser")
            # 移除脚本和样式
            for script in soup(["script", "style"]):
                script.extract()
            # 寻找可能的正文标签
            article = soup.find('article') or soup.find('div', class_=re.compile(r'article|body|content'))
            if article:
                return article.get_text(separator='\n').strip()[:5000]
            return soup.get_text(separator='\n').strip()[:3000]
    except:
        pass
    return "无法抓取正文，请点击链接阅读原文。"

# ==================== Streamlit 界面 ====================
st.title("📊 财经信息聚合监控器")
st.caption("集成 Reddit 热门帖子与全球财经 RSS 实时新闻 (双语对照版)")

# 侧边栏设置
with st.sidebar:
    st.header("控制面板")
    enable_reddit = st.checkbox("开启 Reddit 监控", value=True)
    enable_rss = st.checkbox("开启 RSS 新闻", value=True)
    limit = st.slider("每源抓取数量", 5, 25, 10)
    
    st.divider()
    if st.button("🚀 开始同步数据", type="primary"):
        st.session_state.data_loaded = True
    
    if st.button("🗑 清空缓存"):
        st.session_state.posts = []
        st.rerun()

# 初始化数据容器
if 'posts' not in st.session_state:
    st.session_state.posts = []

# 执行抓取逻辑
if st.session_state.get('data_loaded'):
    new_posts = []
    progress_bar = st.progress(0)
    status_text = st.empty()
    
    sources = []
    if enable_reddit:
        for cat, subs in SUBREDDITS_BY_CATEGORY.items():
            for s in subs: sources.append(('reddit', s))
    if enable_rss:
        for name, url in RSS_FEEDS.items():
            sources.append(('rss', (name, url)))

    for i, src in enumerate(sources):
        step = (i + 1) / len(sources)
        progress_bar.progress(step)
        
        if src[0] == 'reddit':
            sub = src[1]
            status_text.text(f"正在扫描 Reddit: r/{sub}...")
            try:
                r = requests.get(f"https://www.reddit.com/r/{sub}/new.json?limit={limit}", 
                                 headers={'User-Agent': 'StockBot/1.0'}, timeout=10)
                for post in r.json()['data']['children']:
                    p = post['data']
                    new_posts.append({
                        'time': datetime.fromtimestamp(p['created_utc']).strftime("%H:%M"),
                        'source': f"r/{sub}",
                        'title': p['title'],
                        'body': p['selftext'],
                        'url': f"https://www.reddit.com{p['permalink']}",
                        'type': '🔴 Reddit'
                    })
            except: continue
            
        else:
            name, url = src[1]
            status_text.text(f"正在抓取 RSS: {name}...")
            try:
                r = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=10)
                root = ET.fromstring(r.content)
                for item in root.findall('.//item')[:limit]:
                    new_posts.append({
                        'time': "刚刚",
                        'source': name,
                        'title': item.find('title').text,
                        'body': "", # RSS 默认无正文，点击详情时动态抓取
                        'url': item.find('link').text,
                        'type': '📰 RSS'
                    })
            except: continue

    st.session_state.posts = new_posts + st.session_state.posts
    st.session_state.data_loaded = False
    status_text.success(f"同步完成！新增 {len(new_posts)} 条数据。")

# 数据显示区
st.divider()
search_kw = st.text_input("🔍 搜索关键词 (标题或来源)", "")

filtered_posts = [p for p in st.session_state.posts if search_kw.lower() in p['title'].lower() or search_kw.lower() in p['source'].lower()]

if not filtered_posts:
    st.info("暂无数据，请点击左侧「开始同步」。")
else:
    for idx, post in enumerate(filtered_posts):
        with st.expander(f"【{post['type']} | {post['time']}】 {post['title']}"):
            col1, col2 = st.columns(2)
            
            with col1:
                st.subheader("原文内容")
                st.write(f"**来源:** {post['source']}")
                st.write(f"**标题:** {post['title']}")
                if st.button("查看/抓取正文", key=f"body_{idx}"):
                    if not post['body']:
                        with st.spinner('正在提取网页正文...'):
                            post['body'] = fetch_article_body(post['url'])
                    st.text_area("原文正文", post['body'], height=200)
            
            with col2:
                st.subheader("中文翻译")
                if st.button("🔀 点击翻译标题和正文", key=f"trans_{idx}"):
                    with st.spinner('翻译中...'):
                        t_title = safe_translate(post['title'])
                        st.success(f"**标题翻译:** {t_title}")
                        if post['body']:
                            t_body = safe_translate(post['body'])
                            st.text_area("正文翻译", t_body, height=200)
                else:
                    st.write("点击按钮进行翻译...")
            
            st.link_button("🌐 浏览器打开原文", post['url'])
