'use strict';

/* =========================================================
   秒取 · 纯前端短视频解析 PWA
   解析依赖第三方公开接口，下载在浏览器内完成。
   ========================================================= */

const $ = (sel, root = document) => root.querySelector(sel);

const LS_SETTINGS = 'miaoqu.settings.v1';
const LS_HISTORY = 'miaoqu.history.v1';
const HISTORY_LIMIT = 30;

const PLATFORMS = {
  douyin: { name: '抖音', api: 'douyin' },
  kuaishou: { name: '快手', api: 'kuaishou' },
  xhs: { name: '小红书', api: 'xhs' },
  wechat: { name: '视频号', api: '' },
  bilibili: { name: '哔哩哔哩', api: 'bilibili' },
  direct: { name: '视频直链', api: '' },
  unknown: { name: '未识别', api: '' }
};

const DEFAULT_SETTINGS = {
  engine: 'auto',
  customBase: '',
  customKey: '',
  iosSave: 'share'
};

const state = {
  busy: false,
  result: null,
  history: [],
  settings: { ...DEFAULT_SETTINGS }
};

const el = {};

const isIOS = (() => {
  const ua = navigator.userAgent || '';
  return /iPad|iPhone|iPod/.test(ua) ||
    (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
})();

const canNativeShareFiles = (() => {
  try {
    return !!(navigator.canShare && navigator.share);
  } catch (_) {
    return false;
  }
})();

// iOS 上把文件交给系统分享面板存相册，需要安全上下文
const canShareToAlbum = isIOS && isSecureContext && canNativeShareFiles;

/* ---------------- 基础工具 ---------------- */

function safeJsonParse(text, fallback) {
  try { return JSON.parse(text); } catch (_) { return fallback; }
}

function loadSettings() {
  const saved = safeJsonParse(localStorage.getItem(LS_SETTINGS) || '{}', {});
  state.settings = { ...DEFAULT_SETTINGS, ...(saved && typeof saved === 'object' ? saved : {}) };
}

function saveSettings() {
  try { localStorage.setItem(LS_SETTINGS, JSON.stringify(state.settings)); } catch (_) {}
}

function loadHistory() {
  const saved = safeJsonParse(localStorage.getItem(LS_HISTORY) || '[]', []);
  state.history = Array.isArray(saved) ? saved.filter(item => item && item.sourceUrl).slice(0, HISTORY_LIMIT) : [];
}

function persistHistory() {
  try { localStorage.setItem(LS_HISTORY, JSON.stringify(state.history.slice(0, HISTORY_LIMIT))); } catch (_) {}
}

function trimUrl(url) {
  return String(url || '')
    .replace(/^[<（(【\[]+/, '')
    .replace(/[）)】\]>，。；、,.;!?！？'"”’]+$/, '');
}

function normalizeUrl(value) {
  let url = trimUrl(value);
  if (!url) return '';
  if (url.startsWith('//')) url = 'https:' + url;
  if (!/^https?:\/\//i.test(url)) url = 'https://' + url;
  try {
    const parsed = new URL(url);
    if (!/^https?:$/.test(parsed.protocol)) return '';
    return parsed.href;
  } catch (_) {
    return '';
  }
}

function extractUrl(text) {
  if (!text) return '';
  const cleaned = String(text).replace(/[\u200b-\u200f\ufeff]/g, '');
  const full = cleaned.match(/https?:\/\/[^\s"'<>，。；、）】》]+/i);
  if (full) return normalizeUrl(full[0]);

  const bare = cleaned.match(/(?:v\.douyin\.com|v\.kuaishou\.com|xhslink\.com|www\.xiaohongshu\.com|xiaohongshu\.com|channels\.weixin\.qq\.com|weixin\.qq\.com\/sph|b23\.tv|bilibili\.com|v\.m\.chenzhongtech\.com)\/[^\s"'<>，。；、）】》]+/i);
  if (bare) return normalizeUrl(bare[0]);
  return '';
}

function detectPlatform(url) {
  if (!url) return 'unknown';
  let host = '';
  try { host = new URL(url).hostname.toLowerCase(); } catch (_) { return 'unknown'; }
  if (/(^|\.)douyin\.com$/.test(host) || host.endsWith('iesdouyin.com')) return 'douyin';
  if (/(^|\.)kuaishou\.com$/.test(host) || host.endsWith('chenzhongtech.com') || host.endsWith('kwai.com') || host.endsWith('gifshow.com')) return 'kuaishou';
  if (host === 'xhslink.com' || host.endsWith('xiaohongshu.com')) return 'xhs';
  if (host === 'channels.weixin.qq.com' || (host.endsWith('weixin.qq.com') && /\/sph|finder|channels/i.test(url))) return 'wechat';
  if (host.endsWith('bilibili.com') || host === 'b23.tv') return 'bilibili';
  if (/\.(mp4|mov|m4v|webm)(\?|$)/i.test(url) || /mime_type=video/i.test(url)) return 'direct';
  return 'unknown';
}

function platformName(platform) {
  return (PLATFORMS[platform] || PLATFORMS.unknown).name;
}

function formatDuration(seconds) {
  const value = Number(seconds);
  if (!Number.isFinite(value) || value <= 0) return '';
  const total = Math.round(value);
  const min = Math.floor(total / 60);
  const sec = total % 60;
  return `${min}:${String(sec).padStart(2, '0')}`;
}

function formatSize(bytes) {
  const value = Number(bytes);
  if (!Number.isFinite(value) || value <= 0) return '';
  if (value >= 1024 * 1024 * 1024) return (value / 1024 / 1024 / 1024).toFixed(2) + 'GB';
  if (value >= 1024 * 1024) return (value / 1024 / 1024).toFixed(1) + 'MB';
  if (value >= 1024) return Math.round(value / 1024) + 'KB';
  return Math.round(value) + 'B';
}

function formatTimeAgo(timestamp) {
  const ts = Number(timestamp) || 0;
  if (!ts) return '';
  const diff = Date.now() - ts;
  if (diff < 60 * 1000) return '刚刚';
  if (diff < 60 * 60 * 1000) return Math.floor(diff / 60000) + ' 分钟前';
  if (diff < 24 * 60 * 60 * 1000) return Math.floor(diff / 3600000) + ' 小时前';
  if (diff < 48 * 60 * 60 * 1000) return '昨天';
  const date = new Date(ts);
  return `${date.getMonth() + 1}月${date.getDate()}日`;
}

function formatDateTime(date = new Date()) {
  const pad = n => String(n).padStart(2, '0');
  return `${date.getFullYear()}${pad(date.getMonth() + 1)}${pad(date.getDate())}_${pad(date.getHours())}${pad(date.getMinutes())}`;
}

function sanitizeFilename(value, fallback = '视频') {
  const cleaned = String(value || '')
    .replace(/[\\/:*?"<>|\r\n\t]/g, ' ')
    .replace(/#/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
    .slice(0, 56);
  return cleaned || fallback;
}

function extensionFromUrl(url, fallback) {
  try {
    const path = new URL(url).pathname;
    const match = path.match(/\.([a-z0-9]{2,5})$/i);
    if (match) {
      const ext = match[1].toLowerCase();
      if (['mp4', 'mov', 'm4v', 'webm', 'jpg', 'jpeg', 'png', 'webp'].includes(ext)) return ext;
    }
  } catch (_) {}
  return fallback;
}

function firstString(...values) {
  for (const value of values) {
    if (typeof value === 'string' && value.trim()) return value.trim();
  }
  return '';
}

// 有些接口会返回 "..." 之类的占位标题，统一视为空
function readableText(...values) {
  const text = firstString(...values);
  if (!text) return '';
  if (/^[.。·…\s\-—_~]+$/.test(text)) return '';
  return text;
}

/* ---------------- 错误 ---------------- */

class ParseError extends Error {
  constructor(message, options = {}) {
    super(message);
    this.name = 'ParseError';
    this.code = options.code || '';
    this.fatal = !!options.fatal;
  }
}

function httpMessage(status) {
  if (status === 401 || status === 403) return '接口拒绝了请求，可能需要登录或密钥';
  if (status === 404) return '接口不存在或作品已删除';
  if (status === 429) return '请求过于频繁，请稍后再试';
  if (status >= 500) return '解析服务暂时不可用，请稍后重试';
  return `解析服务返回异常（${status}）`;
}

// 接口偶尔返回英文原因，统一转成中文提示
function zhError(message) {
  const text = firstString(message);
  if (!text) return '';
  if (/xsec_token|xsec-token/i.test(text)) {
    return '小红书需要带 xsec_token 的完整链接：在小红书里打开笔记，复制地址栏链接后粘贴';
  }
  if (/rate.?limit|too many|throttl/i.test(text)) return '请求过于频繁，请稍后再试';
  if (/cookie|login|log in|session|unauthor/i.test(text)) return '该平台需要登录态，公开接口暂时不可用';
  if (/not found|deleted|removed|不存在/i.test(text)) return '作品不存在或已被删除';
  if (/timeout|timed out/i.test(text)) return '解析超时，请稍后重试';
  if (/unsupported|not support|invalid url/i.test(text)) return '该链接暂不支持解析';
  if (/param|required|missing/i.test(text)) return '链接格式不完整，请粘贴完整的分享链接';
  if (/network|failed to fetch|load failed/i.test(text)) return '网络连接失败，请检查网络后重试';
  if (!/[\u4e00-\u9fa5]/.test(text) && /^[\x20-\x7e]+$/.test(text)) {
    return '解析服务暂时不可用，请稍后重试';
  }
  return text;
}

function friendlyError(error, platform) {
  const message = zhError(error && error.message ? error.message : error || '');
  if (error && error.name === 'AbortError') return '解析超时，请稍后重试';
  if (platform === 'wechat') {
    return '视频号需要微信登录态，公开接口无法解析。建议在微信内直接保存，或在设置中接入带 Cookie 的自建服务。';
  }
  if (platform === 'xhs' && /xsec_token/.test(message)) return message;
  if (platform === 'xhs' && /登录|过期|cookie|不支持|无法/i.test(message)) {
    return '小红书公开接口需要登录态，暂时无法解析。可改用完整笔记链接，或在设置中配置自建服务。';
  }
  if (/登录已过期/.test(message)) return '该平台需要登录态，公开接口暂时不可用。';
  if (message) return message;
  return '解析失败，请确认链接有效后重试';
}

/* ---------------- 网络 ---------------- */

async function requestJson(url, options = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), options.timeout || 35000);
  let response;
  try {
    response = await fetch(url, {
      method: options.method || 'GET',
      headers: options.headers || {},
      body: options.body,
      credentials: 'omit',
      mode: 'cors',
      signal: controller.signal
    });
  } catch (error) {
    clearTimeout(timer);
    if (error && error.name === 'AbortError') throw error;
    throw new ParseError('网络连接失败，请检查网络后重试', { code: 'NETWORK' });
  }
  clearTimeout(timer);

  if (!response.ok) {
    let detail = '';
    try {
      const data = await response.json();
      detail = firstString(data.retdesc, data.msg, data.message, data.error);
    } catch (_) {}
    throw new ParseError(detail || httpMessage(response.status), { code: response.status });
  }

  try {
    return await response.json();
  } catch (_) {
    throw new ParseError('解析服务返回了无法识别的数据', { code: 'BAD_JSON' });
  }
}

/* ---------------- 结果标准化 ---------------- */

function pickVideoUrl(data, skipGenericUrl = false) {
  const direct = [
    data.video_url, data.videoUrl, data.play_url, data.playUrl,
    data.download_url, data.downloadUrl, data.mp4, data.video
  ];
  let chosen = '';
  for (const item of direct) {
    if (typeof item === 'string' && /^https?:\/\//i.test(item)) { chosen = item; break; }
    if (item && typeof item === 'object' && typeof item.url === 'string') { chosen = item.url; break; }
  }
  if (!chosen && !skipGenericUrl && typeof data.url === 'string' && /^https?:\/\//i.test(data.url) && !/\.(jpg|jpeg|png|webp|heic)(\?|$)/i.test(data.url)) {
    chosen = data.url;
  }

  const backups = Array.isArray(data.video_backup) ? data.video_backup
    : Array.isArray(data.videoBackup) ? data.videoBackup : [];
  const bestBackup = backups
    .filter(item => item && typeof item.url === 'string')
    .sort((a, b) => (Number(b.bit_rate || b.size) || 0) - (Number(a.bit_rate || a.size) || 0))[0];

  if (chosen && bestBackup && /api-play|play\/\?/i.test(chosen)) chosen = bestBackup.url;
  if (!chosen && bestBackup) chosen = bestBackup.url;
  return { url: normalizeUrl(chosen), backup: bestBackup || null };
}

function pickImages(data) {
  const source = data.image_list || data.imageList || data.images || data.pics || data.pictures || data.photos;
  if (!Array.isArray(source)) return [];
  return source.map(item => {
    if (typeof item === 'string') return normalizeUrl(item);
    if (item && typeof item === 'object') return normalizeUrl(item.url || item.src || item.image || item.origin_url || '');
    return '';
  }).filter(Boolean);
}

function pickAuthor(data) {
  const raw = data.author || data.user || data.owner || {};
  let name = '';
  let avatar = '';
  let id = '';
  if (typeof raw === 'string') {
    name = raw;
  } else if (raw && typeof raw === 'object') {
    name = firstString(raw.nickname, raw.name, raw.title, raw.user_name, raw.userName);
    avatar = firstString(raw.avatar, raw.avatar_url, raw.avatarUrl, raw.head_url, raw.headUrl);
    id = firstString(raw.author_id, raw.authorId, raw.id, raw.uid, raw.user_id);
  }
  if (!name) name = firstString(data.nickname, data.author_name, data.authorName, data.user_name, data.userName);
  if (!avatar) avatar = firstString(data.avatar, data.author_avatar, data.authorAvatar);
  return { name, avatar: normalizeUrl(avatar), id };
}

function normalizeResult(data, platform, sourceUrl, engine) {
  if (!data || typeof data !== 'object') {
    throw new ParseError('解析服务没有返回作品信息');
  }
  if (data.data && typeof data.data === 'object') data = data.data;
  if (Array.isArray(data)) data = data[0] || {};

  const images = pickImages(data);
  const isImagePost = images.length > 0 && /image|album|photo|pic|图集/i.test(String(data.type || ''));
  const { url: videoUrl, backup } = pickVideoUrl(data, isImagePost);
  const author = pickAuthor(data);
  const title = readableText(data.title, data.desc, data.description, data.text, data.caption)
    || (platform === 'unknown' ? '未命名作品' : platformName(platform) + '作品');
  const desc = readableText(data.desc, data.description);
  const cover = normalizeUrl(firstString(
    data.cover_url, data.coverUrl, data.cover, data.origin_cover,
    data.thumbnail, data.thumbnail_url, images[0] || ''
  ));
  const duration = Number(data.duration || (data.duration_ms ? data.duration_ms / 1000 : 0)) || 0;
  const size = Number(data.size || data.file_size || (backup && (backup.size || backup.bit_rate)) || 0) || 0;

  if (!videoUrl && !images.length) {
    throw new ParseError(firstString(data.msg, data.message) || '没有找到可下载的视频或图片');
  }

  return {
    platform,
    platformName: platformName(platform),
    engine,
    sourceUrl,
    title,
    desc: desc && desc !== title ? desc : '',
    author: author.name,
    avatar: author.avatar,
    authorId: author.id,
    cover,
    videoUrl,
    images,
    duration,
    size,
    sizeLabel: formatSize(size),
    saved: false,
    createdAt: Date.now()
  };
}

/* ---------------- 解析引擎 ---------------- */

function bugpkEndpoint(platform, url) {
  const api = (PLATFORMS[platform] || {}).api;
  if (!api) return '';
  return `https://api.bugpk.com/api/${api}?url=${encodeURIComponent(url)}`;
}

function mapBugpkMessage(message, platform) {
  const msg = zhError(message);
  if (!msg) return '解析失败，请稍后重试';
  if (/登录|过期|cookie/i.test(msg)) {
    return platform === 'xhs' ? '小红书接口需要登录态，暂时不可用' : msg;
  }
  if (/源异常/.test(msg)) return '解析接口暂时异常，请稍后重试';
  return msg;
}

async function parseWithBugpk(url, platform) {
  const endpoint = bugpkEndpoint(platform, url);
  if (!endpoint) throw new ParseError('该平台暂不走公共接口');
  const json = await requestJson(endpoint, { timeout: 40000 });
  if (!json || Number(json.code) !== 200) {
    throw new ParseError(mapBugpkMessage(json && json.msg, platform), { code: json && json.code });
  }
  return normalizeResult(json.data, platform, url, '公共接口');
}

function platformFromName(name) {
  const text = String(name || '');
  if (text.includes('小红书')) return 'xhs';
  if (text.includes('快手')) return 'kuaishou';
  if (text.includes('抖音')) return 'douyin';
  if (text.includes('视频号')) return 'wechat';
  if (/bilibili|B站/i.test(text)) return 'bilibili';
  return '';
}

function mapServiceMessage(json) {
  const code = firstString(json && json.error_code);
  if (code === 'RATE_LIMITED') return '解析服务请求过于频繁，请稍后再试';
  if (code === 'WECHAT_CHANNELS_COOKIE_REQUIRED') return '视频号需要微信登录态，公开接口无法解析';
  if (code === 'XHS_COOKIE_REQUIRED') return '小红书需要登录态，公开接口无法解析';
  const desc = firstString(json && json.retdesc, json && json.msg, json && json.message);
  return zhError(desc) || '解析失败，请稍后重试';
}

async function parseWithCustom(url, platform) {
  const base = String(state.settings.customBase || '').trim();
  if (!base) throw new ParseError('还没有填写自定义解析服务地址');
  let endpoint;
  if (base.includes('{url}')) {
    endpoint = base.replace(/\{url\}/g, encodeURIComponent(url));
  } else {
    endpoint = base + (base.includes('?') ? '&' : '?') + 'url=' + encodeURIComponent(url);
  }
  const headers = {};
  if (state.settings.customKey) headers['X-API-Key'] = state.settings.customKey;

  const json = await requestJson(endpoint, { headers, timeout: 45000 });
  if (!json) throw new ParseError('自定义服务没有返回数据');
  if (json.succ === false) throw new ParseError(mapServiceMessage(json), { code: json.error_code });
  if (json.code !== undefined && Number(json.code) !== 200 && json.code !== 0) {
    throw new ParseError(firstString(json.msg, json.message) || '自定义服务解析失败', { code: json.code });
  }

  const payload = json.data || json.result || json;
  const detected = platformFromName(payload.platform) || detectPlatform(url);
  return normalizeResult(payload, detected, url, '自定义服务');
}

function directResult(url) {
  let title = '视频直链';
  try {
    const path = new URL(url).pathname;
    const name = decodeURIComponent(path.split('/').pop() || '');
    if (name) title = name.replace(/\.[a-z0-9]{2,5}$/i, '');
  } catch (_) {}
  return {
    platform: 'direct',
    platformName: '视频直链',
    engine: '直链',
    sourceUrl: url,
    title,
    desc: '',
    author: '',
    avatar: '',
    authorId: '',
    cover: '',
    videoUrl: url,
    images: [],
    duration: 0,
    size: 0,
    sizeLabel: '',
    saved: false,
    createdAt: Date.now()
  };
}

async function parseLink(rawText) {
  const url = extractUrl(rawText);
  if (!url) {
    throw new ParseError('没有识别到链接，请粘贴完整的分享链接或分享口令');
  }
  const platform = detectPlatform(url);
  if (platform === 'direct') return directResult(url);

  const engineMode = state.settings.engine;
  const customReady = !!(state.settings.customBase || '').trim();
  const steps = [];

  if (engineMode !== 'custom') {
    if (platform === 'douyin' || platform === 'kuaishou' || platform === 'xhs') {
      steps.push(['公共接口', parseWithBugpk]);
    }
  }
  if (engineMode !== 'public' && customReady) {
    steps.push(['自定义服务', parseWithCustom]);
  }

  if (!steps.length) {
    if (platform === 'wechat') {
      throw new ParseError('视频号需要微信登录态，公开接口无法直接解析');
    }
    if (platform === 'bilibili') {
      throw new ParseError('暂不支持哔哩哔哩，可先保存视频直链再粘贴');
    }
    throw new ParseError('暂不支持这个链接，可在设置中接入自建解析服务');
  }

  let lastError = null;
  for (const [label, handler] of steps) {
    try {
      return await handler(url, platform);
    } catch (error) {
      lastError = error;
      if (error && error.fatal) break;
    }
  }
  throw lastError || new ParseError('解析失败，请稍后重试');
}

/* ---------------- 下载 ---------------- */

async function downloadBlob(url, { onProgress } = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 180000);
  let response;
  try {
    response = await fetch(url, {
      mode: 'cors',
      credentials: 'omit',
      referrerPolicy: 'no-referrer',
      signal: controller.signal
    });
  } catch (error) {
    clearTimeout(timer);
    if (error && error.name === 'AbortError') {
      throw new ParseError('下载超时，请检查网络后重试', { code: 'TIMEOUT' });
    }
    throw new ParseError('浏览器无法直接下载这个地址，已为你打开原视频', { code: 'CORS' });
  }

  try {
    if (!response.ok) {
      throw new ParseError(response.status === 403 ? '视频地址已过期，请重新解析' : httpMessage(response.status), { code: response.status });
    }

    const total = Number(response.headers.get('content-length')) || 0;
    const type = response.headers.get('content-type') || '';
    if (type.includes('text/html')) {
      throw new ParseError('视频地址已失效，请重新解析');
    }

    if (!response.body || typeof response.body.getReader !== 'function') {
      const blob = await response.blob();
      if (onProgress) onProgress(blob.size, blob.size);
      return blob;
    }

    const reader = response.body.getReader();
    const chunks = [];
    let received = 0;
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      if (value) {
        chunks.push(value);
        received += value.length;
        if (onProgress) onProgress(received, total);
      }
    }
    return new Blob(chunks, { type: type || 'application/octet-stream' });
  } catch (error) {
    if (error && error.name === 'AbortError') {
      throw new ParseError('下载超时，请检查网络后重试', { code: 'TIMEOUT' });
    }
    throw error;
  } finally {
    clearTimeout(timer);
  }
}

function openExternal(url) {
  if (!/^https?:\/\//i.test(url)) return;
  const a = document.createElement('a');
  a.href = url;
  a.target = '_blank';
  a.rel = 'noopener noreferrer';
  document.body.appendChild(a);
  a.click();
  a.remove();
}

async function saveBlob(blob, filename, title) {
  const file = new File([blob], filename, { type: blob.type || 'video/mp4' });

  if (canShareToAlbum && state.settings.iosSave === 'share') {
    try {
      if (navigator.canShare({ files: [file] })) {
        await navigator.share({ files: [file], title });
        return 'shared';
      }
    } catch (error) {
      if (error && error.name === 'AbortError') return 'cancelled';
      // 分享失败时退回下载
    }
  }

  const objectUrl = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = objectUrl;
  a.download = filename;
  a.rel = 'noopener';
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(objectUrl), 60000);
  return 'downloaded';
}

/* ---------------- UI ---------------- */

function cacheEls() {
  el.input = $('#linkInput');
  el.inputWrap = $('#inputWrap');
  el.pasteBtn = $('#pasteBtn');
  el.parseBtn = $('#parseBtn');
  el.parseBtnText = $('#parseBtnText');
  el.statusLine = $('#statusLine');
  el.platformPill = $('#platformPill');
  el.platformName = $('#platformName');
  el.metaHint = $('#metaHint');
  el.resultSection = $('#resultSection');
  el.resultCard = $('#resultCard');
  el.coverWrap = $('#coverWrap');
  el.resultCover = $('#resultCover');
  el.resultPlatform = $('#resultPlatform');
  el.resultDuration = $('#resultDuration');
  el.resultTitle = $('#resultTitle');
  el.authorRow = $('#authorRow');
  el.authorAvatar = $('#authorAvatar');
  el.authorName = $('#authorName');
  el.resultSize = $('#resultSize');
  el.resultDesc = $('#resultDesc');
  el.engineTag = $('#engineTag');
  el.saveBtn = $('#saveBtn');
  el.saveBtnText = $('#saveBtnText');
  el.saveHint = $('#saveHint');
  el.copyBtn = $('#copyBtn');
  el.shareBtn = $('#shareBtn');
  el.openBtn = $('#openBtn');
  el.previewBtn = $('#previewBtn');
  el.gallery = $('#gallery');
  el.galleryGrid = $('#galleryGrid');
  el.galleryCount = $('#galleryCount');
  el.historySection = $('#historySection');
  el.historyList = $('#historyList');
  el.clearHistoryBtn = $('#clearHistoryBtn');
  el.settingsBtn = $('#settingsBtn');
  el.settingsModal = $('#settingsModal');
  el.settingsClose = $('#settingsClose');
  el.engineSelect = $('#engineSelect');
  el.customBase = $('#customBase');
  el.customKey = $('#customKey');
  el.testCustomBtn = $('#testCustomBtn');
  el.iosSaveSelect = $('#iosSaveSelect');
  el.clearAllBtn = $('#clearAllBtn');
  el.aboutLine = $('#aboutLine');
  el.previewModal = $('#previewModal');
  el.previewClose = $('#previewClose');
  el.previewVideo = $('#previewVideo');
  el.previewTip = $('#previewTip');
  el.toast = $('#toast');
}

function setStatus(text, isError = false) {
  if (!el.statusLine) return;
  el.statusLine.textContent = text || '';
  el.statusLine.classList.toggle('is-error', !!isError && !!text);
}

let toastTimer = 0;
function showToast(text, duration = 2400) {
  if (!text) return;
  el.toast.textContent = text;
  el.toast.classList.add('is-show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.toast.classList.remove('is-show'), duration);
}

function setParseLoading(loading) {
  el.parseBtn.classList.toggle('is-loading', loading);
  el.parseBtn.disabled = loading || !el.input.value.trim();
  el.parseBtnText.textContent = loading ? '正在解析' : '解析视频';
  el.parseBtn.setAttribute('aria-busy', loading ? 'true' : 'false');
}

function setSaveLoading(loading, label) {
  el.saveBtn.classList.toggle('is-progress', loading);
  el.saveBtn.disabled = loading;
  if (label) el.saveBtnText.textContent = label;
  el.saveBtn.setAttribute('aria-busy', loading ? 'true' : 'false');
}

function clearSaveProgress() {
  el.saveBtn.classList.remove('is-progress');
  el.saveBtn.style.removeProperty('--p');
  el.saveBtn.disabled = false;
  if (state.result) {
    el.saveBtnText.textContent = state.result.videoUrl ? '保存视频' : '保存图片';
  }
}

function updateDetection() {
  const url = extractUrl(el.input.value);
  const platform = url ? detectPlatform(url) : 'unknown';
  const known = platform !== 'unknown';
  el.platformPill.hidden = !known;
  if (known) {
    el.platformPill.dataset.platform = platform;
    el.platformName.textContent = platformName(platform);
  }
  el.metaHint.textContent = known
    ? (platform === 'direct' ? '识别为视频直链' : '已识别，点下方按钮解析')
    : '支持识别整段分享口令';
  el.parseBtn.disabled = state.busy || !url;
}

function renderHistory() {
  const hasHistory = state.history.length > 0;
  el.historySection.hidden = !hasHistory;
  if (!hasHistory) {
    el.historyList.replaceChildren();
    return;
  }
  const fragment = document.createDocumentFragment();
  for (const item of state.history) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'history-item';
    button.dataset.id = item.id;

    const thumb = document.createElement('span');
    thumb.className = 'history-thumb';
    if (item.cover) {
      const img = document.createElement('img');
      img.src = item.cover;
      img.alt = '';
      img.loading = 'lazy';
      img.referrerPolicy = 'no-referrer';
      img.onerror = () => img.remove();
      thumb.appendChild(img);
    }

    const main = document.createElement('span');
    main.className = 'history-main';
    const title = document.createElement('span');
    title.className = 'history-title';
    title.textContent = item.title || '未命名作品';
    const sub = document.createElement('span');
    sub.className = 'history-sub';
    sub.textContent = [item.platformName, item.author, formatTimeAgo(item.createdAt)].filter(Boolean).join(' · ');
    main.append(title, sub);

    const stateEl = document.createElement('span');
    stateEl.className = 'history-state';
    if (item.saved) {
      stateEl.innerHTML = '<svg viewBox="0 0 24 24"><path d="m5 12.5 4.2 4.2L19 7"/></svg>';
      stateEl.append('已保存');
    } else {
      stateEl.textContent = '再解析';
    }

    button.append(thumb, main, stateEl);
    button.addEventListener('click', () => {
      el.input.value = item.sourceUrl;
      updateDetection();
      doParse();
    });
    fragment.appendChild(button);
  }
  el.historyList.replaceChildren(fragment);
}

function addHistory(result) {
  if (!result || !result.sourceUrl) return;
  const existingIndex = state.history.findIndex(item => item.sourceUrl === result.sourceUrl);
  const entry = {
    id: result.sourceUrl + '#' + result.createdAt,
    sourceUrl: result.sourceUrl,
    platform: result.platform,
    platformName: (!result.videoUrl && result.platform === 'unknown') ? '图集' : result.platformName,
    title: result.title,
    author: result.author,
    cover: result.cover || result.images[0] || '',
    createdAt: Date.now(),
    saved: !!result.saved
  };
  if (existingIndex >= 0) {
    const previous = state.history.splice(existingIndex, 1)[0];
    entry.saved = entry.saved || previous.saved;
  }
  state.history.unshift(entry);
  state.history = state.history.slice(0, HISTORY_LIMIT);
  persistHistory();
  renderHistory();
}

function markCurrentSaved() {
  if (!state.result) return;
  state.result.saved = true;
  const entry = state.history.find(item => item.sourceUrl === state.result.sourceUrl);
  if (entry) {
    entry.saved = true;
    persistHistory();
    renderHistory();
  }
}

function hideResult() {
  el.resultSection.hidden = true;
  el.previewVideo.pause();
  el.previewVideo.removeAttribute('src');
  el.previewVideo.load();
}

function renderResult(result) {
  state.result = result;
  el.resultSection.hidden = false;

  el.engineTag.textContent = result.engine ? '来源 · ' + result.engine : '';
  const isGallery = !result.videoUrl && result.images.length > 0;
  el.resultPlatform.textContent = isGallery && result.platform === 'unknown' ? '图集' : result.platformName;
  el.resultPlatform.hidden = false;

  const duration = formatDuration(result.duration);
  el.resultDuration.hidden = !duration;
  el.resultDuration.textContent = duration || '';

  el.resultTitle.textContent = result.title || '未命名作品';

  const hasAuthor = !!(result.author || result.avatar);
  el.authorRow.hidden = !hasAuthor;
  if (hasAuthor) {
    el.authorName.textContent = result.author || '未知作者';
    el.resultSize.textContent = result.sizeLabel || '';
    if (result.avatar) {
      el.authorAvatar.src = result.avatar;
      el.authorAvatar.hidden = false;
      el.authorAvatar.onerror = () => { el.authorAvatar.hidden = true; };
    } else {
      el.authorAvatar.hidden = true;
    }
  }

  el.resultDesc.hidden = !result.desc;
  el.resultDesc.textContent = result.desc || '';

  if (result.cover) {
    el.resultCover.hidden = false;
    el.resultCover.classList.remove('is-loaded');
    el.resultCover.onload = () => el.resultCover.classList.add('is-loaded');
    el.resultCover.onerror = () => { el.resultCover.hidden = true; };
    el.resultCover.src = result.cover;
  } else {
    el.resultCover.removeAttribute('src');
    el.resultCover.hidden = true;
  }

  el.previewBtn.hidden = !result.videoUrl;
  el.copyBtn.hidden = !result.videoUrl;
  el.saveBtnText.textContent = result.videoUrl ? '保存视频' : '保存图片';
  el.saveBtn.hidden = !result.videoUrl && !result.images.length;

  if (result.videoUrl) {
    el.saveHint.textContent = isIOS
      ? (canShareToAlbum
        ? 'iPhone 会弹出系统分享面板，选择「存储视频」即可存入相册。'
        : (isSecureContext
          ? '当前 iPhone 不支持文件分享，保存后会下载到「文件」。'
          : '当前不是 HTTPS，保存后会打开视频；建议用 HTTPS 地址打开以直接存入相册。'))
      : '保存后会出现在「下载」目录或相册中。';
  } else {
    el.saveHint.textContent = isIOS
      ? (canShareToAlbum
        ? 'iPhone 可一次分享多张图片，在分享面板选择「存储图像」。'
        : (isSecureContext
          ? '当前 iPhone 不支持文件分享，图片会保存到「文件」。'
          : '当前不是 HTTPS，图片会逐张打开；建议用 HTTPS 地址打开以存入相册。'))
      : '图片会逐张保存到「下载」目录。';
  }

  el.gallery.hidden = !result.images.length;
  if (result.images.length) {
    el.galleryCount.textContent = result.images.length + ' 张';
    const fragment = document.createDocumentFragment();
    result.images.forEach((imageUrl, index) => {
      const item = document.createElement('div');
      item.className = 'gallery-item';
      const img = document.createElement('img');
      img.src = imageUrl;
      img.alt = '图片 ' + (index + 1);
      img.loading = 'lazy';
      img.referrerPolicy = 'no-referrer';
      const save = document.createElement('button');
      save.type = 'button';
      save.setAttribute('aria-label', '保存第 ' + (index + 1) + ' 张图片');
      save.innerHTML = '<svg viewBox="0 0 24 24"><path d="M12 4v11"/><path d="m7.5 10.5 4.5 4.5 4.5-4.5"/><path d="M5 20h14"/></svg>';
      save.addEventListener('click', () => saveSingleImage(imageUrl, index + 1));
      item.append(img, save);
      fragment.appendChild(item);
    });
    el.galleryGrid.replaceChildren(fragment);
  } else {
    el.galleryGrid.replaceChildren();
  }

  clearSaveProgress();

  requestAnimationFrame(() => {
    el.resultSection.scrollIntoView({ behavior: 'smooth', block: 'start' });
  });
}

/* ---------------- 操作 ---------------- */

async function readClipboard() {
  try {
    if (!navigator.clipboard || !navigator.clipboard.readText) throw new Error('unsupported');
    const text = await navigator.clipboard.readText();
    if (!text || !text.trim()) {
      el.input.focus();
      setStatus('剪贴板是空的，请先复制分享链接', true);
      return;
    }
    el.input.value = text.trim();
    updateDetection();
    setStatus('已粘贴，点「解析视频」', false);
  } catch (_) {
    el.input.focus();
    setStatus('无法读取剪贴板，请在输入框长按粘贴', true);
  }
}

async function doParse() {
  if (state.busy) return;
  const raw = el.input.value.trim();
  if (!raw) {
    setStatus('请先粘贴分享链接', true);
    el.input.focus();
    return;
  }
  state.busy = true;
  setParseLoading(true);
  setStatus('正在解析…', false);
  hideResult();

  try {
    const result = await parseLink(raw);
    renderResult(result);
    addHistory(result);
    setStatus(result.images.length && !result.videoUrl
      ? `解析成功，共 ${result.images.length} 张图片`
      : '解析成功，可以保存了', false);
  } catch (error) {
    console.warn('[秒取] 解析失败', error);
    state.result = null;
    hideResult();
    const platform = detectPlatform(extractUrl(raw));
    setStatus(friendlyError(error, platform), true);
  } finally {
    state.busy = false;
    setParseLoading(false);
    updateDetection();
  }
}

async function saveCurrent() {
  const result = state.result;
  if (!result || !el.saveBtn) return;

  if (!result.videoUrl && result.images.length) {
    await saveAllImages(result.images);
    return;
  }
  if (!result.videoUrl) return;

  const filename = `${result.platformName}_${sanitizeFilename(result.author || result.title)}_${formatDateTime()}.${extensionFromUrl(result.videoUrl, 'mp4')}`;
  setSaveLoading(true, '准备下载');
  setStatus('正在下载视频…', false);
  let lastPercent = -1;

  try {
    const blob = await downloadBlob(result.videoUrl, {
      onProgress: (received, total) => {
        const percent = total ? Math.min(100, Math.round(received / total * 100)) : 0;
        el.saveBtn.style.setProperty('--p', (total ? percent : 8) + '%');
        if (total) {
          if (percent !== lastPercent) {
            lastPercent = percent;
            el.saveBtnText.textContent = `下载中 ${percent}%`;
          }
        } else {
          el.saveBtnText.textContent = '下载中…';
        }
      }
    });

    setSaveLoading(true, '正在保存');
    const mode = await saveBlob(blob, filename, result.title);
    if (mode === 'cancelled') {
      setStatus('已取消保存', false);
      showToast('已取消保存');
      return;
    }
    markCurrentSaved();
    setStatus(mode === 'shared' ? '已打开分享面板，选择「存储视频」即可' : '已保存', false);
    showToast(mode === 'shared' ? '已打开分享面板，选择「存储视频」即可' : '已保存');
    el.saveBtn.classList.remove('is-progress');
    el.saveBtn.style.removeProperty('--p');
    el.saveBtn.disabled = false;
    el.saveBtnText.textContent = '已保存';
  } catch (error) {
    console.warn('[秒取] 下载失败', error);
    if (error && error.code === 'CORS') {
      openExternal(result.videoUrl);
      setStatus('浏览器阻止了直接保存，已打开原视频；长按视频可保存', true);
      showToast('已打开原视频，长按保存');
    } else {
      const message = zhError(error && error.message) || '下载失败，请重新解析后再试';
      setStatus(message, true);
      showToast(message);
    }
    clearSaveProgress();
  }
}

async function saveSingleImage(imageUrl, index) {
  setStatus(`正在下载第 ${index} 张图片…`, false);
  try {
    const blob = await downloadBlob(imageUrl);
    const ext = extensionFromUrl(imageUrl, 'jpg');
    const base = sanitizeFilename(state.result && state.result.title, '图片');
    const filename = `${base}_${index}_${formatDateTime()}.${ext}`;
    const mode = await saveBlob(blob, filename, base);
    if (mode === 'cancelled') return;
    showToast(mode === 'shared' ? '已打开发送面板' : '图片已保存');
    setStatus('图片已保存', false);
  } catch (error) {
    if (error && error.code === 'CORS') {
      openExternal(imageUrl);
      setStatus('已打开原图，长按可保存', true);
    } else {
      setStatus(zhError(error && error.message) || '图片保存失败', true);
    }
  }
}

async function saveAllImages(images) {
  if (!images.length) return;
  setSaveLoading(true, '准备下载');
  setStatus(`正在下载图片 0/${images.length}`, false);
  const files = [];
  let failed = 0;

  for (let i = 0; i < images.length; i += 1) {
    setStatus(`正在下载图片 ${i + 1}/${images.length}`, false);
    el.saveBtn.style.setProperty('--p', Math.round((i / images.length) * 100) + '%');
    try {
      const blob = await downloadBlob(images[i]);
      const ext = extensionFromUrl(images[i], 'jpg');
      const base = sanitizeFilename(state.result && state.result.title, '图片');
      files.push(new File([blob], `${base}_${i + 1}_${formatDateTime()}.${ext}`, { type: blob.type || 'image/jpeg' }));
    } catch (_) {
      failed += 1;
    }
  }

  try {
    if (files.length && canShareToAlbum && state.settings.iosSave === 'share') {
      try {
        await navigator.share({ files, title: state.result ? state.result.title : '图片' });
        markCurrentSaved();
        setStatus('已打开分享面板', false);
        showToast('选择「存储图像」即可存入相册');
        return;
      } catch (error) {
        if (error && error.name === 'AbortError') {
          setStatus('已取消保存', false);
          return;
        }
      }
    }

    for (let i = 0; i < files.length; i += 1) {
      const objectUrl = URL.createObjectURL(files[i]);
      const a = document.createElement('a');
      a.href = objectUrl;
      a.download = files[i].name;
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(objectUrl), 60000);
      await new Promise(resolve => setTimeout(resolve, 350));
    }
    if (files.length) {
      markCurrentSaved();
      setStatus(failed ? `已保存 ${files.length} 张，${failed} 张失败` : `已保存 ${files.length} 张图片`, false);
      showToast(`已保存 ${files.length} 张图片`);
    } else {
      setStatus('图片下载失败，请点单张图片重试', true);
    }
  } finally {
    clearSaveProgress();
  }
}

async function copyDirectLink() {
  const url = state.result && state.result.videoUrl;
  if (!url) {
    showToast('没有可复制的视频直链');
    return;
  }
  try {
    await navigator.clipboard.writeText(url);
    showToast('直链已复制');
  } catch (_) {
    const area = document.createElement('textarea');
    area.value = url;
    area.style.position = 'fixed';
    area.style.opacity = '0';
    document.body.appendChild(area);
    area.select();
    try {
      document.execCommand('copy');
      showToast('直链已复制');
    } catch (error) {
      showToast('复制失败，请长按视频地址手动复制');
    }
    area.remove();
  }
}

async function shareCurrent() {
  if (!state.result) return;
  const { title, sourceUrl, videoUrl } = state.result;
  try {
    if (navigator.share) {
      await navigator.share({ title: title || '分享视频', text: title || '', url: sourceUrl || videoUrl });
      return;
    }
    await copyDirectLink();
  } catch (error) {
    if (error && error.name === 'AbortError') return;
    showToast('分享失败，已尝试复制直链');
    copyDirectLink();
  }
}

function openOriginal() {
  const url = state.result && (state.result.sourceUrl || state.result.videoUrl);
  if (!url) return;
  openExternal(url);
}

function openPreview() {
  if (!state.result || !state.result.videoUrl) return;
  el.previewTip.textContent = '若视频无法播放，可直接保存到手机后观看';
  el.previewVideo.onerror = () => {
    el.previewTip.textContent = '当前地址不支持直接预览，保存后即可观看';
  };
  el.previewVideo.src = state.result.videoUrl;
  el.previewModal.hidden = false;
  document.body.style.overflow = 'hidden';
  el.previewVideo.play().catch(() => {});
}

function closePreview() {
  el.previewModal.hidden = true;
  el.previewVideo.pause();
  el.previewVideo.removeAttribute('src');
  el.previewVideo.load();
  document.body.style.overflow = '';
}

function openSettings() {
  el.engineSelect.value = state.settings.engine;
  el.customBase.value = state.settings.customBase;
  el.customKey.value = state.settings.customKey;
  el.iosSaveSelect.value = state.settings.iosSave;
  el.settingsModal.hidden = false;
  document.body.style.overflow = 'hidden';
}

function closeSettings() {
  el.settingsModal.hidden = true;
  document.body.style.overflow = '';
}

let settingsTimer = 0;
function scheduleSettingsSave() {
  clearTimeout(settingsTimer);
  settingsTimer = setTimeout(() => {
    state.settings.customBase = el.customBase.value.trim();
    state.settings.customKey = el.customKey.value.trim();
    saveSettings();
  }, 450);
}

async function testCustomService() {
  const url = extractUrl(el.input.value) || (state.result && state.result.sourceUrl) || '';
  if (!url) {
    showToast('请先在首页粘贴一个链接');
    return;
  }
  if (!el.customBase.value.trim()) {
    showToast('请先填写自定义服务地址');
    return;
  }
  state.settings.customBase = el.customBase.value.trim();
  state.settings.customKey = el.customKey.value.trim();
  saveSettings();
  el.testCustomBtn.disabled = true;
  el.testCustomBtn.textContent = '测试中…';
  try {
    const result = await parseWithCustom(url, detectPlatform(url));
    showToast('连接成功：' + (result.title || '已返回数据'));
  } catch (error) {
    showToast(error && error.message ? error.message : '连接失败');
  } finally {
    el.testCustomBtn.disabled = false;
    el.testCustomBtn.textContent = '测试服务';
  }
}

function clearHistory() {
  if (!state.history.length) return;
  if (!confirm('清空最近解析记录？')) return;
  state.history = [];
  persistHistory();
  renderHistory();
  showToast('已清空历史');
}

function clearAll() {
  if (!confirm('清除所有历史与设置？此操作不可恢复。')) return;
  localStorage.removeItem(LS_HISTORY);
  localStorage.removeItem(LS_SETTINGS);
  state.history = [];
  state.settings = { ...DEFAULT_SETTINGS };
  state.result = null;
  renderHistory();
  hideResult();
  closeSettings();
  showToast('已清除全部数据');
}

/* ---------------- 事件 ---------------- */

function bindEvents() {
  el.input.addEventListener('input', updateDetection);
  el.input.addEventListener('focus', () => el.inputWrap.classList.add('is-focus'));
  el.input.addEventListener('blur', () => el.inputWrap.classList.remove('is-focus'));
  el.input.addEventListener('keydown', event => {
    if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) {
      event.preventDefault();
      doParse();
    }
  });
  el.pasteBtn.addEventListener('click', readClipboard);
  el.parseBtn.addEventListener('click', doParse);
  el.saveBtn.addEventListener('click', saveCurrent);
  el.copyBtn.addEventListener('click', copyDirectLink);
  el.shareBtn.addEventListener('click', shareCurrent);
  el.openBtn.addEventListener('click', openOriginal);
  el.previewBtn.addEventListener('click', openPreview);
  el.previewClose.addEventListener('click', closePreview);
  el.previewModal.addEventListener('click', event => {
    if (event.target && event.target.dataset && event.target.dataset.close === 'preview') closePreview();
  });

  el.settingsBtn.addEventListener('click', openSettings);
  el.settingsClose.addEventListener('click', closeSettings);
  el.settingsModal.addEventListener('click', event => {
    if (event.target && event.target.dataset && event.target.dataset.close === 'settings') closeSettings();
  });
  el.engineSelect.addEventListener('change', () => {
    state.settings.engine = el.engineSelect.value;
    saveSettings();
  });
  el.iosSaveSelect.addEventListener('change', () => {
    state.settings.iosSave = el.iosSaveSelect.value;
    saveSettings();
  });
  el.customBase.addEventListener('input', scheduleSettingsSave);
  el.customKey.addEventListener('input', scheduleSettingsSave);
  el.testCustomBtn.addEventListener('click', testCustomService);
  el.clearHistoryBtn.addEventListener('click', clearHistory);
  el.clearAllBtn.addEventListener('click', clearAll);

  document.addEventListener('keydown', event => {
    if (event.key === 'Escape') {
      if (!el.previewModal.hidden) closePreview();
      else if (!el.settingsModal.hidden) closeSettings();
    }
  });
}

/* ---------------- 启动 ---------------- */

function handleShareTarget() {
  const params = new URLSearchParams(location.search);
  const shared = params.get('text') || params.get('url') || params.get('title') || '';
  if (!shared) return;
  el.input.value = shared.trim();
  updateDetection();
  history.replaceState(null, '', location.pathname);
  const url = extractUrl(shared);
  if (url) {
    setStatus('已接收分享链接，正在解析…', false);
    setTimeout(() => doParse(), 420);
  }
}

function registerServiceWorker() {
  if (!('serviceWorker' in navigator)) return;
  if (!isSecureContext) return;
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('./sw.js').catch(error => {
      console.warn('[秒取] Service Worker 注册失败', error);
    });
  });
}

async function init() {
  cacheEls();
  loadSettings();
  loadHistory();
  bindEvents();
  updateDetection();
  renderHistory();
  setStatus(isIOS
    ? (canShareToAlbum
      ? 'iPhone 保存时会弹出分享面板，选择「存储视频」即可'
      : (isSecureContext
        ? 'iPhone 保存后会存到「文件」，可在相册中再导入'
        : '当前是局域网 HTTP，可用下方按钮保存；用 HTTPS 打开可直接存入相册'))
    : '支持抖音、快手；小红书与视频号需要登录态', false);
  if (!isSecureContext && el.aboutLine) {
    el.aboutLine.textContent = '秒取 v1.0 · 当前是局域网 HTTP，安装与离线需用 HTTPS 地址打开';
  }
  registerServiceWorker();
  handleShareTarget();
}

document.addEventListener('DOMContentLoaded', init);
