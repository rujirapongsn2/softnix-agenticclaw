/* ── Softnix Mobile Web App ────────────────────────────────── */
"use strict";

const STORAGE_KEY = "softnix_mobile_v1";
const POLL_INTERVAL_MS = 2000;
const MAX_ATTACHMENT_BYTES = 15 * 1024 * 1024;
const REPLY_CONTEXT_WINDOW = 4;
const MAX_STORED_MESSAGES = 200;

let device = null;
let appState = null;
let pollTimer = null;
let isSending = false;
let hasMessages = false;
let currentAgentGroup = null;
let currentReplyTarget = null;
let selectedAttachments = [];
let pushRegistration = null;
let pushConfig = null;
let pushSubscribed = false;
let activeAudioPlayer = null;

const seenMessageIds = new Set();
const messageStore = new Map();

const $ = (id) => document.getElementById(id);

document.addEventListener("DOMContentLoaded", init);

function init() {
  appState = loadAppState();
  device = appState.device;
  const params = new URLSearchParams(window.location.search);
  const token = params.get("token");
  const instanceId = params.get("instance_id");
  const transferToken = params.get("transfer_token");

  if (transferToken) {
    showScreen("pairing");
    $("pairing-status").textContent = "Importing your existing mobile session…";
    void consumeTransferToken(transferToken);
  } else if (token && instanceId) {
    showScreen("pairing");
    registerDevice(instanceId, token);
  } else if (device) {
    showScreen("chat");
    setupChat();
  } else {
    showScreen("error");
    configureDisconnectedState();
  }

  $("btn-retry").addEventListener("click", handleRetryAction);
  $("btn-send").addEventListener("click", () => void handleSend());
  $("btn-attach")?.addEventListener("click", handleAttachClick);
  $("attachment-input")?.addEventListener("change", (event) => void handleAttachmentInput(event));
  $("compose-input").addEventListener("input", handleComposeInput);
  $("compose-input").addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void handleSend();
    }
  });
  $("messages")?.addEventListener("click", handleMessageActions);
  $("btn-disconnect").addEventListener("click", showDisconnectMenu);
  $("btn-chats")?.addEventListener("click", showChatsSheet);
  document.querySelectorAll("[data-quick-prompt]").forEach((button) => {
    button.addEventListener("click", () => applyQuickPrompt(button.dataset.quickPrompt || ""));
  });
}

function showScreen(name) {
  ["pairing", "chat", "error"].forEach((screenName) => {
    const element = $("screen-" + screenName);
    if (element) element.style.display = screenName === name ? "flex" : "none";
  });
  if (name === "chat") {
    $("screen-chat").style.flexDirection = "column";
  }
}

async function registerDevice(instanceId, token) {
  const deviceId = "mob-" + crypto.randomUUID();
  const label = detectDeviceLabel();

  try {
    const response = await apiFetch("/admin/mobile/register", {
      method: "POST",
      body: JSON.stringify({
        instance_id: instanceId,
        device_id: deviceId,
        pairing_token: token,
        label,
      }),
    });

    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(data.error || `Registration failed (${response.status})`);
    }

    device = {
      device_id: deviceId,
      instance_id: instanceId,
      label,
      registered_at: new Date().toISOString(),
      device_token: data.device_token || data.mobile_token || "",
    };
    saveDevice(device);
    window.history.replaceState({}, "", "/mobile");
    showScreen("chat");
    setupChat();
  } catch (error) {
    showScreen("error");
    $("error-message").textContent = error.message || "Registration failed.";
    $("btn-retry").style.display = "inline-block";
  }
}

function setupChat() {
  if (!device) return;
  const chipElement = $("chat-instance-id");
  if (chipElement) chipElement.textContent = device.instance_id;
  applyRequestedSessionFromUrl();
  restoreActiveConversation();
  renderComposerMeta();
  refreshHomeState();
  startPolling();
  void setupPushNotifications();
}

function configureDisconnectedState() {
  $("error-message").textContent = isStandaloneMode()
    ? "Open Safari where this device was already connected, create a transfer code, then import it here."
    : "Scan a QR code from the admin console to connect.";
  const retryButton = $("btn-retry");
  if (!retryButton) return;
  if (isStandaloneMode()) {
    retryButton.style.display = "inline-block";
    retryButton.textContent = "Import Existing Session";
    retryButton.dataset.action = "import-session";
  } else {
    retryButton.style.display = "none";
    retryButton.textContent = "Try Again";
    retryButton.dataset.action = "reload";
  }
}

function handleRetryAction(event) {
  const action = event.currentTarget?.dataset?.action || "reload";
  if (action === "import-session") {
    event.preventDefault();
    void promptForTransferToken();
    return;
  }
  window.location.reload();
}

async function handleSend() {
  const input = $("compose-input");
  const text = input.value.trim();
  if ((!text && selectedAttachments.length === 0) || isSending || !device) return;

  const messageId = "mobu-" + crypto.randomUUID();
  const replyToMessageId = currentReplyTarget?.messageId || null;
  const threadRootId = currentReplyTarget
    ? (currentReplyTarget.threadRootId || currentReplyTarget.messageId)
    : null;
  const sessionId = currentReplyTarget?.sessionId || getActiveSessionId();
  const submittedText = buildSubmittedText({
    text,
    replyTarget: currentReplyTarget,
    attachmentsCount: selectedAttachments.length,
  });
  const attachmentsPayload = await serializeSelectedAttachments();

  isSending = true;
  input.value = "";
  autoResizeInput(input);
  updateSendButton();

  const outboundMessage = {
    messageId,
    role: "user",
    text,
    sessionId,
    threadRootId,
    replyTo: replyToMessageId,
    attachments: selectedAttachments.map((item) => ({
      name: item.file.name,
      mimeType: item.file.type || "application/octet-stream",
      size: item.file.size,
      kind: attachmentKind(item.file.type),
      previewUrl: item.previewUrl,
      local: true,
    })),
  };

  finalizeAgentGroup();
  appendMessage(outboundMessage);
  clearComposerState();
  showTypingIndicator(true);
  setTimeout(() => showTypingIndicator(false), 60_000);

  try {
    const response = await apiFetch("/admin/mobile/message", {
      method: "POST",
      body: JSON.stringify({
        instance_id: device.instance_id,
        sender_id: device.device_id,
        text: submittedText,
        message_id: messageId,
        session_id: sessionId,
        reply_to: replyToMessageId,
        thread_root_id: threadRootId,
        attachments: attachmentsPayload,
      }),
    });
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      appendMessage({
        role: "agent",
        text: "Failed to send: " + (data.error || response.status),
        msgType: "answer",
        messageId: "moberr-" + crypto.randomUUID(),
        sessionId,
      });
    }
  } catch (error) {
    appendMessage({
      role: "agent",
      text: "Network error: " + error.message,
      msgType: "answer",
      messageId: "moberr-" + crypto.randomUUID(),
      sessionId,
    });
  } finally {
    isSending = false;
    updateSendButton();
  }
}

function startPolling() {
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = setInterval(() => void pollReplies(), POLL_INTERVAL_MS);
}

async function pollReplies() {
  if (!device) return;

  try {
    const senderId = getActiveSessionId() || device.device_id;
    const url = `/admin/mobile/poll?instance_id=${encodeURIComponent(device.instance_id)}&sender_id=${encodeURIComponent(senderId)}`;
    const response = await apiFetch(url);
    if (!response.ok) return;

    const data = await response.json();
    const replies = Array.isArray(data.replies) ? data.replies : [];

    if (replies.length > 0) {
      showTypingIndicator(false);
    }

    for (const reply of replies) {
      const message = normalizeReplyMessage(reply);
      if (seenMessageIds.has(message.messageId)) continue;
      appendMessage(message);

      if (
        message.role === "agent" &&
        message.msgType === "answer" &&
        !pushSubscribed &&
        document.visibilityState === "hidden" &&
        "Notification" in window &&
        Notification.permission === "granted"
      ) {
        try {
          new Notification("Softnix Agent", {
            body: (message.text || "").slice(0, 100),
            icon: "/static/Logo_Softnix.png",
            tag: "softnix-reply",
            renotify: true,
          });
        } catch (_) {}
      }
    }
  } catch (_) {}
}

function normalizeReplyMessage(reply) {
  const attachments = normalizeAttachments(reply.attachments);
  return {
    role: "agent",
    text: reply.text || reply.content || "(empty reply)",
    msgType: reply.type || "answer",
    messageId: reply.message_id || "moba-" + crypto.randomUUID(),
    sessionId: reply.session_id || `mobile-${device?.device_id || "unknown"}`,
    replyTo: reply.reply_to || null,
    threadRootId: reply.thread_root_id || reply.reply_to || null,
    attachments,
    timestamp: reply.timestamp || new Date().toISOString(),
  };
}

function normalizeAttachments(items) {
  if (!Array.isArray(items)) return [];
  return items
    .filter((item) => item && typeof item === "object")
    .map((item) => {
      const mimeType = item.mime_type || item.mimeType || "application/octet-stream";
      const fileName = item.file_name || item.fileName || item.name || "attachment";
      let url = item.url || "";
      if (device && url.startsWith("/admin/mobile/media") && device.device_token && !url.includes("mobile_token=")) {
        url += `${url.includes("?") ? "&" : "?"}mobile_token=${encodeURIComponent(device.device_token)}`;
      }
      if (!url && device) {
        url = `/admin/mobile/media?instance_id=${encodeURIComponent(device.instance_id)}&sender_id=${encodeURIComponent(device.device_id)}&file=${encodeURIComponent(fileName)}${device.device_token ? `&mobile_token=${encodeURIComponent(device.device_token)}` : ""}`;
      }
      return {
        name: item.name || fileName,
        fileName,
        mimeType,
        size: Number(item.size || 0),
        kind: item.kind || attachmentKind(mimeType),
        url,
        previewUrl: item.previewUrl || url,
        duration: Number(item.duration || item.audio_duration || 0) || 0,
      };
    });
}

function appendMessage(message) {
  return appendMessageInternal(message, {});
}

function appendMessageInternal(message, options = {}) {
  const normalized = {
    role: message.role || "agent",
    text: message.text || "",
    msgType: message.msgType || "answer",
    messageId: message.messageId || "msg-" + crypto.randomUUID(),
    sessionId: message.sessionId || `mobile-${device?.device_id || "unknown"}`,
    replyTo: message.replyTo || null,
    threadRootId: message.threadRootId || null,
    attachments: Array.isArray(message.attachments) ? message.attachments : [],
    timestamp: message.timestamp || new Date().toISOString(),
  };

  seenMessageIds.add(normalized.messageId);
  messageStore.set(normalized.messageId, normalized);
  if (!options.skipPersist) {
    persistMessage(normalized);
  }
  hasMessages = true;
  refreshHomeState();

  if (normalized.role === "agent") {
    if (normalized.msgType === "tool" || normalized.msgType === "progress") {
      appendAgentToolOrProgress(normalized);
    } else {
      appendAgentAnswer(normalized);
    }
    return normalized;
  }

  finalizeAgentGroup();
  insertMessageElement(createMessageElement(normalized));
  return normalized;
}

function appendAgentToolOrProgress(message) {
  const container = $("messages");

  if (!currentAgentGroup) {
    const groupElement = document.createElement("div");
    groupElement.className = "agent-group";

    const toolsPanel = document.createElement("div");
    toolsPanel.className = "tools-panel";

    const toolsToggle = document.createElement("button");
    toolsToggle.className = "tools-toggle";
    toolsToggle.type = "button";
    toolsToggle.innerHTML = `
      <svg class="tools-toggle-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/>
      </svg>
      <span class="tools-toggle-label">Agent is working...</span>
      <svg class="tools-toggle-chevron" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <polyline points="6 9 12 15 18 9"/>
      </svg>
    `;
    toolsToggle.addEventListener("click", () => {
      toolsPanel.classList.toggle("is-expanded");
      const label = toolsToggle.querySelector(".tools-toggle-label");
      const count = toolsPanel.querySelectorAll(".tool-step").length;
      label.textContent = toolsPanel.classList.contains("is-expanded")
        ? `${count} tool step${count !== 1 ? "s" : ""}`
        : `${count} tool step${count !== 1 ? "s" : ""} (tap to view)`;
    });

    const toolsContent = document.createElement("div");
    toolsContent.className = "tools-content";

    const answerSlot = document.createElement("div");
    answerSlot.className = "agent-group-answer";

    toolsPanel.appendChild(toolsToggle);
    toolsPanel.appendChild(toolsContent);
    groupElement.appendChild(toolsPanel);
    groupElement.appendChild(answerSlot);

    currentAgentGroup = {
      groupElement,
      toolsPanel,
      toolsContent,
      toolsToggle,
      answerSlot,
      toolMessages: [],
    };

    const typing = container.querySelector(".typing-indicator");
    if (typing) {
      container.insertBefore(groupElement, typing);
    } else {
      container.appendChild(groupElement);
    }
  }

  const stepElement = document.createElement("div");
  stepElement.className = "tool-step";
  stepElement.textContent = message.text;

  const timeElement = document.createElement("span");
  timeElement.className = "tool-step-time";
  timeElement.textContent = formatMessageTime(message.timestamp);
  stepElement.appendChild(timeElement);

  currentAgentGroup.toolsContent.appendChild(stepElement);
  currentAgentGroup.toolMessages.push(message.text);

  const count = currentAgentGroup.toolMessages.length;
  const label = currentAgentGroup.toolsToggle.querySelector(".tools-toggle-label");
  label.textContent = `Agent is working... (${count} step${count !== 1 ? "s" : ""})`;
  scrollToBottom();
}

function appendAgentAnswer(message) {
  const element = createMessageElement(message);
  if (currentAgentGroup) {
    const count = currentAgentGroup.toolMessages.length;
    const label = currentAgentGroup.toolsToggle.querySelector(".tools-toggle-label");
    label.textContent = `${count} tool step${count !== 1 ? "s" : ""} (tap to view)`;
    currentAgentGroup.answerSlot.appendChild(element);
    currentAgentGroup = null;
  } else {
    insertMessageElement(element);
  }
}

function finalizeAgentGroup() {
  currentAgentGroup = null;
}

function createMessageElement(message) {
  const element = document.createElement("div");
  element.className = `msg ${message.role === "user" ? "msg--user" : "msg--agent"}`;
  element.dataset.messageId = message.messageId;
  element.dataset.sessionId = message.sessionId;
  if (message.replyTo) element.dataset.replyTo = message.replyTo;
  if (message.threadRootId) element.dataset.threadRootId = message.threadRootId;

  const replyPreview = buildReplyPreview(message.replyTo);
  if (replyPreview) {
    const replyElement = document.createElement("button");
    replyElement.type = "button";
    replyElement.className = "msg-reply-ref";
    replyElement.dataset.scrollMessageId = replyPreview.messageId;
    replyElement.innerHTML = `
      <span class="msg-reply-role">${escapeHtml(replyPreview.role === "user" ? "You" : "Agent")}</span>
      <span class="msg-reply-text">${escapeHtml(replyPreview.text)}</span>
    `;
    element.appendChild(replyElement);
  }

  if (message.text) {
    const content = document.createElement("div");
    content.className = "msg-content";
    content.innerHTML = renderMarkdown(message.text);
    element.appendChild(content);
  }

  if (message.attachments.length > 0) {
    element.appendChild(createAttachmentList(message.attachments));
  }

  const footer = document.createElement("div");
  footer.className = "msg-footer";

  const time = document.createElement("span");
  time.className = "msg-time";
  time.textContent = formatMessageTime(message.timestamp);
  footer.appendChild(time);

  const replyButton = document.createElement("button");
  replyButton.type = "button";
  replyButton.className = "msg-action";
  replyButton.textContent = "Reply";
  replyButton.dataset.replyMessageId = message.messageId;
  footer.appendChild(replyButton);

  element.appendChild(footer);
  return element;
}

function buildReplyPreview(messageId) {
  if (!messageId) return null;
  const message = messageStore.get(messageId);
  if (!message) return null;
  return {
    messageId,
    role: message.role,
    text: summarizeReplyText(message),
  };
}

function summarizeReplyText(message) {
  if (!message) return "";
  const attachmentNote = message.attachments?.length ? ` (${message.attachments.length} attachment${message.attachments.length !== 1 ? "s" : ""})` : "";
  const text = (message.text || "").trim();
  if (text) return (text.length > 72 ? text.slice(0, 69) + "..." : text) + attachmentNote;
  return `Attachment${message.attachments?.length > 1 ? "s" : ""}${attachmentNote}`;
}

function createAttachmentList(attachments) {
  const wrapper = document.createElement("div");
  wrapper.className = "msg-attachments";
  attachments.forEach((attachment) => {
    const kind = attachment.kind || attachmentKind(attachment.mimeType || attachment.mime_type || "");
    if (kind === "audio") {
      wrapper.appendChild(createAudioAttachmentPlayer(attachment));
      return;
    }

    const link = document.createElement("a");
    link.className = `msg-attachment msg-attachment--${kind || "file"}`;
    link.href = attachment.url || attachment.previewUrl || "#";
    link.target = "_blank";
    link.rel = "noopener noreferrer";

    if (kind === "image" && (attachment.previewUrl || attachment.url)) {
      const image = document.createElement("img");
      image.src = attachment.previewUrl || attachment.url;
      image.alt = attachment.name || "Attachment";
      image.loading = "lazy";
      link.appendChild(image);
    } else {
      const icon = document.createElement("span");
      icon.className = "msg-attachment-icon";
      icon.textContent = kind === "video" ? "Video" : "File";
      link.appendChild(icon);
    }

    const meta = document.createElement("span");
    meta.className = "msg-attachment-meta";
    meta.innerHTML = `
      <strong>${escapeHtml(attachment.name || "Attachment")}</strong>
      <span>${escapeHtml(formatBytes(attachment.size || 0))}</span>
    `;
    link.appendChild(meta);
    wrapper.appendChild(link);
  });
  return wrapper;
}

function createAudioAttachmentPlayer(attachment) {
  const card = document.createElement("div");
  card.className = "msg-attachment msg-attachment--audio msg-audio-card";
  const src = attachment.url || attachment.previewUrl || "";
  const attachmentName = attachment.name || "Audio";

  card.innerHTML = `
    <div class="msg-audio-header">
      <span class="msg-attachment-icon msg-attachment-icon--audio">Audio</span>
      <div class="msg-audio-copy">
        <strong>${escapeHtml(attachmentName)}</strong>
        <span>${escapeHtml(formatBytes(attachment.size || 0))}</span>
      </div>
    </div>
    <div class="msg-audio-controls">
      <button type="button" class="msg-audio-button msg-audio-button--play" aria-label="Play audio">Play</button>
      <button type="button" class="msg-audio-button msg-audio-button--stop" aria-label="Stop audio">Stop</button>
      <span class="msg-audio-time">0:00 / 0:00</span>
    </div>
    <div class="msg-audio-progress" aria-hidden="true">
      <div class="msg-audio-progress-bar"></div>
    </div>
    <audio preload="none" playsinline></audio>
  `;

  const audio = card.querySelector("audio");
  const playButton = card.querySelector(".msg-audio-button--play");
  const stopButton = card.querySelector(".msg-audio-button--stop");
  const timeLabel = card.querySelector(".msg-audio-time");
  const progressBar = card.querySelector(".msg-audio-progress-bar");
  if (!(audio instanceof HTMLAudioElement) || !(playButton instanceof HTMLButtonElement) || !(stopButton instanceof HTMLButtonElement) || !(timeLabel instanceof HTMLElement) || !(progressBar instanceof HTMLElement)) {
    return card;
  }

  // Track blob loading state — we fetch audio as a blob to avoid
  // iOS Safari issues when the page is served through ngrok/proxies.
  let blobLoaded = false;
  let blobLoading = false;
  let blobUrl = "";

  const loadAudioBlob = async () => {
    if (blobLoaded || blobLoading || !src) return true;
    blobLoading = true;
    timeLabel.textContent = "Loading…";
    try {
      const response = await fetch(src);
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const blob = await response.blob();
      blobUrl = URL.createObjectURL(blob);
      audio.src = blobUrl;
      blobLoaded = true;
      // Wait for the audio element to process the blob
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
    } catch (err) {
      blobLoading = false;
      return false;
    }
  };

  const controller = {
    audio,
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
      const loaded = await loadAudioBlob();
      if (!loaded) {
        timeLabel.textContent = "Unable to load audio";
        card.classList.add("is-error");
        return;
      }
      await audio.play();
    } catch (error) {
      setBanner(`Unable to play audio: ${error.message || "Playback failed"}`, "error");
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
      // Don't show error for initial state — error will show after blob load attempt
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

function insertMessageElement(element) {
  const container = $("messages");
  const typing = container.querySelector(".typing-indicator");
  if (typing) {
    container.insertBefore(element, typing);
  } else {
    container.appendChild(element);
  }
  scrollToBottom();
}

function scrollToBottom() {
  const container = $("messages");
  requestAnimationFrame(() => {
    container.scrollTop = container.scrollHeight;
  });
}

function scrollToMessage(messageId) {
  const target = document.querySelector(`[data-message-id="${CSS.escape(messageId)}"]`);
  if (!target) return;
  target.scrollIntoView({ behavior: "smooth", block: "center" });
  target.classList.add("is-flash");
  window.setTimeout(() => target.classList.remove("is-flash"), 900);
}

function showTypingIndicator(show) {
  let element = $("messages").querySelector(".typing-indicator");
  if (!element && show) {
    element = document.createElement("div");
    element.className = "typing-indicator spinner spinner--dots";
    element.setAttribute("aria-label", "Agent is typing");
    element.setAttribute("role", "status");
    element.innerHTML = "<span></span><span></span><span></span>";
    $("messages").appendChild(element);
  }
  if (element) {
    element.classList.toggle("is-visible", show);
    if (show) scrollToBottom();
  }
}

function refreshHomeState() {
  const home = $("chat-home");
  const messages = $("messages");
  if (!home || !messages) return;
  home.style.display = hasMessages ? "none" : "block";
  messages.classList.toggle("is-empty", !hasMessages);
}

function applyQuickPrompt(prompt) {
  const input = $("compose-input");
  if (!input) return;
  const templates = {
    "Ask Anything": "Help me with ",
    "Summarize": "Summarize this clearly and briefly: ",
    "Schedule Task": "Help me schedule this task with timeline, priority, and next steps: ",
    "Meeting Notes": "Turn this into clear meeting notes with decisions and next steps: ",
    "Prioritize": "Help me prioritize these tasks by urgency and impact: ",
  };
  input.value = templates[prompt] || prompt;
  autoResizeInput(input);
  updateSendButton();
  input.focus();
}

function handleAttachClick() {
  $("attachment-input")?.click();
}

async function handleAttachmentInput(event) {
  const input = event.target;
  const files = Array.from(input.files || []);
  for (const file of files) {
    if (file.size > MAX_ATTACHMENT_BYTES) {
      appendMessage({
        role: "agent",
        text: `Attachment '${file.name}' is too large. Limit is ${formatBytes(MAX_ATTACHMENT_BYTES)}.`,
        msgType: "answer",
        messageId: "mobattach-" + crypto.randomUUID(),
      });
      continue;
    }
    selectedAttachments.push({
      id: "att-" + crypto.randomUUID(),
      file,
      previewUrl: file.type.startsWith("image/") ? URL.createObjectURL(file) : "",
    });
  }
  input.value = "";
  renderComposerMeta();
  updateSendButton();
}

function handleComposeInput() {
  autoResizeInput($("compose-input"));
  updateSendButton();
}

function handleMessageActions(event) {
  const replyButton = event.target.closest("[data-reply-message-id]");
  if (replyButton) {
    const messageId = replyButton.dataset.replyMessageId || "";
    const message = messageStore.get(messageId);
    if (message) {
      setReplyTarget(message);
    }
    return;
  }

  const scrollButton = event.target.closest("[data-scroll-message-id]");
  if (scrollButton) {
    scrollToMessage(scrollButton.dataset.scrollMessageId || "");
  }
}

function autoResizeInput(textarea) {
  textarea.style.height = "auto";
  textarea.style.height = Math.min(textarea.scrollHeight, 120) + "px";
}

function updateSendButton() {
  const hasText = $("compose-input").value.trim().length > 0;
  $("btn-send").disabled = !(hasText || selectedAttachments.length > 0) || isSending;
}

function setReplyTarget(message) {
  currentReplyTarget = {
    messageId: message.messageId,
    sessionId: message.sessionId,
    threadRootId: message.threadRootId || message.messageId,
    role: message.role,
    text: message.text || "",
    previewText: summarizeReplyText(message),
    attachments: Array.isArray(message.attachments) ? message.attachments : [],
  };
  renderComposerMeta();
  scrollToBottom();
  $("compose-input")?.focus();
}

function clearComposerState() {
  selectedAttachments = [];
  currentReplyTarget = null;
  renderComposerMeta();
}

function renderComposerMeta() {
  const replyElement = $("compose-reply");
  const attachmentsElement = $("compose-attachments");

  if (replyElement) {
    if (currentReplyTarget) {
      replyElement.style.display = "flex";
      replyElement.innerHTML = `
        <div class="compose-reply-copy">
          <strong>Replying to ${escapeHtml(currentReplyTarget.role === "user" ? "your message" : "agent")}</strong>
          <span>${escapeHtml(currentReplyTarget.previewText || currentReplyTarget.text || "")}</span>
        </div>
        <button type="button" class="compose-reply-close" aria-label="Cancel reply">Cancel</button>
      `;
      replyElement.querySelector(".compose-reply-close")?.addEventListener("click", () => {
        currentReplyTarget = null;
        renderComposerMeta();
      });
    } else {
      replyElement.style.display = "none";
      replyElement.innerHTML = "";
    }
  }

  if (attachmentsElement) {
    if (selectedAttachments.length > 0) {
      attachmentsElement.style.display = "flex";
      attachmentsElement.innerHTML = "";
      selectedAttachments.forEach((item) => {
        const chip = document.createElement("div");
        chip.className = "compose-attachment-chip";
        chip.innerHTML = `
          ${item.previewUrl ? `<img src="${item.previewUrl}" alt="${escapeHtml(item.file.name)}">` : `<span class="compose-attachment-kind">${escapeHtml(attachmentKind(item.file.type))}</span>`}
          <div class="compose-attachment-copy">
            <strong>${escapeHtml(item.file.name)}</strong>
            <span>${escapeHtml(formatBytes(item.file.size))}</span>
          </div>
          <button type="button" class="compose-attachment-remove" aria-label="Remove attachment">Remove</button>
        `;
        chip.querySelector(".compose-attachment-remove")?.addEventListener("click", () => {
          if (item.previewUrl) URL.revokeObjectURL(item.previewUrl);
          selectedAttachments = selectedAttachments.filter((entry) => entry.id !== item.id);
          renderComposerMeta();
          updateSendButton();
        });
        attachmentsElement.appendChild(chip);
      });
    } else {
      attachmentsElement.style.display = "none";
      attachmentsElement.innerHTML = "";
    }
  }
}

function buildThreadSessionId(senderId, rootId) {
  return `mobile-${senderId}#thread:${rootId}`;
}

function buildSubmittedText({ text, replyTarget, attachmentsCount }) {
  const normalizedText = (text || "").trim();
  const fallbackText = normalizedText || "[Attachment]";
  if (!replyTarget) {
    return fallbackText;
  }

  const attachmentNote = attachmentsCount > 0 ? `\nAttachments included: ${attachmentsCount}` : "";
  return [
    ...buildReplyContextLines(replyTarget),
    "",
    "[User Reply]",
    fallbackText + attachmentNote,
  ].join("\n");
}

function buildReplyContextLines(replyTarget) {
  const contextMessages = collectReplyContextMessages(replyTarget);
  if (contextMessages.length === 0) {
    const targetLabel = replyTarget.role === "user" ? "user" : "agent";
    return [
      "[Reply Context]",
      `You are replying to the following ${targetLabel} message:`,
      replyTarget.text || "(no text)",
    ];
  }

  return [
    "[Reply Context]",
    "Use the following recent conversation context. The last item is the direct message being replied to.",
    ...contextMessages.flatMap((message, index) => formatReplyContextMessage(message, index + 1)),
  ];
}

function collectReplyContextMessages(replyTarget) {
  const targetMessage = messageStore.get(replyTarget.messageId);
  if (!targetMessage) return [];

  const selected = [];
  const selectedIds = new Set();
  const sessionMessages = Array.from(messageStore.values()).filter((message) => message.sessionId === targetMessage.sessionId);
  const targetIndex = sessionMessages.findIndex((message) => message.messageId === targetMessage.messageId);
  const leadingContext = targetIndex >= 0
    ? sessionMessages.slice(Math.max(0, targetIndex - 2), targetIndex)
    : [];

  leadingContext.forEach((message) => {
    if (!selectedIds.has(message.messageId)) {
      selected.push(message);
      selectedIds.add(message.messageId);
    }
  });

  const chain = [];
  let cursor = targetMessage;
  while (cursor && !selectedIds.has(cursor.messageId)) {
    chain.unshift(cursor);
    selectedIds.add(cursor.messageId);
    cursor = cursor.replyTo ? messageStore.get(cursor.replyTo) : null;
  }

  const combined = [...selected, ...chain];
  return combined.slice(-REPLY_CONTEXT_WINDOW);
}

function formatReplyContextMessage(message, order) {
  const roleLabel = message.role === "user" ? "User" : "Agent";
  const lines = [
    "",
    `[Context ${order}] ${roleLabel} at ${formatMessageTime(message.timestamp)}`,
    normalizeContextText(message.text),
  ];
  if (message.attachments?.length) {
    lines.push(`Attachments: ${message.attachments.map(describeAttachment).join(", ")}`);
  }
  return lines;
}

function normalizeContextText(text) {
  const normalized = String(text || "").trim();
  return normalized || "(no text)";
}

function describeAttachment(attachment) {
  const kind = attachment?.kind || attachmentKind(attachment?.mimeType || attachment?.mime_type || "");
  const name = attachment?.name || attachment?.fileName || attachment?.file_name || "attachment";
  return `${kind}: ${name}`;
}

async function serializeSelectedAttachments() {
  return Promise.all(selectedAttachments.map(async (item) => ({
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

function showDisconnectMenu() {
  let overlay = document.querySelector(".settings-menu");
  if (!overlay) {
    overlay = document.createElement("div");
    overlay.className = "disconnect-menu settings-menu";
    overlay.innerHTML = `
      <div class="disconnect-sheet">
        <h3>Settings</h3>
        <p>Device: ${escapeHtml(device?.label || device?.device_id || "Unknown")}</p>
        <button class="action-btn" id="btn-new-chat">New Chat</button>
        ${!isStandaloneMode() ? '<button class="action-btn" id="btn-transfer-session">Transfer to Home Screen</button>' : ""}
        <p class="settings-note" id="push-settings-note"></p>
        <button class="action-btn" id="btn-toggle-push"></button>
        <button class="action-btn is-danger" id="btn-disconnect-confirm">Disconnect Device</button>
        <button class="action-btn is-cancel" id="btn-disconnect-cancel">Cancel</button>
      </div>`;
    document.body.appendChild(overlay);

    $("btn-new-chat").addEventListener("click", () => {
      startNewChat();
      overlay.classList.remove("is-visible");
    });
    $("btn-transfer-session")?.addEventListener("click", () => void showTransferSessionSheet());
    $("btn-toggle-push").addEventListener("click", () => void togglePushNotifications());
    $("btn-disconnect-confirm").addEventListener("click", () => {
      clearDevice();
      window.location.href = "/mobile";
    });
    $("btn-disconnect-cancel").addEventListener("click", () => {
      overlay.classList.remove("is-visible");
    });
    overlay.addEventListener("click", (event) => {
      if (event.target === overlay) overlay.classList.remove("is-visible");
    });
  }
  void syncPushStatus();
  renderPushSettings();
  overlay.classList.add("is-visible");
}

async function showTransferSessionSheet() {
  if (!device || isStandaloneMode()) return;
  try {
    const response = await apiFetch("/admin/mobile/transfer-session/create", {
      method: "POST",
      body: JSON.stringify({
        device,
        active_session_id: getActiveSessionId(),
        conversations: appState?.conversations || {},
      }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || !data.transfer_token) {
      throw new Error(data.error || "Unable to prepare transfer");
    }
    renderTransferSheet(data.transfer_token, data.expires_at);
  } catch (error) {
    appendMessage({
      role: "agent",
      text: `Unable to prepare Home Screen transfer: ${error.message || "Unknown error"}`,
      msgType: "answer",
      messageId: "mobxfer-" + crypto.randomUUID(),
      sessionId: getActiveSessionId(),
    });
  }
}

function renderTransferSheet(transferToken, expiresAt) {
  let overlay = document.querySelector(".transfer-sheet-menu");
  if (!overlay) {
    overlay = document.createElement("div");
    overlay.className = "disconnect-menu transfer-sheet-menu";
    document.body.appendChild(overlay);
  }
  overlay.innerHTML = `
    <div class="disconnect-sheet">
      <h3>Transfer to Home Screen</h3>
      <p>1. Add this site to Home Screen if you have not done so.</p>
      <p>2. Open the Home Screen app, then tap <strong>Import Existing Session</strong>.</p>
      <p>3. Enter this one-time transfer code before it expires.</p>
      <div class="transfer-code">${escapeHtml(transferToken)}</div>
      <p class="settings-note">Expires: ${escapeHtml(new Date(expiresAt).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }))}</p>
      <button class="action-btn" id="btn-copy-transfer">Copy Code</button>
      <button class="action-btn is-cancel" id="btn-close-transfer">Close</button>
    </div>`;
  overlay.querySelector("#btn-copy-transfer")?.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(transferToken);
    } catch (_) {}
  });
  overlay.querySelector("#btn-close-transfer")?.addEventListener("click", () => {
    overlay.classList.remove("is-visible");
  });
  overlay.addEventListener("click", (event) => {
    if (event.target === overlay) overlay.classList.remove("is-visible");
  }, { once: true });
  overlay.classList.add("is-visible");
}

async function promptForTransferToken() {
  const token = window.prompt("Enter the transfer code from Safari");
  if (!token) return;
  showScreen("pairing");
  $("pairing-status").textContent = "Importing your existing mobile session…";
  await consumeTransferToken(token.trim());
}

async function consumeTransferToken(transferToken) {
  try {
    const response = await apiFetch("/admin/mobile/transfer-session/consume", {
      method: "POST",
      body: JSON.stringify({ transfer_token: transferToken }),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(payload.error || "Unable to import session");
    }
    if (!payload.device || typeof payload.device !== "object") {
      throw new Error("Transfer payload is invalid");
    }
    appState = {
      device: payload.device,
      activeSessionId: payload.activeSessionId || payload.device.current_session_id || `mobile-${payload.device.device_id}`,
      conversations: payload.conversations && typeof payload.conversations === "object" ? payload.conversations : {},
    };
    device = appState.device;
    saveAppState();
    window.history.replaceState({}, "", "/mobile");
    showScreen("chat");
    setupChat();
  } catch (error) {
    showScreen("error");
    configureDisconnectedState();
    $("error-message").textContent = error.message || "Unable to import session.";
  }
}

function showChatsSheet() {
  let overlay = document.querySelector(".chat-sheet-menu");
  if (!overlay) {
    overlay = document.createElement("div");
    overlay.className = "disconnect-menu chat-sheet-menu";
    overlay.innerHTML = `
      <div class="disconnect-sheet">
        <h3>Chats</h3>
        <p>Resume a previous conversation or start a new one.</p>
        <button class="action-btn" id="btn-chat-new">New Chat</button>
        <div id="chat-sheet-list" class="chat-sheet-list"></div>
        <button class="action-btn is-cancel" id="btn-chat-close">Close</button>
      </div>`;
    document.body.appendChild(overlay);

    $("btn-chat-new").addEventListener("click", () => {
      startNewChat();
      renderChatsSheet();
      overlay.classList.remove("is-visible");
    });
    $("btn-chat-close").addEventListener("click", () => {
      overlay.classList.remove("is-visible");
    });
    overlay.addEventListener("click", (event) => {
      if (event.target === overlay) overlay.classList.remove("is-visible");
    });
  }
  renderChatsSheet();
  overlay.classList.add("is-visible");
}

function renderChatsSheet() {
  const target = $("chat-sheet-list");
  if (!target) return;
  const items = listConversationSummaries();
  if (items.length === 0) {
    target.innerHTML = `<div class="chat-sheet-empty">No saved chats yet.</div>`;
    return;
  }

  const activeSessionId = getActiveSessionId();
  target.innerHTML = items.map((item) => `
    <div class="chat-sheet-item ${item.sessionId === activeSessionId ? "is-active" : ""}" data-chat-session-id="${escapeHtml(item.sessionId)}" role="button" tabindex="0">
      <div class="chat-sheet-item-head">
        <span class="chat-sheet-item-title">${escapeHtml(item.title)}</span>
        <span class="chat-sheet-item-time">${escapeHtml(item.updatedLabel)}</span>
      </div>
      <div class="chat-sheet-item-preview">${escapeHtml(item.preview)}</div>
      <div class="chat-sheet-item-meta">
        <span class="chat-sheet-item-badge">${escapeHtml(item.countLabel)}</span>
        <button class="chat-sheet-delete" type="button" data-chat-delete-session-id="${escapeHtml(item.sessionId)}">Delete</button>
      </div>
    </div>
  `).join("");

  target.querySelectorAll("[data-chat-session-id]").forEach((button) => {
    button.addEventListener("click", () => {
      const sessionId = button.dataset.chatSessionId || "";
      if (!sessionId) return;
      switchConversation(sessionId);
      document.querySelector(".chat-sheet-menu")?.classList.remove("is-visible");
    });
    button.addEventListener("keydown", (event) => {
      if (event.key !== "Enter" && event.key !== " ") return;
      event.preventDefault();
      const sessionId = button.dataset.chatSessionId || "";
      if (!sessionId) return;
      switchConversation(sessionId);
      document.querySelector(".chat-sheet-menu")?.classList.remove("is-visible");
    });
  });
  target.querySelectorAll("[data-chat-delete-session-id]").forEach((button) => {
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      const sessionId = button.dataset.chatDeleteSessionId || "";
      if (!sessionId) return;
      deleteConversation(sessionId);
      renderChatsSheet();
    });
  });
}

function listConversationSummaries() {
  const conversations = appState?.conversations || {};
  return Object.entries(conversations)
    .map(([sessionId, conversation]) => {
      const messages = Array.isArray(conversation?.messages) ? conversation.messages : [];
      const latest = messages[messages.length - 1] || null;
      const latestUser = [...messages].reverse().find((message) => message.role === "user");
      return {
        sessionId,
        title: latestUser?.text?.trim()?.slice(0, 36) || "New chat",
        preview: summarizeReplyText(latest || latestUser || { text: "", attachments: [] }) || "No messages yet",
        updatedLabel: latest?.timestamp ? formatMessageTime(latest.timestamp) : "Now",
        countLabel: `${messages.length} message${messages.length !== 1 ? "s" : ""}`,
        updatedAt: conversation?.updatedAt || latest?.timestamp || "",
      };
    })
    .sort((left, right) => String(right.updatedAt || "").localeCompare(String(left.updatedAt || "")));
}

function switchConversation(sessionId) {
  if (!device || !sessionId || !appState?.conversations?.[sessionId]) return;
  appState.activeSessionId = sessionId;
  device.current_session_id = sessionId;
  saveDevice(device);
  restoreActiveConversation();
  renderComposerMeta();
  refreshHomeState();
}

function deleteConversation(sessionId) {
  if (!appState?.conversations?.[sessionId]) return;
  delete appState.conversations[sessionId];
  if (appState.activeSessionId === sessionId) {
    const remaining = listConversationSummaries();
    const nextSessionId = remaining[0]?.sessionId || `mobile-${device?.device_id || "unknown"}-${crypto.randomUUID()}`;
    appState.activeSessionId = nextSessionId;
    if (device) {
      device.current_session_id = nextSessionId;
    }
    saveDevice(device);
    restoreActiveConversation();
    renderComposerMeta();
    refreshHomeState();
  } else {
    saveAppState();
  }
}

async function setupPushNotifications() {
  if (!device || !("serviceWorker" in navigator)) {
    pushRegistration = null;
    pushConfig = null;
    pushSubscribed = false;
    renderPushSettings();
    return;
  }
  try {
    pushRegistration = await navigator.serviceWorker.register("/mobile/sw.js", { scope: "/mobile/" });
  } catch (_) {
    pushRegistration = null;
  }
  await syncPushStatus();
}

async function syncPushStatus() {
  if (!device || !pushRegistration || !("PushManager" in window)) {
    pushConfig = null;
    pushSubscribed = false;
    renderPushSettings();
    return;
  }
  try {
    const response = await apiFetch("/admin/mobile/push/config");
    pushConfig = response.ok ? await response.json() : null;
  } catch (_) {
    pushConfig = null;
  }
  try {
    const subscription = await pushRegistration.pushManager.getSubscription();
    pushSubscribed = Boolean(subscription);
  } catch (_) {
    pushSubscribed = false;
  }
  renderPushSettings();
}

async function togglePushNotifications() {
  if (!device) return;
  if (pushSubscribed) {
    await disablePushNotifications();
    return;
  }
  await enablePushNotifications();
}

async function enablePushNotifications() {
  if (!device) return;
  if (!pushRegistration || !pushConfig?.supported || !pushConfig?.public_key || !("PushManager" in window)) {
    appendMessage({
      role: "agent",
      text: "Push notification is not available on this device or server yet.",
      msgType: "answer",
      messageId: "mobpush-" + crypto.randomUUID(),
      sessionId: getActiveSessionId(),
    });
    return;
  }
  if (isIOSBrowser() && !isStandaloneMode()) {
    appendMessage({
      role: "agent",
      text: "On iPhone, notifications require Add to Home Screen before enabling them.",
      msgType: "answer",
      messageId: "mobpush-" + crypto.randomUUID(),
      sessionId: getActiveSessionId(),
    });
    return;
  }
  let permission = Notification.permission;
  if (permission === "default") {
    permission = await Notification.requestPermission().catch(() => "denied");
  }
  if (permission !== "granted") {
    renderPushSettings();
    return;
  }
  try {
    const subscription = await pushRegistration.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: urlBase64ToUint8Array(pushConfig.public_key),
    });
    const response = await apiFetch("/admin/mobile/push/subscribe", {
      method: "POST",
      body: JSON.stringify({
        instance_id: device.instance_id,
        device_id: device.device_id,
        subscription: subscription.toJSON(),
      }),
    });
    if (!response.ok) {
      await subscription.unsubscribe().catch(() => {});
      throw new Error("Failed to register push subscription");
    }
    pushSubscribed = true;
  } catch (error) {
    appendMessage({
      role: "agent",
      text: `Unable to enable notifications: ${error.message || "Unknown error"}`,
      msgType: "answer",
      messageId: "mobpush-" + crypto.randomUUID(),
      sessionId: getActiveSessionId(),
    });
  }
  renderPushSettings();
}

async function disablePushNotifications() {
  if (!device || !pushRegistration) return;
  try {
    const subscription = await pushRegistration.pushManager.getSubscription();
    if (subscription) {
      await subscription.unsubscribe().catch(() => {});
    }
    await apiFetch("/admin/mobile/push/unsubscribe", {
      method: "POST",
      body: JSON.stringify({
        instance_id: device.instance_id,
        device_id: device.device_id,
      }),
    });
  } catch (_) {}
  pushSubscribed = false;
  renderPushSettings();
}

function renderPushSettings() {
  const note = $("push-settings-note");
  const button = $("btn-toggle-push");
  if (!note || !button) return;
  const browserSupported = "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;
  if (!browserSupported) {
    note.textContent = "Notifications are not supported by this browser.";
    button.disabled = true;
    button.textContent = "Notifications Unavailable";
    return;
  }
  if (isIOSBrowser() && !isStandaloneMode()) {
    note.textContent = "Add this app to your Home Screen first to receive notifications on iPhone.";
    button.disabled = false;
    button.textContent = "Enable Notifications";
    return;
  }
  if (!pushConfig?.supported) {
    note.textContent = "Server push is not configured yet.";
    button.disabled = true;
    button.textContent = "Notifications Unavailable";
    return;
  }
  if (pushSubscribed) {
    note.textContent = "Notifications are enabled for this device.";
    button.disabled = false;
    button.textContent = "Disable Notifications";
    return;
  }
  const permission = "Notification" in window ? Notification.permission : "unsupported";
  if (permission === "denied") {
    note.textContent = "Notification permission is blocked in the browser settings.";
  } else {
    note.textContent = "Enable push notifications to get replies while the screen is off.";
  }
  button.disabled = false;
  button.textContent = "Enable Notifications";
}

function applyRequestedSessionFromUrl() {
  if (!device) return;
  const params = new URLSearchParams(window.location.search);
  const sessionId = (params.get("session_id") || "").trim();
  if (sessionId) {
    appState.activeSessionId = sessionId;
    device.current_session_id = sessionId;
    saveDevice(device);
    window.history.replaceState({}, "", "/mobile");
  }
}

function isStandaloneMode() {
  return window.matchMedia?.("(display-mode: standalone)")?.matches || window.navigator.standalone === true;
}

function isIOSBrowser() {
  return /iPhone|iPad|iPod/i.test(navigator.userAgent || "");
}

function urlBase64ToUint8Array(base64String) {
  const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
  const rawData = atob(base64);
  return Uint8Array.from(rawData, (char) => char.charCodeAt(0));
}

function loadDevice() {
  return loadAppState().device;
}

function loadAppState() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    const parsed = raw ? JSON.parse(raw) : null;
    if (!parsed) {
      return { device: null, activeSessionId: null, conversations: {} };
    }
    if (parsed.device_id) {
      return {
        device: parsed,
        activeSessionId: parsed.current_session_id || `mobile-${parsed.device_id}`,
        conversations: {},
      };
    }
    return {
      device: parsed.device || null,
      activeSessionId: parsed.activeSessionId || parsed.device?.current_session_id || null,
      conversations: parsed.conversations && typeof parsed.conversations === "object" ? parsed.conversations : {},
    };
  } catch (_) {
    return { device: null, activeSessionId: null, conversations: {} };
  }
}

function saveDevice(value) {
  if (!appState) {
    appState = loadAppState();
  }
  appState.device = value;
  if (!appState.activeSessionId && value?.device_id) {
    appState.activeSessionId = value.current_session_id || `mobile-${value.device_id}`;
  }
  saveAppState();
}

function saveAppState() {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(appState || { device: null, activeSessionId: null, conversations: {} }));
  } catch (_) {}
}

function clearDevice() {
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = null;
  hasMessages = false;
  currentAgentGroup = null;
  currentReplyTarget = null;
  clearComposerState();
  messageStore.clear();
  seenMessageIds.clear();
  device = null;
  appState = { device: null, activeSessionId: null, conversations: {} };
  try {
    localStorage.removeItem(STORAGE_KEY);
  } catch (_) {}
}

function getActiveSessionId() {
  if (!device) return "mobile-unknown";
  if (!appState) {
    appState = loadAppState();
  }
  if (!appState.activeSessionId) {
    appState.activeSessionId = device.current_session_id || `mobile-${device.device_id}`;
    device.current_session_id = appState.activeSessionId;
    saveDevice(device);
  }
  return appState.activeSessionId;
}

function startNewChat() {
  if (!device) return;
  finalizeAgentGroup();
  currentReplyTarget = null;
  selectedAttachments = [];
  appState.activeSessionId = `mobile-${device.device_id}-${crypto.randomUUID()}`;
  device.current_session_id = appState.activeSessionId;
  saveDevice(device);
  resetRenderedConversation();
  renderComposerMeta();
  refreshHomeState();
  $("compose-input").value = "";
  updateSendButton();
  $("compose-input")?.focus();
}

function resetRenderedConversation() {
  const messagesElement = $("messages");
  if (messagesElement) {
    messagesElement.innerHTML = "";
  }
  messageStore.clear();
  seenMessageIds.clear();
  hasMessages = false;
  currentAgentGroup = null;
}

function restoreActiveConversation() {
  if (!appState || !device) return;
  const sessionId = getActiveSessionId();
  const conversation = appState.conversations?.[sessionId];
  resetRenderedConversation();
  if (!conversation || !Array.isArray(conversation.messages)) {
    return;
  }
  conversation.messages.forEach((message) => {
    appendMessageInternal(message, { skipPersist: true });
  });
}

function persistMessage(message) {
  if (!appState || !device || !message?.sessionId) return;
  const sessionId = message.sessionId;
  const existing = appState.conversations?.[sessionId]?.messages || [];
  const nextMessages = [...existing.filter((item) => item.messageId !== message.messageId), sanitizeMessageForStorage(message)];
  nextMessages.sort((a, b) => String(a.timestamp || "").localeCompare(String(b.timestamp || "")));
  appState.conversations = appState.conversations || {};
  appState.conversations[sessionId] = {
    messages: nextMessages.slice(-MAX_STORED_MESSAGES),
    updatedAt: new Date().toISOString(),
  };
  appState.activeSessionId = sessionId;
  device.current_session_id = sessionId;
  saveDevice(device);
}

function sanitizeMessageForStorage(message) {
  return {
    role: message.role,
    text: message.text,
    msgType: message.msgType,
    messageId: message.messageId,
    sessionId: message.sessionId,
    replyTo: message.replyTo,
    threadRootId: message.threadRootId,
    timestamp: message.timestamp,
    attachments: (message.attachments || []).map((attachment) => ({
      name: attachment.name || attachment.fileName || "attachment",
      fileName: attachment.fileName || attachment.name || "attachment",
      mimeType: attachment.mimeType || "application/octet-stream",
      size: Number(attachment.size || 0),
      kind: attachment.kind || "file",
      url: attachment.url || "",
      previewUrl: attachment.url || attachment.previewUrl || "",
      duration: Number(attachment.duration || 0) || 0,
    })),
  };
}

function apiFetch(url, options = {}) {
  const headers = {
    "Content-Type": "application/json",
    ...(options.headers || {}),
  };
  if (device?.device_token) {
    headers["X-Mobile-Token"] = device.device_token;
  }
  return fetch(url, {
    ...options,
    headers,
  });
}

function renderMarkdown(text) {
  const blocks = tokenizeMarkdown(text);
  return blocks.map(renderMarkdownBlock).join("");
}

function tokenizeMarkdown(text) {
  const lines = String(text || "").replace(/\r\n/g, "\n").split("\n");
  const blocks = [];
  let paragraph = [];
  let listItems = [];
  let quoteLines = [];
  let codeLines = [];
  let codeFence = false;
  let tableLines = [];

  const flushParagraph = () => {
    if (paragraph.length) {
      blocks.push({ type: "paragraph", text: paragraph.join(" ") });
      paragraph = [];
    }
  };
  const flushList = () => {
    if (listItems.length) {
      blocks.push({ type: "list", items: [...listItems] });
      listItems = [];
    }
  };
  const flushQuote = () => {
    if (quoteLines.length) {
      blocks.push({ type: "quote", text: quoteLines.join("\n") });
      quoteLines = [];
    }
  };
  const flushTable = () => {
    if (tableLines.length) {
      blocks.push({ type: "table", lines: [...tableLines] });
      tableLines = [];
    }
  };

  for (const line of lines) {
    const trimmed = line.trim();

    if (trimmed.startsWith("```")) {
      flushParagraph();
      flushList();
      flushQuote();
      flushTable();
      if (codeFence) {
        blocks.push({ type: "code", text: codeLines.join("\n") });
        codeLines = [];
        codeFence = false;
      } else {
        codeFence = true;
      }
      continue;
    }

    if (codeFence) {
      codeLines.push(line);
      continue;
    }

    if (/^\|.+\|$/.test(trimmed)) {
      flushParagraph();
      flushList();
      flushQuote();
      tableLines.push(trimmed);
      continue;
    }
    flushTable();

    const headingMatch = line.match(/^(#{1,6})\s+(.+)$/);
    if (headingMatch) {
      flushParagraph();
      flushList();
      flushQuote();
      blocks.push({ type: "heading", level: headingMatch[1].length, text: headingMatch[2] });
      continue;
    }

    const listMatch = line.match(/^\s*[-*]\s+(.+)$/);
    if (listMatch) {
      flushParagraph();
      flushQuote();
      listItems.push(listMatch[1]);
      continue;
    }
    flushList();

    const quoteMatch = line.match(/^\s*>\s?(.*)$/);
    if (quoteMatch) {
      flushParagraph();
      quoteLines.push(quoteMatch[1]);
      continue;
    }
    flushQuote();

    if (!trimmed) {
      flushParagraph();
      blocks.push({ type: "spacer" });
      continue;
    }

    paragraph.push(trimmed);
  }

  flushParagraph();
  flushList();
  flushQuote();
  flushTable();
  if (codeLines.length) blocks.push({ type: "code", text: codeLines.join("\n") });
  return blocks;
}

function renderMarkdownBlock(block) {
  if (block.type === "heading") {
    return `<h${Math.min(block.level, 4)} class="msg-heading">${renderInlineMarkdown(block.text)}</h${Math.min(block.level, 4)}>`;
  }
  if (block.type === "quote") {
    return `<blockquote class="msg-quote">${renderInlineMarkdown(block.text).replace(/\n/g, "<br>")}</blockquote>`;
  }
  if (block.type === "list") {
    return `<ul class="msg-list">${block.items.map((item) => `<li>${renderInlineMarkdown(item)}</li>`).join("")}</ul>`;
  }
  if (block.type === "code") {
    return `<pre class="msg-code"><code>${escapeHtml(block.text)}</code></pre>`;
  }
  if (block.type === "table") {
    return buildHtmlTable(block.lines);
  }
  if (block.type === "spacer") {
    return '<div class="msg-spacer"></div>';
  }
  return `<p class="msg-paragraph">${renderInlineMarkdown(block.text)}</p>`;
}

function renderInlineMarkdown(text) {
  let value = escapeHtml(text || "");
  value = value.replace(/`([^`]+)`/g, "<code>$1</code>");
  value = value.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  value = value.replace(/(^|[\s(])_([^_]+)_/g, "$1<em>$2</em>");
  value = value.replace(/\*([^*]+)\*/g, "<em>$1</em>");
  value = value.replace(/~~([^~]+)~~/g, "<del>$1</del>");
  value = value.replace(/\[([^\]]+)\]\((https?:\/\/[^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
  return value;
}

function buildHtmlTable(lines) {
  const dataLines = [];
  let headerLine = null;

  for (const line of lines) {
    const cells = parsePipeLine(line);
    const isSeparator = cells.every((cell) => /^[-:]+$/.test(cell.trim()));
    if (isSeparator) continue;
    if (!headerLine) {
      headerLine = cells;
    } else {
      dataLines.push(cells);
    }
  }

  if (!headerLine) return "";

  let html = '<div class="msg-table-wrap"><table class="msg-table"><thead><tr>';
  for (const cell of headerLine) {
    html += `<th>${renderInlineMarkdown(cell.trim())}</th>`;
  }
  html += "</tr></thead>";

  if (dataLines.length > 0) {
    html += "<tbody>";
    for (const row of dataLines) {
      html += "<tr>";
      for (let index = 0; index < headerLine.length; index += 1) {
        html += `<td>${renderInlineMarkdown((row[index] || "").trim())}</td>`;
      }
      html += "</tr>";
    }
    html += "</tbody>";
  }

  html += "</table></div>";
  return html;
}

function parsePipeLine(line) {
  let inner = line.trim();
  if (inner.startsWith("|")) inner = inner.slice(1);
  if (inner.endsWith("|")) inner = inner.slice(0, -1);
  return inner.split("|");
}

function escapeHtml(value) {
  const element = document.createElement("span");
  element.textContent = value || "";
  return element.innerHTML;
}

function detectDeviceLabel() {
  const ua = navigator.userAgent;
  if (/iPhone/.test(ua)) return "iPhone";
  if (/iPad/.test(ua)) return "iPad";
  if (/Android/.test(ua)) {
    const match = ua.match(/;\s*([^;)]+)\s*Build/);
    return match ? match[1].trim() : "Android";
  }
  return "Mobile Browser";
}

function formatMessageTime(value) {
  const date = value ? new Date(value) : new Date();
  return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function attachmentKind(mimeType) {
  const value = String(mimeType || "").toLowerCase();
  if (value.startsWith("image/")) return "image";
  if (value.startsWith("audio/")) return "audio";
  if (value.startsWith("video/")) return "video";
  return "file";
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
