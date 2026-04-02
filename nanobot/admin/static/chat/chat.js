"use strict";

const POLL_INTERVAL_MS = 2000;
const MAX_ATTACHMENT_BYTES = 15 * 1024 * 1024;
const MESSAGE_FOLLOW_THRESHOLD_PX = 96;
const SIDEBAR_STORAGE_KEY = "softnix.web-chat.sidebarCollapsed";
let pollTimer = null;
let loginTimer = null;
let loginExpiryTimer = null;
let loginState = null;
let toastTimer = null;
let selectedAttachments = [];
let isSending = false;
let isVoiceRecording = false;
let isVoiceTranscribing = false;
let voiceRecorder = null;
let voiceStream = null;
let voiceChunks = [];
let unreadReplyCount = 0;
let shouldAutoFollowLatest = true;
let isSidebarCollapsed = false;
let typingIndicatorTimer = null;
let activeAudioPlayer = null;
let activeLightboxTrigger = null;
const baseDocumentTitle = document.title;

const state = {
  device: null,
  session: null,
  conversations: {},
  activeSessionId: null,
  lastEventId: null,
};

const $ = (id) => document.getElementById(id);

document.addEventListener("DOMContentLoaded", () => {
  bindEvents();
  void init();
});

function bindEvents() {
  $("btn-copy-code")?.addEventListener("click", () => {
    if (!loginState?.login_ticket) return;
    void navigator.clipboard?.writeText(loginState.login_ticket);
    showToast("Login code copied.");
  });
  $("btn-refresh-login")?.addEventListener("click", () => void beginLogin());
  $("btn-logout")?.addEventListener("click", () => void logoutWebChat());
  $("btn-new-chat")?.addEventListener("click", () => startNewConversation());
  $("btn-attach")?.addEventListener("click", handleAttachClick);
  $("btn-voice")?.addEventListener("click", () => void handleVoiceButton());
  $("btn-toggle-sidebar")?.addEventListener("click", () => toggleSidebar());
  $("btn-latest")?.addEventListener("click", () => scrollMessagesToLatest({ smooth: true }));
  $("reply-banner-action")?.addEventListener("click", () => scrollMessagesToLatest({ smooth: true }));
  $("image-lightbox-close")?.addEventListener("click", closeImageLightbox);
  $("image-lightbox-backdrop")?.addEventListener("click", closeImageLightbox);
  $("attachment-input")?.addEventListener("change", (event) => void handleAttachmentInput(event));
  $("composer")?.addEventListener("submit", (event) => {
    event.preventDefault();
    void sendMessage();
  });
  $("compose-input")?.addEventListener("input", (event) => {
    autoResizeInput(event.target);
    updateSendButton();
  });
  $("compose-input")?.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void sendMessage();
    }
  });
  $("messages")?.addEventListener("scroll", handleMessagesScroll, { passive: true });
  document.addEventListener("keydown", handleGlobalKeydown);
}

function handleGlobalKeydown(event) {
  if (event.key === "Escape" && !$("image-lightbox")?.classList.contains("is-hidden")) {
    event.preventDefault();
    closeImageLightbox();
  }
}

async function init() {
  try {
    if (await tryBootstrap()) {
      return;
    }
  } catch (error) {
    showToast(error?.message || "Unable to initialize web chat.", true);
  }
  await beginLogin();
}

async function tryBootstrap() {
  const response = await fetch("/admin/web-chat/bootstrap", { credentials: "same-origin" });
  if (response.status === 401) {
    return false;
  }
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    showToast(payload.error || "Unable to load web chat.", true);
    return false;
  }
  applyBootstrap(payload);
  return true;
}

async function beginLogin() {
  stopPolling();
  clearLoginTimers();
  stopActiveAudioPlayback();
  setLoginMode(true);
  $("login-status").textContent = "Preparing secure login…";
  $("login-expiry").textContent = "";
  $("manual-code").textContent = "";
  try {
    const response = await fetch("/admin/web-chat/login/init", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
      credentials: "same-origin",
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(payload.error || `Unable to initialize login (${response.status})`);
    }
    loginState = payload;
    $("manual-code").textContent = payload.login_ticket || "";
    $("login-status").textContent = "Scan with Softnix Mobile to sign in.";
    await renderLoginQR(payload);
    startLoginStatusPolling();
    startLoginCountdown();
  } catch (error) {
    $("login-status").textContent = error.message || "Unable to initialize login.";
  }
}

function clearLoginTimers() {
  if (loginTimer) window.clearInterval(loginTimer);
  if (loginExpiryTimer) window.clearInterval(loginExpiryTimer);
  loginTimer = null;
  loginExpiryTimer = null;
}

function setLoginMode(isLoginMode) {
  $("login-shell")?.classList.toggle("is-hidden", !isLoginMode);
  $("chat-shell")?.classList.toggle("is-hidden", isLoginMode);
}

function readSidebarPreference() {
  try {
    return window.localStorage?.getItem(SIDEBAR_STORAGE_KEY) === "1";
  } catch (_) {
    return false;
  }
}

function setSidebarCollapsed(isCollapsed) {
  isSidebarCollapsed = !!isCollapsed;
  const shell = $("chat-shell");
  if (shell) {
    shell.classList.toggle("is-sidebar-collapsed", isSidebarCollapsed);
  }
  updateSidebarToggle();
}

function toggleSidebar() {
  setSidebarCollapsed(!isSidebarCollapsed);
  try {
    window.localStorage?.setItem(SIDEBAR_STORAGE_KEY, isSidebarCollapsed ? "1" : "0");
  } catch (_) {
    // Ignore storage errors.
  }
}

function updateSidebarToggle() {
  const button = $("btn-toggle-sidebar");
  if (!button) return;
  button.setAttribute("aria-expanded", String(!isSidebarCollapsed));
  button.title = isSidebarCollapsed ? "Show sidebar" : "Hide sidebar";
  button.setAttribute("aria-label", isSidebarCollapsed ? "Show sidebar" : "Hide sidebar");
}

async function renderLoginQR(payload) {
  const container = $("qr-container");
  if (!container) return;
  const qrValue = payload?.qr_payload || payload?.login_ticket || "";
  await loadQRCodeLibrary();
  if (window.QRCode && window.QRCode.CorrectLevel) {
    container.innerHTML = "";
    new window.QRCode(container, {
      text: qrValue,
      width: 224,
      height: 224,
      colorDark: "#102332",
      colorLight: "#ffffff",
      correctLevel: window.QRCode.CorrectLevel.M,
    });
    return;
  }
  container.innerHTML = "";
  const img = document.createElement("img");
  img.alt = "Web Chat QR";
  img.width = 224;
  img.height = 224;
  img.src = `https://api.qrserver.com/v1/create-qr-code/?size=224x224&data=${encodeURIComponent(qrValue)}`;
  container.appendChild(img);
}

function loadQRCodeLibrary() {
  return new Promise((resolve) => {
    if (window.QRCode && window.QRCode.CorrectLevel) {
      resolve();
      return;
    }
    const existing = document.querySelector('script[data-qrcode-lib="1"]');
    if (existing) {
      existing.addEventListener("load", () => resolve(), { once: true });
      existing.addEventListener("error", () => resolve(), { once: true });
      return;
    }
    const script = document.createElement("script");
    script.src = "https://cdnjs.cloudflare.com/ajax/libs/qrcodejs/1.0.0/qrcode.min.js";
    script.dataset.qrcodeLib = "1";
    script.onload = () => resolve();
    script.onerror = () => resolve();
    document.head.appendChild(script);
  });
}

function startLoginStatusPolling() {
  if (loginTimer) window.clearInterval(loginTimer);
  loginTimer = window.setInterval(() => void pollLoginStatus(), 1500);
  void pollLoginStatus();
}

function startLoginCountdown() {
  if (loginExpiryTimer) window.clearInterval(loginExpiryTimer);
  const tick = () => {
    if (!loginState?.expires_at) return;
    const seconds = Math.max(0, Math.round((new Date(loginState.expires_at).getTime() - Date.now()) / 1000));
    const minutes = Math.floor(seconds / 60);
    const remainder = seconds % 60;
    $("login-expiry").textContent = seconds > 0
      ? `Expires in ${minutes}:${String(remainder).padStart(2, "0")}`
      : "Expired. Refresh to generate a new QR code.";
  };
  tick();
  loginExpiryTimer = window.setInterval(tick, 1000);
}

async function pollLoginStatus() {
  if (!loginState?.login_ticket) return;
  try {
    const response = await fetch(`/admin/web-chat/login/status?login_ticket=${encodeURIComponent(loginState.login_ticket)}`, {
      credentials: "same-origin",
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) return;
    if (payload.status === "approved") {
      $("login-status").textContent = `Approved by ${payload.device_label || payload.device_id || "your mobile device"}. Signing in…`;
      clearLoginTimers();
      try {
        await exchangeLoginTicket(payload.login_ticket || loginState.login_ticket);
      } catch (error) {
        showToast(error?.message || "Unable to complete sign-in.", true);
        await beginLogin();
      }
      return;
    }
    if (payload.status === "expired") {
      $("login-status").textContent = "QR code expired. Generate a new one.";
      clearLoginTimers();
    }
  } catch (_) {
    // Retry on next interval.
  }
}

async function exchangeLoginTicket(loginTicket) {
  const response = await fetch("/admin/web-chat/login/exchange", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ login_ticket: loginTicket }),
    credentials: "same-origin",
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.error || `Unable to exchange login (${response.status})`);
  }
  await tryBootstrap();
}

function applyBootstrap(payload) {
  clearLoginTimers();
  showTypingIndicator(false);
  unreadReplyCount = 0;
  shouldAutoFollowLatest = true;
  const shared = window.SoftnixChatShared;
  setSidebarCollapsed(readSidebarPreference());
  state.device = payload.device || null;
  state.session = payload.session || null;
  const built = shared?.buildConversationState
    ? shared.buildConversationState(payload.events || [], state.device?.device_id || "")
    : { conversations: {}, lastEventId: null };
  state.conversations = built.conversations || {};
  state.lastEventId = built.lastEventId || null;
  state.activeSessionId = shared?.pickActiveSessionId
    ? shared.pickActiveSessionId(
        state.conversations,
        payload.active_session_id || state.session?.active_session_id || null,
        state.device?.device_id || "",
      )
    : payload.active_session_id || `mobile-${state.device?.device_id || "unknown"}`;
  setLoginMode(false);
  $("instance-label").textContent = state.device?.instance_id || "Softnix Web Chat";
  $("device-label").textContent = state.device?.label || state.device?.device_id || "Softnix Mobile";
  updateSidebarToggle();
  renderConversationList();
  renderActiveConversation();
  renderComposerMeta();
  autoResizeInput($("compose-input"));
  updateSendButton();
  startPolling();
}

function startPolling() {
  stopPolling();
  pollTimer = window.setInterval(() => void pollEvents(), POLL_INTERVAL_MS);
}

function stopPolling() {
  if (pollTimer) {
    window.clearInterval(pollTimer);
    pollTimer = null;
  }
}

async function pollEvents() {
  if (!state.device) return;
  const messagesContainer = $("messages");
  const followLatest = shouldAutoFollowLatest || isMessagesNearBottom(messagesContainer);
  const preserveScrollTop = messagesContainer?.scrollTop || 0;
  const response = await fetch(
    `/admin/web-chat/events${state.lastEventId ? `?after_event_id=${encodeURIComponent(state.lastEventId)}` : ""}`,
    { credentials: "same-origin" },
  );
  if (response.status === 401) {
    await beginLogin();
    return;
  }
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) return;
  const events = Array.isArray(payload.events) ? payload.events : [];
  if (!events.length) return;
  let activeConversationTouched = false;
  let newReplyCount = 0;
  events.forEach((event) => {
    const message = window.SoftnixChatShared?.normalizeChatEvent
      ? window.SoftnixChatShared.normalizeChatEvent(event, state.device?.device_id || "")
      : null;
    if (!message) return;
    upsertMessage(message);
    if (message.sessionId === state.activeSessionId) {
      activeConversationTouched = true;
      if (isAssistantAnswerMessage(message)) {
        newReplyCount += 1;
      }
    }
    if (message.eventId) {
      state.lastEventId = message.eventId;
    }
  });
  renderConversationList();
  if (activeConversationTouched) {
    if (followLatest) {
      unreadReplyCount = 0;
    } else if (newReplyCount > 0) {
      unreadReplyCount += newReplyCount;
      showToast(`Softnix replied${newReplyCount > 1 ? ` (${newReplyCount} new replies)` : ""}. Jump to latest to read it.`);
    }
    renderActiveConversation({
      followLatest,
      preserveScrollTop,
    });
  } else {
    syncUnreadIndicators();
  }
}

function upsertMessage(message) {
  const sessionId = message.sessionId;
  const bucket = state.conversations[sessionId] || { messages: [], updatedAt: "" };
  const existingIndex = bucket.messages.findIndex((item) => item.messageId === message.messageId);
  if (existingIndex >= 0) {
    bucket.messages[existingIndex] = message;
  } else {
    bucket.messages.push(message);
  }
  bucket.messages.sort((a, b) => String(a.timestamp || "").localeCompare(String(b.timestamp || "")));
  bucket.updatedAt = String(bucket.messages[bucket.messages.length - 1]?.timestamp || bucket.updatedAt || "");
  state.conversations[sessionId] = bucket;
}

function renderConversationList() {
  const target = $("conversation-list");
  if (!target) return;
  const summaries = window.SoftnixChatShared?.summarizeConversations
    ? window.SoftnixChatShared.summarizeConversations(state.conversations)
    : [];
  target.innerHTML = "";
  summaries.forEach((item, index) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `conversation-item${item.session_id === state.activeSessionId ? " is-active" : ""}`;
    button.innerHTML = `
      <strong>${escapeHtml(item.title || (index === 0 ? "Latest Conversation" : `Conversation ${index + 1}`))}</strong>
      <p>${escapeHtml(item.preview || "No messages yet")}</p>
    `;
    button.addEventListener("click", () => {
      unreadReplyCount = 0;
      shouldAutoFollowLatest = true;
      state.activeSessionId = item.session_id;
      renderConversationList();
      renderActiveConversation();
      void persistActiveSession();
    });
    target.appendChild(button);
  });
}

function renderActiveConversation(options = {}) {
  const target = $("messages");
  if (!target) return;
  const conversation = state.conversations[state.activeSessionId] || { messages: [] };
  const followLatest = options.followLatest !== false;
  const preserveScrollTop = Number.isFinite(options.preserveScrollTop) ? options.preserveScrollTop : target.scrollTop;
  const playbackState = captureActiveAudioPlaybackState();
  if (activeAudioPlayer?.audio instanceof HTMLAudioElement) {
    activeAudioPlayer.audio.pause();
  }
  $("conversation-label").textContent = state.activeSessionId || "";
  activeAudioPlayer = null;
  target.innerHTML = "";
  const groupState = createProcessingGroupState(target);
  conversation.messages.forEach((message) => {
    appendConversationMessage(groupState, message);
  });
  finalizeProcessingGroup(groupState);
  window.requestAnimationFrame(() => {
    restoreActiveAudioPlaybackState(playbackState, target);
    if (followLatest) {
      unreadReplyCount = 0;
      shouldAutoFollowLatest = true;
      scrollMessagesToLatest({ smooth: false });
      return;
    }
    shouldAutoFollowLatest = false;
    target.scrollTop = Math.max(0, preserveScrollTop);
    syncUnreadIndicators();
  });
}

function appendConversationMessage(groupState, message) {
  if (isToolProgressMessage(message)) {
    appendProcessingStep(groupState, message);
    return;
  }
  if (String(message?.role || "") === "agent") {
    appendAgentAnswer(groupState, message);
    return;
  }
  finalizeProcessingGroup(groupState);
  groupState.container.appendChild(renderUserMessage(message));
}

function isToolProgressMessage(message) {
  if (window.SoftnixChatShared?.isToolProgressMessage) {
    return window.SoftnixChatShared.isToolProgressMessage(message);
  }
  return String(message?.role || "") === "agent"
    && (String(message?.msgType || "") === "tool" || String(message?.msgType || "") === "progress");
}

function isAssistantAnswerMessage(message) {
  return String(message?.role || "") === "agent" && !isToolProgressMessage(message);
}

function renderUserMessage(message) {
  const element = document.createElement("article");
  element.className = "message user";
  const text = String(message.text || "");
  if (text.trim()) {
    const body = document.createElement("div");
    body.className = "message-body";
    body.appendChild(renderRichText(text));
    element.appendChild(body);
  }
  const meta = document.createElement("div");
  meta.className = "message-meta";
  meta.textContent = formatTimestamp(message.timestamp);
  element.appendChild(meta);
  const attachments = normalizeAttachments(message.attachments);
  if (attachments.length) {
    element.insertBefore(renderAttachmentList(attachments), meta);
  }
  return element;
}

function renderAssistantMessage(message) {
  const element = document.createElement("article");
  element.className = "message agent assistant-entry";
  const text = String(message.text || "");
  const attachments = normalizeAttachments(message.attachments);
  const hasText = !!text.trim();
  const header = document.createElement("div");
  header.className = "assistant-entry__meta";
  const label = document.createElement("span");
  label.className = "assistant-entry__label";
  label.textContent = "Assistant";
  header.appendChild(label);
  if (hasText) {
    const copy = document.createElement("button");
    copy.type = "button";
    copy.className = "assistant-entry__copy";
    copy.textContent = "Copy";
    copy.addEventListener("click", async () => {
      try {
        await navigator.clipboard?.writeText(text);
        showToast("Assistant answer copied.");
      } catch (_) {
        showToast("Unable to copy answer.", true);
      }
    });
    header.appendChild(copy);
  }
  element.appendChild(header);
  if (text.trim()) {
    const body = document.createElement("div");
    body.className = "assistant-entry__content message-body";
    body.appendChild(renderRichText(text));
    element.appendChild(body);
  }
  if (attachments.length) {
    element.appendChild(renderAttachmentList(attachments));
  }
  const meta = document.createElement("div");
  meta.className = "message-meta";
  meta.textContent = formatTimestamp(message.timestamp);
  element.appendChild(meta);
  return element;
}

function renderRichText(text) {
  const fragment = document.createDocumentFragment();
  const normalized = String(text || "").replace(/\r\n/g, "\n").replace(/\r/g, "\n");
  const lines = normalized.split("\n");
  let paragraphLines = [];
  let quoteLines = [];
  let listItems = [];
  let listOrdered = false;
  let codeLines = [];
  let tableLines = [];
  let inCode = false;
  let inTable = false;

  const flushParagraph = () => {
    if (!paragraphLines.length) return;
    const paragraph = document.createElement("p");
    appendInlineTokens(paragraph, paragraphLines.join(" "));
    fragment.appendChild(paragraph);
    paragraphLines = [];
  };

  const flushQuote = () => {
    if (!quoteLines.length) return;
    const blockquote = document.createElement("blockquote");
    quoteLines.forEach((line) => {
      const quoteLine = document.createElement("p");
      appendInlineTokens(quoteLine, line);
      blockquote.appendChild(quoteLine);
    });
      fragment.appendChild(blockquote);
    quoteLines = [];
  };

  const flushList = () => {
    if (!listItems.length) return;
    const list = document.createElement(listOrdered ? "ol" : "ul");
    listItems.forEach((item) => {
      const li = document.createElement("li");
      appendInlineTokens(li, item);
      list.appendChild(li);
    });
    fragment.appendChild(list);
    listItems = [];
  };

  const flushCode = () => {
    if (!codeLines.length) return;
    fragment.appendChild(renderCodeBlock(codeLines.join("\n")));
    codeLines = [];
  };

  const flushTable = () => {
    if (!tableLines.length) return;
    const table = renderMarkdownTable(tableLines);
    if (table) {
      fragment.appendChild(table);
    }
    tableLines = [];
  };

  lines.forEach((line, index) => {
    const trimmed = line.trim();
    const nextTrimmed = String(lines[index + 1] || "").trim();
    if (trimmed.startsWith("```")) {
      if (inCode) {
        flushCode();
        inCode = false;
      } else {
        flushParagraph();
        flushQuote();
        flushList();
        flushTable();
        inTable = false;
        inCode = true;
      }
      return;
    }
    if (inCode) {
      codeLines.push(line);
      return;
    }
    if (inTable) {
      if (isMarkdownTableSeparatorLine(trimmed)) {
        return;
      }
      if (isMarkdownTableRowLine(line)) {
        tableLines.push(line);
        return;
      }
      flushTable();
      inTable = false;
    }
    if (isMarkdownTableRowLine(line) && isMarkdownTableSeparatorLine(nextTrimmed)) {
      flushParagraph();
      flushQuote();
      flushList();
      flushTable();
      tableLines = [line, lines[index + 1]];
      inTable = true;
      return;
    }
    const quoteMatch = line.match(/^\s*>\s?(.*)$/);
    if (quoteMatch) {
      flushParagraph();
      flushList();
      flushTable();
      inTable = false;
      quoteLines.push(quoteMatch[1]);
      return;
    }
    if (quoteLines.length) {
      flushQuote();
    }
    const listMatch = line.match(/^\s*(?:([-*+•])|(\d+\.))\s+(.*)$/);
    if (listMatch) {
      flushParagraph();
      flushTable();
      inTable = false;
      const ordered = !!listMatch[2];
      if (listItems.length && ordered !== listOrdered) {
        flushList();
      }
      listOrdered = ordered;
      listItems.push(listMatch[3]);
      return;
    }
    if (listItems.length) {
      flushList();
    }
    if (!trimmed) {
      flushParagraph();
      flushTable();
      inTable = false;
      return;
    }
    paragraphLines.push(line.trim());
  });

  flushParagraph();
  flushQuote();
  flushList();
  flushTable();
  flushCode();
  return fragment;
}

function isMarkdownTableRowLine(line) {
  const value = String(line || "");
  if (!value.includes("|")) return false;
  const trimmed = value.trim();
  if (!trimmed || !trimmed.includes("|")) return false;
  return /\|/.test(trimmed);
}

function isMarkdownTableSeparatorLine(line) {
  const trimmed = String(line || "").trim();
  if (!trimmed.includes("|")) return false;
  const cells = splitMarkdownTableCells(trimmed);
  if (cells.length < 2) return false;
  return cells.every((cell) => /^:?-{3,}:?$/.test(cell.replace(/\s+/g, "")));
}

function splitMarkdownTableCells(line) {
  const trimmed = String(line || "").trim();
  const withoutEdgePipes = trimmed.replace(/^\|/, "").replace(/\|$/, "");
  return withoutEdgePipes.split("|").map((cell) => cell.trim());
}

function renderMarkdownTable(tableLines) {
  const lines = Array.isArray(tableLines) ? tableLines.filter((line) => String(line || "").trim()) : [];
  if (lines.length < 2) return null;
  const headerCells = splitMarkdownTableCells(lines[0] || "");
  const separatorIndex = lines.findIndex((line, index) => index > 0 && isMarkdownTableSeparatorLine(line));
  if (separatorIndex < 1) return null;
  const bodyLines = lines.slice(separatorIndex + 1);
  const thead = document.createElement("thead");
  const headRow = document.createElement("tr");
  headerCells.forEach((cell) => {
    const th = document.createElement("th");
    appendInlineTokens(th, cell);
    headRow.appendChild(th);
  });
  thead.appendChild(headRow);

  const tbody = document.createElement("tbody");
  bodyLines.forEach((line) => {
    if (!isMarkdownTableRowLine(line)) return;
    const row = document.createElement("tr");
    splitMarkdownTableCells(line).forEach((cell) => {
      const td = document.createElement("td");
      appendInlineTokens(td, cell);
      row.appendChild(td);
    });
    tbody.appendChild(row);
  });

  const table = document.createElement("table");
  table.className = "message-table";
  table.appendChild(thead);
  table.appendChild(tbody);
  return table;
}

function renderCodeBlock(codeText) {
  const wrapper = document.createElement("div");
  wrapper.className = "code-block";

  const header = document.createElement("div");
  header.className = "code-block__header";

  const label = document.createElement("span");
  label.className = "code-block__label";
  label.textContent = "Code";
  header.appendChild(label);

  const copy = document.createElement("button");
  copy.type = "button";
  copy.className = "code-block__copy";
  copy.textContent = "Copy";
  copy.addEventListener("click", async () => {
    try {
      await navigator.clipboard?.writeText(String(codeText || ""));
      showToast("Code copied.");
    } catch (_) {
      showToast("Unable to copy code.", true);
    }
  });
  header.appendChild(copy);

  const pre = document.createElement("pre");
  pre.className = "code-block__pre";
  const code = document.createElement("code");
  code.textContent = codeText;
  pre.appendChild(code);

  wrapper.appendChild(header);
  wrapper.appendChild(pre);
  return wrapper;
}

function appendInlineTokens(parent, text) {
  const value = String(text || "");
  const tokenRe = /\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)|https?:\/\/[^\s<]+|`([^`]+)`|\*\*([^*]+)\*\*|\*([^*]+)\*/g;
  let lastIndex = 0;
  let match;
  while ((match = tokenRe.exec(value)) !== null) {
    if (match.index > lastIndex) {
      parent.appendChild(document.createTextNode(value.slice(lastIndex, match.index)));
    }
    if (match[1] && match[2]) {
      parent.appendChild(createLinkElement(match[2], match[1]));
    } else if (match[0].startsWith("http")) {
      const urlToken = trimTrailingUrlPunctuation(match[0]);
      parent.appendChild(createLinkElement(urlToken.value, urlToken.value));
      if (urlToken.trailing) {
        parent.appendChild(document.createTextNode(urlToken.trailing));
      }
    } else if (match[3]) {
      const code = document.createElement("code");
      code.textContent = match[3];
      parent.appendChild(code);
    } else if (match[4]) {
      const strong = document.createElement("strong");
      strong.textContent = match[4];
      parent.appendChild(strong);
    } else if (match[5]) {
      const em = document.createElement("em");
      em.textContent = match[5];
      parent.appendChild(em);
    }
    lastIndex = tokenRe.lastIndex;
  }
  if (lastIndex < value.length) {
    parent.appendChild(document.createTextNode(value.slice(lastIndex)));
  }
}

function createLinkElement(href, label) {
  const anchor = document.createElement("a");
  anchor.textContent = label;
  try {
    const url = new URL(href, window.location.origin);
    if (url.protocol === "http:" || url.protocol === "https:") {
      anchor.href = url.href;
      anchor.target = "_blank";
      anchor.rel = "noreferrer";
      return anchor;
    }
  } catch (_) {
    // Fall through to plain text.
  }
  anchor.removeAttribute("href");
  return anchor;
}

function trimTrailingUrlPunctuation(value) {
  const source = String(value || "");
  const trimmed = source.match(/^(.*?)([.,!?;:)\]]+)?$/);
  return {
    value: trimmed?.[1] || source,
    trailing: trimmed?.[2] || "",
  };
}

function createProcessingGroupState(container) {
  return {
    container,
    wrapper: null,
    panel: null,
    toggleLabel: null,
    spinner: null,
    content: null,
    answerSlot: null,
    toolMessages: [],
    isExpanded: false,
  };
}

function ensureProcessingGroup(groupState) {
  if (groupState.wrapper) return groupState;

  const wrapper = document.createElement("div");
  wrapper.className = "agent-group";

  const panel = document.createElement("div");
  panel.className = "tools-panel";

  const toggle = document.createElement("button");
  toggle.className = "tools-toggle";
  toggle.type = "button";
  toggle.innerHTML = `
    <svg class="tools-toggle-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
      <path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"></path>
    </svg>
    <span class="tools-toggle-label">Agent processing </span>
    <span class="tools-toggle-spinner is-visible" aria-hidden="true"></span>
    <svg class="tools-toggle-chevron" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
      <polyline points="6 9 12 15 18 9"></polyline>
    </svg>
  `;

  const toggleLabel = toggle.querySelector(".tools-toggle-label");
  const spinner = toggle.querySelector(".tools-toggle-spinner");

  toggle.addEventListener("click", () => {
    groupState.isExpanded = !groupState.isExpanded;
    panel.classList.toggle("is-expanded", groupState.isExpanded);
    if (spinner) {
      spinner.classList.remove("is-visible");
      spinner.style.display = "none";
    }
    updateProcessingGroupLabel(groupState);
  });

  const content = document.createElement("div");
  content.className = "tools-content";

  const answerSlot = document.createElement("div");
  answerSlot.className = "agent-group-answer";

  panel.appendChild(toggle);
  panel.appendChild(content);
  wrapper.appendChild(panel);
  wrapper.appendChild(answerSlot);
  groupState.container.appendChild(wrapper);

  groupState.wrapper = wrapper;
  groupState.panel = panel;
  groupState.toggleLabel = toggleLabel;
  groupState.spinner = spinner;
  groupState.content = content;
  groupState.answerSlot = answerSlot;
  return groupState;
}

function updateProcessingGroupLabel(groupState) {
  if (!groupState.toggleLabel) return;
  const count = groupState.toolMessages.length;
  groupState.toggleLabel.textContent = groupState.isExpanded
    ? `${count} tool step${count === 1 ? "" : "s"}`
    : `${count} tool step${count === 1 ? "" : "s"} (tap to view)`;
}

function appendProcessingStep(groupState, message) {
  showTypingIndicator(false);
  ensureProcessingGroup(groupState);
  const step = document.createElement("div");
  step.className = "tool-step";
  const text = String(message.text || "");
  const textBlock = document.createElement("div");
  textBlock.className = "tool-step-text";
  textBlock.appendChild(renderRichText(text));
  step.appendChild(textBlock);
  const time = document.createElement("span");
  time.className = "tool-step-time";
  time.textContent = formatTimestamp(message.timestamp);
  step.appendChild(time);
  groupState.content.appendChild(step);
  groupState.toolMessages.push(message);
  groupState.panel.classList.remove("is-expanded");
  updateProcessingGroupLabel(groupState);
}

function settleProcessingGroupSpinner(groupState) {
  const spinner = groupState?.spinner;
  if (!spinner) return;
  spinner.classList.remove("is-visible");
  spinner.style.display = "none";
  spinner.setAttribute("aria-hidden", "true");
}

function appendAgentAnswer(groupState, message) {
  showTypingIndicator(false);
  if (groupState.wrapper) {
    const answer = renderAssistantMessage(message);
    settleProcessingGroupSpinner(groupState);
    groupState.answerSlot.appendChild(answer);
    finalizeProcessingGroup(groupState);
    return;
  }
  groupState.container.appendChild(renderAssistantMessage(message));
}

function finalizeProcessingGroup(groupState) {
  if (!groupState.wrapper) return;
  if (groupState.answerSlot.childNodes.length > 0) {
    settleProcessingGroupSpinner(groupState);
  }
  if (groupState.toolMessages.length === 0 && groupState.answerSlot.childNodes.length === 0) {
    groupState.wrapper.remove();
  }
  groupState.wrapper = null;
  groupState.panel = null;
  groupState.toggleLabel = null;
  groupState.spinner = null;
  groupState.content = null;
  groupState.answerSlot = null;
  groupState.toolMessages = [];
  groupState.isExpanded = false;
}

function normalizeAttachments(items) {
  if (!Array.isArray(items)) return [];
  return items
    .filter((item) => item && typeof item === "object")
    .map((item) => {
      const mimeType = item?.mime_type || item?.mimeType || "application/octet-stream";
      const fileName = item?.file_name || item?.fileName || item?.name || "attachment";
      const senderId = item?.sender_id || item?.senderId || state.device?.device_id || "";
      let url = item?.url || item?.previewUrl || "";
      const looksLikeLocalPath = typeof url === "string" && url && !/^(https?:|blob:|data:|\/admin\/mobile\/media)/i.test(url);
      if ((looksLikeLocalPath || !url) && state.device?.instance_id && senderId) {
        url = `/admin/mobile/media?instance_id=${encodeURIComponent(state.device.instance_id)}&sender_id=${encodeURIComponent(senderId)}&file=${encodeURIComponent(fileName)}`;
      }
      return {
        name: item?.name || fileName,
        fileName,
        mimeType,
        size: Number(item?.size || 0),
        previewUrl: item?.previewUrl || url,
        url,
        senderId,
        duration: Number(item?.duration || item?.audio_duration || 0) || 0,
        kind: item?.kind || inferAttachmentKind(mimeType, fileName),
      };
    });
}

function renderAttachmentList(attachments) {
  const wrapper = document.createElement("div");
  wrapper.className = "attachment-list";
  attachments.forEach((item) => {
    if ((item.kind || "") === "audio") {
      wrapper.appendChild(createAudioAttachmentPlayer(item));
      return;
    }
    wrapper.appendChild(createAttachmentCard(item));
  });
  return wrapper;
}

function createAttachmentCard(attachment) {
  const mediaUrl = attachment.url || attachment.previewUrl || "";
  const isImage = attachment.kind === "image" && mediaUrl;
  const element = document.createElement(isImage ? "button" : mediaUrl ? "a" : "div");
  element.className = `attachment-card attachment-card--${attachment.kind || "file"}`;
  if (isImage) {
    element.type = "button";
    element.setAttribute("aria-label", `Open image ${attachment.name || "attachment"}`);
    element.addEventListener("click", (event) => {
      event.preventDefault();
      openImageLightbox(attachment, element);
    });
  } else if (mediaUrl) {
    element.href = mediaUrl;
    element.target = "_blank";
    element.rel = "noreferrer";
  }

  if (isImage) {
    const image = document.createElement("img");
    image.src = attachment.previewUrl || mediaUrl;
    image.alt = attachment.name || "Attachment";
    image.loading = "lazy";
    if (/logo/i.test(String(attachment.name || attachment.fileName || ""))) {
      image.classList.add("attachment-image--logo");
    }
    element.appendChild(image);
  } else {
    const icon = document.createElement("span");
    icon.className = `attachment-icon${attachment.kind === "audio" ? " attachment-icon--audio" : ""}`;
    icon.textContent = attachment.kind === "video" ? "Video" : "File";
    element.appendChild(icon);
  }

  const meta = document.createElement("span");
  meta.className = "attachment-meta";
  meta.innerHTML = `
    <strong>${escapeHtml(attachment.name || "Attachment")}</strong>
    <span>${escapeHtml(formatBytes(attachment.size || 0))}</span>
  `;
  element.appendChild(meta);
  return element;
}

function openImageLightbox(attachment, triggerElement) {
  const overlay = $("image-lightbox");
  const image = $("image-lightbox-image");
  const title = $("image-lightbox-title");
  const subtitle = $("image-lightbox-subtitle");
  const download = $("image-lightbox-download");
  const mediaUrl = attachment?.url || attachment?.previewUrl || "";
  if (!overlay || !(image instanceof HTMLImageElement) || !title || !subtitle || !(download instanceof HTMLAnchorElement) || !mediaUrl) {
    return;
  }
  activeLightboxTrigger = triggerElement instanceof HTMLElement ? triggerElement : null;
  image.src = attachment.previewUrl || mediaUrl;
  image.alt = attachment.name || "Expanded attachment preview";
  title.textContent = attachment.name || "Image preview";
  subtitle.textContent = formatBytes(attachment.size || 0);
  download.href = mediaUrl;
  download.download = attachment.fileName || attachment.name || "attachment";
  overlay.classList.remove("is-hidden");
  document.body.classList.add("is-lightbox-open");
  $("image-lightbox-close")?.focus();
}

function closeImageLightbox() {
  const overlay = $("image-lightbox");
  const image = $("image-lightbox-image");
  if (!overlay || overlay.classList.contains("is-hidden")) return;
  overlay.classList.add("is-hidden");
  document.body.classList.remove("is-lightbox-open");
  if (image instanceof HTMLImageElement) {
    image.removeAttribute("src");
  }
  if (activeLightboxTrigger instanceof HTMLElement) {
    activeLightboxTrigger.focus();
  }
  activeLightboxTrigger = null;
}

function createAudioAttachmentPlayer(attachment) {
  const card = document.createElement("div");
  card.className = "attachment-card attachment-card--audio attachment-audio-card";
  const src = attachment.url || attachment.previewUrl || "";
  const attachmentName = attachment.name || "Audio";
  const sourceKey = src || attachment.fileName || attachment.name || "";

  card.innerHTML = `
    <div class="attachment-audio-header">
      <span class="attachment-icon attachment-icon--audio">Audio</span>
      <div class="attachment-audio-copy">
        <strong>${escapeHtml(attachmentName)}</strong>
        <span>${escapeHtml(formatBytes(attachment.size || 0))}</span>
      </div>
    </div>
    <div class="attachment-audio-controls">
      <button type="button" class="attachment-audio-button attachment-audio-button--play" aria-label="Play audio">Play</button>
      <button type="button" class="attachment-audio-button attachment-audio-button--stop" aria-label="Stop audio">Stop</button>
      <span class="attachment-audio-time">0:00 / 0:00</span>
    </div>
    <div class="attachment-audio-progress" aria-hidden="true">
      <div class="attachment-audio-progress-bar"></div>
    </div>
    <audio preload="none" playsinline></audio>
  `;

  const audio = card.querySelector("audio");
  const playButton = card.querySelector(".attachment-audio-button--play");
  const stopButton = card.querySelector(".attachment-audio-button--stop");
  const timeLabel = card.querySelector(".attachment-audio-time");
  const progressBar = card.querySelector(".attachment-audio-progress-bar");
  if (!(audio instanceof HTMLAudioElement)
      || !(playButton instanceof HTMLButtonElement)
      || !(stopButton instanceof HTMLButtonElement)
      || !(timeLabel instanceof HTMLElement)
      || !(progressBar instanceof HTMLElement)) {
    return card;
  }
  audio.dataset.mediaSourceKey = sourceKey;
  audio.dataset.directUrl = src;

  let blobLoaded = false;
  let blobLoading = false;
  let blobUrl = "";
  let directSourceReady = false;
  let blobFallbackAttempted = false;

  const ensureDirectSource = () => {
    if (!src) return false;
    if (!directSourceReady || !audio.src) {
      audio.src = src;
      directSourceReady = true;
    }
    return true;
  };

  const resetAudioSource = () => {
    audio.pause();
    audio.removeAttribute("src");
    audio.load();
    directSourceReady = false;
  };

  const loadAudioBlob = async () => {
    if (blobLoaded || blobLoading || !src) return true;
    blobLoading = true;
    timeLabel.textContent = "Loading...";
    try {
      const response = await fetch(src);
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const blob = await response.blob();
      blobUrl = URL.createObjectURL(blob);
      audio.src = blobUrl;
      blobLoaded = true;
      await new Promise((resolve, reject) => {
        const onReady = () => { cleanup(); resolve(); };
        const onError = () => { cleanup(); reject(audio.error); };
        const cleanup = () => {
          audio.removeEventListener("canplay", onReady);
          audio.removeEventListener("loadedmetadata", onReady);
          audio.removeEventListener("error", onError);
        };
        audio.addEventListener("canplay", onReady, { once: true });
        audio.addEventListener("loadedmetadata", onReady, { once: true });
        audio.addEventListener("error", onError, { once: true });
        audio.load();
      });
      return true;
    } catch (_) {
      blobLoading = false;
      return false;
    } finally {
      blobLoading = false;
    }
  };

  const controller = {
    audio,
    sourceKey,
    sessionId: state.activeSessionId,
    stop(reset = true) {
      audio.pause();
      if (reset) {
        audio.currentTime = 0;
      }
      updateUI();
    },
  };

  const formatClock = (seconds) => {
    const value = Number.isFinite(seconds) && seconds > 0 ? seconds : 0;
    const mins = Math.floor(value / 60);
    const secs = Math.floor(value % 60);
    return `${mins}:${String(secs).padStart(2, "0")}`;
  };

  const updateUI = () => {
    const duration = Number.isFinite(audio.duration) ? audio.duration : Number(attachment.duration || 0);
    const current = Number.isFinite(audio.currentTime) ? audio.currentTime : 0;
    const ratio = duration > 0 ? Math.max(0, Math.min(1, current / duration)) : 0;
    const isPlaying = !audio.paused && !audio.ended;

    playButton.textContent = isPlaying ? "Pause" : "Play";
    playButton.setAttribute("aria-label", isPlaying ? "Pause audio" : "Play audio");
    stopButton.disabled = !isPlaying && current === 0;
    timeLabel.textContent = `${formatClock(current)} / ${formatClock(duration)}`;
    progressBar.style.width = `${Math.max(0, Math.min(100, ratio * 100))}%`;
    card.classList.toggle("is-playing", isPlaying);
  };

  const activate = async () => {
    if (activeAudioPlayer && activeAudioPlayer !== controller) {
      activeAudioPlayer.stop(true);
    }
    activeAudioPlayer = controller;
    try {
      if (!ensureDirectSource()) {
        timeLabel.textContent = "Unable to load audio";
        card.classList.add("is-error");
        return;
      }
      await audio.play();
    } catch (error) {
      if (!blobFallbackAttempted && !blobLoaded && src) {
        blobFallbackAttempted = true;
        resetAudioSource();
        const loaded = await loadAudioBlob();
        if (loaded) {
          try {
            await audio.play();
            return;
          } catch (blobError) {
            error = blobError;
          }
        }
      }
      showToast(`Unable to play audio: ${error?.message || "Playback failed"}`, true);
    }
  };

  playButton.addEventListener("click", () => {
    if (audio.paused) {
      void activate();
    } else {
      audio.pause();
      updateUI();
    }
  });

  stopButton.addEventListener("click", () => {
    controller.stop(true);
    if (activeAudioPlayer === controller) {
      activeAudioPlayer = null;
    }
  });

  audio.addEventListener("loadedmetadata", updateUI);
  audio.addEventListener("timeupdate", updateUI);
  audio.addEventListener("play", updateUI);
  audio.addEventListener("pause", () => {
    updateUI();
    if (audio.currentTime === 0 && activeAudioPlayer === controller) {
      activeAudioPlayer = null;
    }
  });
  audio.addEventListener("ended", () => {
    audio.currentTime = 0;
    updateUI();
    if (activeAudioPlayer === controller) {
      activeAudioPlayer = null;
    }
  });
  audio.addEventListener("error", () => {
    if (!blobLoaded && !blobLoading) {
      return;
    }
    playButton.disabled = true;
    stopButton.disabled = true;
    const code = audio.error ? audio.error.code : 0;
    const reasons = { 1: "aborted", 2: "network error", 3: "decode error", 4: "format not supported" };
    timeLabel.textContent = `Unable to load audio${reasons[code] ? ` (${reasons[code]})` : ""}`;
    card.classList.add("is-error");
    if (activeAudioPlayer === controller) {
      activeAudioPlayer = null;
    }
  });

  updateUI();
  return card;
}

function captureActiveAudioPlaybackState() {
  const controller = activeAudioPlayer;
  const audio = controller?.audio;
  if (!(audio instanceof HTMLAudioElement)) return null;
  const sourceKey = String(controller?.sourceKey || audio.dataset.mediaSourceKey || "").trim();
  if (!sourceKey) return null;
  return {
    sessionId: String(controller?.sessionId || state.activeSessionId || ""),
    sourceKey,
    directUrl: String(audio.dataset.directUrl || audio.currentSrc || audio.src || "").trim(),
    currentTime: Number.isFinite(audio.currentTime) ? audio.currentTime : 0,
    wasPlaying: !audio.paused && !audio.ended,
  };
}

function restoreActiveAudioPlaybackState(playbackState, container) {
  if (!playbackState || playbackState.sessionId !== state.activeSessionId) return;
  const candidates = Array.from(container.querySelectorAll("audio[data-media-source-key]"));
  const audio = candidates.find((item) => String(item.dataset.mediaSourceKey || "") === playbackState.sourceKey);
  if (!(audio instanceof HTMLAudioElement)) return;
  const directUrl = String(audio.dataset.directUrl || playbackState.directUrl || "").trim();
  if (!directUrl) return;
  if (!audio.src) {
    audio.src = directUrl;
  }
  const seekToSavedTime = () => {
    if (Number.isFinite(playbackState.currentTime) && playbackState.currentTime > 0) {
      try {
        audio.currentTime = playbackState.currentTime;
      } catch (_) {
        // Ignore seek failures before metadata is ready.
      }
    }
    if (playbackState.wasPlaying) {
      void audio.play().then(() => {
        activeAudioPlayer = {
          audio,
          sourceKey: playbackState.sourceKey,
          sessionId: playbackState.sessionId,
          stop(reset = true) {
            audio.pause();
            if (reset) {
              audio.currentTime = 0;
            }
          },
        };
      }).catch(() => {});
    }
  };
  if (audio.readyState >= 1) {
    seekToSavedTime();
    return;
  }
  const onReady = () => {
    audio.removeEventListener("loadedmetadata", onReady);
    seekToSavedTime();
  };
  audio.addEventListener("loadedmetadata", onReady, { once: true });
  audio.load();
}

function handleAttachClick() {
  if (!state.session || isSending) return;
  $("attachment-input")?.click();
}

async function handleAttachmentInput(event) {
  const input = event.target;
  const files = Array.from(input?.files || []);
  files.forEach((file) => {
    if (file.size > MAX_ATTACHMENT_BYTES) {
      showToast(`Attachment '${file.name}' is too large. Limit is ${formatBytes(MAX_ATTACHMENT_BYTES)}.`, true);
      return;
    }
    selectedAttachments.push({
      id: `att-${crypto.randomUUID()}`,
      file,
      previewUrl: inferAttachmentKind(file.type, file.name) === "image" ? URL.createObjectURL(file) : "",
    });
  });
  if (input) {
    input.value = "";
  }
  renderComposerMeta();
  updateSendButton();
}

async function handleVoiceButton() {
  if (!state.device || isSending) return;
  if (isVoiceTranscribing) return;
  if (isVoiceRecording) {
    stopVoiceRecording();
    return;
  }
  await startVoiceRecording();
}

async function startVoiceRecording() {
  if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
    showToast("Voice recording is not supported in this browser.", true);
    return;
  }
  if (!state.device) return;
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    const mimeType = pickVoiceRecorderMimeType();
    const recorder = mimeType ? new MediaRecorder(stream, { mimeType }) : new MediaRecorder(stream);
    voiceStream = stream;
    voiceRecorder = recorder;
    voiceChunks = [];
    isVoiceRecording = true;
    syncVoiceButtonState();
    updateSendButton();

    recorder.addEventListener("dataavailable", (event) => {
      if (event.data && event.data.size > 0) {
        voiceChunks.push(event.data);
      }
    });

    recorder.addEventListener("error", () => {
      stopVoiceRecording(true);
      showToast("Recording failed. Please try again.", true);
    });

    recorder.addEventListener("stop", () => {
      const streamToStop = voiceStream;
      voiceStream = null;
      voiceRecorder = null;
      isVoiceRecording = false;
      syncVoiceButtonState();
      updateSendButton();
      if (streamToStop) {
        streamToStop.getTracks().forEach((track) => track.stop());
      }
      const mime = recorder.mimeType || mimeType || "audio/webm";
      const blob = new Blob(voiceChunks, { type: mime });
      voiceChunks = [];
      void transcribeVoiceBlob(blob, mime);
    }, { once: true });

    recorder.start();
  } catch (error) {
    cleanupVoiceRecording();
    showToast(`Unable to access microphone: ${error?.message || "permission denied"}`, true);
  }
}

function stopVoiceRecording(silent = false) {
  if (!voiceRecorder) return;
  try {
    if (voiceRecorder.state !== "inactive") {
      voiceRecorder.stop();
    }
  } catch (_) {
    cleanupVoiceRecording();
    if (!silent) {
      showToast("Recording stopped unexpectedly.", true);
    }
  }
}

function cleanupVoiceRecording() {
  isVoiceRecording = false;
  voiceChunks = [];
  if (voiceStream) {
    voiceStream.getTracks().forEach((track) => track.stop());
  }
  voiceStream = null;
  voiceRecorder = null;
  syncVoiceButtonState();
  updateSendButton();
}

async function transcribeVoiceBlob(blob, mimeType) {
  if (!state.device || !blob || blob.size === 0) {
    return;
  }
  isVoiceTranscribing = true;
  syncVoiceButtonState();
  updateSendButton();
  try {
    const fileName = `voice-${Date.now()}${extensionForMimeType(mimeType)}`;
    const audio = {
      name: fileName,
      type: mimeType || blob.type || "audio/webm",
      size: blob.size,
      data_base64: await blobToBase64(blob),
    };
    const response = await fetch("/admin/web-chat/transcribe", {
      method: "POST",
      credentials: "same-origin",
      headers: {
        "Content-Type": "application/json",
        "X-CSRF-Token": state.session?.csrf_token || "",
      },
      body: JSON.stringify({
        audio,
      }),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      const err = new Error(payload.error || `Transcription failed (${response.status})`);
      err.code = payload.error_code || "";
      throw err;
    }
    const transcript = String(payload.transcript || "").trim();
    if (!transcript) {
      showToast("No speech detected.", true);
      return;
    }
    const input = $("compose-input");
    if (input) {
      const current = input.value.trim();
      input.value = current ? `${current}${current.endsWith(" ") ? "" : " "}${transcript}` : transcript;
      autoResizeInput(input);
      updateSendButton();
      input.focus();
    }
  } catch (error) {
    const errorText = String(error?.message || "Unknown error");
    if (error?.code === "groq_key_missing" || /Groq API key is not configured for transcription/i.test(errorText)) {
      showToast("Voice transcription is not configured for this instance. Ask an admin to set the Groq API key in Providers.", true);
    } else {
      showToast(`Unable to transcribe audio: ${errorText}`, true);
    }
  } finally {
    isVoiceTranscribing = false;
    syncVoiceButtonState();
    updateSendButton();
  }
}

function syncVoiceButtonState() {
  const button = $("btn-voice");
  if (!(button instanceof HTMLButtonElement)) return;
  button.disabled = isVoiceTranscribing || isSending || !state.device || !state.session?.csrf_token;
  button.classList.toggle("is-recording", isVoiceRecording);
  button.classList.toggle("is-transcribing", isVoiceTranscribing);
  button.setAttribute("aria-pressed", String(isVoiceRecording));
  button.setAttribute("aria-busy", String(isVoiceTranscribing));
  button.title = isVoiceTranscribing
    ? "Transcribing audio"
    : isVoiceRecording
      ? "Stop recording"
      : "Record voice";
  button.innerHTML = isVoiceRecording
    ? `
      <svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
        <rect x="7" y="7" width="10" height="10" rx="2"></rect>
      </svg>
    `
    : `
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true">
        <rect x="9" y="3" width="6" height="11" rx="3"></rect>
        <path d="M5 11a7 7 0 0 0 14 0"></path>
        <path d="M12 18v3"></path>
        <path d="M8 21h8"></path>
      </svg>
    `;
}

function pickVoiceRecorderMimeType() {
  const candidates = [
    "audio/mp4",
    "audio/mp4;codecs=mp4a.40.2",
    "audio/webm;codecs=opus",
    "audio/webm",
    "audio/ogg;codecs=opus",
  ];
  for (const candidate of candidates) {
    if (window.MediaRecorder?.isTypeSupported?.(candidate)) {
      return candidate;
    }
  }
  return "";
}

function extensionForMimeType(mimeType) {
  const normalized = String(mimeType || "").toLowerCase();
  if (normalized.includes("mp4") || normalized.includes("m4a")) return ".m4a";
  if (normalized.includes("webm")) return ".webm";
  if (normalized.includes("ogg")) return ".ogg";
  if (normalized.includes("wav")) return ".wav";
  if (normalized.includes("mpeg") || normalized.includes("mp3")) return ".mp3";
  return ".audio";
}

function blobToBase64(blob) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const result = String(reader.result || "");
      const marker = result.indexOf(",");
      resolve(marker >= 0 ? result.slice(marker + 1) : result);
    };
    reader.onerror = () => reject(reader.error || new Error("Failed to read audio"));
    reader.readAsDataURL(blob);
  });
}

async function sendMessage() {
  const input = $("compose-input");
  const rawText = String(input?.value || "");
  const text = rawText.trim();
  const pendingAttachments = selectedAttachments.map((item) => ({ ...item }));
  if ((!text && pendingAttachments.length === 0) || !state.session?.csrf_token || !state.device || isSending) return;
  const message = {
    role: "user",
    text,
    msgType: "message",
    messageId: `webu-${crypto.randomUUID()}`,
    sessionId: state.activeSessionId || `mobile-${state.device.device_id}`,
    senderId: state.device.device_id,
    replyTo: null,
    threadRootId: null,
    attachments: pendingAttachments.map((item) => ({
      name: item.file.name,
      fileName: item.file.name,
      mimeType: item.file.type || "application/octet-stream",
      size: item.file.size,
      kind: inferAttachmentKind(item.file.type, item.file.name),
      previewUrl: item.previewUrl || "",
      url: item.previewUrl || "",
    })),
    timestamp: new Date().toISOString(),
  };
  isSending = true;
  shouldAutoFollowLatest = true;
  upsertMessage(message);
  renderConversationList();
  renderActiveConversation({ followLatest: true });
  showTypingIndicator(true);
  if (input) {
    input.value = "";
    autoResizeInput(input);
  }
  await new Promise((resolve) => window.requestAnimationFrame(() => resolve()));
  selectedAttachments = [];
  renderComposerMeta();
  updateSendButton();
  try {
    const serializedAttachments = await serializeAttachments(pendingAttachments);
    const response = await fetch("/admin/web-chat/message", {
      method: "POST",
      credentials: "same-origin",
      headers: {
        "Content-Type": "application/json",
        "X-CSRF-Token": state.session.csrf_token,
      },
      body: JSON.stringify({
        text,
        message_id: message.messageId,
        session_id: message.sessionId,
        attachments: serializedAttachments,
      }),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(payload.error || `Unable to send message (${response.status})`);
    }
    upsertMessage({
      ...message,
      attachments: Array.isArray(payload.attachments) ? payload.attachments : message.attachments,
    });
    releaseSelectedAttachments(pendingAttachments);
    renderConversationList();
    renderActiveConversation({ followLatest: true });
    showTypingIndicator(true);
  } catch (error) {
    removeMessage(message.messageId);
    if (input) {
      input.value = rawText;
      autoResizeInput(input);
    }
    selectedAttachments = pendingAttachments;
    renderComposerMeta();
    renderConversationList();
    renderActiveConversation({ followLatest: true });
    showToast(error.message || "Unable to send message.", true);
  } finally {
    isSending = false;
    updateSendButton();
  }
}

function startNewConversation() {
  if (!state.device) return;
  stopActiveAudioPlayback();
  showTypingIndicator(false);
  unreadReplyCount = 0;
  shouldAutoFollowLatest = true;
  state.activeSessionId = `mobile-${state.device.device_id}-${crypto.randomUUID()}`;
  if (!state.conversations[state.activeSessionId]) {
    state.conversations[state.activeSessionId] = { messages: [], updatedAt: new Date().toISOString() };
  }
  renderConversationList();
  renderActiveConversation({ followLatest: true });
  void persistActiveSession();
  $("compose-input")?.focus();
}

async function persistActiveSession() {
  if (!state.session?.csrf_token || !state.activeSessionId) return;
  await fetch("/admin/web-chat/session/active", {
    method: "POST",
    credentials: "same-origin",
    headers: {
      "Content-Type": "application/json",
      "X-CSRF-Token": state.session.csrf_token,
    },
    body: JSON.stringify({ active_session_id: state.activeSessionId }),
  }).catch(() => {});
}

async function logoutWebChat() {
  stopActiveAudioPlayback();
  cleanupVoiceRecording();
  await fetch("/admin/web-chat/logout", {
    method: "POST",
    credentials: "same-origin",
    headers: {
      "Content-Type": "application/json",
      "X-CSRF-Token": state.session?.csrf_token || "",
    },
    body: JSON.stringify({}),
  }).catch(() => {});
  state.device = null;
  state.session = null;
  state.conversations = {};
  state.activeSessionId = null;
  state.lastEventId = null;
  showTypingIndicator(false);
  unreadReplyCount = 0;
  shouldAutoFollowLatest = true;
  releaseSelectedAttachments();
  selectedAttachments = [];
  renderComposerMeta();
  isSending = false;
  updateSendButton();
  await beginLogin();
}

function showToast(message, isError = false) {
  const toast = $("toast");
  if (!toast) return;
  if (toastTimer) {
    window.clearTimeout(toastTimer);
  }
  toast.textContent = message;
  toast.style.background = isError ? "rgba(146, 40, 40, 0.92)" : "rgba(16, 35, 50, 0.92)";
  toast.classList.add("is-visible");
  toastTimer = window.setTimeout(() => {
    toast.classList.remove("is-visible");
  }, 3000);
}

function stopActiveAudioPlayback() {
  if (activeAudioPlayer?.audio instanceof HTMLAudioElement) {
    activeAudioPlayer.audio.pause();
    try {
      activeAudioPlayer.audio.currentTime = 0;
    } catch (_) {
      // Ignore reset failures while the element is being replaced.
    }
  }
  activeAudioPlayer = null;
}

function showTypingIndicator(show) {
  const container = $("messages");
  if (!container) return;
  if (typingIndicatorTimer) {
    window.clearTimeout(typingIndicatorTimer);
    typingIndicatorTimer = null;
  }
  let element = container.querySelector(".typing-indicator");
  if (!show) {
    if (element) element.remove();
    return;
  }
  if (!element) {
    element = document.createElement("div");
    element.className = "typing-indicator";
    element.setAttribute("role", "status");
    element.setAttribute("aria-live", "polite");
    element.innerHTML = `
      <span class="typing-indicator__dots" aria-hidden="true">
        <span></span><span></span><span></span>
      </span>
      <span class="typing-indicator__text">Agent is typing</span>
    `;
    container.appendChild(element);
  }
  element.classList.add("is-visible");
  scrollMessagesToLatest({ smooth: false });
}

function formatTimestamp(value) {
  const date = value ? new Date(value) : null;
  if (!date || Number.isNaN(date.getTime())) return "";
  return date.toLocaleString([], { hour: "2-digit", minute: "2-digit", month: "short", day: "numeric" });
}

function inferAttachmentKind(mimeType, fileName) {
  const mime = String(mimeType || "").toLowerCase();
  if (mime.startsWith("image/")) return "image";
  if (mime.startsWith("audio/")) return "audio";
  if (mime.startsWith("video/")) return "video";
  const suffix = String(fileName || "").toLowerCase();
  if (/\.(png|jpg|jpeg|gif|webp|svg|bmp|avif)$/.test(suffix)) return "image";
  if (/\.(mp3|wav|m4a|ogg|aac|flac|webm)$/.test(suffix)) return "audio";
  if (/\.(mp4|mov|m4v|webm)$/.test(suffix)) return "video";
  return "file";
}

function autoResizeInput(textarea) {
  if (!textarea) return;
  textarea.style.height = "auto";
  textarea.style.height = `${Math.min(textarea.scrollHeight, 180)}px`;
}

function updateSendButton() {
  const input = $("compose-input");
  const sendButton = $("btn-send");
  const attachButton = $("btn-attach");
  const voiceButton = $("btn-voice");
  if (!sendButton) return;
  const hasText = String(input?.value || "").trim().length > 0;
  sendButton.disabled = !(hasText || selectedAttachments.length > 0) || isSending || isVoiceRecording || isVoiceTranscribing || !state.session?.csrf_token;
  if (attachButton) {
    attachButton.disabled = isSending || isVoiceRecording || isVoiceTranscribing || !state.session?.csrf_token;
  }
  if (voiceButton) {
    syncVoiceButtonState();
  }
}

function renderComposerMeta() {
  const target = $("compose-attachments");
  if (!target) return;
  if (!selectedAttachments.length) {
    target.innerHTML = "";
    target.classList.add("is-hidden");
    return;
  }
  target.classList.remove("is-hidden");
  target.innerHTML = "";
  selectedAttachments.forEach((item) => {
    const chip = document.createElement("div");
    chip.className = "compose-attachment-chip";
    chip.innerHTML = `
      ${item.previewUrl ? `<img src="${item.previewUrl}" alt="${escapeHtml(item.file.name)}">` : `<span class="compose-attachment-kind">${escapeHtml(inferAttachmentKind(item.file.type, item.file.name))}</span>`}
      <div class="compose-attachment-copy">
        <strong>${escapeHtml(item.file.name)}</strong>
        <span>${escapeHtml(formatBytes(item.file.size))}</span>
      </div>
      <button type="button" class="compose-attachment-remove" aria-label="Remove attachment">Remove</button>
    `;
    chip.querySelector(".compose-attachment-remove")?.addEventListener("click", () => {
      releaseAttachmentPreview(item);
      selectedAttachments = selectedAttachments.filter((entry) => entry.id !== item.id);
      renderComposerMeta();
      updateSendButton();
    });
    target.appendChild(chip);
  });
}

function releaseAttachmentPreview(item) {
  if (item?.previewUrl && String(item.previewUrl).startsWith("blob:")) {
    URL.revokeObjectURL(item.previewUrl);
  }
}

function releaseSelectedAttachments(items = selectedAttachments) {
  (Array.isArray(items) ? items : []).forEach((item) => releaseAttachmentPreview(item));
}

async function serializeAttachments(items) {
  return Promise.all((Array.isArray(items) ? items : []).map(async (item) => ({
    name: item.file.name,
    type: item.file.type || "application/octet-stream",
    size: item.file.size,
    data_base64: await fileToBase64(item.file),
  })));
}

function fileToBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const result = String(reader.result || "");
      const marker = result.indexOf(",");
      resolve(marker >= 0 ? result.slice(marker + 1) : result);
    };
    reader.onerror = () => reject(reader.error || new Error("Failed to read file"));
    reader.readAsDataURL(file);
  });
}

function removeMessage(messageId) {
  Object.values(state.conversations).forEach((conversation) => {
    if (!Array.isArray(conversation?.messages)) return;
    const nextMessages = conversation.messages.filter((item) => item.messageId !== messageId);
    if (nextMessages.length === conversation.messages.length) return;
    conversation.messages = nextMessages;
    conversation.updatedAt = String(nextMessages[nextMessages.length - 1]?.timestamp || "");
  });
}

function handleMessagesScroll() {
  shouldAutoFollowLatest = isMessagesNearBottom();
  syncUnreadIndicators();
}

function isMessagesNearBottom(container = $("messages")) {
  if (!container) return true;
  const distance = container.scrollHeight - container.clientHeight - container.scrollTop;
  return distance <= MESSAGE_FOLLOW_THRESHOLD_PX;
}

function syncLatestMessageButton() {
  syncUnreadIndicators();
}

function syncUnreadIndicators() {
  const button = $("btn-latest");
  const container = $("messages");
  const banner = $("reply-banner");
  const bannerText = $("reply-banner-text");
  const bannerAction = $("reply-banner-action");
  if (!button || !container) return;
  const atBottom = isMessagesNearBottom(container);
  if (atBottom) {
    unreadReplyCount = 0;
  }
  const shouldShow = unreadReplyCount > 0 && !atBottom && container.childElementCount > 0;
  button.classList.toggle("is-visible", shouldShow);
  button.setAttribute("aria-hidden", String(!shouldShow));
  const badge = button.querySelector(".latest-message-button__badge");
  const text = button.querySelector(".latest-message-button__text");
  if (badge) {
    badge.textContent = `${Math.max(1, unreadReplyCount)} new ${unreadReplyCount === 1 ? "reply" : "replies"}`;
  }
  if (text) {
    text.textContent = "Jump to latest";
  }
  if (banner) {
    banner.classList.toggle("is-hidden", !shouldShow);
  }
  if (bannerText) {
    bannerText.textContent = `${Math.max(1, unreadReplyCount)} new ${unreadReplyCount === 1 ? "reply" : "replies"} received`;
  }
  if (bannerAction) {
    bannerAction.disabled = !shouldShow;
  }
  document.title = shouldShow ? `(${Math.max(1, unreadReplyCount)}) ${baseDocumentTitle}` : baseDocumentTitle;
}

function scrollMessagesToLatest({ smooth = false } = {}) {
  const container = $("messages");
  if (!container) return;
  window.requestAnimationFrame(() => {
    shouldAutoFollowLatest = true;
    container.scrollTo({
      top: container.scrollHeight,
      behavior: smooth ? "smooth" : "auto",
    });
    unreadReplyCount = 0;
    syncUnreadIndicators();
  });
}

function formatBytes(bytes) {
  const value = Number(bytes || 0);
  if (!value) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  let current = value;
  let index = 0;
  while (current >= 1024 && index < units.length - 1) {
    current /= 1024;
    index += 1;
  }
  return `${current >= 10 || index === 0 ? current.toFixed(0) : current.toFixed(1)} ${units[index]}`;
}

function escapeHtml(value) {
  return String(value || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}
