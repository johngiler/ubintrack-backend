"""Respuesta paginada estándar para timelines por ítem."""

from __future__ import annotations

from rest_framework.response import Response

from apps.common.pagination import StandardPagination


def paginated_movements_response(
    request,
    *,
    queryset,
    serializer_class,
    meta: dict | None = None,
) -> Response:
    paginator = StandardPagination()
    page = paginator.paginate_queryset(queryset, request)
    data = serializer_class(page, many=True).data
    payload = paginator.get_paginated_response(data)
    # Compat: clients that still read `.movements` / `.total`
    body = payload.data
    body["movements"] = body.get("results", [])
    body["total"] = body.get("count", 0)
    if meta:
        body.update(meta)
    return Response(body)
