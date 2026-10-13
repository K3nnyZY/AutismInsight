import PropTypes from "prop-types";
import ReactMarkdown from "react-markdown";
import Icon from "./Icon";

function sourceUrl(url) {
  try {
    const parsed = new URL(url);
    return parsed.protocol === "https:" ? parsed.href : null;
  } catch { return null; }
}

export default function ChatMessage({ message, speaking, onSpeak, copied, onCopy }) {
  const sources = message.response?.sources || [];
  const assistant = message.role === "assistant";
  const content = assistant ? message.content.replace(/\[(\d+)\]/g, (match, id) =>
    sources.some((source) => source.citation === Number(id))
      ? `[${id}](#source-${message.id}-${id})` : match) : message.content;

  const openCitation = (event) => {
    const link = event.target.closest("a");
    if (!link?.getAttribute("href")?.startsWith(`#source-${message.id}-`)) return;
    event.preventDefault();
    const source = document.getElementById(link.getAttribute("href").slice(1));
    if (source) {
      source.closest("details").open = true;
      source.focus();
    }
  };

  return <article className={`message ${message.role}`}>
    <div className="message-avatar" aria-hidden="true">
      {assistant ? <img src="/Logo.png" alt="" /> : "Tú"}
    </div>
    <div className="message-body">
      <div className="message-heading">
        <h2>{assistant ? "Autism Insight" : "Tú"}</h2>
        {assistant && message.response?.evidence_status === "grounded" &&
          <span className="grounded-label"><Icon name="book" />Con fuentes</span>}
      </div>
      <div className="message-markdown" onClick={openCitation}>
        <ReactMarkdown skipHtml>{content}</ReactMarkdown>
      </div>
      {message.response?.evidence_status === "insufficient" &&
        <p className="evidence-note"><Icon name="info" />Información insuficiente en las fuentes disponibles.</p>}
      {Boolean(sources.length) && <details className="sources">
        <summary><Icon name="book" /><span>Ver fuentes ({sources.length})</span><Icon name="chevron" /></summary>
        <div className="sources-list">{sources.map((source) => <div key={source.chunk_id}
          id={`source-${message.id}-${source.citation}`} className="source" tabIndex={-1}>
          <div className="source-heading">
            <span className="source-number">{source.citation}</span>
            <div><strong>{source.title}</strong>
              <span className="source-meta">{source.page ? `Página ${source.page}` : "Documento"}
                {source.trust_level === "demo" && <span className="demo-label">Material de prueba</span>}
              </span>
            </div>
          </div>
          <blockquote>{source.excerpt}</blockquote>
          {sourceUrl(source.url) && <a href={sourceUrl(source.url)} target="_blank"
            rel="noopener noreferrer">Abrir fuente original<Icon name="arrow" /></a>}
        </div>)}</div>
      </details>}
      {assistant && <div className="message-actions">
        {"speechSynthesis" in window && <button className="text-button" onClick={() => onSpeak(message.id, message.content)}>
          <Icon name={speaking === message.id ? "stop" : "volume"} />
          {speaking === message.id ? "Detener lectura" : "Leer en voz alta"}
        </button>}
        <button className="text-button" onClick={() => onCopy(message.id, message.content)}
          aria-label={copied === message.id ? "Respuesta copiada" : "Copiar respuesta"}>
          <Icon name={copied === message.id ? "check" : "copy"} />{copied === message.id ? "Copiado" : "Copiar"}
        </button>
      </div>}
    </div>
  </article>;
}

ChatMessage.propTypes = {
  message: PropTypes.shape({ id: PropTypes.string.isRequired, role: PropTypes.string.isRequired,
    content: PropTypes.string.isRequired, response: PropTypes.object }).isRequired,
  speaking: PropTypes.string, onSpeak: PropTypes.func.isRequired,
  copied: PropTypes.string, onCopy: PropTypes.func.isRequired,
};
