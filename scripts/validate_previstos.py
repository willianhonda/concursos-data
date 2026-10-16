#!/usr/bin/env python3
"""Confere o contrato de docs/previstos.json (concursos previstos, spike CA-007).

O arquivo é separado do feed: as versões antigas do app não o leem, então um erro aqui
nunca derruba a lista de concursos. O contrato é o que as versões novas leriam:
- um array JSON de objetos, ordenado por authorizedOn decrescente; `[]` é válido e é o estado normal;
- obrigatórios, texto não vazio: id (12 hex, sha1 da url), organization, authorizedOn, url;
- opcionais: vacancies (inteiro positivo), roles (lista de texto), education (subconjunto de
  fundamental, medio, tecnico, superior), editalDeadline (AAAA-MM-DD, não antes de authorizedOn),
  sphere (federal, estadual ou municipal), source (dou), act (texto);
- datas em AAAA-MM-DD, `authorizedOn` não no futuro;
- ids sem repetição.

Campos novos são permitidos (o app ignoraria chaves que não conhece).

Uso:
    python3 scripts/validate_previstos.py docs/previstos.json
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import sys
from pathlib import Path

from validate_feed import ISO_DATE

REQUIRED = ("id", "organization", "authorizedOn", "url")
EDUCATION = ("fundamental", "medio", "tecnico", "superior")
SPHERES = {"federal", "estadual", "municipal"}
SOURCES = {"dou"}
PREVISTO_ID = re.compile(r"^[0-9a-f]{12}$")


def forecast_id(url: str) -> str:
    """Id estável do previsto: sha1 curto da URL do ato."""
    return hashlib.sha1(url.encode()).hexdigest()[:12]


def _date(value: object, label: str, errors: list[str]) -> dt.date | None:
    if not isinstance(value, str) or not ISO_DATE.match(value):
        errors.append(f"{label!r} não está em AAAA-MM-DD: {value!r}")
        return None
    try:
        return dt.date.fromisoformat(value)
    except ValueError:
        errors.append(f"{label!r} não é uma data válida: {value!r}")
        return None


def forecast_errors(item: object, today: dt.date | None = None) -> list[str]:
    """Problemas de um previsto (lista vazia = válido)."""
    if not isinstance(item, dict):
        return [f"não é um objeto: {type(item).__name__}"]
    errors = []
    for key in REQUIRED:
        if not isinstance(item.get(key), str) or not item[key].strip():
            errors.append(f"falta o campo obrigatório {key!r} (texto não vazio)")
    if errors:
        return errors

    if not PREVISTO_ID.match(item["id"]):
        errors.append(f"'id' fora do formato (12 hex): {item['id']!r}")
    if not item["url"].startswith(("https://", "http://")):
        errors.append(f"'url' não é um link: {item['url']!r}")
    authorized = _date(item["authorizedOn"], "authorizedOn", errors)
    if authorized and today and authorized > today + dt.timedelta(days=1):
        errors.append(f"'authorizedOn' no futuro: {item['authorizedOn']}")

    if item.get("editalDeadline") is not None:
        deadline = _date(item["editalDeadline"], "editalDeadline", errors)
        if deadline and authorized and deadline < authorized:
            errors.append("'editalDeadline' anterior a 'authorizedOn'")
    if item.get("vacancies") is not None:
        vacancies = item["vacancies"]
        if isinstance(vacancies, bool) or not isinstance(vacancies, int) or vacancies < 1:
            errors.append(f"'vacancies' deve ser um inteiro positivo ou null, veio {vacancies!r}")
    if item.get("roles") is not None:
        roles = item["roles"]
        if not isinstance(roles, list) or not all(isinstance(r, str) and r.strip() for r in roles):
            errors.append("'roles' deve ser uma lista de textos não vazios ou null")
    if item.get("education") is not None:
        education = item["education"]
        if not isinstance(education, list) or not all(isinstance(e, str) for e in education):
            errors.append("'education' deve ser uma lista de textos ou null")
        else:
            unknown = [e for e in education if e not in EDUCATION]
            if unknown:
                errors.append(f"'education' com valores desconhecidos: {unknown}")
    for key, allowed in (("sphere", SPHERES), ("source", SOURCES)):
        if item.get(key) is not None and item[key] not in allowed:
            errors.append(f"{key!r} fora dos valores conhecidos: {item[key]!r}")
    if item.get("act") is not None and not isinstance(item["act"], str):
        errors.append("'act' deve ser texto ou null")
    return errors


def forecasts_errors(items: object, today: dt.date | None = None) -> list[str]:
    """Problemas do arquivo inteiro; cada item aparece com o seu id ou posição."""
    if not isinstance(items, list):
        return [f"o arquivo deve ser um array JSON, veio {type(items).__name__}"]
    errors, seen = [], set()
    for index, item in enumerate(items):
        label = item.get("id") if isinstance(item, dict) and isinstance(item.get("id"), str) else f"#{index}"
        errors += [f"previsto {label}: {problem}" for problem in forecast_errors(item, today)]
        if not isinstance(item, dict):
            continue
        if label in seen:
            errors.append(f"previsto {label}: 'id' repetido")
        seen.add(label)
    dates = [i["authorizedOn"] for i in items if isinstance(i, dict) and isinstance(i.get("authorizedOn"), str)]
    if dates != sorted(dates, reverse=True):
        errors.append("o array deve estar ordenado por authorizedOn decrescente")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("previstos", type=Path, nargs="?", default=Path("docs/previstos.json"))
    args = parser.parse_args()

    try:
        items = json.loads(args.previstos.read_text())
    except (OSError, json.JSONDecodeError) as err:
        print(f"arquivo ilegível: {err}", file=sys.stderr)
        return 1

    errors = forecasts_errors(items, dt.date.today())
    if errors:
        print(f"previstos inválidos ({len(errors)} problema(s)):", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1
    print(f"previstos válidos: {len(items)} itens")
    return 0


if __name__ == "__main__":
    sys.exit(main())
