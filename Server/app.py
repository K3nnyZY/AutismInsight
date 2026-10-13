"""Entrada de desarrollo compatible con `python app.py`.

Inicia Uvicorn en 127.0.0.1:8000. Para configurar recarga y logging,
usa el comando de Uvicorn explicado en Server/README.md.
"""

import uvicorn

if __name__ == "__main__":
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000)
