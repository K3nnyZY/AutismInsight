"""Errores de negocio que pueden convertirse en respuestas HTTP comprensibles."""


class ServiceError(Exception):
    """Excepción con código HTTP y mensaje público, sin detalles internos del proveedor."""

    def __init__(self, status: int, detail: str):
        """Conserva status y detail para que el manejador de FastAPI construya la respuesta."""
        super().__init__(detail)
        self.status = status
        self.detail = detail
