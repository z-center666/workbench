#!/usr/bin/env python3
"""
AI 新闻简报抓取脚本
每日抓取主流平台 AI 相关新闻，生成 news_data.js 供工作台展示。

使用方式：
  python3 fetch_news.py

输出：
  news_data.js — 包含今日 AI 新闻简报数据
"""

import json
import os
import ssl
import sys
import time
import re
from datetime import datetime, timedelta
from urllib.request import urlopen, Request
from urllib.error import URLError
import xml.etree.ElementTree as ET

# 绕过 SSL 证书验证（macOS 常见问题）
SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE

# ========== 配置 ==========

RSS_SOURCES = [
    {"name": "36kr", "url": "https://36kr.com/feed", "category": "科技"},
    {"name": "机器之心", "url": "https://www.jiqizhixin.com/rss", "category": "AI"},
    {"name": "量子位", "url": "https://www.qbitai.com/feed", "category": "AI"},
    {"name": "InfoQ", "url": "https://www.infoq.cn/feed", "category": "技术"},
    {"name": "极客公园", "url": "https://www.geekpark.net/rss", "category": "科技"},
]

AI_KEYWORDS = [
    "AI", "人工智能", "大模型", "GPT", "Claude", "Gemini", "LLM",
    "OpenAI", "DeepSeek", "智能体", "Agent", "RAG", "向量",
    "机器学习", "深度学习", "神经网络", "AIGC", "多模态",
    "Copilot", "通义", "文心", "智谱", "具身智能", "推理"
]

MAX_ITEMS_PER_SOURCE = 5
MAX_TOTAL_ITEMS = 20

# ========== RSS 抓取 ==========

def fetch_rss(source):
    """抓取单个 RSS 源"""
    try:
        req = Request(source["url"], headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
        })
        with urlopen(req, timeout=10, context=SSL_CTX) as resp:
            data = resp.read().decode("utf-8", errors="ignore")
        return parse_rss(data, source)
    except Exception as e:
        print(f"  [警告] 抓取 {source['name']} 失败: {e}", file=sys.stderr)
        return []


def parse_rss(xml_text, source):
    """解析 RSS XML"""
    items = []
    try:
        root = ET.fromstring(xml_text)
        # RSS 2.0
        for item in root.findall(".//item"):
            title = item.findtext("title", "")
            link = item.findtext("link", "")
            desc = item.findtext("description", "")
            pub_date = item.findtext("pubDate", "")
            
            if not title:
                continue
            
            # 清理 HTML 标签
            desc = re.sub(r'<[^>]+>', '', desc)[:200]
            
            items.append({
                "title": title.strip(),
                "link": link.strip(),
                "description": desc.strip(),
                "source": source["name"],
                "category": source["category"],
                "pub_date": pub_date.strip(),
            })
    except ET.ParseError as e:
        print(f"  [警告] 解析 {source['name']} RSS 失败: {e}", file=sys.stderr)
    
    return items


def is_ai_related(item):
    """判断新闻是否与 AI 相关"""
    text = (item.get("title", "") + " " + item.get("description", "")).lower()
    for kw in AI_KEYWORDS:
        if kw.lower() in text:
            return True
    return False


def filter_recent(items, hours=48):
    """过滤最近 N 小时的新闻"""
    now = time.time()
    recent = []
    for item in items:
        pub = item.get("pub_date", "")
        try:
            # 尝试解析日期
            from email.utils import parsedate_to_datetime
            dt = parsedate_to_datetime(pub)
            ts = dt.timestamp()
            if (now - ts) < hours * 3600:
                recent.append(item)
        except Exception:
            # 无法解析日期的也保留
            recent.append(item)
    return recent


# ========== 主流程 ==========

def main():
    print(f"=== AI 新闻简报抓取 {datetime.now().strftime('%Y-%m-%d %H:%M')} ===")
    
    all_items = []
    
    for source in RSS_SOURCES:
        print(f"正在抓取: {source['name']}...")
        items = fetch_rss(source)
        print(f"  获取到 {len(items)} 条")
        
        # 过滤 AI 相关
        ai_items = [item for item in items if is_ai_related(item)]
        print(f"  AI 相关: {len(ai_items)} 条")
        
        # 限制每源数量
        all_items.extend(ai_items[:MAX_ITEMS_PER_SOURCE])
    
    # 限制总量
    all_items = all_items[:MAX_TOTAL_ITEMS]
    
    # 去重（按标题）
    seen = set()
    deduped = []
    for item in all_items:
        key = item["title"][:50]
        if key not in seen:
            seen.add(key)
            deduped.append(item)
    
    print(f"\n总计: {len(deduped)} 条 AI 新闻")
    
    # 生成简报摘要
    briefing = generate_briefing(deduped)
    
    # 输出 JS 文件
    output = {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "time": datetime.now().strftime("%H:%M"),
        "weekday": ["周一","周二","周三","周四","周五","周六","周日"][datetime.now().weekday()],
        "total": len(deduped),
        "briefing": briefing,
        "items": deduped,
    }
    
    js_content = f"// 自动生成于 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
    js_content += "// 由 fetch_news.py 生成，请勿手动编辑\n"
    js_content += "var NEWS_DATA = " + json.dumps(output, ensure_ascii=False, indent=2) + ";\n"
    
    output_dir = os.path.dirname(os.path.abspath(__file__))
    output_path = os.path.join(output_dir, "news_data.js")
    
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(js_content)
    
    print(f"\n已生成: {output_path}")
    return output_path


def generate_briefing(items):
    """生成简报摘要"""
    if not items:
        return "今日暂无 AI 相关新闻。"
    
    # 按来源统计
    source_count = {}
    for item in items:
        src = item["source"]
        source_count[src] = source_count.get(src, 0) + 1
    
    summary = f"今日共抓取 {len(items)} 条 AI 相关新闻"
    if source_count:
        top_sources = sorted(source_count.items(), key=lambda x: -x[1])[:3]
        summary += f"，来源: {', '.join(f'{s}({c}条)' for s, c in top_sources)}"
    summary += "。"
    
    # 提取热点关键词
    titles = " ".join(item["title"] for item in items)
    hot_keywords = []
    for kw in ["GPT", "Claude", "Gemini", "DeepSeek", "OpenAI", "大模型", "智能体", "Agent", "RAG", "多模态"]:
        if kw.lower() in titles.lower():
            hot_keywords.append(kw)
    
    if hot_keywords:
        summary += f" 热点关键词: {', '.join(hot_keywords[:5])}。"
    
    return summary


if __name__ == "__main__":
    main()
