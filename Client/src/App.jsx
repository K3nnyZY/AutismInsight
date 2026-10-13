import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api";
import ChatMessage from "./components/ChatMessage";
import Icon from "./components/Icon";
import useChat from "./hooks/useChat";
import "./App.css";
import "./styles/layout.css";

const TOPICS = [
  { id: "start", label: "Para empezar" }, { id: "communication", label: "Comunicación" },
  { id: "education", label: "Educación" }, { id: "daily", label: "Vida cotidiana" },
];
const SUGGESTIONS = {
  start: [
    { icon: "sparkles", tone: "violet", title: "Comprender el autismo", description: "Un punto de partida, con palabras claras.", question: "¿Qué es el autismo?" },
    { icon: "chat", tone: "blue", title: "Comunicación cotidiana", description: "Exploremos cómo entendernos mejor.", question: "¿Qué dice el documento sobre la comunicación?" },
    { icon: "book", tone: "peach", title: "Apoyo en el aprendizaje", description: "Preguntas sobre el entorno educativo.", question: "¿Qué dice el documento sobre el aprendizaje y la educación?" },
    { icon: "leaf", tone: "mint", title: "Explorar las fuentes", description: "Conoce la información que podemos consultar.", question: "¿Qué información tienen tus fuentes?" },
  ],
  communication: [
    { icon: "chat", tone: "blue", title: "Comunicación verbal", description: "Una pregunta para conversar con claridad.", question: "¿Qué información hay sobre la comunicación verbal?" },
    { icon: "heart", tone: "violet", title: "Expresar emociones", description: "Explora lo que dicen los documentos.", question: "¿Qué dice tu material sobre expresar las emociones?" },
  ],
  education: [
    { icon: "book", tone: "peach", title: "El entorno educativo", description: "Descubre qué recoge el material disponible.", question: "¿Qué dice el documento sobre el aprendizaje y la educación?" },
    { icon: "leaf", tone: "mint", title: "Metodologías de aprendizaje", description: "Compara las descripciones de las fuentes.", question: "¿Qué metodologías educativas aparecen en el documento de prueba?" },
  ],
  daily: [
    { icon: "people", tone: "violet", title: "Relaciones sociales", description: "Un espacio para explorar tus preguntas.", question: "¿Qué información hay sobre las relaciones sociales?" },
    { icon: "leaf", tone: "mint", title: "Cambios y rutinas", description: "Consulta qué información está disponible.", question: "¿Qué dicen tus fuentes sobre los cambios de rutina?" },
  ],
};

function readPreferences() {
  try {
    const saved = JSON.parse(localStorage.getItem("autism-insight-preferences") || "{}");
    return { audience: ["person", "family", "professional"].includes(saved.audience) ? saved.audience : "person",
      style: ["simple", "standard", "detailed"].includes(saved.style) ? saved.style : "standard",
      largeText: saved.largeText === true, contrast: saved.contrast === true };
  } catch { return { audience: "person", style: "standard", largeText: false, contrast: false }; }
}

export default function App() {
  const chat = useChat();
  const [preferences, setPreferences] = useState(readPreferences);
  const [topic, setTopic] = useState("start");
  const [ready, setReady] = useState({ status: "checking" });
  const [speaking, setSpeaking] = useState(null);
  const [copied, setCopied] = useState(null);
  const [actionNotice, setActionNotice] = useState("");
  const [menuOpen, setMenuOpen] = useState(false);
  const [mobile, setMobile] = useState(() => window.matchMedia("(max-width: 900px)").matches);
  const [showLatest, setShowLatest] = useState(false);
  const composer = useRef(null);
  const scrollArea = useRef(null);
  const following = useRef(true);
  const sidebar = useRef(null);
  const menuButton = useRef(null);
  const menuClose = useRef(null);
  const deleteDialog = useRef(null);
  const infoDialog = useRef(null);
  const readyController = useRef(null);
  const copyTimer = useRef(null);
  const previousPending = useRef(false);
  const disabled = chat.pending || chat.loadingHistory;
  const canSend = !disabled && !chat.historyFailed && !chat.retryRequest && chat.consent && Boolean(chat.input.trim());

  const checkReady = useCallback(async () => {
    readyController.current?.abort();
    const controller = new AbortController();
    readyController.current = controller;
    setReady({ status: "checking" });
    try {
      const data = await api("/health/ready", { signal: controller.signal });
      if (!controller.signal.aborted) setReady(data);
    } catch (error) {
      if (!controller.signal.aborted) setReady({ status: "unavailable", detail: error.message });
    }
  }, []);

  useEffect(() => { checkReady(); return () => readyController.current?.abort(); }, [checkReady]);
  useEffect(() => {
    try { localStorage.setItem("autism-insight-preferences", JSON.stringify(preferences)); } catch { /* Optional. */ }
  }, [preferences]);
  useEffect(() => {
    const query = window.matchMedia("(max-width: 900px)");
    const change = () => { setMobile(query.matches); if (!query.matches) setMenuOpen(false); };
    query.addEventListener("change", change);
    return () => query.removeEventListener("change", change);
  }, []);

  useEffect(() => {
    if (!menuOpen || !mobile) return;
    const previouslyFocused = document.activeElement;
    menuClose.current?.focus();
    const trapFocus = (event) => {
      if (event.key === "Escape") { setMenuOpen(false); return; }
      if (event.key !== "Tab") return;
      const items = [...sidebar.current.querySelectorAll("button:not(:disabled), select, a[href]")];
      const first = items[0], last = items[items.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    };
    document.addEventListener("keydown", trapFocus);
    return () => { document.removeEventListener("keydown", trapFocus); previouslyFocused?.focus(); };
  }, [menuOpen, mobile]);

  useEffect(() => {
    if (!chat.messages.length) { following.current = true; setShowLatest(false); scrollArea.current?.scrollTo(0, 0); return; }
    if (following.current || chat.messages.at(-1)?.role === "user") {
      scrollArea.current?.scrollTo({ top: scrollArea.current.scrollHeight, behavior: "instant" });
    } else setShowLatest(true);
  }, [chat.messages, chat.pending]);
  useEffect(() => {
    if (!chat.pending && previousPending.current) composer.current?.focus({ preventScroll: true });
    previousPending.current = chat.pending;
  }, [chat.pending]);
  useEffect(() => {
    if (!composer.current) return;
    composer.current.style.height = "auto";
    composer.current.style.height = `${Math.min(composer.current.scrollHeight, 160)}px`;
  }, [chat.input]);
  useEffect(() => {
    window.speechSynthesis?.cancel();
    setSpeaking(null);
  }, [chat.conversationId]);
  useEffect(() => () => { window.speechSynthesis?.cancel(); clearTimeout(copyTimer.current); }, []);

  const updatePreference = (key, value) => setPreferences((current) => ({ ...current, [key]: value }));
  const chooseSuggestion = (question) => { chat.setInput(question); composer.current?.focus({ preventScroll: true }); };
  const submit = (event) => { event?.preventDefault(); if (canSend) chat.submit(preferences); };
  const newChat = () => { chat.newConversation(); setMenuOpen(false); following.current = true; composer.current?.focus(); };

  const speak = (id, text) => {
    window.speechSynthesis.cancel();
    if (speaking === id) { setSpeaking(null); return; }
    const utterance = new SpeechSynthesisUtterance(text.replace(/\[\d+\]/g, "").replace(/[*_`#]/g, ""));
    utterance.lang = "es-ES"; utterance.rate = 0.9;
    utterance.onend = () => setSpeaking((current) => current === id ? null : current);
    utterance.onerror = () => setSpeaking((current) => current === id ? null : current);
    setSpeaking(id); window.speechSynthesis.speak(utterance);
  };
  const copy = async (id, text) => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(id); setActionNotice("Respuesta copiada.");
      clearTimeout(copyTimer.current);
      copyTimer.current = setTimeout(() => { setCopied(null); setActionNotice(""); }, 2500);
    } catch { setActionNotice("Tu navegador no permite copiar automáticamente. Puedes seleccionar el texto."); }
  };

  return <div className="app-shell" data-reading={preferences.largeText ? "large" : "standard"}
    data-contrast={preferences.contrast ? "high" : "standard"}>
    <a className="skip-link" href="#question">Ir a escribir una pregunta</a>
    {mobile && menuOpen && <div className="menu-backdrop" onClick={() => setMenuOpen(false)} aria-hidden="true" />}
    <aside ref={sidebar} id="sidebar" className={`sidebar ${menuOpen ? "is-open" : ""}`}
      role={mobile && menuOpen ? "dialog" : undefined} aria-modal={mobile && menuOpen ? true : undefined}
      aria-label="Panel de conversación" inert={mobile && !menuOpen ? "" : undefined}>
      <div className="sidebar-brand">
        <img src="/Logo.png" width="40" height="40" alt="" />
        <div><span>Autism<span className="brand-accent">Insight</span></span><small>Comprender. Conectar. Acompañar.</small></div>
        <button ref={menuClose} className="icon-button menu-close" aria-label="Cerrar menú" onClick={() => setMenuOpen(false)}><Icon name="close" /></button>
      </div>
      <button className="new-chat" onClick={newChat} disabled={disabled}><Icon name="plus" />Nueva conversación</button>
      <nav className="conversation-nav" aria-label="Tus conversaciones">
        <p className="sidebar-label">TU ESPACIO</p>
        {!chat.conversationIds.length && <div className="empty-conversations"><Icon name="chat" /><p>Tu próxima conversación<br />empieza aquí.</p></div>}
        {chat.conversationIds.map((id, index) => <button key={id}
          className={`conversation-link ${id === chat.conversationId ? "active" : ""}`}
          aria-current={id === chat.conversationId ? "page" : undefined} disabled={disabled}
          title={chat.titles[id] || `Conversación ${chat.conversationIds.length - index}`}
          onClick={() => { chat.selectConversation(id); setMenuOpen(false); following.current = true; }}>
          <Icon name="chat" /><span>{chat.titles[id] || `Conversación ${chat.conversationIds.length - index}`}</span>
          {id === chat.conversationId && <span className="active-conversation-dot" />}
        </button>)}
      </nav>
      <div className="preferences">
        <p className="sidebar-label"><Icon name="settings" />A TU MANERA</p>
        <label>Busco orientación para<select value={preferences.audience}
          onChange={(e) => updatePreference("audience", e.target.value)} disabled={disabled}>
          <option value="person">Mí</option><option value="family">Mi familia</option>
          <option value="professional">Mi trabajo profesional</option>
        </select></label>
        <label>Forma de explicar<select value={preferences.style}
          onChange={(e) => updatePreference("style", e.target.value)} disabled={disabled}>
          <option value="simple">Lenguaje sencillo</option><option value="standard">Explicación habitual</option>
          <option value="detailed">Más detalle</option>
        </select></label>
        <button className="preference-toggle" aria-pressed={preferences.largeText}
          onClick={() => updatePreference("largeText", !preferences.largeText)}>
          <span className="text-size-icon" aria-hidden="true">Aa</span>Texto más grande<span className={`toggle ${preferences.largeText ? "on" : ""}`} />
        </button>
        <button className="preference-toggle" aria-pressed={preferences.contrast}
          onClick={() => updatePreference("contrast", !preferences.contrast)}>
          <span className="contrast-icon" aria-hidden="true" />Mayor contraste<span className={`toggle ${preferences.contrast ? "on" : ""}`} />
        </button>
      </div>
      <div className="sidebar-bottom">
        <div className="sidebar-note"><Icon name="heart" /><p>Cada experiencia es única.<br /><strong>Este espacio se adapta a ti.</strong></p></div>
        <button className="privacy-button" onClick={() => { setMenuOpen(false); infoDialog.current?.showModal(); }}><Icon name="shield" />Información y privacidad<Icon name="chevron" /></button>
      </div>
    </aside>

    <div className="workspace" inert={mobile && menuOpen ? "" : undefined}>
      <header className="workspace-header">
        <div className="header-title"><button ref={menuButton} className="icon-button menu-button"
          aria-label="Abrir menú" aria-controls="sidebar" aria-expanded={menuOpen}
          onClick={() => setMenuOpen(true)}><Icon name="menu" /></button>
          <div><h1>Tu espacio de conversación</h1><span>Preguntas pequeñas. Nuevas perspectivas.</span></div>
        </div>
        <div className="header-actions"><span className={`connection-status ${ready.status}`}>
          <span />{ready.status === "ready" ? "Disponible" : ready.status === "checking" ? "Conectando" : "No disponible"}
        </span><button className="icon-button delete-button" aria-label="Eliminar conversación"
          title="Eliminar conversación" disabled={disabled || !chat.conversationId}
          onClick={() => deleteDialog.current?.showModal()}><Icon name="trash" /></button></div>
      </header>

      <main id="main-content" className="workspace-main">
        {(ready.status === "unavailable" || ready.demo_sources) && <div className="service-notice" role="status">
          <Icon name="info" /><span>{ready.status === "unavailable" ? ready.detail : "Estás en modo de prueba. Las fuentes se identifican en cada respuesta."}</span>
          {ready.status === "unavailable" && <button className="text-button" onClick={checkReady}>Comprobar conexión<Icon name="refresh" /></button>}
        </div>}
        <div ref={scrollArea} className="conversation-scroll" onScroll={(event) => {
          const area = event.currentTarget;
          following.current = area.scrollHeight - area.scrollTop - area.clientHeight < 150;
          setShowLatest(!following.current);
        }}>
          <div className="conversation-content">
            {!chat.messages.length && !chat.loadingHistory && <div className="welcome">
              <div className="welcome-symbol" aria-hidden="true"><span /><img src="/Logo.png" alt="" /><span /></div>
              <span className="eyebrow"><Icon name="leaf" />UN ESPACIO A TU RITMO</span>
              <h2>¿Qué te gustaría<br /><span>comprender hoy?</span></h2>
              <p className="welcome-description">Hablemos de autismo, comunicación y apoyo cotidiano.<br className="desktop-break" /> Con calma, palabras claras y fuentes que puedes consultar.</p>
              <div className="topic-tabs" role="group" aria-label="Temas para comenzar">
                {TOPICS.map((item) => <button key={item.id} aria-pressed={topic === item.id}
                  className={topic === item.id ? "selected" : ""} onClick={() => setTopic(item.id)}>{item.label}</button>)}
              </div>
              <div className="suggestions">{SUGGESTIONS[topic].map((item) => <button key={item.question}
                className="suggestion-card" aria-label={item.question} disabled={disabled || Boolean(chat.retryRequest)}
                onClick={() => chooseSuggestion(item.question)}>
                <span className={`suggestion-icon ${item.tone}`}><Icon name={item.icon} /></span>
                <span className="suggestion-text"><strong>{item.title}</strong><span>{item.description}</span></span>
                <Icon name="arrow" className="suggestion-arrow" />
              </button>)}</div>
              <div className="welcome-promises"><span><Icon name="heart" />Sin prisa, sin juicios</span><span><Icon name="book" />Fuentes consultables</span></div>
            </div>}
            <section className="chat" role="log" aria-label="Conversación" aria-live="polite"
              aria-relevant="additions" aria-busy={chat.pending || chat.loadingHistory}>
              {chat.loadingHistory && <p className="pending" role="status"><Icon name="refresh" />Recuperando tu conversación…</p>}
              {chat.messages.map((message) => <ChatMessage key={message.id} message={message}
                speaking={speaking} onSpeak={speak} copied={copied} onCopy={copy} />)}
              {chat.pending && <div className="pending-message" role="status"><img src="/Logo.png" alt="" />
                <div><span className="pending-dots" aria-hidden="true"><i /><i /><i /></span>
                  <p>Revisando la información disponible…</p></div></div>}
            </section>
          </div>
        </div>

        <div className="composer-area">
          {showLatest && <button className="jump-to-latest" onClick={() => {
            following.current = true; scrollArea.current?.scrollTo({ top: scrollArea.current.scrollHeight, behavior: "instant" });
            setShowLatest(false);
          }}><Icon name="send" />Ir al último mensaje</button>}
          {chat.error && <div className="error" role="alert"><Icon name="info" /><div><p>{chat.error}</p>
            {chat.retryRequest && <button className="text-button" disabled={chat.pending}
              onClick={() => chat.submit(preferences, chat.retryRequest)}>Reintentar respuesta<Icon name="refresh" /></button>}
            {chat.historyFailed && <button className="text-button" onClick={chat.reloadHistory}>Volver a cargar conversación<Icon name="refresh" /></button>}
          </div></div>}
          {!chat.conversationId && !chat.loadingHistory && <label className="consent">
            <input type="checkbox" checked={chat.consent} onChange={(e) => chat.setConsent(e.target.checked)} />
            <span>Acepto enviar mi pregunta, el contexto y fragmentos de las fuentes a OpenAI para responder.
              La conversación se guarda temporalmente y puedo eliminarla. Evitaré compartir datos de otras personas.</span>
          </label>}
          <form className="composer" onSubmit={submit}>
            <label htmlFor="question" className="sr-only">Tu pregunta</label>
            <textarea id="question" ref={composer} value={chat.input} maxLength={4000} rows={1}
              disabled={disabled || chat.historyFailed || Boolean(chat.retryRequest)}
              onChange={(e) => chat.setInput(e.target.value)} placeholder="Escribe tu pregunta, estamos aquí para escucharte…"
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing && canSend) submit(event);
              }} />
            <div className="composer-bottom"><span><Icon name="sparkles" />A tu ritmo, a tu manera</span>
              <div>{chat.input.length > 3500 && <span className="character-count">{chat.input.length}/4000</span>}
                <span className="keyboard-hint">Enter para enviar</span>
                <button className="send-button" type="submit" disabled={!canSend} aria-label="Enviar"><Icon name="send" /></button>
              </div>
            </div>
          </form>
          <p className="footer-note"><Icon name="shield" />Orientación informativa. No sustituye la atención profesional. La IA puede equivocarse.</p>
          <p className="sr-only" role="status">{actionNotice}</p>
        </div>
      </main>
    </div>

    <dialog ref={deleteDialog} className="modal" aria-labelledby="delete-title">
      <div className="modal-symbol danger"><Icon name="trash" /></div>
      <h2 id="delete-title">¿Eliminar esta conversación?</h2><p>Se eliminarán sus mensajes de este servicio. Esta acción no se puede deshacer.</p>
      <div className="modal-actions"><button className="secondary" onClick={() => deleteDialog.current.close()}>Conservar conversación</button>
        <button className="danger-button" onClick={() => { deleteDialog.current.close(); chat.removeConversation(); }}>Eliminar definitivamente</button></div>
    </dialog>
    <dialog ref={infoDialog} className="modal information-modal" aria-labelledby="info-title">
      <button className="icon-button modal-close" aria-label="Cerrar información" onClick={() => infoDialog.current.close()}><Icon name="close" /></button>
      <div className="modal-symbol"><Icon name="shield" /></div><h2 id="info-title">Un espacio para comprender</h2>
      <p>Autism Insight ofrece información sobre autismo, comunicación y accesibilidad para personas autistas, familias y profesionales.</p>
      <h3>Información que puedes consultar</h3><p>Las respuestas muestran sus fuentes cuando hay evidencia suficiente. Si no la hay, te lo diremos. El material de prueba está identificado.</p>
      <h3>Tu conversación y tu privacidad</h3><p>Para responder, enviamos a OpenAI tu pregunta, el contexto necesario y fragmentos de documentos. La conversación se guarda temporalmente en este servicio y puedes eliminarla desde la papelera.</p>
      <p>Las conversaciones visibles aquí pertenecen a esta pestaña. No guardamos el texto de tus mensajes en el almacenamiento del navegador. Evita compartir datos que identifiquen a otras personas.</p>
      <div className="information-note"><Icon name="heart" />Cada persona tiene una experiencia distinta. Este asistente no ofrece diagnósticos ni sustituye la atención profesional.</div>
      <button className="primary" onClick={() => infoDialog.current.close()}>Entendido</button>
    </dialog>
  </div>;
}
