"""Catálogo global de categorías de producto (compartido entre owners)."""

from __future__ import annotations

from apps.inventory.models import Category

DEFAULT_CATEGORIES = [
    {
        "name": "Carne y Productos de Origen Animal",
        "category_code": "Cat. 1",
        "coexistence": ["Carne y Productos de Origen Animal"],
    },
    {
        "name": "Lácteos y Derivados",
        "category_code": "Cat. 2",
        "coexistence": ["Lácteos y Derivados", "Panadería y Pastelería"],
    },
    {
        "name": "Frutas y Verduras",
        "category_code": "Cat. 3",
        "coexistence": ["Panadería y Pastelería", "Productos Listos para Comer"],
    },
    {
        "name": "Panadería y Pastelería",
        "category_code": "Cat. 4",
        "coexistence": [
            "Lácteos y Derivados",
            "Productos Listos para Comer",
            "Frutas y Verduras",
        ],
    },
    {
        "name": "Productos Listos para Comer",
        "category_code": "Cat. 5",
        "coexistence": ["Panadería y Pastelería", "Frutas y Verduras"],
    },
    {
        "name": "Químicos y Productos No Alimenticios",
        "category_code": "Cat. 6",
        "coexistence": ["Químicos y Productos No Alimenticios"],
    },
    {
        "name": "Fármacos y Medicina",
        "category_code": "Cat. 7",
        "coexistence": [],
    },
]


def seed_default_categories(*, stdout=None, style=None) -> tuple[int, int]:
    """
    Crea o actualiza categorías y coexistencias (idempotente por category_code).

    Returns:
        (created_count, updated_count)
    """
    by_name: dict[str, Category] = {}
    created = 0
    updated = 0

    for item in DEFAULT_CATEGORIES:
        category, was_created = Category.objects.update_or_create(
            category_code=item["category_code"],
            defaults={"name": item["name"]},
        )
        by_name[item["name"]] = category
        if was_created:
            created += 1
        else:
            updated += 1

    for item in DEFAULT_CATEGORIES:
        category = by_name[item["name"]]
        related = [by_name[name] for name in item["coexistence"]]
        category.coexistence.set(related)

    if stdout is not None:
        msg = f"Categorías: {created} creadas, {updated} actualizadas ({len(DEFAULT_CATEGORIES)} total)."
        if style:
            stdout.write(style.SUCCESS(msg))
        else:
            stdout.write(msg + "\n")

    return created, updated
