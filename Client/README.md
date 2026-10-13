# Cliente de Autism Insight

React + Vite. El chat muestra fuentes, permite leer mensajes en voz alta y ofrece
preferencias de lenguaje para personas autistas, familias y profesionales.
La vista incluye navegación lateral, sugerencias por tema, citas que abren sus
fragmentos, copia de respuestas y controles de tamaño de texto y contraste.

```bash
npm ci
npm run dev
```

La API debe estar en `http://127.0.0.1:8000`. El proxy de Vite redirige `/api`;
el cliente nunca necesita la clave del proveedor. En Docker, Nginx hace ese proxy.
La sesión utiliza una cookie privada. `sessionStorage` conserva el UUID seleccionado
y hasta 20 UUID de conversaciones recientes de esta pestaña; no guarda mensajes.
Los títulos de conversaciones se calculan en memoria y se recuperan del servidor.
`localStorage` guarda únicamente las preferencias de lenguaje, tamaño y contraste.

Enter envía la pregunta y Mayús+Enter añade un salto de línea. Las respuestas
se anuncian mediante una región accesible. El menú móvil conserva el foco y se
cierra con Escape. El borrado requiere confirmar dentro de un diálogo.

```bash
npm run lint
npm run build
npx playwright install chromium
npm run test:e2e
```

Las nueve pruebas de navegador simulan la API y no consumen el modelo. También
generan capturas de escritorio, conversación y móvil en `../artifacts/`.
Consulta [el README principal](../README.md) para configurar backend y documentos.
