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
let unreadReplyCount = 0;
let shouldAutoFollowLatest = true;
let isSidebarCollapsed = false;
let typingIndicatorTimer = null;
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
  $("btn-toggle-sidebar")?.addEventListener("click", () => toggleSidebar());
  $("btn-latest")?.addEventListener("click", () => scrollMessagesToLatest({ smooth: true }));
  $("reply-banner-action")?.addEventListener("click", () => scrollMessagesToLatest({ smooth: true }));
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
  $("conversation-label").textContent = state.activeSessionId || "";
  target.innerHTML = "";
  const groupState = createProcessingGroupState(target);
  conversation.messages.forEach((message) => {
    appendConversationMessage(groupState, message);
  });
  finalizeProcessingGroup(groupState);
  window.requestAnimationFrame(() => {
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
  return items.map((item) => ({
    name: item?.name || item?.file_name || item?.fileName || "attachment",
    fileName: item?.file_name || item?.fileName || item?.name || "attachment",
    mimeType: item?.mime_type || item?.mimeType || "application/octet-stream",
    size: Number(item?.size || 0),
    previewUrl: item?.previewUrl || "",
    url: item?.url || item?.previewUrl || "",
    kind: item?.kind || inferAttachmentKind(item?.mime_type || item?.mimeType || "", item?.file_name || item?.fileName || item?.name || ""),
  }));
}

function renderAttachmentList(attachments) {
  const wrapper = document.createElement("div");
  wrapper.className = "attachment-list";
  attachments.forEach((item) => {
    const mediaUrl = item.url || item.previewUrl || "";
    if (item.kind === "image" && mediaUrl) {
      const image = document.createElement("img");
      image.src = mediaUrl;
      image.alt = item.name;
      wrapper.appendChild(image);
      return;
    }
    if (item.kind === "audio" && mediaUrl) {
      const audio = document.createElement("audio");
      audio.controls = true;
      audio.src = mediaUrl;
      wrapper.appendChild(audio);
      return;
    }
    const chip = document.createElement(mediaUrl ? "a" : "span");
    chip.className = "attachment-chip";
    if (mediaUrl) {
      chip.href = mediaUrl;
      chip.target = "_blank";
      chip.rel = "noreferrer";
    }
    chip.textContent = item.size ? `${item.name} (${formatBytes(item.size)})` : item.name;
    wrapper.appendChild(chip);
  });
  return wrapper;
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
  const mime = String(mimeType || "");
  if (mime.startsWith("image/")) return "image";
  if (mime.startsWith("audio/")) return "audio";
  const suffix = String(fileName || "").toLowerCase();
  if (/\.(png|jpg|jpeg|gif|webp|svg|bmp|avif)$/.test(suffix)) return "image";
  if (/\.(mp3|wav|m4a|ogg|aac|flac|webm)$/.test(suffix)) return "audio";
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
  if (!sendButton) return;
  const hasText = String(input?.value || "").trim().length > 0;
  sendButton.disabled = !(hasText || selectedAttachments.length > 0) || isSending || !state.session?.csrf_token;
  if (attachButton) {
    attachButton.disabled = isSending || !state.session?.csrf_token;
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
