"""CORP y marco seguro en ``/media/`` cuando la petición llega a Django (Gunicorn). Si Nginx sirve ``alias`` a disco, replica cabeceras (ver ``scripts/nginx/nginx-api.*.conf``)."""


class MediaCrossOriginResourcePolicyMiddleware:
    """
    - ``Cross-Origin-Resource-Policy: cross-origin`` para peticiones cross-origin al binario.
    - ``X-Frame-Options: SAMEORIGIN``: el clickjacking middleware suele poner ``DENY``; el portal (Next
      proxy) incrusta PDF/imágenes en ``<iframe>`` bajo el mismo host que la SPA, así que hace falta
      permitir el mismo origen (no ``DENY``).
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if request.path.startswith("/media/"):
            response["Cross-Origin-Resource-Policy"] = "cross-origin"
            response["X-Frame-Options"] = "SAMEORIGIN"
        return response
