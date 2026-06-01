<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no, viewport-fit=cover">
<title>АрбузAI</title>
<script src="https://telegram.org/js/telegram-web-app.js"></script>
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');

:root {
  --bg: #f9f9f9;
  --white: #ffffff;
  --surface: rgba(255, 255, 255, 0.75);
  --surface2: rgba(242, 242, 247, 0.8);
  --border: rgba(0, 0, 0, 0.06);
  --border2: rgba(255, 255, 255, 0.5);
  --text: #191919;
  --text2: #6e6e73;
  --text3: #bcbcc2;
  --accent: #10a37f;
  --accent2: #1a7f64;
  --blue: #007aff;
  --msg-user-bg: #f4f4f4;
  --msg-ai-bg: transparent;
  --radius: 24px;
  --radius-sm: 16px;
  --radius-xs: 12px;
  --blur: blur(25px);
  --shadow: 0 8px 32px rgba(0,0,0,0.04);
  --shadow-sm: 0 4px 12px rgba(0,0,0,0.02);
  --ios-anim: cubic-bezier(0.25, 1, 0.5, 1);
  --transition-smooth: all 0.4s cubic-bezier(0.25, 1, 0.5, 1);
}

@media (prefers-color-scheme: dark) {
  :root {
    --bg: #171717;
    --white: #212121;
    --surface: rgba(33, 33, 33, 0.75);
    --surface2: rgba(47, 47, 47, 0.8);
    --border: rgba(255, 255, 255, 0.08);
    --border2: rgba(255, 255, 255, 0.1);
    --text: #ececec;
    --text2: #b4b4b4;
    --text3: #676767;
    --msg-user-bg: #2f2f2f;
    --shadow: 0 8px 32px rgba(0,0,0,0.2);
  }
}

* { box-sizing: border-box; margin: 0; padding: 0; -webkit-tap-highlight-color: transparent; }

body {
  font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
  background: var(--bg);
  color: var(--text);
  height: 100vh;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  font-size: 16px;
}

/* ── Sidebar (Шторка iOS) ── */
.sidebar {
  position: fixed;
  top: 0; left: 0; bottom: 0;
  width: 290px;
  background: var(--surface);
  backdrop-filter: var(--blur);
  -webkit-backdrop-filter: var(--blur);
  border-right: 1px solid var(--border);
  display: flex;
  flex-direction: column;
  transform: translateX(-100%);
  transition: var(--transition-smooth);
  z-index: 200;
  padding-top: env(safe-area-inset-top);
}
.sidebar.open { transform: translateX(0); }

.sidebar-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0,0,0,0.15);
  backdrop-filter: blur(5px);
  -webkit-backdrop-filter: blur(5px);
  z-index: 199;
  display: none;
  opacity: 0;
  transition: var(--transition-smooth);
}
.sidebar-overlay.open { display: block; opacity: 1; }

.sidebar-header {
  padding: 24px 20px 16px;
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.sidebar-title { font-size: 20px; font-weight: 700; letter-spacing: -0.5px; }
.sidebar-new-btn {
  width: 36px; height: 36px; background: var(--white); border: 1px solid var(--border);
  border-radius: 50%; display: flex; align-items: center; justify-content: center;
  cursor: pointer; box-shadow: var(--shadow-sm); color: var(--text); transition: var(--transition-smooth);
}
.sidebar-new-btn:active { transform: scale(0.9); }

.sidebar-nav { padding: 0 10px 10px; display: flex; flex-direction: column; gap: 4px; }
.sidebar-nav-item {
  display: flex; align-items: center; gap: 12px; padding: 12px;
  border-radius: var(--radius-xs); cursor: pointer; font-weight: 500; transition: var(--transition-smooth);
}
.sidebar-nav-item:active { background: var(--border); }
.sidebar-nav-icon {
  width: 28px; height: 28px; background: var(--white); border-radius: 8px;
  display: flex; align-items: center; justify-content: center; box-shadow: var(--shadow-sm);
}

.sidebar-section { padding: 16px 20px 8px; font-size: 11px; font-weight: 600; color: var(--text2); text-transform: uppercase; letter-spacing: 0.5px; }
.sidebar-chats { flex: 1; overflow-y: auto; padding: 0 10px; }
.sidebar-chats::-webkit-scrollbar { display: none; }

.chat-item {
  padding: 12px; border-radius: var(--radius-xs); cursor: pointer;
  transition: var(--transition-smooth); display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-bottom: 2px;
}
.chat-item:active { background: var(--border); }
.chat-item.active { background: var(--white); box-shadow: var(--shadow-sm); }
.chat-item-title { font-size: 14px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; flex: 1; }
.chat-item-delete { background: none; border: none; color: var(--text3); cursor: pointer; padding: 4px; border-radius: 6px; }

/* ── Header ── */
.header {
  display: flex; align-items: center; justify-content: space-between; padding: 12px 16px;
  padding-top: max(12px, env(safe-area-inset-top)); background: rgba(var(--bg), 0.8);
  backdrop-filter: var(--blur); -webkit-backdrop-filter: var(--blur); border-bottom: 1px solid var(--border); position: relative; z-index: 10;
}
.header-title { flex: 1; text-align: center; cursor: pointer; }
.header-title span { font-size: 16px; font-weight: 600; letter-spacing: -0.2px; }
.header-title .model-badge { font-size: 12px; color: var(--text2); font-weight: 500; margin-top: 1px; }
.hdr-btn {
  width: 38px; height: 38px; background: transparent; border: none; border-radius: 50%;
  display: flex; align-items: center; justify-content: center; cursor: pointer; color: var(--text); transition: var(--transition-smooth);
}
.hdr-btn:active { background: var(--border); transform: scale(0.9); }

/* ── LIQUID GLASS MODEL PICKER (iOS СТИЛЬ С ПЛАВНОЙ АНИМАЦИЕЙ) ── */
.model-picker { position: fixed; inset: 0; z-index: 300; display: none; align-items: flex-end; }
.model-picker.open { display: flex; }
.model-picker-overlay {
  position: absolute; inset: 0; background: rgba(0, 0, 0, 0.15);
  backdrop-filter: blur(15px) saturate(190%); -webkit-backdrop-filter: blur(15px) saturate(190%);
  opacity: 0; transition: opacity 0.5s var(--ios-anim);
}
.model-picker.open .model-picker-overlay { opacity: 1; }

.model-picker-sheet {
  position: relative; width: 100%;
  background: linear-gradient(135deg, rgba(255, 255, 255, 0.7), rgba(245, 245, 247, 0.5));
  backdrop-filter: blur(40px) saturate(200%); -webkit-backdrop-filter: blur(40px) saturate(200%);
  border-top: 1px solid rgba(255, 255, 255, 0.3); box-shadow: 0 -15px 40px rgba(0, 0, 0, 0.06), inset 0 1px 0 rgba(255, 255, 255, 0.4);
  border-radius: 32px 32px 0 0; padding: 10px 0 max(24px, env(safe-area-inset-bottom)) 0;
  transform: translateY(100%); transition: transform 0.5s var(--ios-anim);
}
@media (prefers-color-scheme: dark) {
  .model-picker-sheet {
    background: linear-gradient(135deg, rgba(35, 35, 37, 0.7), rgba(20, 20, 20, 0.5));
    border-top: 1px solid rgba(255, 255, 255, 0.15); box-shadow: 0 -15px 40px rgba(0, 0, 0, 0.25), inset 0 1px 0 rgba(255, 255, 255, 0.08);
  }
}
.model-picker.open .model-picker-sheet { transform: translateY(0); }
.sheet-handle { width: 36px; height: 5px; background: rgba(0, 0, 0, 0.12); border-radius: 3px; margin: 0 auto 20px; }
@media (prefers-color-scheme: dark) { .sheet-handle { background: rgba(255, 255, 255, 0.18); } }
.sheet-title { font-size: 12px; font-weight: 600; color: var(--text2); text-transform: uppercase; letter-spacing: 0.5px; padding: 0 24px 12px; }

.model-group { margin: 0 16px 12px; background: rgba(255, 255, 255, 0.45); border-radius: var(--radius-sm); overflow: hidden; border: 1px solid rgba(255, 255, 255, 0.25); box-shadow: var(--shadow-sm); }
@media (prefers-color-scheme: dark) { .model-group { background: rgba(255, 255, 255, 0.04); border: 1px solid rgba(255, 255, 255, 0.06); } }
.model-group-header { display: flex; align-items: center; gap: 12px; padding: 14px 16px; cursor: pointer; transition: background 0.25s; }
.model-group-icon { width: 26px; height: 26px; border-radius: 6px; display: flex; align-items: center; justify-content: center; font-size: 14px; }
.model-group-icon.gpt { background: #10a37f; }
.model-group-icon.claude { background: #d97753; }
.model-group-name { font-size: 15px; font-weight: 600; flex: 1; }
.model-group-chevron { color: var(--text3); transition: transform 0.35s var(--ios-anim); }
.model-group-chevron.open { transform: rotate(90deg); }

.model-group-items { display: none; max-height: 0; overflow: hidden; transition: max-height 0.35s var(--ios-anim); }
.model-group-items.open { display: block; max-height: max-content; }

.model-item { display: flex; align-items: center; justify-content: space-between; padding: 14px 16px; cursor: pointer; border-top: 1px solid var(--border); transition: var(--transition-smooth); }
.model-item:active { background: rgba(0,0,0,0.04); }
.model-item.selected { background: rgba(16, 163, 127, 0.08); }
.model-item-name { font-size: 15px; font-weight: 500; }
.model-check { color: var(--accent); opacity: 0; transition: transform 0.25s var(--ios-anim), opacity 0.25s; transform: scale(0.6); }
.model-item.selected .model-check { opacity: 1; transform: scale(1); }

/* ── Messages ── */
.messages { flex: 1; overflow-y: auto; padding: 20px 16px; display: flex; flex-direction: column; gap: 24px; }
.messages::-webkit-scrollbar { display: none; }

.empty-state { flex: 1; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 24px; padding-bottom: 20px; }
.empty-logo { width: 64px; height: 64px; background: var(--accent); border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: 32px; box-shadow: 0 8px 24px rgba(16, 163, 127, 0.15); }
.quick-actions { display: flex; flex-direction: column; gap: 10px; width: 100%; max-width: 320px; }
.quick-action { display: flex; align-items: center; gap: 14px; padding: 14px 16px; background: var(--white); border: 1px solid var(--border); border-radius: var(--radius-sm); cursor: pointer; transition: var(--transition-smooth); box-shadow: var(--shadow-sm); }
.quick-action:active { transform: scale(0.96); background: var(--surface2); }

.msg { display: flex; flex-direction: column; gap: 6px; transform: translateY(15px); opacity: 0; animation: iosPopIn 0.45s cubic-bezier(0.25, 1, 0.5, 1) forwards; }
@keyframes iosPopIn { to { opacity: 1; transform: translateY(0); } }

.msg.user { align-items: flex-end; }
.msg.ai { align-items: flex-start; width: 100%; }
.msg-bubble { max-width: 85%; padding: 12px 16px; line-height: 1.58; word-break: break-word; font-size: 15px; }
.msg.user .msg-bubble { background: var(--msg-user-bg); color: var(--text); border-radius: 20px 20px 4px 20px; }
.msg.ai .msg-bubble { background: var(--msg-ai-bg); padding: 0; max-width: 100%; color: var(--text); }
.msg-image-wrap { max-width: 85%; border-radius: var(--radius-sm); overflow: hidden; box-shadow: var(--shadow); margin-bottom: 4px; transition: var(--transition-smooth); }
.msg-image-wrap img { width: 100%; display: block; }
.msg-actions { display: flex; gap: 6px; margin-top: 4px; }
.msg-action { background: transparent; border: none; color: var(--text2); cursor: pointer; padding: 6px 10px; font-size: 13px; border-radius: 8px; transition: var(--transition-smooth); }
.msg-action:active { background: var(--border); }

.typing { display: flex; align-items: center; gap: 5px; padding: 8px 0; }
.typing span { width: 6px; height: 6px; background: var(--text3); border-radius: 50%; animation: iosBounce 1.4s infinite both; }
.typing span:nth-child(2) { animation-delay: .2s }
.typing span:nth-child(3) { animation-delay: .4s }
@keyframes iosBounce { 0%,80%,100%{transform:scale(0.6);opacity:0.4} 40%{transform:scale(1.2);opacity:1} }

/* ── Файлы и Инпут ── */
.file-preview { display: flex; flex-wrap: wrap; gap: 8px; padding: 0 16px 8px; }
.preview-item { position: relative; width: 60px; height: 60px; border-radius: 10px; overflow: hidden; border: 1px solid var(--border); animation: iosPopIn 0.3s var(--ios-anim); }
.preview-item img { width: 100%; height: 100%; object-fit: cover; }
.preview-item .doc-icon { width: 100%; height: 100%; display: flex; align-items: center; justify-content: center; background: var(--surface2); font-size: 12px; font-weight: bold; color: var(--text2); }
.preview-item .remove-btn { position: absolute; top: 2px; right: 2px; background: rgba(0,0,0,0.5); color: white; border: none; border-radius: 50%; width: 16px; height: 16px; font-size: 10px; cursor: pointer; display: flex; align-items: center; justify-content: center; }

.input-area { padding: 12px 16px max(16px, env(safe-area-inset-bottom)) 16px; background: transparent; flex-shrink: 0; }
.input-box { display: flex; align-items: flex-end; gap: 8px; background: var(--white); border-radius: 26px; padding: 6px 8px 6px 14px; box-shadow: var(--shadow); border: 1px solid var(--border); transition: var(--transition-smooth); }
.input-box:focus-within { border-color: rgba(16, 163, 127, 0.25); box-shadow: 0 0 0 3px rgba(16, 163, 127, 0.04); }
.text-input { flex: 1; background: none; border: none; color: var(--text); font-family: inherit; font-size: 16px; resize: none; outline: none; max-height: 140px; line-height: 1.4; padding: 6px 0; }
.send-btn { background: var(--text); border: none; border-radius: 50%; color: var(--bg); width: 34px; height: 34px; display: flex; align-items: center; justify-content: center; cursor: pointer; flex-shrink: 0; transition: var(--transition-smooth); }
@media (prefers-color-scheme: dark) { .send-btn { background: var(--white); color: var(--bg); } }
.send-btn:disabled { background: transparent; color: var(--text3); cursor: not-allowed; transform: scale(1) !important; }
.send-btn:active:not(:disabled) { transform: scale(0.9); }

.toast { position: fixed; bottom: 100px; left: 50%; transform: translateX(-50%) translateY(20px); background: rgba(0, 0, 0, 0.8); backdrop-filter: var(--blur); border-radius: 14px; padding: 10px 20px; font-size: 14px; color: white; opacity: 0; transition: var(--transition-smooth); pointer-events: none; z-index: 500; }
.toast.show { opacity: 1; transform: translateX(-50%) translateY(0); }

pre { background: var(--surface2); border-radius: 12px; padding: 14px; overflow-x: auto; margin: 8px 0; font-size: 13px; border: 1px solid var(--border); }
code { font-family: 'SF Mono', Menlo, monospace; font-size: 13px; background: var(--surface2); padding: 2px 5px; border-radius: 6px; }
pre code { background: none; padding: 0; }
</style>
</head>
<body>

<div class="sidebar-overlay" id="sidebarOverlay" onclick="toggleSidebar()"></div>

<div class="sidebar" id="sidebar">
  <div class="sidebar-header">
    <span class="sidebar-title">АрбузAI</span>
    <button class="sidebar-new-btn" onclick="newChat();toggleSidebar()">
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
    </button>
  </div>
  <div class="sidebar-nav">
    <div class="sidebar-nav-item"><div class="sidebar-nav-icon">🖼️</div>Изображения</div>
    <div class="sidebar-nav-item"><div class="sidebar-nav-icon">📁</div>Проекты</div>
  </div>
  <div class="sidebar-section">Недавнее</div>
  <div class="sidebar-chats" id="sidebarChats"></div>
</div>

<div class="header">
  <div class="header-left">
    <button class="hdr-btn" onclick="toggleSidebar()">
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="3" y1="6" x2="21" y2="6"/><line x1="3" y1="12" x2="21" y2="12"/><line x1="3" y1="18" x2="21" y2="18"/></svg>
    </button>
  </div>
  <div class="header-title" onclick="toggleModelPicker()">
    <span>АрбузAI</span>
    <div class="model-badge" id="modelBadge">Claude Haiku 4.5 ▾</div>
  </div>
  <div class="header-right">
    <button class="hdr-btn" onclick="newChat()">
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 20h9"/><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"/></svg>
    </button>
  </div>
</div>

<div class="messages" id="messages"></div>

<div class="input-area">
  <div class="file-preview" id="filePreview"></div>
  <div class="input-box">
    <button class="attach-btn" style="background:none; border:none; color:var(--text2); padding: 4px; cursor:pointer;" onclick="document.getElementById('file-input').click()">
      <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="m21.44 11.05-9.19 9.19a6 6 0 0 1-8.49-8.49l8.57-8.57A4 4 0 1 1 18 8.84l-8.59 8.57a2 2 0 0 1-2.83-2.83l8.49-8.48"/></svg>
    </button>
    <input type="file" id="file-input" style="display:none;" accept="image/*,.pdf,.docx,.doc,.txt" multiple onchange="handleFiles(this)">
    <textarea class="text-input" id="textInput" placeholder="Спросить АрбузAI" rows="1" onkeydown="handleKey(event)" oninput="autoResize(this)"></textarea>
    <button class="send-btn" id="sendBtn" onclick="sendMessage()" disabled>
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><line x1="12" y1="19" x2="12" y2="5"/><polyline points="5 12 12 5 19 12"/></svg>
    </button>
  </div>
</div>

<div class="model-picker" id="modelPicker">
  <div class="model-picker-overlay" onclick="toggleModelPicker()"></div>
  <div class="model-picker-sheet">
    <div class="sheet-handle"></div>
    <div class="sheet-title">Выбор модели</div>

    <div class="model-group">
      <div class="model-group-header" onclick="toggleGroup('gpt')">
        <div class="model-group-icon gpt">🍉</div>
        <span class="model-group-name">ChatGPT</span>
        <svg class="model-group-chevron" id="chevron-gpt" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="m9 18 6-6-6-6"/></svg>
      </div>
      <div class="model-group-items" id="group-gpt">
        <div class="model-item" data-model="gpt-5.5" onclick="selectModel(this)">
          <div class="model-item-left"><span class="model-item-name">GPT-5.5</span></div>
          <svg class="model-check" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg>
        </div>
        <div class="model-item" data-model="gpt-5.4-mini" onclick="selectModel(this)">
          <div class="model-item-left"><span class="model-item-name">GPT-5.4 Mini</span></div>
          <svg class="model-check" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg>
        </div>
      </div>
    </div>

    <div class="model-group">
      <div class="model-group-header" onclick="toggleGroup('claude')">
        <div class="model-group-icon claude">🔮</div>
        <span class="model-group-name">Claude</span>
        <svg class="model-group-chevron" id="chevron-claude" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="m9 18 6-6-6-6"/></svg>
      </div>
      <div class="model-group-items" id="group-claude">
        <div class="model-item" data-model="claude-sonnet-4-5" onclick="selectModel(this)">
          <div class="model-item-left"><span class="model-item-name">Claude Sonnet 4.5</span></div>
          <svg class="model-check" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg>
        </div>
        <div class="model-item selected" data-model="claude-haiku-4-5" onclick="selectModel(this)">
          <div class="model-item-left"><span class="model-item-name">Claude Haiku 4.5</span></div>
          <svg class="model-check" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg>
        </div>
      </div>
    </div>
  </div>
</div>

<div class="toast" id="toast"></div>

<script src="https://cdnjs.cloudflare.com/ajax/libs/mammoth/1.6.0/mammoth.browser.min.js"></script>
<script>
const API_BASE = "https://ai-proxy.izisoft.xyz/v1";
const IMAGE_API = "https://ai-proxy.izisoft.xyz/v1/image/generation";
let API_KEY = localStorage.getItem("api_key") || "";
let currentModel = localStorage.getItem("selected_model") || "claude-haiku-4-5";
let currentChatId = null;
let chats = {};
let attachedFiles = [];
let isLoading = false;

const MODEL_NAMES = {
  "gpt-5.5": "GPT-5.5",
  "gpt-5.4-mini": "GPT-5.4 Mini",
  "claude-sonnet-4-5": "Claude Sonnet 4.5",
  "claude-haiku-4-5": "Claude Haiku 4.5"
};

const VISION_MODELS = new Set(["gpt-5.5","gpt-5.4-mini","claude-sonnet-4-5","claude-haiku-4-5"]);

function triggerHaptic() {
  window.Telegram?.WebApp?.HapticFeedback?.impactOccurred('light');
}

window.Telegram?.WebApp?.ready();
window.Telegram?.WebApp?.expand();

// УМНОЕ АВТООПРЕДЕЛЕНИЕ ЗАПРОСА (ИНТЕНТ-АНАЛИЗАТОР)
function detectIntent(text, hasImage) {
  const t = text.toLowerCase();
  
  // Ключевые слова для создания картинок с нуля
  const generateImageWords = ["создай картинку", "сгенерируй", "нарисуй", "создай фото", "нарисуй картинку", "рисунок"];
  // Ключевые слова для редактирования имеющихся фото
  const editImageWords = ["измени", "отредактируй", "замени", "удали с фото", "поменяй", "исправь на фото"];
  // Ключевые слова, которые явно указывают на текстовое задание по фото (Vision-анализ)
  const visionTaskWords = ["напиши", "объясни", "реши", "прочитай", "переведи", "что на", "опиши"];

  // 1. Если пользователь хочет просто сгенерировать картинку текстом
  if (generateImageWords.some(w => t.includes(w))) {
    return "image";
  }
  
  // 2. Если прикреплено фото
  if (hasImage) {
    // Если есть слова-триггеры на редактирование фото -> модель для картинок
    if (editImageWords.some(w => t.includes(w))) {
      return "image";
    }
    // Если просят сделать текстовое описание/задание по фото -> текстовая модель с Vision
    if (visionTaskWords.some(w => t.includes(w))) {
      return "chat";
    }
    // По умолчанию, если к фото нет конкретного текста или текст нейтральный, считаем это заданием по анализу фото
    return "chat";
  }

  // 3. Во всех остальных случаях (просто текст, код, документы) -> текстовая модель
  return "chat";
}

function loadData() {
  const saved = localStorage.getItem("arbuz_chats");
  if (saved) chats = JSON.parse(saved);
  updateModelBadge();
  newChat();
}

function saveData() { localStorage.setItem("arbuz_chats", JSON.stringify(chats)); }

function newChat() {
  currentChatId = Date.now().toString();
  chats[currentChatId] = { title: "Новый чат", messages: [], created: Date.now() };
  saveData();
  renderMessages();
  updateSendBtn();
}

function toggleSidebar() {
  triggerHaptic();
  document.getElementById("sidebar").classList.toggle("open");
  document.getElementById("sidebarOverlay").classList.toggle("open");
  renderSidebarChats();
}

function toggleModelPicker() {
  triggerHaptic();
  document.getElementById("modelPicker").classList.toggle("open");
}

function selectModel(el) {
  triggerHaptic();
  document.querySelectorAll(".model-item").forEach(i => i.classList.remove("selected"));
  el.classList.add("selected");
  currentModel = el.dataset.model;
  localStorage.setItem("selected_model", currentModel);
  updateModelBadge();
  setTimeout(toggleModelPicker, 200);
}

function updateModelBadge() {
  document.getElementById("modelBadge").textContent = (MODEL_NAMES[currentModel] || currentModel) + " ▾";
  document.querySelectorAll(".model-item").forEach(el => {
    el.classList.toggle("selected", el.dataset.model === currentModel);
  });
}

function toggleGroup(name) {
  triggerHaptic();
  const items = document.getElementById("group-"+name);
  const chevron = document.getElementById("chevron-"+name);
  items.classList.toggle("open");
  chevron.classList.toggle("open");
}

function renderSidebarChats() {
  const c = document.getElementById("sidebarChats");
  const ids = Object.keys(chats).sort((a,b)=>b-a);
  if (!ids.length) { c.innerHTML='<div style="padding:20px;text-align:center;color:var(--text3);font-size:14px;">Нет чатов</div>'; return; }
  c.innerHTML = ids.map(id => {
    const chat = chats[id];
    return `<div class="chat-item ${id===currentChatId?'active':''}" onclick="loadChat('${id}')">
      <span class="chat-item-title">${escapeHtml(chat.title)}</span>
      <button class="chat-item-delete" onclick="event.stopPropagation(); deleteChat('${id}')">✕</button>
    </div>`;
  }).join("");
}

function deleteChat(id) {
  triggerHaptic();
  delete chats[id];
  saveData();
  if (currentChatId === id) newChat();
  renderSidebarChats();
}

function loadChat(id) {
  currentChatId = id;
  renderMessages();
  toggleSidebar();
}

function renderMessages() {
  const c = document.getElementById("messages");
  const msgs = (currentChatId && chats[currentChatId]) ? chats[currentChatId].messages : [];
  if (!msgs.length) {
    c.innerHTML = `<div class="empty-state">
      <div class="empty-logo">🍉</div>
      <div class="quick-actions">
        <div class="quick-action" onclick="setInput('Создай картинку ')">
          <div>🎨 Создать изображение</div>
        </div>
        <div class="quick-action" onclick="setInput('')">
          <div>✏️ Написать текст или код</div>
        </div>
      </div>
    </div>`;
    return;
  }

  c.innerHTML = msgs.map((msg,i) => {
    if (msg.role==="user") return `<div class="msg user">
      <div class="msg-bubble">${msg.content ? escapeHtml(msg.content) : ""}</div>
    </div>`;

    return `<div class="msg ai">
      ${msg.imageUrl ? `<div class="msg-image-wrap"><img src="${msg.imageUrl}"></div>` : ""}
      ${msg.content ? `<div class="msg-bubble">${formatText(msg.content)}</div>` : ""}
      <div class="msg-actions">
         <button class="msg-action" onclick="copyText(this, \`${escapeJsString(msg.content || '')}\`)">Копировать</button>
      </div>
    </div>`;
  }).join("");

  c.scrollTop = c.scrollHeight;
}

function escapeJsString(str) {
  return str.replace(/\\/g, '\\\\').replace(/`/g, '\\`').replace(/\$/g, '\\$');
}

function copyText(btn, text) {
  triggerHaptic();
  navigator.clipboard.writeText(text).then(() => {
    const old = btn.textContent;
    btn.textContent = "Скопировано!";
    setTimeout(() => btn.textContent = old, 2000);
  });
}

async function handleFiles(input) {
  triggerHaptic();
  const files = Array.from(input.files);
  for (const file of files) {
    if (attachedFiles.length >= 5) { showToast("Максимум 5 файлов"); break; }
    const fileData = { name: file.name, type: file.type, size: file.size, rawFile: file };
    if (file.type.startsWith("image/")) {
      fileData.base64 = await fileToBase64(file);
    } else if (file.name.endsWith(".docx")) {
      fileData.text = await parseDocx(file);
    } else if (file.type === "text/plain" || file.name.endsWith(".txt")) {
      fileData.text = await file.text();
    } else if (file.name.endsWith(".pdf")) {
      fileData.text = `[Содержимое PDF: ${file.name}]`;
    }
    attachedFiles.push(fileData);
  }
  renderFilePreview();
  updateSendBtn();
  input.value = "";
}

function fileToBase64(file) {
  return new Promise((res, rej) => {
    const r = new FileReader(); r.onload = () => res(r.result); r.onerror = rej; r.readAsDataURL(file);
  });
}

function parseDocx(file) {
  return new Promise((res) => {
    const r = new FileReader();
    r.onload = (e) => {
      mammoth.extractRawText({ arrayBuffer: e.target.result })
        .then(resObj => res(resObj.value))
        .catch(() => res("[Ошибка чтения docx]"));
    };
    r.readAsArrayBuffer(file);
  });
}

function renderFilePreview() {
  const p = document.getElementById("filePreview");
  p.innerHTML = attachedFiles.map((f, i) => `
    <div class="preview-item">
      ${f.base64 ? `<img src="${f.base64}">` : `<div class="doc-icon">${f.name.split('.').pop().toUpperCase()}</div>`}
      <button class="remove-btn" onclick="removeFile(${i})">✕</button>
    </div>
  `).join("");
}

function removeFile(i) {
  triggerHaptic();
  attachedFiles.splice(i, 1);
  renderFilePreview();
  updateSendBtn();
}

async function sendMessage() {
  if (isLoading) return;
  const input = document.getElementById("textInput");
  const text = input.value.trim();
  if (!text && !attachedFiles.length) return;

  triggerHaptic();
  isLoading = true;
  input.value = ""; autoResize(input);

  if (!currentChatId) newChat();
  
  let displayContent = text;
  if (attachedFiles.length) {
    const docs = attachedFiles.filter(f => !f.base64).map(f => `[Файл: ${f.name}]\n${f.text || ''}`).join("\n");
    if (docs) displayContent += (text ? "\n\n" : "") + docs;
  }

  chats[currentChatId].messages.push({ role: "user", content: displayContent, time: Date.now() });
  if (chats[currentChatId].messages.filter(m=>m.role==="user").length === 1) {
    chats[currentChatId].title = text ? text.substring(0,30) : attachedFiles[0].name;
  }

  const currentFiles = [...attachedFiles];
  attachedFiles = []; renderFilePreview(); updateSendBtn();
  renderMessages();
  showTyping();

  // Использование умного детектора интентов
  const hasImage = currentFiles.some(f => f.base64);
  const intent = detectIntent(text, hasImage);

  try {
    if (intent === "image") {
      await handleImageGeneration(text || "Отредактируй изображение по смыслу");
    } else {
      await handleChatCompletion(text, currentFiles);
    }
  } catch (err) {
    chats[currentChatId].messages.push({ role: "assistant", content: "Ошибка соединения с прокси: " + err.message, time: Date.now() });
    saveData(); renderMessages();
  } finally {
    hideTyping(); isLoading = false; updateSendBtn();
  }
}

async function handleChatCompletion(text, files) {
  const hasImage = files.some(f => f.base64);
  const reqMessages = [];

  chats[currentChatId].messages.forEach(m => {
    if (m.role === "user") {
      reqMessages.push({ role: "user", content: m.content });
    } else if (m.role === "assistant" && !m.imageUrl) {
      reqMessages.push({ role: "assistant", content: m.content });
    }
  });

  if (hasImage && VISION_MODELS.has(currentModel)) {
    const lastMsg = reqMessages[reqMessages.length - 1];
    const contentArr = [];
    if (text) contentArr.push({ type: "text", text: text });
    files.forEach(f => {
      if (f.base64) {
        const b64Data = f.base64.split(",")[1];
        contentArr.push({ type: "image_url", image_url: { url: `data:${f.type};base64,${b64Data}` } });
      }
    });
    lastMsg.content = contentArr;
  }

  const res = await fetch(`${API_BASE}/chat/completions`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "Authorization": `Bearer ${API_KEY}` },
    body: JSON.stringify({ model: currentModel, messages: reqMessages })
  });
  
  if (!res.ok) throw new Error(res.status);
  const data = await res.json();
  const reply = data.choices?.[0]?.message?.content || "[Пустой ответ]";
  chats[currentChatId].messages.push({ role: "assistant", content: reply, time: Date.now() });
  saveData(); renderMessages();
}

async function handleImageGeneration(text) {
  const res = await fetch(IMAGE_API, {
    method: "POST",
    headers: { "Content-Type": "application/json", "Authorization": `Bearer ${API_KEY}` },
    body: JSON.stringify({ prompt: text, model: "dall-e-3" })
  });
  if (!res.ok) throw new Error(res.status);
  const data = await res.json();
  const b64 = data.data?.[0]?.b64_json;
  if (!b64) throw new Error("Нет данных изображения");
  
  chats[currentChatId].messages.push({
    role: "assistant",
    content: "Изображение обработано/сгенерировано автоматически.",
    imageUrl: `data:image/png;base64,${b64}`,
    time: Date.now()
  });
  saveData(); renderMessages();
}

let typingEl=null;
function showTyping() {
  typingEl=document.createElement("div"); typingEl.className="msg ai";
  typingEl.innerHTML=`<div class="typing"><span></span><span></span><span></span></div>`;
  document.getElementById("messages").appendChild(typingEl);
  document.getElementById("messages").scrollTop=99999;
}
function hideTyping() { if(typingEl){typingEl.remove();typingEl=null;} }

function setInput(text) { triggerHaptic(); const el=document.getElementById("textInput"); el.value=text; el.focus(); autoResize(el); updateSendBtn(); }
function handleKey(e) { if(e.key==="Enter"&&!e.shiftKey){e.preventDefault();sendMessage();} }
function autoResize(el) { el.style.height="auto"; el.style.height=Math.min(el.scrollHeight,140)+"px"; }
function updateSendBtn() {
  const t=document.getElementById("textInput").value.trim();
  document.getElementById("sendBtn").disabled=isLoading || (!t && !attachedFiles.length);
}
function showToast(msg) {
  const t=document.getElementById("toast"); t.textContent=msg; t.classList.add("show");
  setTimeout(()=>t.classList.remove("show"),2000);
}
function escapeHtml(t) { return (t||"").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;"); }
function formatText(t) {
  return escapeHtml(t)
    .replace(/```[\w]*\n?([\s\S]*?)```/g,'<pre><code>$1</code></pre>')
    .replace(/`([^`]+)`/g,'<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g,'<strong>$1</strong>')
    .replace(/\n/g,'<br>');
}

loadData();
document.getElementById("textInput").addEventListener("input",updateSendBtn);
toggleGroup('claude');
</script>
</body>
</html>
