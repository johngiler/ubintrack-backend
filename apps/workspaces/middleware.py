from django.http import JsonResponse

from apps.workspaces.tenant import resolve_request_workspace


class TenantMiddleware:
    """Asigna `request.workspace` y corta `/api/` si el subdominio no corresponde a un owner."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        workspace, err = resolve_request_workspace(request)
        request.workspace = workspace
        request.workspace_resolution_error = err

        if (
            err == "unknown_slug"
            and request.path.startswith("/api/")
            and request.method != "OPTIONS"
        ):
            return JsonResponse({"detail": "Workspace no encontrado."}, status=404)

        return self.get_response(request)
