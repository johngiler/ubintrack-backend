"""Resolución de productos del owner coldcave contra el catálogo."""

from __future__ import annotations

from dataclasses import dataclass

from django.db.models import Q

from apps.inventory.models import Product
from apps.workspaces.models import Workspace

from .parse import normalize_name


@dataclass
class ProductMatch:
    product: Product | None
    note: str | None = None


def _sku_variants(value: str) -> set[str]:
    text = str(value or "").strip()
    if not text:
        return set()
    out = {text, text.lstrip("0") or "0"}
    if text.isdigit():
        out.add(str(int(text)))
    return {v for v in out if v}


def build_product_index(workspace: Workspace) -> dict:
    products = list(
        Product.objects.filter(client__workspace=workspace, is_active=True).select_related(
            "client"
        )
    )
    by_name: dict[str, list[Product]] = {}
    by_sku: dict[str, list[Product]] = {}
    for product in products:
        key = normalize_name(product.name).upper()
        by_name.setdefault(key, []).append(product)
        for sku in _sku_variants(product.client_sku or ""):
            by_sku.setdefault(sku, []).append(product)
    return {"by_name": by_name, "by_sku": by_sku, "all": products}


def match_product(
    index: dict,
    *,
    product_name: str,
    sap_code: str = "",
    barcode: str = "",
) -> ProductMatch:
    name_key = normalize_name(product_name).upper()
    if not name_key:
        return ProductMatch(None, "Producto sin nombre")

    candidates: list[Product] = []
    seen: set[int] = set()

    for token in list(_sku_variants(sap_code)) + list(_sku_variants(barcode)):
        for product in index["by_sku"].get(token, []):
            if product.id not in seen:
                seen.add(product.id)
                candidates.append(product)

    if len(candidates) == 1:
        return ProductMatch(candidates[0])
    if len(candidates) > 1:
        # Prefer exact name among SKU hits
        named = [p for p in candidates if normalize_name(p.name).upper() == name_key]
        if len(named) == 1:
            return ProductMatch(named[0], "Match por SKU+nombre")
        return ProductMatch(
            None,
            f"SKU/barcode ambiguo para '{product_name}' ({len(candidates)} hits)",
        )

    named = index["by_name"].get(name_key, [])
    if len(named) == 1:
        return ProductMatch(named[0], "Match por nombre")
    if len(named) > 1:
        return ProductMatch(
            None,
            f"Nombre ambiguo '{product_name}' ({len(named)} productos)",
        )

    # Soft: name contains / contained
    soft = [
        p
        for p in index["all"]
        if name_key in normalize_name(p.name).upper()
        or normalize_name(p.name).upper() in name_key
    ]
    if len(soft) == 1:
        return ProductMatch(soft[0], f"Match aproximado de nombre → {soft[0].name}")

    return ProductMatch(None, f"Producto no encontrado: {product_name}")


def resolve_workspace(slug: str = "coldcave") -> Workspace:
    return Workspace.objects.get(slug=slug)
