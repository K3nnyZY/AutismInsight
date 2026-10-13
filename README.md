# Autism Insight

Chatbot informativo sobre autismo, comunicación y accesibilidad para personas
autistas, familias y profesionales. Utiliza RAG con LangChain, LangGraph y
PostgreSQL + pgvector, respuestas con citas y conversaciones aisladas por navegador.

El proyecto nació en una hackathon de 2024. Esta versión reemplaza el servidor Flask
y la lectura parcial del PDF por una API asíncrona, ingesta de documentos y
recuperación híbrida. El PDF original se conserva como material de prueba.

## Arquitectura

| Capa | Tecnología |
| --- | --- |
| Cliente | React + Vite, Markdown y voz del navegador |
| API | FastAPI + Pydantic |
| Orquestación | LangGraph: intención → recuperación → evidencia → respuesta → verificación |
| Integración IA | LangChain y `langchain-openai` |
| Embeddings | `text-embedding-3-large` con 1536 dimensiones |
| Modelo inicial | `gpt-5.4-mini`, configurable por entorno |
| Recuperación | pgvector HNSW + texto completo en español + RRF + selección por modelo |
| Persistencia | PostgreSQL, SQLAlchemy, Psycopg y Alembic |
| Verificación | Pytest, Playwright y CI |

Consulta [las decisiones, el diagrama y los límites de la arquitectura](docs/architecture.md).
El mejor modelo y los parámetros finales se deben elegir mediante evaluación del
corpus y preguntas reales; los valores incluidos son un punto de partida medible.

## Arranque con Docker

Requiere Docker con su motor iniciado y una clave de API de OpenAI con acceso a
los modelos configurados. La ingesta y el chat consumen la API de pago.

Desde la raíz:

```bash
cp .env.example .env
# Edita .env y configura OPENAI_API_KEY. No pongas la clave en el frontend.
docker compose up --build -d db migrate api web
docker compose --profile tools run --rm ingest
```

Abre **http://localhost:5173**. La ingesta puede ejecutarse de nuevo: si el documento
no cambió, no volverá a generar embeddings. El cliente envía sus preguntas a `/api`
y Nginx las dirige al backend.

La ingesta anterior indexa `Server/app/Asperger.pdf` como `demo`, con la clave
`asperger-demo`. `.env.example` permite estas fuentes para las pruebas locales.
El chatbot identifica este material en las citas; no está aprobado para uso clínico.

Si ya existe `.env`, conserva sus valores al actualizar el proyecto. Las credenciales
de PostgreSQL incluidas son exclusivamente para desarrollo local. La clave escrita
en el prototipo anterior fue retirada del código, pero debe revocarse porque todavía
puede existir en el historial de Git.

## Desarrollo sin contenedores para API y cliente

Requiere Python 3.14, Node.js 24 y PostgreSQL con pgvector 0.8.2 o posterior.
Puedes ejecutar solamente la base de datos con `docker compose up -d db`.

Desde la raíz:

```bash
python3.14 -m venv .venv
source .venv/bin/activate
pip install -r Server/requirements-dev.lock
pip install --no-deps -e Server
cp .env.example .env
# Configura OPENAI_API_KEY y DATABASE_URL en .env.
cd Server
alembic upgrade head
python -m app.ingest app/Asperger.pdf \
  --source-key asperger-demo \
  --title "Asperger — documento de prueba" \
  --trust-level demo
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000 \
  --no-access-log --log-config logging.json
```

En otra terminal, desde la raíz:

```bash
cd Client
npm ci
npm run dev
```

Vite redirige `/api` a `http://127.0.0.1:8000`. No se necesita una clave ni una IP
fija en el cliente. El contrato de la API está disponible en **http://127.0.0.1:8000/docs**.
`Server/requirements.txt` apunta al lock de dependencias de ejecución; el lock de
desarrollo añade las herramientas de pruebas. Los locks se verificaron en Python 3.14.

## Añadir conocimiento

Coloca documentos en `knowledge/`: PDF con texto, TXT o Markdown UTF-8.
Las URLs de origen son metadatos; la ingesta no descarga esas páginas.

Con contenedores:

```bash
docker compose --profile tools run --rm ingest \
  python -m app.ingest /knowledge/guia.pdf \
  --source-key guia-comunicacion \
  --title "Guía de comunicación" \
  --source-url https://ejemplo.org/guia \
  --trust-level reviewed
```

Asigna `reviewed` únicamente después de revisar el contenido, la actualidad,
la procedencia y los permisos de uso. Para actualizar una fuente usa la misma
`source-key`; el reemplazo es transaccional. La ingesta envía los fragmentos a
OpenAI para generar embeddings. Los documentos deben excluir datos identificables
de pacientes. Los PDF escaneados necesitan OCR previo.

## Pruebas y evaluación

Desde la raíz, con el entorno virtual activo:

```bash
ruff check Server
ruff format --check Server
pytest Server/tests -q
```

Sin `TEST_DATABASE_URL`, se omiten las pruebas de integración. Para ejecutarlas,
crea una base de datos aislada cuyo nombre termine en `_test`, aplica las migraciones
con su `DATABASE_URL` y luego exporta `TEST_DATABASE_URL` apuntando a ella.
Las pruebas vacían tablas de esa base: nunca deben apuntar a datos de usuarios.
CI crea y migra una base de prueba automáticamente. Las pruebas no llaman a OpenAI.

Para el cliente:

```bash
cd Client
npm run lint
npm run build
npx playwright install chromium
npm run test:e2e
```

Si ya tienes Google Chrome instalado, puedes usar
`E2E_CHROME_CHANNEL=chrome npm run test:e2e`.

La evaluación con modelos reales necesita una clave válida y el PDF indexado:

```bash
cd Server
python -m app.evaluate evals/smoke.jsonl --output ../artifacts/evaluation.json
```

Este comando realiza llamadas facturables. El reporte guarda métricas, no textos
de conversaciones. Los casos son sintéticos; la evaluación clínica y de usabilidad
requiere un conjunto de referencia revisado y participación de los usuarios previstos.

## Operación

- `/api/health/live` verifica que la API responde.
- `/api/health/ready` verifica migraciones, firma del índice, configuración del modelo
  y presencia de fuentes habilitadas; no prueba el acceso real al proveedor.
- El historial vence en 30 días por defecto. Programa diariamente
  `docker compose --profile tools run --rm cleanup` para borrarlo físicamente.
- El usuario puede eliminar su conversación desde la interfaz.
- Los reintentos conservan `request_id` para evitar duplicar turnos.
- En producción configura HTTPS, `ENVIRONMENT=production`, orígenes HTTPS exactos,
  `ALLOW_DEMO_SOURCES=false`, secretos, respaldos y presupuestos del proveedor.

La implementación actual usa sesiones anónimas por navegador y un corpus común.
Los pasos pendientes para un servicio público están descritos en
[arquitectura](docs/architecture.md), incluidas cuentas, autorización por tenant,
política de datos y evaluación independiente. No se ha publicado ni desplegado un servicio externo.

## Autores del prototipo

Kenny Zhu, Roberto Gil, Ayrgthon Soraca, Francesca Martinez y Leonardo Troncoso.
