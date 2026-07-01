from apps.core.utils.user_roles import get_user_client


class ClientFilterMixin:
    def get_queryset(self, request):
        qs = super().get_queryset(request)

        if request.user.is_superuser:
            return qs

        client = get_user_client(request.user)
        if client:
            return qs.filter(client=client)
        return qs.none()
