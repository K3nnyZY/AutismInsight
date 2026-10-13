const API_BASE = (import.meta.env.VITE_API_BASE || "/api").replace(/\/$/, "");

export class ApiError extends Error {
  constructor(message, status) { super(message); this.status = status; }
}

export async function api(path, { method = "GET", body, signal } = {}) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 105000);
  const abort = () => controller.abort();
  signal?.addEventListener("abort", abort, { once: true });
  if (signal?.aborted) controller.abort();
  try {
    const response = await fetch(`${API_BASE}${path}`, {
      method, credentials: "include",
      headers: body ? { "Content-Type": "application/json" } : undefined,
      body: body ? JSON.stringify(body) : undefined, signal: controller.signal,
    });
    if (response.status === 204) return null;
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new ApiError(
      typeof data.detail === "string" ? data.detail : "No se pudo completar la solicitud.",
      response.status,
    );
    return data;
  } catch (error) {
    if (error instanceof ApiError) throw error;
    if (error.name === "AbortError") {
      throw new ApiError("La respuesta tardó demasiado. Puedes reintentar.", 504);
    }
    throw new ApiError("No pude conectar con el servidor. Comprueba tu conexión.", 0);
  } finally {
    clearTimeout(timeout);
    signal?.removeEventListener("abort", abort);
  }
}
