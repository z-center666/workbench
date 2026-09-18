#!/usr/bin/env python3
"""
知识库收件箱 - 消息处理服务
监听飞书机器人消息，自动提取链接/创建文档，写入多维表格
"""

import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta

# 配置
LARK_CLI = os.path.expanduser("~/.trae-cn/plugins/trae-remote-official/lark/1.0.3/bin/lark-cli")
BASE_TOKEN = "UbgNbjn2GaUCJ0sVMHbcOUlAn5d"
TABLE_ID = "tblK5vgKxnhPOgMK"
FOLDER_TOKEN = "DCgsfCBGllj9BkdJhJ1cqMY3n9e"  # 知识问答文件夹
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".inbox_state.json")

# 视频平台域名
VIDEO_DOMAINS = [
    "douyin.com", "iesdouyin.com",  # 抖音
    "xiaohongshu.com", "xhslink.com",  # 小红书
    "channels.weixin.qq.com", "finder.video.qq.com",  # 视频号
    "bilibili.com", "b23.tv",  # B站
    "kuaishou.com",  # 快手
    "youtube.com", "youtu.be",  # YouTube
    "tiktok.com",  # TikTok
]

def run_lark(args, timeout=30):
    """执行 lark-cli 命令"""
    cmd = [LARK_CLI] + args
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if result.stdout:
            try:
                return json.loads(result.stdout)
            except json.JSONDecodeError:
                return {"ok": True, "raw": result.stdout}
        if result.stderr:
            print(f"[WARN] lark-cli stderr: {result.stderr}", file=sys.stderr)
        return {"ok": False, "error": result.stderr}
    except subprocess.TimeoutExpired:
        print(f"[ERROR] lark-cli timeout: {' '.join(args[:5])}", file=sys.stderr)
        return {"ok": False, "error": "timeout"}
    except Exception as e:
        print(f"[ERROR] lark-cli exception: {e}", file=sys.stderr)
        return {"ok": False, "error": str(e)}

def extract_urls(text):
    """从文本中提取所有 http/https 链接"""
    url_pattern = r'https?://[^\s<>"\')\]]+[^\s<>"\')\]\.,;!?。，；：！？）】》]'
    urls = re.findall(url_pattern, text)
    return list(dict.fromkeys(urls))  # 去重保序

def is_video_url(url):
    """判断是否为视频平台链接"""
    url_lower = url.lower()
    return any(domain in url_lower for domain in VIDEO_DOMAINS)

def extract_user_tags(text):
    """
    从消息中提取用户自定义标签（最高优先级）
    格式：标签：标签1、标签2  （可在消息任意位置）
    返回 (用户标签列表, 去除标签行后的正文)
    """
    lines = text.strip().split('\n')
    tags = []
    content_lines = []
    
    for line in lines:
        stripped = line.strip()
        # 匹配 "标签：" 或 "标签:" 开头的行
        match = re.match(r'^标签[：:]\s*(.+)$', stripped)
        if match:
            tags_str = match.group(1).strip()
            # 用常见分隔符拆分（不含单个.，以保留 React.js 等技术词；但 …/…/..+ 视为分隔）
            parts = re.split(r'[,，、;；。\s]+|\.{2,}|…+', tags_str)
            tags.extend([_strip_punctuation(t) for t in parts if t.strip() and _strip_punctuation(t)])
        else:
            content_lines.append(line)
    
    return tags, '\n'.join(content_lines).strip()

# 无意义词和噪音文本（分享模板词、平台引导词等）
_NOISE_WORDS = {
    # 中文停用词/功能词
    '可以', '这个', '那个', '什么', '怎么', '为什么', '我们', '他们', '她们',
    '自己', '一个', '没有', '不是', '就是', '还是', '但是', '如果', '已经',
    '通过', '进行', '使用', '相关', '其中', '以及', '或者', '而且', '因为',
    '所以', '虽然', '然后', '这样', '那样', '非常', '比较', '一些', '一种',
    '真的', '觉得', '知道', '看看', '告诉', '复制', '打开', '精彩',
    '内容', '作品', '分享', '推荐', '笔记', '视频', '文章', '链接',
    '今天', '昨天', '明天', '大家', '学习', '入门', '入门到', '跟着',
    '分享视频', '介绍了', '非常好', '非常好用', '推荐大家', '跟着学',
    # 英文停用词
    'the', 'and', 'for', 'that', 'this', 'with', 'from', 'are', 'was',
    'have', 'has', 'been', 'will', 'not', 'but', 'you', 'your', 'just',
}

# 噪音短语/句子片段模式（匹配这些模式的片段不适合作为标签）
_NOISE_PATTERNS = [
    re.compile(r'^的'),           # 以"的"开头
    re.compile(r'(的视频|的文章|的教程|的内容|的作品)$'),  # 以"的XX"结尾
    re.compile(r'了$'),           # 以"了"结尾（动词过去式）
    re.compile(r'^(如何|怎么|怎样|关于)'),  # 以疑问词/介词开头
    re.compile(r'(推荐|介绍|分享|学习|看了)'),  # 包含分享类动词
    re.compile(r'^(只|就|也|都|还|又|再)'),   # 以副词开头
    re.compile(r'(就对了|就够了|就行了|就可以了)'),  # 口语结尾
    re.compile(r'^从.{1,4}到'),    # "从X到Y"结构
    re.compile(r'(非常好用|好用)'),  # 评价词
    re.compile(r'(把.{1,4}变)'),    # "把X变Y"口语结构
    re.compile(r'(到\w+$)'),        # "到X"结尾
    re.compile(r'(的演进之路|的进阶)'), # 固定尾部
]

def _is_noise_phrase(text):
    """判断是否为噪音短语"""
    for pattern in _NOISE_PATTERNS:
        if pattern.search(text):
            return True
    return False

# 中英文标点符号（从标签中移除，不含 . 以保留 React.js 等技术词）
_PUNCT_RE = re.compile(r'[,，。;；:：!！?？、…·~\-\u2014\u2013\u2018\u2019\u201c\u201d\u3001\u3002\uff01\uff0c\uff1b\uff1a\uff1f\u300a\u300b\uff08\uff09\(\)\[\]\{\}]+')

def _strip_punctuation(text):
    """移除标签中的标点符号"""
    return _PUNCT_RE.sub('', text).strip()

def _strip_noise_suffix(text):
    """剥离噪音后缀，提取核心实体"""
    suffixes = ['的视频', '的文章', '的教程', '的内容', '的作品', '的笔记',
                '的演进之路', '的进阶', '框架非常', '框架']
    for s in suffixes:
        if text.endswith(s) and len(text) > len(s) + 1:
            text = text[:-len(s)]
    return text

def _extract_bracket_tags(text):
    """提取【】内的内容（第二优先级）"""
    matches = re.findall(r'[【\[]([^】\]]+)[】\]]', text)
    tags = []
    for m in matches:
        m = _strip_punctuation(m)
        # 去掉分享模板后缀："的作品"、"的视频"、"的文章"
        m = re.sub(r'的(作品|视频|文章|笔记|内容|教程)$', '', m)
        if m and 2 <= len(m) <= 20:
            tags.append(m)
    return tags

def _clean_share_noise(text):
    """清除分享模板噪音"""
    # 移除 URL
    text = re.sub(r'https?://\S+', '', text)
    # 移除抖音/小红书分享模板
    text = re.sub(r'\d+\s*复制打开.*?看看', '', text)
    text = re.sub(r'复制打开抖音.*?看看', '', text)
    text = re.sub(r'复制打开小红书.*?看看', '', text)
    text = re.sub(r'复制这条消息.*?打开', '', text)
    # 移除"看看"独立出现
    text = re.sub(r'(?<![一-鿿])看看(?![一-鿿])', '', text)
    # 移除常见分享引导语
    text = re.sub(r'[【\[][^】\]]*[】\]]', '', text)  # 移除【】内容（已单独提取）
    # 移除 hashtag（但保留 # 后面的中文内容作为候选）
    hashtags = re.findall(r'#\s*([一-鿿\w]{2,})', text)
    text = re.sub(r'#\s*\S+', '', text)
    for ht in hashtags:
        text += ' ' + ht  # 将 hashtag 内容追加回文本
    # 移除短链接代码段 (如 fBG:/ Q@k.cn :5pm 01/27)
    text = re.sub(r'[a-zA-Z0-9]+[:/]\s*\S+', '', text)
    # 移除时间戳样式的文本
    text = re.sub(r'\d+[/:.]\d+[/:.]\d+', '', text)
    text = re.sub(r'\d+[ap]m\s*\d+[/:]\d+', '', text)
    # 移除开头的纯数字
    text = re.sub(r'^[\d.\s]+', '', text)
    return text.strip()

def generate_tags_from_content(content):
    """
    从内容中提取有意义的实体标签（最多5个）
    优先级：用户标签 > 【】内内容 > 有意义的短语/实体
    """
    # 1. 先提取【】内的内容（高优先级）
    bracket_tags = _extract_bracket_tags(content)
    
    # 2. 清理噪音后，按分隔符切分成有意义的片段
    clean = _clean_share_noise(content)
    
    # 按标点、空格、换行切分，保留有意义的片段
    segments = re.split(r'[，,。！？\n\r\s!?;；:：、]+', clean)
    segments = [s.strip() for s in segments if s.strip()]
    
    # 从片段中提取候选实体
    scores = {}
    
    # 【】标签直接高分
    for t in bracket_tags:
        scores[t] = scores.get(t, 0) + 100
    
    # 从每个片段中提取有意义的实体
    for seg in segments:
        # 跳过太短的片段
        if len(seg) < 2:
            continue
        # 跳过纯噪音词
        if seg in _NOISE_WORDS:
            continue
        
        # 提取中文连续字符片段作为候选
        cn_parts = re.findall(r'[\u4e00-\u9fff]{2,}', seg)
        for part in cn_parts:
            if part in _NOISE_WORDS:
                continue
            # 先尝试剥离噪音后缀
            cleaned_part = _strip_noise_suffix(part)
            if cleaned_part in _NOISE_WORDS or _is_noise_phrase(cleaned_part):
                continue
            # 优先选较长但完整的片段（<=8字视为实体）
            if len(cleaned_part) <= 8:
                scores[cleaned_part] = scores.get(cleaned_part, 0) + len(cleaned_part) * 2
            else:
                # 对长片段，取前8字
                head = cleaned_part[:8]
                if not _is_noise_phrase(head):
                    scores[head] = scores.get(head, 0) + len(head)
        
        # 提取英文/技术术语
        en_parts = re.findall(r'[A-Za-z][A-Za-z0-9+#.\-]{1,}', seg)
        for part in en_parts:
            pl = part.lower()
            if pl in _NOISE_WORDS:
                continue
            # 过滤短域名类字符串（如 k.cn, abc.com）
            if '.' in part and len(part) <= 6:
                continue
            # 过滤时间标识（am/pm）和单字母
            if pl in ('am', 'pm', 'a', 'i') or len(part) <= 1:
                continue
            scores[part] = scores.get(part, 0) + len(part) * 2
    
    # 按分数排序
    ranked = sorted(scores.items(), key=lambda x: -x[1])
    
    # 去重选标签（子串去重）
    result = []
    for tag, score in ranked:
        if score <= 0:
            continue
        tag = _strip_punctuation(tag)
        if not tag or len(tag) < 2:
            continue
        is_sub = False
        for existing in result:
            if tag in existing or existing in tag:
                is_sub = True
                break
        if not is_sub:
            result.append(tag)
        if len(result) >= 5:
            break
    
    return result

def create_feishu_doc(title, content):
    """创建飞书文档并返回链接"""
    result = run_lark([
        "docs", "+create",
        "--title", title,
        "--parent-token", FOLDER_TOKEN,
        "--content", content,
        "--doc-format", "markdown",
        "--as", "user",
        "--format", "json"
    ])
    
    if result.get("ok"):
        data = result.get("data", {})
        # 尝试多种可能的返回结构
        doc_url = (
            data.get("url") or 
            data.get("doc", {}).get("url") or
            data.get("document", {}).get("url")
        )
        return doc_url
    else:
        print(f"[ERROR] Failed to create doc: {result}", file=sys.stderr)
        return None

def add_record_to_bitable(知识类型, 链接, 标签列表):
    """向多维表格添加记录"""
    # 构建 batch-create JSON
    fields = ["知识类型", "链接", "标签", "标签2", "标签3", "标签4", "标签5"]
    row = [知识类型, 链接]
    
    # 添加标签（不足时用 null）
    for i in range(5):
        if i < len(标签列表) and 标签列表[i]:
            row.append(标签列表[i])
        else:
            row.append(None)
    
    batch_json = {"fields": fields, "rows": [row]}
    
    result = run_lark([
        "base", "+record-batch-create",
        "--base-token", BASE_TOKEN,
        "--table-id", TABLE_ID,
        "--json", json.dumps(batch_json, ensure_ascii=False),
        "--as", "user",
        "--format", "json"
    ])
    
    return result.get("ok", False)

def load_state():
    """加载处理状态"""
    try:
        with open(STATE_FILE, 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"processed_ids": [], "last_catchup_at": None}

def save_state(state):
    """保存处理状态（只保留最近500条ID，防止文件过大）"""
    state["processed_ids"] = state["processed_ids"][-500:]
    try:
        with open(STATE_FILE, 'w') as f:
            json.dump(state, f, ensure_ascii=False)
    except Exception as e:
        print(f"[WARN] Failed to save state: {e}", file=sys.stderr)

def mark_processed(state, message_id):
    """标记消息已处理"""
    if message_id and message_id not in state["processed_ids"]:
        state["processed_ids"].append(message_id)

def catchup_messages(state):
    """
    开机补处理：拉取机器人最近的 P2P 消息，处理离线期间遗漏的消息。
    飞书 WebSocket 不缓存断线期间的事件，所以需要主动拉取聊天历史来补处理。
    """
    print("[CATCHUP] Checking for missed messages...")
    
    # 获取机器人的 P2P 聊天列表
    result = run_lark([
        "im", "+chat-list", "--as", "bot", "--format", "json"
    ], timeout=15)
    
    if not result.get("ok"):
        print(f"[CATCHUP] Failed to get chat list: {result.get('error')}")
        return 0
    
    chats = result.get("data", {}).get("chats", []) or []
    processed_count = 0
    
    for chat in chats:
        chat_id = chat.get("chat_id", "")
        chat_type = chat.get("chat_type", "")
        
        # 只处理 P2P 聊天
        if chat_type != "p2p":
            continue
        
        # 拉取最近10条消息
        msg_result = run_lark([
            "im", "+chat-messages-list",
            "--chat-id", chat_id,
            "--as", "bot",
            "--format", "json"
        ], timeout=15)
        
        if not msg_result.get("ok"):
            continue
        
        messages = msg_result.get("data", {}).get("messages", []) or []
        
        for msg in messages:
            message_id = msg.get("message_id", "")
            sender = msg.get("sender", {})
            sender_type = sender.get("sender_type", "")
            content = msg.get("content", "")
            sender_id = sender.get("id", "")
            
            # 跳过机器人自己发的消息
            if sender_type == "app":
                continue
            
            # 跳过已处理的消息
            if message_id in state["processed_ids"]:
                continue
            
            # 处理消息
            if content and sender_id:
                print(f"[CATCHUP] Processing missed message: {content[:100]}...")
                process_message(content, sender_id, message_id)
                mark_processed(state, message_id)
                processed_count += 1
    
    state["last_catchup_at"] = datetime.now().isoformat()
    save_state(state)
    print(f"[CATCHUP] Done. Processed {processed_count} missed message(s).")
    return processed_count

def process_message(content, sender_id, message_id=""):
    """处理收到的消息"""
    print(f"[INFO] Processing message from {sender_id}: {content[:100]}...")
    
    # 提取用户自定义标签
    user_tags, content_without_tags = extract_user_tags(content)
    
    # 提取URL
    urls = extract_urls(content_without_tags)
    
    if urls:
        # 有链接的情况
        for url in urls:
            知识类型 = "视频" if is_video_url(url) else "文本"
            
            # 生成标签（最多5个）
            tags = list(user_tags)  # 用户标签优先
            if len(tags) < 5:
                auto_tags = generate_tags_from_content(content_without_tags)
                for t in auto_tags:
                    if t not in tags and len(tags) < 5:
                        tags.append(t)
            
            print(f"[INFO] Adding URL record: type={知识类型}, url={url[:80]}..., tags={tags[:5]}")
            success = add_record_to_bitable(知识类型, url, tags[:5])
            
            if success:
                print(f"[OK] Record added successfully")
                # 回复确认
                tag_str = ', '.join(tags[:5]) if tags else '无'
                reply_text = f"已收藏到知识库收件箱\\n类型：{知识类型}\\n标签：{tag_str}"
                run_lark([
                    "im", "+messages-reply",
                    "--message-id", message_id,
                    "--text", reply_text,
                    "--as", "bot"
                ], timeout=10)
            else:
                print(f"[ERROR] Failed to add record")
    else:
        # 纯文本内容 - 创建飞书文档
        text_content = content_without_tags.strip()
        if not text_content:
            print("[WARN] Empty message, skipping")
            return
        
        # 生成文档标题（按时间命名）
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        doc_title = f"收藏_{timestamp}"
        
        # 生成标签（最多5个）
        tags = list(user_tags)
        if len(tags) < 5:
            auto_tags = generate_tags_from_content(text_content)
            for t in auto_tags:
                if t not in tags and len(tags) < 5:
                    tags.append(t)
        
        # 创建飞书文档
        print(f"[INFO] Creating Feishu doc: {doc_title}")
        doc_url = create_feishu_doc(doc_title, text_content)
        
        if doc_url:
            print(f"[OK] Doc created: {doc_url}")
            # 写入多维表格
            success = add_record_to_bitable("文本", doc_url, tags[:5])
            if success:
                print(f"[OK] Record added with doc link")
                tag_str = ', '.join(tags[:5]) if tags else '无'
                reply_text = f"已创建文档并收藏\\n文档：{doc_title}\\n标签：{tag_str}"
                run_lark([
                    "im", "+messages-reply",
                    "--message-id", message_id,
                    "--text", reply_text,
                    "--as", "bot"
                ], timeout=10)
        else:
            print(f"[ERROR] Failed to create doc")

def main():
    """主循环 - 消费事件并处理消息"""
    print("=" * 60)
    print("知识库收件箱 - 消息处理服务")
    print(f"Base: {BASE_TOKEN}")
    print(f"Table: {TABLE_ID}")
    print("=" * 60)
    print()
    
    # 加载状态
    state = load_state()
    print(f"[STATE] Loaded {len(state['processed_ids'])} processed message IDs")
    if state.get("last_catchup_at"):
        print(f"[STATE] Last catchup: {state['last_catchup_at']}")
    
    # 开机补处理：拉取离线期间的遗漏消息
    catchup_messages(state)
    
    # 构建事件消费命令
    cmd = [
        LARK_CLI, "event", "consume", "im.message.receive_v1",
        "--as", "bot",
        "--timeout", "999h"  # 长时间运行
    ]
    
    print(f"[INFO] Starting event consumer...")
    
    while True:
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1
            )
            
            print(f"[INFO] Event consumer started (pid={proc.pid})")
            
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                
                # 跳过非JSON行（如 [event] 开头的信息行）
                if not line.startswith('{'):
                    print(f"[EVENT] {line}")
                    continue
                
                try:
                    event = json.loads(line)
                    content = event.get("content", "")
                    sender_id = event.get("sender_id", "")
                    message_id = event.get("message_id", "")
                    
                    # 跳过已处理的消息
                    if message_id in state["processed_ids"]:
                        continue
                    
                    if content and sender_id:
                        print(f"\n[MSG] from {sender_id}: {content[:200]}")
                        process_message(content, sender_id, message_id)
                        mark_processed(state, message_id)
                        save_state(state)
                        
                except json.JSONDecodeError:
                    print(f"[WARN] Invalid JSON: {line[:200]}")
            
            # 如果进程结束，等待一下再重启
            proc.wait()
            print(f"[WARN] Event consumer exited with code {proc.returncode}, restarting in 5s...")
            time.sleep(5)
            
            # 重连时也做一次补处理
            catchup_messages(state)
            
        except KeyboardInterrupt:
            print("\n[INFO] Shutting down...")
            save_state(state)
            if 'proc' in locals():
                proc.terminate()
            break
        except Exception as e:
            print(f"[ERROR] Main loop error: {e}", file=sys.stderr)
            time.sleep(5)

if __name__ == "__main__":
    main()
