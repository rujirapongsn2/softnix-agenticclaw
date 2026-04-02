(function (global) {
  "use strict";

  function normalizeChatEvent(event, fallbackDeviceId) {
    if (!event || typeof event !== "object") return null;
    const role = String(event.role || "") === "user" ? "user" : "agent";
    const sessionId = String(event.session_id || event.sessionId || "").trim()
      || `mobile-${fallbackDeviceId || "unknown"}`;
    const messageId = String(event.message_id || event.messageId || "").trim()
      || `msg-${Math.random().toString(36).slice(2, 10)}`;
    return {
      eventId: String(event.event_id || event.eventId || "").trim() || null,
      role,
      text: String(event.text || event.content || ""),
      msgType: String(event.type || event.msgType || (role === "agent" ? "answer" : "message")),
      messageId,
      sessionId,
      senderId: String(event.device_id || event.sender_id || event.senderId || fallbackDeviceId || "").trim() || null,
      replyTo: String(event.reply_to || event.replyTo || "").trim() || null,
      threadRootId: String(event.thread_root_id || event.threadRootId || "").trim() || null,
      attachments: Array.isArray(event.attachments) ? event.attachments : [],
      timestamp: String(event.timestamp || new Date().toISOString()),
      direction: String(event.direction || ""),
      legacy: !!event.legacy,
    };
  }

  function compareMessages(a, b) {
    const ta = String(a?.timestamp || "");
    const tb = String(b?.timestamp || "");
    if (ta < tb) return -1;
    if (ta > tb) return 1;
    const ma = String(a?.messageId || "");
    const mb = String(b?.messageId || "");
    return ma.localeCompare(mb);
  }

  function buildConversationState(events, fallbackDeviceId) {
    const conversations = {};
    let lastEventId = null;
    (Array.isArray(events) ? events : []).forEach((item) => {
      const message = normalizeChatEvent(item, fallbackDeviceId);
      if (!message) return;
      const sessionId = message.sessionId;
      const bucket = conversations[sessionId] || { messages: [], updatedAt: "" };
      const existingIndex = bucket.messages.findIndex((entry) => entry.messageId === message.messageId);
      if (existingIndex >= 0) {
        bucket.messages[existingIndex] = message;
      } else {
        bucket.messages.push(message);
      }
      bucket.messages.sort(compareMessages);
      if (!bucket.updatedAt || String(message.timestamp || "") >= String(bucket.updatedAt || "")) {
        bucket.updatedAt = message.timestamp;
      }
      conversations[sessionId] = bucket;
      if (message.eventId) {
        lastEventId = message.eventId;
      }
    });
    return { conversations, lastEventId };
  }

  function summarizeConversations(conversations) {
    return Object.entries(conversations || {})
      .map(([sessionId, conversation]) => {
        const messages = Array.isArray(conversation?.messages) ? conversation.messages : [];
        const lastMessage = messages[messages.length - 1] || null;
        const latestUser = [...messages].reverse().find((entry) => String(entry?.role || "") === "user") || null;
        const attachmentCount = Array.isArray(lastMessage?.attachments) ? lastMessage.attachments.length : 0;
        const rawTitle = stripMarkdownDecorations(String(latestUser?.text || lastMessage?.text || "")).trim().replace(/\s+/g, " ");
        const previewText = stripMarkdownDecorations(String(lastMessage?.text || "")).trim().replace(/\s+/g, " ");
        const title = compactConversationText(rawTitle || previewText || (attachmentCount > 0 ? `[Attachment${attachmentCount > 1 ? "s" : ""}]` : "Conversation"), 40);
        const preview = compactConversationText(previewText || (attachmentCount > 0 ? `[Attachment${attachmentCount > 1 ? "s" : ""}]` : ""), 64);
        return {
          session_id: sessionId,
          updated_at: String(conversation?.updatedAt || lastMessage?.timestamp || ""),
          message_count: messages.length,
          title,
          preview,
        };
      })
      .sort((a, b) => String(b.updated_at || "").localeCompare(String(a.updated_at || "")));
  }

  function pickActiveSessionId(conversations, preferredSessionId, fallbackDeviceId) {
    const summaries = summarizeConversations(conversations);
    if (preferredSessionId && conversations && conversations[preferredSessionId]) {
      return preferredSessionId;
    }
    if (summaries[0]?.session_id) {
      return summaries[0].session_id;
    }
    return `mobile-${fallbackDeviceId || "unknown"}`;
  }

  function parseWebChatLoginTicket(value) {
    const raw = String(value || "").trim();
    if (!raw) return "";
    if (/^wclogin-[A-Za-z0-9_-]+$/.test(raw)) return raw;
    try {
      const parsed = new URL(raw);
      return String(parsed.searchParams.get("ticket") || parsed.searchParams.get("login_ticket") || "").trim();
    } catch (_) {
      const match = raw.match(/(?:ticket|login_ticket)=([A-Za-z0-9_-]+)/);
      return match ? String(match[1] || "").trim() : "";
    }
  }

  function isToolProgressMessage(message) {
    return String(message?.role || "") === "agent"
      && (String(message?.msgType || "") === "tool" || String(message?.msgType || "") === "progress");
  }

  function stripMarkdownDecorations(text) {
    const value = String(text || "")
      .replace(/\r\n/g, "\n")
      .replace(/\r/g, "\n")
      .replace(/^#{1,6}\s+/gm, "")
      .replace(/^\s*[-*+]\s+/gm, "• ")
      .replace(/^\s*\d+\.\s+/gm, "")
      .replace(/\*\*(.*?)\*\*/g, "$1")
      .replace(/\*(.*?)\*/g, "$1")
      .replace(/`([^`]+)`/g, "$1")
      .replace(/!\[([^\]]*)\]\(([^)]+)\)/g, "$1")
      .replace(/\[([^\]]+)\]\(([^)]+)\)/g, "$1")
      .replace(/```[\s\S]*?```/g, (match) => match.replace(/```/g, ""))
      .replace(/\n{3,}/g, "\n\n");
    return value;
  }

  function compactConversationText(value, limit) {
    const clean = String(value || "").trim().replace(/\s+/g, " ");
    if (!clean) return "";
    if (clean.length <= limit) return clean;
    return `${clean.slice(0, Math.max(0, limit - 1)).trimEnd()}…`;
  }

  global.SoftnixChatShared = {
    normalizeChatEvent,
    buildConversationState,
    summarizeConversations,
    pickActiveSessionId,
    parseWebChatLoginTicket,
    isToolProgressMessage,
    stripMarkdownDecorations,
  };
})(window);
