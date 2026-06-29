from django.db import migrations


CATEGORIES = [
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


def seed_categories(apps, schema_editor):
    Category = apps.get_model("inventory", "Category")
    by_name = {}

    for item in CATEGORIES:
        category, _ = Category.objects.update_or_create(
            category_code=item["category_code"],
            defaults={"name": item["name"]},
        )
        by_name[item["name"]] = category

    for item in CATEGORIES:
        category = by_name[item["name"]]
        related = [by_name[name] for name in item["coexistence"]]
        category.coexistence.set(related)


def unseed_categories(apps, schema_editor):
    Category = apps.get_model("inventory", "Category")
    codes = [item["category_code"] for item in CATEGORIES]
    Category.objects.filter(category_code__in=codes).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0004_alter_binmovement_bin_alter_binproduct_bin_and_more"),
    ]

    operations = [
        migrations.RunPython(seed_categories, unseed_categories),
    ]
