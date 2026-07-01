"""Paginación estándar para listados del API (DRF)."""

from rest_framework.pagination import PageNumberPagination


class StandardPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 1000


class MediumPagination(PageNumberPagination):
    """Listados más amplios (p. ej. almacenes y posiciones)."""

    page_size = 50
    page_size_query_param = "page_size"
    max_page_size = 1000
