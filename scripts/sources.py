"""Fontes novas do feed: quais coletores rodam, em que modo, e o kill switch.

Cada coletor novo tem um nome (`sigpub`) e grava o campo opcional `collector` nos itens ("sigpub:amp").
O modo de cada um vem de três variáveis de ambiente, todas listas separadas por vírgula:

- SOURCES_ON: coletores que entram em docs/concursos.json (produção). Padrão: nenhum.
- SOURCES_SHADOW: coletores que rodam, mas gravam só em shadow/fontes-novas.json, que o app nunca lê.
  É o modo sombra de 2 semanas para medir a precisão antes de ligar de verdade.
- SOURCES_OFF: kill switch. Tem prioridade sobre as outras duas: o coletor nem roda e os itens dele
  (inclusive os já publicados) saem do feed na próxima execução. Também vale para um valor de `source`
  ("doe") ou para parte de um coletor ("sigpub:amp").

Um coletor que não está em nenhuma lista não roda. Assim o código das fontes novas pode ir para a main
sem mudar o que o app recebe.
"""

from __future__ import annotations

import os
import re
import unicodedata

from validate_feed import is_source_off, sources_off

# Coletores conhecidos; o nome é o prefixo de `collector`.
KNOWN_COLLECTORS = ("sigpub", "dosp", "ibgp", "proseleta", "cebraspe")

OFF, SKIP, SHADOW, LIVE = "off", "skip", "shadow", "live"


def _names(var: str, env: dict | None) -> set[str]:
    raw = (os.environ if env is None else env).get(var, "")
    return {n.strip().lower() for n in raw.split(",") if n.strip()}


def collector_mode(name: str, env: dict | None = None) -> str:
    """off (kill switch), live (vai para o feed), shadow (só shadow/) ou skip (não roda)."""
    off = sources_off(env)
    if name in off:
        return OFF
    if name in _names("SOURCES_ON", env):
        return LIVE
    if name in _names("SOURCES_SHADOW", env):
        return SHADOW
    return SKIP


def unknown_names(env: dict | None = None) -> list[str]:
    """Nomes nas variáveis que nenhum coletor conhece: costuma ser erro de digitação num kill switch."""
    known = set(KNOWN_COLLECTORS)
    names = _names("SOURCES_ON", env) | _names("SOURCES_SHADOW", env)
    return sorted(n for n in names if n not in known)


def drop_disabled(feed: list[dict], off: set[str]) -> list[dict]:
    """Tira do feed os itens de fontes desligadas, inclusive os que já estavam publicados."""
    return [item for item in feed if not is_source_off(item, off)] if off else feed


# ---------------------------------------------------------------- deduplicação entre fontes

EDITAL = re.compile(r"n[ºo°]\s*([\d.]+\s*/\s*\d{2,4})", re.IGNORECASE)


def _plain(text: str) -> str:
    text = unicodedata.normalize("NFD", text)
    return re.sub(r"\s+", " ", "".join(c for c in text if unicodedata.category(c) != "Mn")).strip().lower()


def match_key(item: dict) -> tuple | None:
    """Mesma seleção publicada por duas fontes: UF + cidade + tipo + número do edital. None sem número ou cidade."""
    m = EDITAL.search(item.get("title") or "")
    if not m or not item.get("city"):
        return None
    number, year = re.sub(r"\s+", "", m.group(1)).replace(".", "").split("/")
    return (item.get("state"), _plain(item["city"]), item.get("kind"), f"{int(number)}/{year[-4:] if len(year) == 4 else '20' + year}")


def dedupe(new: list[dict], existing: list[dict]) -> list[dict]:
    """Descarta os itens novos que já existem em `existing` (ou antes na própria lista) com o mesmo `match_key`."""
    seen = {k for k in (match_key(i) for i in existing) if k}
    ids = {i["id"] for i in existing}
    kept = []
    for item in new:
        key = match_key(item)
        if item["id"] in ids or (key and key in seen):
            continue
        if key:
            seen.add(key)
        kept.append(item)
    return kept
