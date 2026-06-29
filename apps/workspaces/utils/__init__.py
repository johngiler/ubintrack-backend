"""
Utilidades de workspace (submódulos: ``common``, ``smtp_test``, ``workspace_validators``, …).

No reexportar aquí funciones que dependan de ``tenant`` o ``models``: importar cualquier
submódulo de este paquete ejecuta este ``__init__.py``; si encadena ``tenant`` → ``models``
mientras ``models`` aún se está cargando (p. ej. vía ``validators``), se produce import circular.
"""
