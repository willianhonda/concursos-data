#!/usr/bin/env python3
"""Confere o contrato de shadow/atualizacoes.json (modo sombra, spike CA-006).

Nenhum app lê este arquivo ainda. O contrato é o que as versões novas do app leriam
(formato da seção 6(b) do spike), para que o arquivo chegue pronto quando o modo sombra
passar no critério:
- um array JSON de objetos;
- obrigatórios, texto não vazio: id (`upd-` + 12 hex), examId, type, published, gazetteUrl;
- opcionais, texto ou null: title, organization, edital, state, city, excerpt, source;
- `type` em gabarito, resultado, homologacao, convocacao; `source` em querido_diario, dou;
- `published` em AAAA-MM-DD, dentro da janela de 90 dias e não no futuro;
- ids sem repetição e, com `--index`, todo examId presente em shadow/aberturas.json.

Campos novos são permitidos (o app ignoraria chaves que não conhece).

Uso:
    python3 scripts/validate_updates.py shadow/atualizacoes.json --index shadow/aberturas.json
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path

from validate_feed import ISO_DATE, UFS

REQUIRED = ("id", "examId", "type", "published", "gazetteUrl")
OPTIONAL = ("title", "organization", "edital", "state", "city", "excerpt", "source")
TYPES = {"gabarito", "resultado", "homologacao", "convocacao"}
SOURCES = {"querido_diario", "dou"}
UPDATE_ID = re.compile(r"^upd-[0-9a-f]{12}$")
WINDOW_DAYS = 90


def update_errors(item: object, today: dt.date | None = None) -> list[str]:
    """Problemas de uma atualização (lista vazia = válida)."""
    if not isinstance(item, dict):
        return [f"não é um objeto: {type(item).__name__}"]
    errors = []
    for key in REQUIRED:
        if not isinstance(item.get(key), str) or not item[key].strip():
            errors.append(f"falta o campo obrigatório {key!r} (texto não vazio)")
    for key in OPTIONAL:
        value = item.get(key)
        if value is not None and not isinstance(value, str):
            errors.append(f"{key!r} deve ser texto ou null, veio {type(value).__name__}")
    if errors:
        return errors

    if not UPDATE_ID.match(item["id"]):
        errors.append(f"'id' fora do formato upd-xxxxxxxxxxxx: {item['id']!r}")
    if item["type"] not in TYPES:
        errors.append(f"'type' fora dos valores conhecidos: {item['type']!r}")
    if item.get("source") is not None and item["source"] not in SOURCES:
        errors.append(f"'source' fora dos valores conhecidos: {item['source']!r}")
    if item.get("state") is not None and item["state"] not in UFS:
        errors.append(f"'state' inválido: {item['state']!r}")
    if not item["gazetteUrl"].startswith(("https://", "http://")):
        errors.append(f"'gazetteUrl' não é um link: {item['gazetteUrl']!r}")
    try:
        if not ISO_DATE.match(item["published"]):
            raise ValueError
        published = dt.date.fromisoformat(item["published"])
    except ValueError:
        errors.append(f"'published' não está em AAAA-MM-DD: {item['published']!r}")
    else:
        if today and published > today + dt.timedelta(days=1):
            errors.append(f"'published' no futuro: {item['published']}")
        if today and published < today - dt.timedelta(days=WINDOW_DAYS):
            errors.append(f"'published' fora da janela de {WINDOW_DAYS} dias: {item['published']}")
    return errors


def updates_errors(updates: object, exam_ids: set[str] | None = None, today: dt.date | None = None) -> list[str]:
    """Problemas do arquivo inteiro; cada item aparece com o seu id ou posição."""
    if not isinstance(updates, list):
        return [f"o arquivo deve ser um array JSON, veio {type(updates).__name__}"]
    errors, seen = [], set()
    for index, item in enumerate(updates):
        label = item.get("id") if isinstance(item, dict) and isinstance(item.get("id"), str) else f"#{index}"
        errors += [f"atualização {label}: {problem}" for problem in update_errors(item, today)]
        if not isinstance(item, dict):
            continue
        if label in seen:
            errors.append(f"atualização {label}: 'id' repetido")
        seen.add(label)
        if exam_ids is not None and isinstance(item.get("examId"), str) and item["examId"] not in exam_ids:
            errors.append(f"atualização {label}: examId {item['examId']!r} não está no índice de aberturas")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("updates", type=Path, nargs="?", default=Path("shadow/atualizacoes.json"))
    parser.add_argument("--index", type=Path, help="shadow/aberturas.json, para conferir os examId")
    args = parser.parse_args()

    try:
        updates = json.loads(args.updates.read_text())
        exam_ids = None
        if args.index:
            index = json.loads(args.index.read_text())
            exam_ids = {e["id"] for e in index if isinstance(e, dict) and isinstance(e.get("id"), str)}
    except (OSError, json.JSONDecodeError) as err:
        print(f"arquivo ilegível: {err}", file=sys.stderr)
        return 1

    errors = updates_errors(updates, exam_ids, dt.date.today())
    if errors:
        print(f"atualizações inválidas ({len(errors)} problema(s)):", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1
    print(f"atualizações válidas: {len(updates)} itens")
    return 0


if __name__ == "__main__":
    sys.exit(main())
