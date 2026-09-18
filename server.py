#!/usr/bin/env python3
"""
个人工作台本地服务器
提供飞书日程 API 代理和静态文件服务

使用方式：
  python3 server.py
  # 然后浏览器打开 http://localhost:8765
"""

import json
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from http.server import HTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, quote

PORT = int(os.environ.get("PORT", 8765))
WORK_DIR = os.path.dirname(os.path.abspath(__file__))

# 解析 lark-cli 可执行路径：优先 PATH，回退到插件目录
_PLUGIN_LARK = os.path.expanduser(
    "~/.trae-cn/plugins/trae-remote-official/lark/1.0.3/bin/lark-cli"
)
LARK_CLI = shutil.which("lark-cli") or (_PLUGIN_LARK if os.path.exists(_PLUGIN_LARK) else None) or os.path.expanduser("~/.local/node/bin/lark-cli")
if not LARK_CLI or not os.path.exists(LARK_CLI):
    LARK_CLI = None
    sys.stderr.write("[WARN] lark-cli 未安装，飞书相关功能将不可用\n")

# 绕过代理的环境变量
ENV = {
    **os.environ,
    "HTTP_PROXY": "",
    "HTTPS_PROXY": "",
    "http_proxy": "",
    "https_proxy": "",
    "ALL_PROXY": "",
    "all_proxy": "",
    "NO_PROXY": "*",
    "no_proxy": "*",
    "LARKSUITE_CLI_NO_UPDATE_NOTIFIER": "1",
    "LARKSUITE_CLI_NO_SKILLS_NOTIFIER": "1",
}

# 缓存当前登录用户的 open_id，用于创建待办时自动分配给本人
_USER_OPEN_ID = None

# 本地存储 AI 生成的任务备注
_TASK_NOTES_PATH = os.path.join(WORK_DIR, "task_notes.json")

def _load_task_notes():
    """加载本地 task_notes.json（按 guid 索引的备注数据）"""
    if os.path.exists(_TASK_NOTES_PATH):
        try:
            with open(_TASK_NOTES_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {}

def _save_task_notes(notes):
    """保存本地 task_notes.json"""
    try:
        with open(_TASK_NOTES_PATH, "w", encoding="utf-8") as f:
            json.dump(notes, f, ensure_ascii=False, indent=2)
    except IOError as e:
        sys.stderr.write(f"[WARN] 保存 task_notes 失败: {e}\n")

# 本地存储 AI 生成的日程备注
_AGENDA_NOTES_PATH = os.path.join(WORK_DIR, "agenda_notes.json")

def _load_agenda_notes():
    """加载本地 agenda_notes.json（按 event_id 索引的备注数据）"""
    if os.path.exists(_AGENDA_NOTES_PATH):
        try:
            with open(_AGENDA_NOTES_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {}

def _save_agenda_notes(notes):
    """保存本地 agenda_notes.json"""
    try:
        with open(_AGENDA_NOTES_PATH, "w", encoding="utf-8") as f:
            json.dump(notes, f, ensure_ascii=False, indent=2)
    except IOError as e:
        sys.stderr.write(f"[WARN] 保存 agenda_notes 失败: {e}\n")

# ── 百度网盘 Token 管理 ──

_BAIDU_TOKEN_PATH = os.path.join(WORK_DIR, "百度网盘", "baidu_token.json")
_BAIDU_FAVORITES_PATH = os.path.join(WORK_DIR, "百度网盘", "favorites.json")
_CONFIG_PATH = os.path.join(WORK_DIR, "config.json")

def _load_baidu_favorites():
    """加载百度网盘收藏夹"""
    if os.path.exists(_BAIDU_FAVORITES_PATH):
        try:
            with open(_BAIDU_FAVORITES_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return []

def _save_baidu_favorites(favorites):
    """保存百度网盘收藏夹"""
    try:
        with open(_BAIDU_FAVORITES_PATH, "w", encoding="utf-8") as f:
            json.dump(favorites, f, ensure_ascii=False, indent=2)
    except IOError as e:
        sys.stderr.write(f"[WARN] 保存 favorites 失败: {e}\n")

def _load_baidu_token():
    """加载百度网盘 OAuth token"""
    if os.path.exists(_BAIDU_TOKEN_PATH):
        try:
            with open(_BAIDU_TOKEN_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {}

def _save_baidu_token(token_data):
    """保存百度网盘 OAuth token"""
    try:
        with open(_BAIDU_TOKEN_PATH, "w", encoding="utf-8") as f:
            json.dump(token_data, f, ensure_ascii=False, indent=2)
    except IOError as e:
        sys.stderr.write(f"[WARN] 保存 baidu_token 失败: {e}\n")

def _load_config():
    """加载 config.json"""
    if os.path.exists(_CONFIG_PATH):
        try:
            with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {}

def _get_baidu_access_token():
    """获取有效的百度 access_token（自动刷新过期 token）"""
    token_data = _load_baidu_token()
    if not token_data.get("access_token"):
        return None

    # 检查是否过期（提前 5 分钟刷新）
    expires_at = token_data.get("expires_at", 0)
    if time.time() < expires_at - 300:
        return token_data["access_token"]

    # 使用 refresh_token 刷新
    config = _load_config()
    baidu_cfg = config.get("baidu_pan", {})
    app_key = baidu_cfg.get("app_key", "")
    secret_key = baidu_cfg.get("secret_key", "")
    refresh_token = token_data.get("refresh_token", "")

    if not all([app_key, secret_key, refresh_token]):
        return None

    try:
        url = (
            f"https://openapi.baidu.com/oauth/2.0/token"
            f"?grant_type=refresh_token"
            f"&refresh_token={refresh_token}"
            f"&client_id={app_key}"
            f"&client_secret={secret_key}"
        )
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        if data.get("access_token"):
            token_data = {
                "access_token": data["access_token"],
                "refresh_token": data.get("refresh_token", refresh_token),
                "expires_at": time.time() + data.get("expires_in", 2592000),
                "scope": data.get("scope", ""),
            }
            _save_baidu_token(token_data)
            return token_data["access_token"]
    except Exception as e:
        sys.stderr.write(f"[WARN] 刷新百度 token 失败: {e}\n")

    return None

def _generate_event_summary(summary, event_info=None):
    """基于日程标题和信息生成 AI 执行建议（针对日程场景的模板）
    
    日程模板特性：关注会前准备、参与人、资料、会议目标、会后跟进
    """
    text = (summary or "").strip()
    info = event_info or {}
    
    # 日程类型关键词映射
    EVENT_KEYWORDS = {
        "meeting": ["会议", "开会", "沟通", "讨论", "评审", "review", "meeting", "同步", "例会"],
        "review": ["评审", "代码审查", "code review", "复盘", "retro", "review"],
        "training": ["培训", "分享", "讲座", "workshop", "训练营", "学习"],
        "demo": ["演示", "demo", "产品演示", "presentation", "展示"],
        "interview": ["面试", "interview", "面谈"],
        "planning": ["规划", "计划", "planning", "启动会", "kickoff"],
        "personal": ["个人", "休息", "日程", "提醒"],
        "deadline": ["截止", "deadline", "交付", "提交"],
    }
    
    EVENT_TEMPLATES = {
        "meeting": [
            "🎯 会议目标：明确本次会议的核心议题与期望产出",
            "👥 参与人员：确认参会者名单、关键决策人和主持人",
            "📋 议程安排：准备 3-5 项讨论议题，预估每项耗时",
            "📎 会前资料：收集相关文档、数据、历史沟通记录并提前分享",
            "🖍️ 会场准备：确认会议地点/会议室、投影/视频设备可用",
            "📝 记录分工：指定会议记录人，准备好纪要模板",
            "⏰ 时间提醒：提前 15 分钟到达，测试设备",
        ],
        "review": [
            "🎯 评审目标：明确评审范围、验收标准和通过条件",
            "📋 准备材料：整理代码/文档/方案，准备 self-review 结论",
            "🔍 检查清单：对照 review checklist 逐项自查",
            "👥 评审人：确认评审人到位，提前发送材料",
            "💬 讨论策略：准备好回应可能的质疑和改进建议",
            "✅ 结论跟进：记录评审结论、跟进人、截止日期",
        ],
        "training": [
            "🎯 学习目标：明确培训后要掌握的核心知识点",
            "📚 预习准备：预先阅读相关材料，记录疑问点",
            "🖍️ 主动参与：积极提问、参与讨论和练习环节",
            "📝 笔记整理：用康奈尔笔记法记录核心观点和行动项",
            "🔁 课后巩固：24 小时内回顾笔记，完成配套练习",
        ],
        "demo": [
            "🎯 演示目标：明确要展示的核心价值和目标观众",
            "📽️ 演示脚本：准备开场白 → 场景串讲 → 亮点展示 → 总结",
            "🖥️ 技术准备：确认 Demo 环境可用，数据/账号已就绪",
            "🎯 观众分析：了解观众背景、关注点和可能的疑问",
            "🎁 结束跟进：准备资料包、联系方式和后续行动项",
        ],
        "interview": [
            "🎯 面试目标：明确岗位要求和面试评估维度",
            "📋 问题清单：准备 STAR 法则的案例回答（情境、任务、行动、结果）",
            "🏢 公司研究：了解公司业务、产品、近期动态",
            "👔 形象准备：确认着装要求，面试地点和时间",
            "💬 反向提问：准备 2-3 个有质量的问题展示思考",
            "✉️ 跟进致谢：面试后 24 小时内发送感谢信",
        ],
        "planning": [
            "🎯 规划目标：明确规划周期和核心目标（OKR/KPI）",
            "📊 现状分析：梳理当前数据、业务痛点和机会点",
            "💡 方案设计：设计 2-3 个候选方案，评估利弊",
            "👥 协同对齐：与核心干系人预沟通，获得初步支持",
            "📋 资源评估：预算、人力、时间、风险评估",
            "📌 里程碑：设定关键里程碑和验收标准",
        ],
        "personal": [
            "🎯 今日焦点：明确今天最重要的 1-3 件事",
            "🌅 时间分配：合理分配深度工作和沟通时间",
            "🔕 减少干扰：关闭非必要通知，预留专注时段",
            "💡 能量管理：注意劳逸结合，避免过度疲劳",
        ],
        "deadline": [
            "🎯 交付目标：明确交付物规格、验收标准和交付形式",
            "📊 进度盘点：盘点当前完成度和剩余工作项",
            "🗓️ 时间倒推：从截止日倒推，拆解每天的关键任务",
            "🚨 风险预案：识别阻塞项和风险点，准备 Plan B",
            "👥 协同确认：与干系人同步进展，确认验收安排",
            "✅ 最终检查：交付前做一次完整的 self-check",
        ],
    }
    
    # 匹配类型
    matched_types = []
    for typ, keywords in EVENT_KEYWORDS.items():
        for kw in keywords:
            if kw.lower() in text.lower():
                matched_types.append(typ)
                break
    
    # 时间信息
    time_info = ''
    if info.get('start') and info.get('end'):
        try:
            s = datetime.fromisoformat(str(info['start']))
            e = datetime.fromisoformat(str(info['end']))
            time_info = f"\n⏰ 时间：{s.strftime('%H:%M')} - {e.strftime('%H:%M')}（预计 {int((e-s).total_seconds()/60)} 分钟）"
        except (ValueError, TypeError):
            pass
    
    # 地点信息
    location_info = ''
    if info.get('location'):
        location_info = f"\n📍 地点：{info['location']}"
    
    steps = [
        f"📌 日程：{text}",
        f"🕐 创建时间：{info.get('created_at', '')[:10] if info.get('created_at') else datetime.now().strftime('%Y-%m-%d')}",
    ]
    
    if info.get('organizer'):
        steps.append(f"👤 组织者：{info['organizer']}")
    
    if matched_types:
        steps.append(f"\n━━━ 类型：{' / '.join(matched_types)} ━━━")
        steps.extend(EVENT_TEMPLATES[matched_types[0]])
    else:
        steps.append("\n━━━ 通用日程准备 ━━━")
        steps.extend([
            "🎯 核心目标：明确这次日程要达成的 1-3 个关键目标",
            "📋 日程准备：梳理讨论议题或活动流程",
            "📎 资料准备：预读相关文档、准备好需要的材料",
            "👥 了解参与者：知道谁会参加、他们的角色和立场",
            "🖍️ 设备检查：提前确认场地、投影、视频设备",
        ])
    
    steps.extend([
        "",
        "━━━ 提醒 ━━━",
        "💡 提前 10-15 分钟到达/上线",
        "💡 关键讨论点和决策要记录下来",
        "💡 结束后 24 小时内发送纪要/行动项",
    ])
    
    return "\n".join(steps)

def _generate_ai_summary(summary):
    """基于任务标题生成结构化的执行建议（可扩展为调用 LLM）
    
    采用规则化模板引擎：
    1. 识别任务类型关键词（会议/报告/代码/邮件/设计/学习/数据/沟通等）
    2. 组合通用执行步骤 + 特定类型步骤
    3. 输出分行动作清单
    """
    text = (summary or "").strip()
    
    # 任务类型关键词映射
    TYPE_KEYWORDS = {
        "meeting": ["会议", "开会", "沟通", "讨论", "评审", "review", "meeting"],
        "report": ["报告", "总结", "周报", "月报", "分析", "撰写", "撰写报告", "汇报"],
        "coding": ["代码", "开发", "实现", "修复", "bug", "debug", "编码", "功能", "接口", "api", "bug"],
        "design": ["设计", "方案", "原型", "UI", "交互", "架构", "图纸"],
        "email": ["邮件", "回复", "回复邮件", "发送", "通知"],
        "study": ["学习", "阅读", "研究", "培训", "课程", "book", "教程"],
        "data": ["数据", "报表", "统计", "分析", "excel", "报表", "导出", "图表"],
        "biz": ["客户", "销售", "合同", "拜访", "投标", "报价", "方案"],
        "admin": ["行政", "报销", "审批", "流程", "盖章", "发票"],
    }
    
    TEMPLATES = {
        "meeting": [
            "🎯 会议目标：明确本次会议的核心议题与期望产出",
            "👥 参会人员：梳理关键干系人及需邀请的对象",
            "📋 议程准备：准备 3-5 项讨论议题和时间分配",
            "📝 会前资料：收集相关文档、数据和历史沟通记录",
            "🎤 会议进行：记录决议、责任人 (RACI) 和截止日期",
            "📌 会后跟进：24 小时内发送纪要和行动项跟踪表",
        ],
        "report": [
            "🎯 报告目标：明确报告的读者、目的和核心结论",
            "📊 数据收集：汇总所需的原始数据和关键指标",
            "📈 分析框架：确定分析维度和方法论 (对比/归因/趋势)",
            "📝 结构撰写：搭建章节结构，填充分析与论据",
            "🎨 可视化：选择合适的图表辅助理解",
            "✅ 审阅交付：自检逻辑、数据准确性，按流程提交",
        ],
        "coding": [
            "🎯 需求澄清：确认功能范围、边界条件和验收标准",
            "🔍 技术调研：评估现有代码、依赖库和技术方案",
            "📐 设计拆解：按模块拆分任务，明确接口与数据结构",
            "⚙️ 编码实现：按模块实现，保持提交原子化",
            "🧪 测试验证：单元测试 + 集成测试 + 边界用例",
            "🔀 Code Review：自测后发起评审，处理反馈",
        ],
        "design": [
            "🎯 目标用户：明确用户画像与核心使用场景",
            "🔍 竞品调研：分析竞品方案、提炼设计参考",
            "📐 信息架构：梳理页面/流程/组件关系",
            "🎨 视觉设计：确定风格、配色、字体、图标风格",
            "🧩 交互原型：低保真 → 高保真，明确状态与动效",
            "✅ 设计评审：与团队/产品/开发对齐可行性",
        ],
        "email": [
            "🎯 邮件目的：明确要沟通的核心信息和期望回复",
            "👤 收件人：核对收件人、抄送人是否恰当",
            "📝 撰写内容：结构清晰 (背景/要点/行动项)",
            "📎 附件检查：确认附件齐全并标注",
            "✅ 发送前自检：检查语气、格式、错别字",
            "📅 跟进提醒：设置 1-2 天后的跟进提醒",
        ],
        "study": [
            "🎯 学习目标：明确需要掌握的知识点和可衡量的目标",
            "📚 资料收集：收集教材/文档/课程/参考案例",
            "📖 阅读笔记：采用康奈尔笔记或思维导图方式记录",
            "✍️ 动手实践：完成配套练习或小型项目",
            "💡 总结输出：撰写学习心得或分享笔记",
            "🔁 间隔复习：1/3/7 天后回顾巩固",
        ],
        "data": [
            "🎯 分析目标：明确业务问题和需要回答的核心疑问",
            "📥 数据准备：确定数据源、字段、时间范围",
            "🔍 数据清洗：处理缺失值、异常值、格式统一",
            "📊 指标定义：明确核心指标口径和计算逻辑",
            "📈 可视化分析：选择合适的图表呈现洞察",
            "📝 结论建议：输出数据支撑的结论和行动建议",
        ],
        "biz": [
            "🎯 目标客户：明确客户画像与核心痛点",
            "🔍 信息调研：收集客户背景、行业动态、决策链",
            "📝 方案撰写：围绕客户痛点定制解决方案",
            "💰 报价谈判：准备报价方案和谈判策略",
            "🤝 沟通推进：预约关键干系人、推进方案确认",
            "📋 合同执行：关注合同条款、交付节点和收款",
        ],
        "admin": [
            "🎯 目标：明确流程起点、标准和审批路径",
            "📋 材料准备：收集所需凭证/表格/签字",
            "📝 填写提交：准确填写表单，附件齐全",
            "📅 审批跟进：跟进各环节审批人，必要时提醒",
            "🗂️ 归档保存：完成后归档电子和纸质材料",
        ],
    }
    
    # 匹配类型（取优先级最高的匹配）
    matched_types = []
    for typ, keywords in TYPE_KEYWORDS.items():
        for kw in keywords:
            if kw.lower() in text.lower():
                matched_types.append(typ)
                break
    
    # 通用步骤（所有类型都包含）
    common_steps = [
        f"📌 任务：{text}",
        f"⏱️ 建议用时：根据任务复杂度预留合理时间",
    ]
    
    steps = list(common_steps)
    
    # 先放匹配类型的模板（取第一个匹配），后面放通用收尾
    if matched_types:
        steps.append(f"\n━━━ 类型：{' / '.join(matched_types)} ━━━")
        steps.extend(TEMPLATES[matched_types[0]])
    else:
        steps.append("\n━━━ 通用执行步骤 ━━━")
        steps.extend([
            "🎯 目标明确：拆解为 2-3 个可验证的小目标",
            "📐 步骤拆解：列出完成任务的关键步骤",
            "🔧 动手执行：按优先级逐步完成",
            "✅ 验收检查：回顾交付物是否达标",
            "📝 总结沉淀：记录经验和可复用方法",
        ])
    
    steps.extend([
        "",
        "━━━ 提醒 ━━━",
        "💡 开始前：关闭无关通知，预留完整专注时段",
        "💡 遇到阻塞：拆分任务或寻求协作，避免卡住",
    ])
    
    return "\n".join(steps)


class WorkbenchHandler(SimpleHTTPRequestHandler):
    """处理工作台请求"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=WORK_DIR, **kwargs)

    def do_GET(self):
        """处理 GET 请求"""
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/agenda":
            self.handle_agenda(parsed.query)
        elif path == "/api/calendars":
            self.handle_calendars()
        elif path == "/api/tasks":
            self.handle_tasks(parsed.query)
        elif path == "/api/task/note":
            self.handle_get_task_note(parsed.query)
        elif path == "/api/agenda/note":
            self.handle_get_agenda_note(parsed.query)
        elif path == "/api/news":
            self.handle_news()
        elif path == "/api/knowledge-inbox":
            self.handle_knowledge_inbox()
        elif path == "/api/health":
            self.send_json({"ok": True, "service": "workbench"})
        elif path == "/api/baidu/auth":
            self.handle_baidu_auth()
        elif path == "/baidu/callback":
            self.handle_baidu_callback(parsed.query)
        elif path == "/api/baidu/status":
            self.handle_baidu_status()
        elif path == "/api/baidu/files":
            self.handle_baidu_files(parsed.query)
        elif path == "/api/baidu/stream":
            self.handle_baidu_stream(parsed.query)
        elif path == "/api/baidu/dirs":
            self.handle_baidu_dirs(parsed.query)
        elif path == "/api/baidu/favorites":
            self.handle_baidu_favorites_list()
        else:
            super().do_GET()

    def do_POST(self):
        """处理 POST 请求"""
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/event":
            self.handle_create_event()
        elif path == "/api/event/delete":
            self.handle_delete_event()
        elif path == "/api/task":
            self.handle_create_task()
        elif path == "/api/task/complete":
            self.handle_complete_task()
        elif path == "/api/task/reopen":
            self.handle_reopen_task()
        elif path == "/api/task/delete":
            self.handle_delete_task()
        elif path == "/api/task/note":
            self.handle_save_task_note()
        elif path == "/api/task/ai-summary":
            self.handle_generate_ai_summary()
        elif path == "/api/agenda/note":
            self.handle_save_agenda_note()
        elif path == "/api/agenda/ai-summary":
            self.handle_generate_agenda_summary()
        elif path == "/api/refresh-news":
            self.handle_refresh_news()
        elif path == "/api/baidu/exchange-code":
            self.handle_baidu_exchange_code()
        elif path == "/api/baidu/favorite":
            self.handle_baidu_favorite_toggle()
        else:
            self.send_error(404, "Not Found")

    def handle_agenda(self, query_string=""):
        """获取飞书日程（跨所有日历），支持 day=yesterday/today/tomorrow 参数"""
        try:
            from concurrent.futures import ThreadPoolExecutor, as_completed
            from datetime import timedelta
            from urllib.parse import parse_qs

            qs = parse_qs(query_string)
            day = qs.get("day", ["today"])[0]

            now = datetime.now()
            if day == "yesterday":
                day_offset = -1
                date_label = "昨日"
            elif day == "tomorrow":
                day_offset = 1
                date_label = "明日"
            else:
                day_offset = 0
                date_label = "今日"

            start_date = (now + timedelta(days=day_offset)).strftime("%Y-%m-%d")
            end_date = (now + timedelta(days=day_offset + 1)).strftime("%Y-%m-%d")

            # 先获取日历列表
            cal_result = subprocess.run(
                [LARK_CLI, "calendar", "calendars", "list", "--as", "user", "--format", "json"],
                capture_output=True, text=True, timeout=15, env=ENV,
            )
            calendar_ids = []  # 不再硬编码 "primary"，直接用日历列表
            cal_names = {}
            primary_id = ""
            if cal_result.returncode == 0:
                cal_data = json.loads(cal_result.stdout)
                for cal in cal_data.get("data", {}).get("calendar_list", []):
                    if not cal.get("is_deleted"):
                        cid = cal.get("calendar_id", "")
                        if cid:
                            calendar_ids.append(cid)
                            cal_names[cid] = cal.get("summary", "")
                            if cal.get("type") == "primary":
                                primary_id = cid
            # 如果没有获取到任何日历，回退到 primary
            if not calendar_ids:
                calendar_ids = ["primary"]

            # 并行查询所有日历的今日日程
            def query_calendar(cal_id):
                cmd = [LARK_CLI, "calendar", "+agenda",
                       "--start", start_date, "--end", end_date,
                       "--calendar-id", cal_id,
                       "--as", "user", "--format", "json"]
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=15, env=ENV)
                if r.returncode == 0:
                    d = json.loads(r.stdout)
                    evts = d.get("data", []) if isinstance(d.get("data"), list) else []
                    return [(cal_id, e) for e in evts]
                return []

            all_events = []
            with ThreadPoolExecutor(max_workers=5) as executor:
                futures = [executor.submit(query_calendar, cid) for cid in calendar_ids]
                for f in as_completed(futures):
                    all_events.extend(f.result())

            # 简化并去重（按 event_id 去重）
            simplified = []
            seen_ids = set()
            for cal_id, evt in all_events:
                eid = evt.get("event_id", "")
                if eid and eid in seen_ids:
                    continue
                if eid:
                    seen_ids.add(eid)
                start = evt.get("start_time", {}).get("datetime", "")
                end = evt.get("end_time", {}).get("datetime", "")
                simplified.append({
                    "event_id": eid,
                    "summary": evt.get("summary", ""),
                    "start": start,
                    "end": end,
                    "vchat_url": evt.get("vchat", {}).get("meeting_url", ""),
                    "organizer": evt.get("event_organizer", {}).get("display_name", ""),
                    "app_link": evt.get("app_link", ""),
                    "calendar_id": cal_id,
                    "calendar_name": cal_names.get(cal_id, ""),
                })
            # 按开始时间排序
            simplified.sort(key=lambda x: x.get("start", ""))
            self.send_json({"ok": True, "events": simplified, "total": len(simplified), "date_label": date_label})
        except json.JSONDecodeError:
            self.send_json({"ok": False, "error": "解析日程数据失败"}, 500)
        except subprocess.TimeoutExpired:
            self.send_json({"ok": False, "error": "查询超时"}, 504)
        except Exception as e:
            self.send_json({"ok": False, "error": str(e)}, 500)

    def handle_calendars(self):
        """获取飞书日历列表（同步日历类型）"""
        try:
            result = subprocess.run(
                [LARK_CLI, "calendar", "calendars", "list", "--as", "user", "--format", "json"],
                capture_output=True,
                text=True,
                timeout=15,
                env=ENV,
            )
            if result.returncode == 0:
                data = json.loads(result.stdout)
                raw_list = data.get("data", {}).get("calendar_list", [])
                if not raw_list:
                    # 兼容部分版本直接返回数组的情况
                    raw_list = data.get("data", []) if isinstance(data.get("data"), list) else []
                simplified = []
                for cal in raw_list:
                    if cal.get("is_deleted"):
                        continue
                    simplified.append({
                        "calendar_id": cal.get("calendar_id", ""),
                        "summary": cal.get("summary", "未命名日历"),
                        "summary_alias": cal.get("summary_alias", ""),
                        "type": cal.get("type", "unknown"),
                        "role": cal.get("role", "unknown"),
                        "description": cal.get("description", ""),
                        "is_third_party": cal.get("is_third_party", False),
                        "permissions": cal.get("permissions", ""),
                    })
                self.send_json({"ok": True, "calendars": simplified, "total": len(simplified)})
            else:
                self.send_json({"ok": False, "error": result.stderr.strip() or "lark-cli 执行失败"}, 500)
        except json.JSONDecodeError:
            self.send_json({"ok": False, "error": "解析日历数据失败"}, 500)
        except subprocess.TimeoutExpired:
            self.send_json({"ok": False, "error": "查询超时"}, 504)
        except Exception as e:
            self.send_json({"ok": False, "error": str(e)}, 500)

    def handle_create_event(self):
        """创建飞书日程，可指定日历类型"""
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length).decode("utf-8")

        try:
            params = json.loads(body)
        except json.JSONDecodeError:
            self.send_json({"ok": False, "error": "无效的 JSON"}, 400)
            return

        summary = params.get("summary", "").strip()
        start = params.get("start", "").strip()
        end = params.get("end", "").strip()
        desc = params.get("description", "").strip()
        calendar_id = params.get("calendar_id", "").strip()

        if not summary or not start or not end:
            self.send_json({"ok": False, "error": "缺少必要参数: summary, start, end"}, 400)
            return

        cmd = [
            LARK_CLI, "calendar", "+create",
            "--summary", summary,
            "--start", start,
            "--end", end,
            "--as", "user",
            "--format", "json",
        ]
        if desc:
            cmd.extend(["--description", desc])
        # 指定日历类型（省略则使用主日历）
        if calendar_id and calendar_id != "primary":
            cmd.extend(["--calendar-id", calendar_id])

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=15,
                env=ENV,
            )
            if result.returncode == 0:
                try:
                    data = json.loads(result.stdout)
                    self.send_json({"ok": True, "data": data.get("data", {})})
                except json.JSONDecodeError:
                    self.send_json({"ok": True, "message": "日程创建成功"})
            else:
                self.send_json({"ok": False, "error": result.stderr.strip() or "创建失败"}, 500)
        except subprocess.TimeoutExpired:
            self.send_json({"ok": False, "error": "创建超时"}, 504)
        except Exception as e:
            self.send_json({"ok": False, "error": str(e)}, 500)

    def _run_lark(self, cmd, timeout=15):
        """统一执行 lark-cli 命令并返回 (success, payload, error)"""
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True,
                timeout=timeout, env=ENV,
            )
            if result.returncode == 0:
                try:
                    data = json.loads(result.stdout)
                except json.JSONDecodeError:
                    data = {"raw": result.stdout}
                return True, data, None
            return False, None, result.stderr.strip() or "lark-cli 执行失败"
        except subprocess.TimeoutExpired:
            return False, None, "请求超时"
        except Exception as e:
            return False, None, str(e)

    def _get_user_open_id(self):
        """获取当前登录用户的 open_id（带缓存），用于创建待办时自动分配给本人"""
        global _USER_OPEN_ID
        if _USER_OPEN_ID:
            return _USER_OPEN_ID
        ok, data, err = self._run_lark([
            LARK_CLI, "auth", "status", "--json",
        ], timeout=10)
        if ok and isinstance(data, dict):
            oid = data.get("identities", {}).get("user", {}).get("openId", "")
            if oid:
                _USER_OPEN_ID = oid
                return oid
        return ""

    def handle_tasks(self, query_string=""):
        """获取我的待办任务（支持 completed 参数切换已完成/未完成）"""
        from urllib.parse import parse_qs
        qs = parse_qs(query_string)
        completed = qs.get("completed", ["false"])[0] == "true"

        ok, data, err = self._run_lark([
            LARK_CLI, "task", "+get-my-tasks",
            "--complete=" + str(completed).lower(), "--as", "user", "--format", "json",
        ], timeout=20)
        if not ok:
            self.send_json({"ok": False, "error": err}, 500)
            return
        items = (data.get("data", {}).get("items", []) if isinstance(data.get("data"), dict) else []) or []
        simplified = []
        for t in items:
            # 解析 due_at（ISO 8601 格式）
            due_ts = ""
            due_at = t.get("due_at", "")
            if due_at:
                try:
                    # ISO 8601: "2026-08-04T11:38:57+08:00" -> Unix 时间戳（毫秒）
                    dt = datetime.fromisoformat(due_at)
                    due_ts = str(int(dt.timestamp() * 1000))
                except (ValueError, TypeError):
                    due_ts = due_at
            else:
                # 兼容旧格式：due 字段可能是 dict 或 string
                due = t.get("due") or {}
                if isinstance(due, dict):
                    due_ts = due.get("timestamp", "") or due.get("time", "")
                elif isinstance(due, str):
                    due_ts = due

            # 提取提醒信息
            reminder_val = ""
            reminders = t.get("reminders", [])
            if reminders and isinstance(reminders, list) and len(reminders) > 0:
                r = reminders[0]
                if isinstance(r, dict):
                    reminder_val = str(r.get("relative_fire_minute", ""))
                elif isinstance(r, (int, str)):
                    reminder_val = str(r)
            elif isinstance(t.get("reminder"), (int, str)):
                reminder_val = str(t.get("reminder"))

            simplified.append({
                "guid": t.get("guid", ""),
                "summary": t.get("summary", ""),
                "description": t.get("description", ""),
                "due": due_ts,
                "url": t.get("url", ""),
                "created_at": t.get("created_at", ""),
                "reminder": reminder_val,
            })
        self.send_json({"ok": True, "tasks": simplified, "total": len(simplified)})

    def handle_create_task(self):
        """创建飞书待办任务"""
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length).decode("utf-8")
        try:
            params = json.loads(body)
        except json.JSONDecodeError:
            self.send_json({"ok": False, "error": "无效的 JSON"}, 400)
            return

        summary = params.get("summary", "").strip()
        due = params.get("due", "").strip()
        reminder = params.get("reminder", "").strip()
        desc = params.get("description", "").strip()

        if not summary:
            self.send_json({"ok": False, "error": "缺少必要参数: summary"}, 400)
            return

        # 提醒必须设置在有截止时间的情况下
        if reminder and not due:
            self.send_json({"ok": False, "error": "设置提醒需要先设置截止时间"}, 400)
            return

        cmd = [LARK_CLI, "task", "+create", "--summary", summary, "--as", "user", "--format", "json"]
        # 自动分配给当前登录用户，否则任务不会出现在「我的待办」列表中
        assignee = self._get_user_open_id()
        if assignee:
            cmd.extend(["--assignee", assignee])
        if due:
            cmd.extend(["--due", due])
        if desc:
            cmd.extend(["--description", desc])

        ok, data, err = self._run_lark(cmd, timeout=15)
        if not ok:
            self.send_json({"ok": False, "error": err}, 500)
            return

        task_data = data.get("data", {}) if isinstance(data, dict) else {}

        # 如果设置了提醒，在任务创建后添加提醒
        if reminder and task_data.get("guid"):
            task_guid = task_data["guid"]
            ok2, data2, err2 = self._run_lark([
                LARK_CLI, "task", "+reminder",
                "--task-id", task_guid,
                "--set", reminder,
                "--as", "user", "--format", "json",
            ], timeout=10)
            if ok2:
                task_data["reminder"] = reminder
            # 提醒设置失败不影响任务创建成功
            elif err2:
                task_data["reminder_error"] = str(err2)

        # 任务创建成功后，自动生成 AI 执行建议备注并存入本地
        if task_data.get("guid"):
            task_guid = task_data["guid"]
            try:
                ai_note = _generate_ai_summary(summary)
                notes = _load_task_notes()
                now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                notes[task_guid] = {
                    "guid": task_guid,
                    "summary": summary,
                    "note": ai_note,
                    "source": "auto_ai",
                    "created_at": now_str,
                    "updated_at": now_str,
                }
                _save_task_notes(notes)
                task_data["ai_note_saved"] = True
            except Exception as e:
                sys.stderr.write(f"[WARN] AI 备注生成失败（不影响任务创建）: {e}\n")

        self.send_json({"ok": True, "data": task_data})

    def handle_complete_task(self):
        """完成待办任务"""
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length).decode("utf-8")
        try:
            params = json.loads(body)
        except json.JSONDecodeError:
            self.send_json({"ok": False, "error": "无效的 JSON"}, 400)
            return

        task_id = params.get("task_id", "").strip() or params.get("guid", "").strip()
        if not task_id:
            self.send_json({"ok": False, "error": "缺少必要参数: task_id"}, 400)
            return

        ok, data, err = self._run_lark([
            LARK_CLI, "task", "+complete", "--task-id", task_id, "--as", "user", "--format", "json",
        ], timeout=15)
        if not ok:
            self.send_json({"ok": False, "error": err}, 500)
            return
        self.send_json({"ok": True, "data": data.get("data", {}) if isinstance(data, dict) else {}})

    def handle_reopen_task(self):
        """重新打开已完成的待办任务"""
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length).decode("utf-8")
        try:
            params = json.loads(body)
        except json.JSONDecodeError:
            self.send_json({"ok": False, "error": "无效的 JSON"}, 400)
            return

        task_id = params.get("task_id", "").strip() or params.get("guid", "").strip()
        if not task_id:
            self.send_json({"ok": False, "error": "缺少必要参数: task_id"}, 400)
            return

        ok, data, err = self._run_lark([
            LARK_CLI, "task", "+reopen", "--task-id", task_id, "--as", "user", "--format", "json",
        ], timeout=15)
        if not ok:
            self.send_json({"ok": False, "error": err}, 500)
            return
        self.send_json({"ok": True, "data": data.get("data", {}) if isinstance(data, dict) else {}})

    def handle_delete_task(self):
        """删除待办任务"""
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length).decode("utf-8")
        try:
            params = json.loads(body)
        except json.JSONDecodeError:
            self.send_json({"ok": False, "error": "无效的 JSON"}, 400)
            return

        task_id = params.get("task_id", "").strip() or params.get("guid", "").strip()
        if not task_id:
            self.send_json({"ok": False, "error": "缺少必要参数: task_id"}, 400)
            return

        ok, data, err = self._run_lark([
            LARK_CLI, "task", "tasks", "delete",
            "--task-guid", task_id,
            "--as", "user", "--yes", "--format", "json",
        ], timeout=15)
        if not ok:
            self.send_json({"ok": False, "error": err}, 500)
            return
        self.send_json({"ok": True})

    def handle_delete_event(self):
        """删除飞书日程"""
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length).decode("utf-8")
        try:
            params = json.loads(body)
        except json.JSONDecodeError:
            self.send_json({"ok": False, "error": "无效的 JSON"}, 400)
            return

        event_id = params.get("event_id", "").strip()
        calendar_id = params.get("calendar_id", "").strip()
        if not event_id or not calendar_id:
            self.send_json({"ok": False, "error": "缺少必要参数: event_id, calendar_id"}, 400)
            return

        ok, data, err = self._run_lark([
            LARK_CLI, "calendar", "events", "delete",
            "--calendar-id", calendar_id,
            "--event-id", event_id,
            "--as", "user", "--format", "json",
        ], timeout=15)
        if not ok:
            self.send_json({"ok": False, "error": err}, 500)
            return
        self.send_json({"ok": True})

    def handle_get_task_note(self, query_string=""):
        """获取指定任务的本地 AI 备注"""
        from urllib.parse import parse_qs
        qs = parse_qs(query_string)
        guid = qs.get("guid", [""])[0].strip()
        if not guid:
            self.send_json({"ok": False, "error": "缺少必要参数: guid"}, 400)
            return
        notes = _load_task_notes()
        note = notes.get(guid, {})
        if not note:
            self.send_json({"ok": True, "note": None})
            return
        self.send_json({"ok": True, "note": note})

    def handle_save_task_note(self):
        """保存或更新指定任务的备注（用户手动编辑后保存）"""
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length).decode("utf-8")
        try:
            params = json.loads(body)
        except json.JSONDecodeError:
            self.send_json({"ok": False, "error": "无效的 JSON"}, 400)
            return

        guid = params.get("guid", "").strip()
        note_text = params.get("note", "")
        if not guid:
            self.send_json({"ok": False, "error": "缺少必要参数: guid"}, 400)
            return

        notes = _load_task_notes()
        existing = notes.get(guid, {})
        notes[guid] = {
            "guid": guid,
            "summary": params.get("summary", "") or existing.get("summary", ""),
            "note": note_text,
            "source": params.get("source", "manual"),  # manual / auto_ai / regenerated_ai
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "created_at": existing.get("created_at", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        }
        _save_task_notes(notes)
        self.send_json({"ok": True, "note": notes[guid]})

    def handle_generate_ai_summary(self):
        """根据任务标题生成 AI 执行建议（可手动触发重新生成）"""
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length).decode("utf-8")
        try:
            params = json.loads(body)
        except json.JSONDecodeError:
            self.send_json({"ok": False, "error": "无效的 JSON"}, 400)
            return

        guid = params.get("guid", "").strip()
        summary = params.get("summary", "").strip()
        if not summary:
            self.send_json({"ok": False, "error": "缺少必要参数: summary"}, 400)
            return

        note_text = _generate_ai_summary(summary)
        
        # 若提供了 guid，则自动保存到本地（source=regenerated_ai 表示手动重新生成）
        if guid:
            notes = _load_task_notes()
            existing = notes.get(guid, {})
            notes[guid] = {
                "guid": guid,
                "summary": summary,
                "note": note_text,
                "source": "regenerated_ai",
                "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "created_at": existing.get("created_at", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
            }
            _save_task_notes(notes)
        
        self.send_json({
            "ok": True,
            "summary": summary,
            "note": note_text,
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        })

    def handle_get_agenda_note(self, query_string=""):
        """获取指定日程的本地 AI 备注"""
        from urllib.parse import parse_qs
        qs = parse_qs(query_string)
        event_id = qs.get("event_id", [""])[0].strip()
        if not event_id:
            self.send_json({"ok": False, "error": "缺少必要参数: event_id"}, 400)
            return
        notes = _load_agenda_notes()
        note = notes.get(event_id, {})
        if not note:
            self.send_json({"ok": True, "note": None})
            return
        self.send_json({"ok": True, "note": note})

    def handle_save_agenda_note(self):
        """保存或更新指定日程的备注"""
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length).decode("utf-8")
        try:
            params = json.loads(body)
        except json.JSONDecodeError:
            self.send_json({"ok": False, "error": "无效的 JSON"}, 400)
            return

        event_id = params.get("event_id", "").strip()
        note_text = params.get("note", "")
        if not event_id:
            self.send_json({"ok": False, "error": "缺少必要参数: event_id"}, 400)
            return

        notes = _load_agenda_notes()
        existing = notes.get(event_id, {})
        notes[event_id] = {
            "event_id": event_id,
            "summary": params.get("summary", "") or existing.get("summary", ""),
            "note": note_text,
            "source": params.get("source", "manual"),
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "created_at": existing.get("created_at", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        }
        _save_agenda_notes(notes)
        self.send_json({"ok": True, "note": notes[event_id]})

    def handle_generate_agenda_summary(self):
        """根据日程标题和信息生成 AI 执行建议"""
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length).decode("utf-8")
        try:
            params = json.loads(body)
        except json.JSONDecodeError:
            self.send_json({"ok": False, "error": "无效的 JSON"}, 400)
            return

        event_id = params.get("event_id", "").strip()
        summary = params.get("summary", "").strip()
        if not summary:
            self.send_json({"ok": False, "error": "缺少必要参数: summary"}, 400)
            return

        event_info = {
            "start": params.get("start", ""),
            "end": params.get("end", ""),
            "organizer": params.get("organizer", ""),
            "location": params.get("location", ""),
            "calendar_name": params.get("calendar_name", ""),
            "created_at": params.get("created_at", ""),
        }
        
        note_text = _generate_event_summary(summary, event_info)
        
        if event_id:
            notes = _load_agenda_notes()
            existing = notes.get(event_id, {})
            notes[event_id] = {
                "event_id": event_id,
                "summary": summary,
                "note": note_text,
                "source": params.get("source", "auto_ai"),
                "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "created_at": existing.get("created_at", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
            }
            _save_agenda_notes(notes)
        
        self.send_json({
            "ok": True,
            "summary": summary,
            "note": note_text,
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        })

    def handle_news(self):
        """返回新闻数据"""
        news_path = os.path.join(WORK_DIR, "news_data.js")
        try:
            with open(news_path, "r", encoding="utf-8") as f:
                content = f.read()
            # 提取 JSON 部分
            start = content.find("{")
            end = content.rfind("}") + 1
            if start >= 0 and end > start:
                data = json.loads(content[start:end])
                self.send_json({"ok": True, "data": data})
            else:
                self.send_json({"ok": False, "error": "新闻数据格式错误"}, 500)
        except FileNotFoundError:
            self.send_json({"ok": False, "error": "新闻数据不存在，请先运行 fetch_news.py"}, 404)
        except Exception as e:
            self.send_json({"ok": False, "error": str(e)}, 500)

    def handle_refresh_news(self):
        """刷新新闻数据"""
        try:
            result = subprocess.run(
                ["python3", os.path.join(WORK_DIR, "fetch_news.py")],
                capture_output=True,
                text=True,
                timeout=60,
                env=ENV,
            )
            if result.returncode == 0:
                self.send_json({"ok": True, "message": "新闻刷新完成", "log": result.stdout})
            else:
                self.send_json({"ok": False, "error": result.stderr or "刷新失败"}, 500)
        except subprocess.TimeoutExpired:
            self.send_json({"ok": False, "error": "刷新超时"}, 504)
        except Exception as e:
            self.send_json({"ok": False, "error": str(e)}, 500)

    def handle_knowledge_inbox(self):
        """获取知识收藏收件箱的多维表格记录"""
        BASE_TOKEN = "UbgNbjn2GaUCJ0sVMHbcOUlAn5d"
        TABLE_ID = "tblK5vgKxnhPOgMK"
        
        ok, data, err = self._run_lark([
            LARK_CLI, "base", "+record-list",
            "--base-token", BASE_TOKEN,
            "--table-id", TABLE_ID,
            "--as", "user",
            "--format", "json",
        ], timeout=20)
        
        if not ok:
            self.send_json({"ok": False, "error": err or "获取知识收藏失败"}, 500)
            return
        
        # 解析多维表格数据：fields + data (2D array)
        raw = data.get("data", {})
        fields = raw.get("fields", [])
        rows = raw.get("data", [])
        
        # 将每行数据转为结构化对象
        records = []
        for row in rows:
            record = {}
            for j, field in enumerate(fields):
                record[field] = row[j] if j < len(row) else None
            
            # 提取知识类型（select 字段返回数组）
            类型 = record.get("知识类型", [])
            if isinstance(类型, list):
                类型 = 类型[0] if 类型 else "未知"
            
            # 收集所有非空标签
            tags = []
            for key in ["标签", "标签2", "标签3", "标签4", "标签5"]:
                val = record.get(key)
                if val and isinstance(val, str) and val.strip():
                    tags.append(val.strip())
            
            # 解析链接（可能是 markdown 格式 [title](url) 或纯 URL）
            link_raw = record.get("链接", "") or ""
            link_url = ""
            link_title = ""
            if link_raw:
                md_match = re.match(r'\[([^\]]+)\]\(([^)]+)\)', link_raw)
                if md_match:
                    link_title = md_match.group(1)
                    link_url = md_match.group(2)
                elif link_raw.startswith("http"):
                    link_url = link_raw
                    link_title = link_raw
            
            records.append({
                "type": 类型,
                "link": link_url,
                "title": link_title or link_url,
                "tags": tags,
            })
        
        # 按时间倒序（最新记录在前）
        records.reverse()
        
        self.send_json({"ok": True, "data": records})

    # ── 百度网盘 API 处理 ──

    def handle_baidu_auth(self):
        """发起百度 OAuth2 授权，重定向到百度授权页（oob 模式）"""
        config = _load_config()
        baidu_cfg = config.get("baidu_pan", {})
        app_key = baidu_cfg.get("app_key", "")
        if not app_key:
            self.send_json({"ok": False, "error": "请先在 config.json 中配置 baidu_pan.app_key"}, 400)
            return

        auth_url = (
            f"https://openapi.baidu.com/oauth/2.0/authorize"
            f"?response_type=code"
            f"&client_id={app_key}"
            f"&redirect_uri=oob"
            f"&scope=basic,netdisk"
            f"&display=popup"
        )
        self.send_response(302)
        self.send_header("Location", auth_url)
        self.end_headers()

    def handle_baidu_callback(self, query_string):
        """百度 OAuth 回调（保留兼容），用 code 换取 access_token"""
        qs = parse_qs(query_string)
        code = qs.get("code", [""])[0]
        if not code:
            error = qs.get("error_description", ["授权失败"])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(f"<html><body><h2>授权失败: {error}</h2><p>请关闭窗口后重试</p></body></html>".encode("utf-8"))
            return
        self._exchange_baidu_code(code)

    def handle_baidu_exchange_code(self):
        """接收前端手动输入的授权码，换取 access_token（oob 模式）"""
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length).decode("utf-8")
        try:
            params = json.loads(body)
        except json.JSONDecodeError:
            self.send_json({"ok": False, "error": "无效的 JSON"}, 400)
            return

        code = params.get("code", "").strip()
        if not code:
            self.send_json({"ok": False, "error": "缺少授权码"}, 400)
            return
        self._exchange_baidu_code(code, json_mode=True)

    def _exchange_baidu_code(self, code, json_mode=False):
        """用授权码换取百度 access_token"""
        config = _load_config()
        baidu_cfg = config.get("baidu_pan", {})
        app_key = baidu_cfg.get("app_key", "")
        secret_key = baidu_cfg.get("secret_key", "")

        try:
            url = (
                f"https://openapi.baidu.com/oauth/2.0/token"
                f"?grant_type=authorization_code"
                f"&code={code}"
                f"&client_id={app_key}"
                f"&client_secret={secret_key}"
                f"&redirect_uri=oob"
            )
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))

            if data.get("access_token"):
                token_data = {
                    "access_token": data["access_token"],
                    "refresh_token": data.get("refresh_token", ""),
                    "expires_at": time.time() + data.get("expires_in", 2592000),
                    "scope": data.get("scope", ""),
                }
                _save_baidu_token(token_data)
                if json_mode:
                    self.send_json({"ok": True, "message": "授权成功"})
                else:
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.end_headers()
                    self.wfile.write(
                        "<html><body style='font-family:system-ui;text-align:center;padding:60px'>"
                        "<h2>授权成功！</h2><p>可以关闭此页面，回到播放器页面刷新即可。</p>"
                        "<script>setTimeout(function(){window.close()},3000)</script>"
                        "</body></html>".encode("utf-8")
                    )
            else:
                error_msg = data.get("error_description", "未知错误")
                if json_mode:
                    self.send_json({"ok": False, "error": f"换取 token 失败: {error_msg}"}, 500)
                else:
                    self.send_json({"ok": False, "error": f"换取 token 失败: {error_msg}"}, 500)
        except Exception as e:
            self.send_json({"ok": False, "error": f"换取 token 异常: {e}"}, 500)

    def handle_baidu_status(self):
        """查询百度网盘授权状态"""
        token_data = _load_baidu_token()
        config = _load_config()
        baidu_cfg = config.get("baidu_pan", {})

        has_credentials = bool(baidu_cfg.get("app_key") and baidu_cfg.get("secret_key"))
        has_token = bool(token_data.get("access_token"))
        token_expired = False

        if has_token:
            expires_at = token_data.get("expires_at", 0)
            token_expired = time.time() >= expires_at
            # 如果过期，尝试刷新
            if token_expired:
                refreshed = _get_baidu_access_token()
                token_expired = refreshed is None

        self.send_json({
            "ok": True,
            "has_credentials": has_credentials,
            "authorized": has_token and not token_expired,
            "token_expired": token_expired if has_token else None,
            "folder_path": baidu_cfg.get("folder_path", "/我的音乐"),
        })

    def handle_baidu_files(self, query_string):
        """列出百度网盘指定文件夹下的音频/视频文件"""
        access_token = _get_baidu_access_token()
        if not access_token:
            self.send_json({"ok": False, "error": "未授权或 token 已失效，请重新授权"}, 401)
            return

        qs = parse_qs(query_string)
        config = _load_config()
        baidu_cfg = config.get("baidu_pan", {})

        folder_path = qs.get("dir", [baidu_cfg.get("folder_path", "/我的音乐")])[0]
        audio_exts = set(
            ext.lower() for ext in baidu_cfg.get(
                "audio_extensions", [".mp3", ".flac", ".wav", ".aac", ".ogg", ".m4a", ".wma"]
            )
        )
        video_exts = set(
            ext.lower() for ext in baidu_cfg.get(
                "video_extensions", [".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm"]
            )
        )
        media_exts = audio_exts | video_exts

        try:
            url = (
                f"https://pan.baidu.com/rest/2.0/xpan/file"
                f"?method=list"
                f"&dir={quote(folder_path, safe='')}"
                f"&access_token={access_token}"
                f"&limit=1000"
            )
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))

            if data.get("errno", 0) != 0:
                self.send_json({"ok": False, "error": f"百度 API 错误: errno={data.get('errno')}"}, 500)
                return

            files = []
            for item in data.get("list", []):
                server_filename = item.get("server_filename", "")
                if item.get("isdir", 0) == 1:
                    # 文件夹：加入列表
                    files.append({
                        "fs_id": item.get("fs_id"),
                        "filename": server_filename,
                        "size": 0,
                        "path": item.get("path", ""),
                        "server_mtime": item.get("server_mtime", 0),
                        "type": "dir",
                    })
                    continue
                # 提取扩展名
                dot_pos = server_filename.rfind(".")
                if dot_pos >= 0:
                    ext = server_filename[dot_pos:].lower()
                else:
                    ext = ""
                if ext not in media_exts:
                    continue

                files.append({
                    "fs_id": item.get("fs_id"),
                    "filename": server_filename,
                    "size": item.get("size", 0),
                    "path": item.get("path", ""),
                    "server_mtime": item.get("server_mtime", 0),
                    "type": "video" if ext in video_exts else "audio",
                })

            # 排序：文件夹在前，文件在后；同类按名称排序
            files.sort(key=lambda x: (0 if x["type"] == "dir" else 1, x["filename"].lower()))
            self.send_json({"ok": True, "files": files, "total": len(files), "dir": folder_path})

        except urllib.error.URLError as e:
            self.send_json({"ok": False, "error": f"网络请求失败: {e}"}, 502)
        except Exception as e:
            self.send_json({"ok": False, "error": str(e)}, 500)

    def handle_baidu_stream(self, query_string):
        """代理百度网盘音频/视频文件流，支持 Range 请求"""
        access_token = _get_baidu_access_token()
        if not access_token:
            self.send_json({"ok": False, "error": "未授权或 token 已失效"}, 401)
            return

        qs = parse_qs(query_string)
        fs_id = qs.get("fs_id", [""])[0]
        if not fs_id:
            self.send_json({"ok": False, "error": "缺少必要参数: fs_id"}, 400)
            return

        filename = qs.get("filename", ["audio"])[0]

        try:
            # 获取文件下载链接
            url = (
                f"https://pan.baidu.com/rest/2.0/xpan/multimedia"
                f"?method=filemetas"
                f"&access_token={access_token}"
                f"&fsids=[{fs_id}]"
                f"&dlink=1"
                f"&extra=0"
            )
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=15) as resp:
                meta_data = json.loads(resp.read().decode("utf-8"))

            if meta_data.get("errno", 0) != 0:
                self.send_json({"ok": False, "error": f"获取文件信息失败: errno={meta_data.get('errno')}"}, 500)
                return

            file_list = meta_data.get("list", [])
            if not file_list:
                self.send_json({"ok": False, "error": "文件不存在"}, 404)
                return

            dlink = file_list[0].get("dlink", "")
            file_size = file_list[0].get("size", 0)
            if not dlink:
                self.send_json({"ok": False, "error": "无下载链接"}, 500)
                return

            # 构建下载请求（携带 access_token）
            download_url = f"{dlink}&access_token={access_token}"

            # 处理 Range 请求
            range_header = self.headers.get("Range")
            headers = {"User-Agent": "pan.baidu.com"}
            if range_header:
                headers["Range"] = range_header

            dl_req = urllib.request.Request(download_url, headers=headers)

            try:
                dl_resp = urllib.request.urlopen(dl_req, timeout=120)
            except urllib.error.HTTPError as he:
                if he.code == 206:
                    # Partial content (Range 请求正常响应)
                    dl_resp = he
                else:
                    self.send_json({"ok": False, "error": f"下载失败: HTTP {he.code}"}, 502)
                    return

            # 推断 MIME 类型
            mime_type, _ = mimetypes.guess_type(filename)
            if not mime_type:
                mime_type = "audio/mpeg"

            # 发送响应头
            if range_header and hasattr(dl_resp, 'status') and dl_resp.status == 206:
                self.send_response(206)
                content_range = dl_resp.getheader("Content-Range", "")
                if content_range:
                    self.send_header("Content-Range", content_range)
            else:
                self.send_response(200)
                if file_size:
                    self.send_header("Content-Length", str(file_size))

            self.send_header("Content-Type", mime_type)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Access-Control-Allow-Origin", "*")
            # 使用 filename 设置内容处置（inline 以便浏览器播放）
            self.send_header("Content-Disposition", f'inline; filename="{quote(filename)}"')
            self.end_headers()

            # 流式转发数据
            chunk_size = 65536  # 64KB
            while True:
                chunk = dl_resp.read(chunk_size)
                if not chunk:
                    break
                self.wfile.write(chunk)

        except urllib.error.URLError as e:
            sys.stderr.write(f"[百度网盘] 网络错误: {e}\n")
            if not self.headers.get("Range"):
                self.send_json({"ok": False, "error": f"网络请求失败: {e}"}, 502)
        except Exception as e:
            sys.stderr.write(f"[百度网盘] 流代理异常: {e}\n")
            try:
                self.send_json({"ok": False, "error": str(e)}, 500)
            except Exception:
                pass  # 可能已经开始发送响应，无法再发送错误 JSON

    def handle_baidu_dirs(self, query_string):
        """列出百度网盘指定目录下的子文件夹"""
        access_token = _get_baidu_access_token()
        if not access_token:
            self.send_json({"ok": False, "error": "未授权或 token 已失效"}, 401)
            return

        qs = parse_qs(query_string)
        dir_path = qs.get("dir", ["/"])[0]

        try:
            url = (
                f"https://pan.baidu.com/rest/2.0/xpan/file"
                f"?method=list"
                f"&dir={quote(dir_path, safe='')}"
                f"&access_token={access_token}"
                f"&limit=1000"
            )
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))

            if data.get("errno", 0) != 0:
                self.send_json({"ok": False, "error": f"百度 API 错误: errno={data.get('errno')}"}, 500)
                return

            dirs = []
            for item in data.get("list", []):
                if item.get("isdir", 0) == 1:
                    dirs.append({
                        "name": item.get("server_filename", ""),
                        "path": item.get("path", ""),
                    })

            dirs.sort(key=lambda x: x["name"].lower())
            self.send_json({"ok": True, "dirs": dirs, "current": dir_path})

        except Exception as e:
            self.send_json({"ok": False, "error": str(e)}, 500)

    def handle_baidu_favorites_list(self):
        """获取收藏夹列表"""
        favorites = _load_baidu_favorites()
        self.send_json({"ok": True, "favorites": favorites, "total": len(favorites)})

    def handle_baidu_favorite_toggle(self):
        """添加或移除收藏项"""
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length).decode("utf-8")
        try:
            params = json.loads(body)
        except json.JSONDecodeError:
            self.send_json({"ok": False, "error": "无效的 JSON"}, 400)
            return

        action = params.get("action", "add")  # add | remove
        item = params.get("item", {})
        item_type = item.get("type", "")  # dir | file
        item_path = item.get("path", "")

        if not item_type or not item_path:
            self.send_json({"ok": False, "error": "缺少必要参数: type, path"}, 400)
            return

        favorites = _load_baidu_favorites()

        if action == "remove":
            favorites = [f for f in favorites if not (f.get("type") == item_type and f.get("path") == item_path)]
            _save_baidu_favorites(favorites)
            self.send_json({"ok": True, "action": "removed", "favorites": favorites})
        else:
            # 检查是否已存在
            exists = any(f.get("type") == item_type and f.get("path") == item_path for f in favorites)
            if not exists:
                favorites.append({
                    "type": item_type,
                    "name": item.get("name", ""),
                    "path": item_path,
                    "fs_id": item.get("fs_id"),
                    "size": item.get("size", 0),
                    "added_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                })
                _save_baidu_favorites(favorites)
            self.send_json({"ok": True, "action": "added", "favorites": favorites})

    def send_json(self, data, status=200):
        """发送 JSON 响应"""
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))

    def do_OPTIONS(self):
        """处理 CORS 预检请求"""
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def log_message(self, format, *args):
        """简化日志"""
        msg = format % args
        if "/api/" in msg:
            sys.stderr.write(f"[API] {msg}\n")
        elif "404" in msg:
            pass  # 忽略 404 噪音
        else:
            sys.stderr.write(f"[静态] {msg}\n")



# 启动时检查 news_data.js 是否存在，不存在则自动生成
news_data_path = os.path.join(WORK_DIR, "news_data.js")
if not os.path.exists(news_data_path):
    print("news_data.js 不存在，自动生成中...")
    try:
        subprocess.run(
            ["python3", os.path.join(WORK_DIR, "fetch_news.py")],
            capture_output=True, text=True, timeout=60, env=ENV,
        )
        print("news_data.js 生成完成")
    except Exception as e:
        print(f"news_data.js 生成失败: {e}")

def main():
    print(f"╔════════════════════════════════════════╗")
    print(f"║     个人工作台服务器 v1.0              ║")
    print(f"╚════════════════════════════════════════╝")
    print(f"")
    print(f"  地址: http://0.0.0.0:{PORT}")
    print(f"  目录: {WORK_DIR}")
    print(f"")
    print(f"  API 端点:")
    print(f"    GET  /api/agenda        - 获取今日飞书日程")
    print(f"    GET  /api/calendars     - 同步飞书日历类型列表")
    print(f"    POST /api/event         - 创建飞书日程（可指定日历）")
    print(f"    GET  /api/tasks         - 获取我的未完成待办")
    print(f"    POST /api/task          - 创建待办任务")
    print(f"    POST /api/task/complete - 完成待办任务")
    print(f"    GET  /api/news          - 获取新闻数据")
    print(f"    POST /api/refresh-news  - 刷新新闻")
    print(f"    GET  /api/health        - 健康检查")
    print(f"")
    print(f"  百度网盘:")
    print(f"    GET  /api/baidu/auth    - 发起百度 OAuth 授权")
    print(f"    GET  /api/baidu/status  - 查询授权状态")
    print(f"    GET  /api/baidu/files   - 列出网盘音频文件")
    print(f"    GET  /api/baidu/stream  - 代理音频文件流")
    print(f"")
    print(f"  页面:")
    print(f"    /index.html             - 个人工作台")
    print(f"    /player.html            - 百度网盘音频播放器")
    print(f"")
    print(f"  按 Ctrl+C 停止服务器")
    print(f"")

    try:
        server = HTTPServer(("0.0.0.0", PORT), WorkbenchHandler)
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n服务器已停止")
    except OSError as e:
        if "Address already in use" in str(e):
            print(f"错误: 端口 {PORT} 已被占用，请关闭占用程序或修改 PORT")
        else:
            print(f"错误: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
