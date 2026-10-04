#!/usr/bin/env python3
"""Confere se docs/concursos.json continua legível pelo app antes de publicar.

O feed chega a todas as versões instaladas do app no mesmo dia, sem revisão da App Store.
Versões até a 2.2.x decodificam o array inteiro de uma vez: um único item fora do formato
derruba a lista de todo mundo. Por isso o workflow só publica se este script passar.

O contrato é o `EventModel` do app:
- obrigatórios, sempre texto: id, title, deadline, state, salary, vacancies, description, url;
- opcionais, texto ou null: city, published, registrationStarts, registrationEnds, gazetteUrl,
  kind, source, linkKind, banca;
- opcionais, lista de texto ou null (app 2.3+): roles, education, areas;
- campos novos são permitidos (o app ignora chaves que não conhece), mas nunca se remove,
  renomeia ou muda o tipo de um campo existente.

Uso:
    python3 scripts/validate_feed.py docs/concursos.json --previous /tmp/anterior.json
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path

REQUIRED = ("id", "title", "deadline", "state", "salary", "vacancies", "description", "url")
OPTIONAL = ("city", "published", "registrationStarts", "registrationEnds", "gazetteUrl", "kind", "source", "linkKind", "banca")
# Listas opcionais lidas pelo app a partir da 2.3; as versões anteriores ignoram as chaves.
LISTS = ("roles", "education", "areas")
LIST_ENUMS = {
    "education": {"fundamental", "medio", "tecnico", "superior"},
    "areas": {"saude", "educacao", "ti", "juridica", "seguranca", "engenharia", "administrativa",
              "assistencia_social", "operacional"},
}
DATES = ("published", "registrationStarts", "registrationEnds")
ENUMS = {
    "kind": {"concurso", "processo_seletivo"},
    "source": {"querido_diario", "dou"},
    "linkKind": {"edital", "banca", "orgao", "diario"},
}
UFS = {
    "AC", "AL", "AM", "AP", "BA", "CE", "DF", "ES", "GO", "MA", "MG", "MS", "MT", "PA", "PB",
    "PE", "PI", "PR", "RJ", "RN", "RO", "RR", "RS", "SC", "SE", "SP", "TO", "BR",
}
ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# Um feed que encolhe mais que isso de um dia para o outro indica erro no extrator, não concursos encerrados.
MAX_SHRINK = 0.5
MIN_PREVIOUS_FOR_SHRINK_CHECK = 10


def item_errors(item: object) -> list[str]:
    """Problemas que impediriam o app de ler este item (lista vazia = item válido)."""
    if not isinstance(item, dict):
        return [f"não é um objeto: {type(item).__name__}"]
    errors = []
    for key in REQUIRED:
        if key not in item:
            errors.append(f"falta o campo obrigatório {key!r}")
        elif not isinstance(item[key], str):
            errors.append(f"{key!r} deve ser texto, veio {type(item[key]).__name__}")
    for key in OPTIONAL:
        value = item.get(key)
        if value is not None and not isinstance(value, str):
            errors.append(f"{key!r} deve ser texto ou null, veio {type(value).__name__}")
    for key in LISTS:
        value = item.get(key)
        if value is not None and (not isinstance(value, list) or not all(isinstance(v, str) for v in value)):
            errors.append(f"{key!r} deve ser lista de texto ou null")
    if errors:
        return errors

    if not item["id"].strip():
        errors.append("'id' vazio")
    if not item["title"].strip():
        errors.append("'title' vazio")
    if item["state"] not in UFS:
        errors.append(f"'state' inválido: {item['state']!r}")
    if not item["url"].startswith(("https://", "http://")):
        errors.append(f"'url' não é um link: {item['url']!r}")
    for key, allowed in ENUMS.items():
        if item.get(key) is not None and item[key] not in allowed:
            errors.append(f"{key!r} fora dos valores conhecidos: {item[key]!r}")
    for key, allowed in LIST_ENUMS.items():
        unknown = sorted(set(item.get(key) or []) - allowed)
        if unknown:
            errors.append(f"{key!r} com valores desconhecidos: {unknown!r}")
    dates = {}
    for key in DATES:
        value = item.get(key)
        if value is None:
            continue
        try:
            if not ISO_DATE.match(value):
                raise ValueError
            dates[key] = dt.date.fromisoformat(value)
        except ValueError:
            errors.append(f"{key!r} não está em AAAA-MM-DD: {value!r}")
    if "registrationStarts" in dates and "registrationEnds" in dates and dates["registrationStarts"] > dates["registrationEnds"]:
        errors.append("'registrationStarts' depois de 'registrationEnds'")
    return errors


def feed_errors(feed: object, previous_count: int | None = None) -> list[str]:
    """Problemas do feed como um todo; cada item inválido aparece com o seu id ou posição."""
    if not isinstance(feed, list):
        return [f"o feed deve ser um array JSON, veio {type(feed).__name__}"]
    errors = []
    seen: set[str] = set()
    for index, item in enumerate(feed):
        label = item.get("id") if isinstance(item, dict) and isinstance(item.get("id"), str) else f"#{index}"
        errors += [f"item {label}: {problem}" for problem in item_errors(item)]
        if isinstance(item, dict) and isinstance(item.get("id"), str):
            if item["id"] in seen:
                errors.append(f"item {label}: 'id' repetido")
            seen.add(item["id"])
    if previous_count is not None and previous_count >= MIN_PREVIOUS_FOR_SHRINK_CHECK:
        if len(feed) < previous_count * MAX_SHRINK:
            errors.append(f"o feed encolheu de {previous_count} para {len(feed)} itens; confira o extrator antes de publicar")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("feed", type=Path, nargs="?", default=Path("docs/concursos.json"))
    parser.add_argument("--previous", type=Path, help="feed publicado antes desta execução, para detectar encolhimento")
    args = parser.parse_args()

    try:
        feed = json.loads(args.feed.read_text())
    except (OSError, json.JSONDecodeError) as err:
        print(f"feed ilegível: {err}", file=sys.stderr)
        return 1
    previous_count = None
    if args.previous and args.previous.exists():
        try:
            previous = json.loads(args.previous.read_text())
            previous_count = len(previous) if isinstance(previous, list) else None
        except json.JSONDecodeError:
            previous_count = None

    errors = feed_errors(feed, previous_count)
    if errors:
        print(f"feed inválido ({len(errors)} problema(s)); nada será publicado:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1
    print(f"feed válido: {len(feed)} itens" + (f" (antes: {previous_count})" if previous_count is not None else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
