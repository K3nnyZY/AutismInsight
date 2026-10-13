# Verificación local — 12 de octubre de 2026

Se ejecutaron estas comprobaciones durante la implementación:

| Comprobación | Resultado |
| --- | --- |
| Pytest | 46 pruebas aprobadas, sin omisiones, con PostgreSQL y pgvector reales |
| Integración de base de datos | 8 pruebas: índices, firma, sesiones, idempotencia, borrado, concurrencia, fallos de ingesta y filtros |
| Contrato LangChain / SDK OpenAI | Transporte HTTP simulado: esquema estricto, rechazo del proveedor, embeddings y logs sin contenido |
| Playwright con Google Chrome | 9 pruebas aprobadas: consentimiento, preferencias, fuentes, recarga, borrado con confirmación, reintento, navegación entre conversaciones, teclado, copia y navegación móvil |
| Ruff | Lint y formato aprobados |
| ESLint | Aprobado |
| Vite | Compilación de producción aprobada |
| npm audit | 0 vulnerabilidades reportadas tras actualizar dependencias |
| pip check | Sin incompatibilidades de dependencias |
| Docker Compose | Configuración validada |
| Git | Sin errores de whitespace en los cambios rastreados |

Python 3.14.8, Node.js 24.21.0 y Vite 8.3.4 en macOS ARM64.

El motor Docker local no estuvo disponible. Las pruebas de integración utilizaron
PostgreSQL temporal con pgvector 0.8.2, descargado mediante `pgembed` fuera del
proyecto y eliminado al terminar. No se modificó ninguna base de datos de usuarios.
Las imágenes Docker no se construyeron ni ejecutaron en esta sesión.

La ingesta del PDF se probó con embeddings deterministas. Las pruebas de modelos
simulan respuestas y transporte; no se realizaron llamadas facturables a OpenAI
porque no hay una clave nueva configurada. El evaluador de modelos reales y la
validación editorial del conocimiento quedan pendientes de esa configuración y
de la revisión del corpus. El flujo CI está definido, pero no se ejecutó en GitHub.

La mejora del frontend se revisó con capturas de escritorio (1440 × 1000),
conversación y móvil (390 × 844). Las capturas usan una API simulada y están
disponibles en `artifacts/frontend-desktop.png`, `frontend-conversation.png`
y `frontend-mobile.png`. El estado «Disponible» de esas capturas corresponde
al escenario simulado, no a una conexión real con OpenAI.
