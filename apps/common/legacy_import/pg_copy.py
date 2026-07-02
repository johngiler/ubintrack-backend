from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

COPY_HEADER = re.compile(r"^COPY public\.(\w+) \((.+)\) FROM stdin;$")

_PG_RESTORE_VERSION_HINT = (
    "El dump fue creado con pg_dump de PostgreSQL 17 (formato custom 1.16). "
    "Ejecuta extract_legacy_rbcold en tu Mac (pg_restore 17+) y sube solo "
    "data/rbcold/import/parsed/ al servidor; import_legacy_rbcold sí corre en prod. "
    "Alternativa: instalar postgresql-client-17 en el servidor."
)


def check_pg_restore_can_read_dump(dump_path: Path) -> None:
    result = subprocess.run(
        ["pg_restore", "-l", str(dump_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 0:
        return
    stderr = (result.stderr or "").strip()
    if "unsupported version" in stderr.lower():
        version = subprocess.run(
            ["pg_restore", "--version"],
            capture_output=True,
            text=True,
            check=False,
        )
        pg_restore_ver = (version.stdout or version.stderr or "").strip()
        raise RuntimeError(
            f"{stderr}\n"
            f"pg_restore local: {pg_restore_ver or 'desconocido'}\n"
            f"{_PG_RESTORE_VERSION_HINT}"
        )
    raise RuntimeError(f"No se pudo leer el dump: {stderr or result.returncode}")


def run_pg_restore_data(dump_path: Path, table_name: str) -> str:
    result = subprocess.run(
        ["pg_restore", "-a", "-t", table_name, "-f", "-", str(dump_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        if "unsupported version" in stderr.lower():
            raise RuntimeError(f"{stderr}\n{_PG_RESTORE_VERSION_HINT}")
        raise RuntimeError(
            f"pg_restore falló para la tabla {table_name}: {stderr or result.returncode}"
        )
    return result.stdout


def parse_copy_fields(line: str) -> list[str | None]:
    fields: list[str | None] = []
    buf: list[str] = []
    i = 0
    while i <= len(line):
        if i == len(line) or line[i] == "\t":
            raw = "".join(buf)
            fields.append(None if raw == "\\N" else raw)
            buf = []
            i += 1
            continue
        if line[i] == "\\" and i + 1 < len(line):
            esc = line[i + 1]
            mapping = {
                "n": "\n",
                "r": "\r",
                "t": "\t",
                "b": "\b",
                "f": "\f",
                "v": "\v",
                "\\": "\\",
            }
            if esc in mapping:
                buf.append(mapping[esc])
                i += 2
                continue
        buf.append(line[i])
        i += 1
    return fields


def parse_copy_sections(sql_text: str) -> dict[str, list[dict[str, Any]]]:
    tables: dict[str, list[dict[str, Any]]] = {}
    lines = sql_text.splitlines()
    i = 0
    while i < len(lines):
        match = COPY_HEADER.match(lines[i])
        if not match:
            i += 1
            continue
        table_name = match.group(1)
        columns = [col.strip() for col in match.group(2).split(",")]
        rows: list[dict[str, Any]] = []
        i += 1
        while i < len(lines) and lines[i] != "\\.":
            if lines[i].strip():
                values = parse_copy_fields(lines[i])
                if len(values) != len(columns):
                    raise ValueError(
                        f"Fila inválida en {table_name}: "
                        f"esperaba {len(columns)} columnas, obtuvo {len(values)}"
                    )
                rows.append(dict(zip(columns, values)))
            i += 1
        tables[table_name] = rows
        i += 1
    return tables


def extract_table_rows(dump_path: Path, table_name: str) -> list[dict[str, Any]]:
    sql_text = run_pg_restore_data(dump_path, table_name)
    return parse_copy_sections(sql_text).get(table_name, [])
