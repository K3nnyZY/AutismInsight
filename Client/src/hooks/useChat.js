import { useEffect, useRef, useState } from "react";
import { api } from "../api";

const SESSION_KEY = "autism-insight-conversation";
const HISTORY_KEY = "autism-insight-conversations";

function read(key, fallback) {
  try { return sessionStorage.getItem(key) || fallback; } catch { return fallback; }
}

function readConversations() {
  try {
    const value = JSON.parse(read(HISTORY_KEY, "[]"));
    return Array.isArray(value) ? value.filter((id) => typeof id === "string").slice(0, 20) : [];
  } catch { return []; }
}

function remember(id, ids) {
  try {
    if (id) sessionStorage.setItem(SESSION_KEY, id);
    else sessionStorage.removeItem(SESSION_KEY);
    if (ids.length) sessionStorage.setItem(HISTORY_KEY, JSON.stringify(ids.slice(0, 20)));
    else sessionStorage.removeItem(HISTORY_KEY);
  } catch { /* Session storage is optional; message contents are never stored here. */ }
}

export default function useChat() {
  const [conversationId, setConversationId] = useState(() => read(SESSION_KEY, null));
  const [conversationIds, setConversationIds] = useState(readConversations);
  const [titles, setTitles] = useState({});
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [pending, setPending] = useState(false);
  const [loadingHistory, setLoadingHistory] = useState(Boolean(conversationId));
  const [error, setError] = useState("");
  const [retryRequest, setRetryRequest] = useState(null);
  const [consent, setConsent] = useState(false);
  const [historyFailed, setHistoryFailed] = useState(false);
  const [historyRevision, setHistoryRevision] = useState(0);
  const busy = useRef(false);
  const skipHistory = useRef(null);

  useEffect(() => { remember(conversationId, conversationIds); }, [conversationId, conversationIds]);

  useEffect(() => {
    if (!conversationId) { setLoadingHistory(false); return; }
    if (skipHistory.current === conversationId) {
      skipHistory.current = null;
      return;
    }
    const controller = new AbortController();
    setLoadingHistory(true);
    setHistoryFailed(false);
    api(`/conversations/${conversationId}`, { signal: controller.signal })
      .then((data) => {
        if (controller.signal.aborted) return;
        setMessages(data);
        setConsent(true);
        setConversationIds((ids) => ids.includes(conversationId) ? ids : [conversationId, ...ids].slice(0, 20));
        const title = data.find((message) => message.role === "user")?.content;
        if (title) setTitles((current) => ({ ...current, [conversationId]: title }));
      })
      .catch((err) => {
        if (controller.signal.aborted) return;
        if ([401, 404, 422].includes(err.status)) {
          setConversationIds((ids) => ids.filter((id) => id !== conversationId));
          setConversationId(null);
          setConsent(false);
        } else setHistoryFailed(true);
        setError(err.message);
      })
      .finally(() => { if (!controller.signal.aborted) setLoadingHistory(false); });
    return () => controller.abort();
  }, [conversationId, historyRevision]);

  const submit = async ({ audience, style }, retry = null) => {
    if (busy.current || loadingHistory || historyFailed ||
      (!retry && (!input.trim() || !consent || retryRequest))) return;
    busy.current = true;
    setPending(true);
    setError("");
    let payload = retry;
    try {
      let id = conversationId;
      if (!id) {
        const created = await api("/conversations", { method: "POST", body: { consent: true } });
        id = created.conversation_id;
        skipHistory.current = id;
        setConversationId(id);
        setConversationIds((ids) => [id, ...ids.filter((item) => item !== id)].slice(0, 20));
      }
      if (!payload) {
        payload = { conversation_id: id, request_id: crypto.randomUUID(),
          message: input.trim(), audience, style };
        setTitles((current) => ({ ...current, [id]: current[id] || payload.message }));
        setMessages((current) => [...current, {
          id: payload.request_id, role: "user", content: payload.message,
        }]);
        setInput("");
      }
      setRetryRequest(payload);
      const result = await api("/chat", { method: "POST", body: payload });
      setMessages((current) => [...current, {
        id: result.message_id, role: "assistant", content: result.answer, response: result,
      }]);
      setRetryRequest(null);
    } catch (err) {
      setError(err.message);
      if ([401, 404].includes(err.status)) {
        const unavailableId = payload?.conversation_id || conversationId;
        setConversationIds((ids) => ids.filter((id) => id !== unavailableId));
        setConversationId(null);
        setConsent(false);
        setRetryRequest(null);
      }
    } finally { busy.current = false; setPending(false); }
  };

  const newConversation = () => {
    if (busy.current || loadingHistory) return;
    setConversationId(null);
    setMessages([]);
    setRetryRequest(null);
    setInput("");
    setError("");
    setHistoryFailed(false);
  };

  const selectConversation = (id) => {
    if (busy.current || loadingHistory || id === conversationId) return;
    setMessages([]);
    setRetryRequest(null);
    setInput("");
    setError("");
    setConversationId(id);
  };

  const removeConversation = async () => {
    if (busy.current || loadingHistory || !conversationId) return;
    busy.current = true;
    setPending(true);
    setError("");
    try {
      await api(`/conversations/${conversationId}`, { method: "DELETE" });
      setConversationIds((ids) => ids.filter((id) => id !== conversationId));
      setTitles((current) => {
        const next = { ...current };
        delete next[conversationId];
        return next;
      });
      setConversationId(null);
      setMessages([]);
      setRetryRequest(null);
      setInput("");
      setConsent(false);
      setHistoryFailed(false);
    } catch (err) { setError(err.message); }
    finally { busy.current = false; setPending(false); }
  };

  const reloadHistory = () => {
    setError("");
    setHistoryRevision((value) => value + 1);
  };

  return { conversationId, conversationIds, titles, messages, input, setInput, pending,
    loadingHistory, error, retryRequest, consent, setConsent, historyFailed, reloadHistory,
    submit, newConversation, selectConversation, removeConversation };
}
