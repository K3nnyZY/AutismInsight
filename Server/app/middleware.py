"""Middleware ASGI para acotar el cuerpo real de las peticiones POST."""

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send


class BodySizeLimit:
    """Cuenta bytes recibidos, también si no existe Content-Length.

    Conserva como máximo el límite configurado antes de pasar el cuerpo
    a FastAPI; si se supera, devuelve HTTP 413.
    """

    def __init__(self, app: ASGIApp, limit: int):
        """Recibe la siguiente aplicación ASGI y el máximo de bytes del cuerpo."""
        self.app = app
        self.limit = limit

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        """Recibe y limita POST; los demás métodos y eventos se delegan directamente.

        Tras reunir el cuerpo, proporciona replay al siguiente middleware
        para que el parser de FastAPI pueda leerlo normalmente.
        """
        if scope["type"] != "http" or scope["method"] != "POST":
            return await self.app(scope, receive, send)
        chunks = []
        size = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            data = message.get("body", b"")
            size += len(data)
            if size > self.limit:
                return await JSONResponse(
                    {"detail": "Solicitud demasiado grande."}, status_code=413
                )(scope, receive, send)
            chunks.append(data)
            if not message.get("more_body", False):
                break
        delivered = False

        async def replay():
            """Entrega el cuerpo reunido una vez y luego delega mensajes al receive original."""
            nonlocal delivered
            if delivered:
                return await receive()
            delivered = True
            return {"type": "http.request", "body": b"".join(chunks), "more_body": False}

        await self.app(scope, replay, send)
