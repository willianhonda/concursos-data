"""Coletor das associações municipais que publicam no Sigpub (diariomunicipal.com.br).

Cada associação (AMP-PR, FAMURS-RS, AMM-MG, AGM/FGM-GO…) tem uma busca pública por título e período e uma
página por matéria, em HTML. O coletor:

1. pesquisa os títulos com "concurso" e "seletivo" no período, 25 matérias por página;
2. descarta pelo título as que claramente não são abertura (convocação, resultado, homologação…);
3. baixa a matéria e passa o texto pelo mesmo `extract_openings` do Querido Diário;
4. marca o item: id = sha1("sigpub|<código da matéria>"), source `diario_municipal`, collector `sigpub:<slug>`,
   sphere `municipal`, gazetteName e gazetteUrl com o link da matéria.

O robots.txt do site é `Disallow:` vazio. Usamos o User-Agent identificável do projeto e 1 s entre requisições.
Só entram prefeituras e câmaras: o município é deduzido do nome da entidade e, sem ele, a matéria é ignorada.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import html
import re
import time
import unicodedata
import urllib.parse
from typing import Callable

BASE = "https://www.diariomunicipal.com.br"
# slug da associação na URL -> UF dos municípios dela
SLUGS = {
    "amp": "PR", "famurs": "RS", "amm-mg": "MG", "agm": "GO", "fgm": "GO", "arom": "RO",
    "famep": "PA", "famup": "PB", "amupe": "PE", "femurn": "RN", "aprece": "CE",
}
TERMS = ("concurso", "seletivo")
MAX_PAGES = 8
PAGE_SIZE = 25
DELAY = 1.0

# Títulos de atos que nunca são a abertura de um concurso.
SKIP_TITLE = re.compile(
    r"convoca|chamamento|chamada|resultado|homologa|classifica|gabarito|nomea|posse|comunicado|prorroga|"
    r"anula|suspen|cancela|aprovad|desempate|lista\s+de|deferi|indeferi|recurso|"
    r"divulga[çc][ãa]o\s+d[aoe]s?\s+inscri|inscritos|locais\s+(?:e\s+hor|de\s+prova)|vice-?\s?diretor",
    re.IGNORECASE,
)
ENTITY_PREFIX = re.compile(
    r"^\s*(?:prefeitura\s+municipal|prefeitura|munic[íi]pio|c[âa]mara\s+municipal|c[âa]mara)\s+(?:de|do|da)\s+(.+?)\s*(?:[-/]\s*[A-Z]{2})?\s*$",
    re.IGNORECASE,
)
LOWER_WORDS = {"de", "da", "do", "das", "dos", "e"}

CARD = re.compile(
    r'<h2 class="materia-card__titulo">\s*<a href="/([\w-]+)/materia/(\w+)"[^>]*>(.*?)</a>.*?'
    r'materia-card__entidade">(.*?)</span>.*?<dt>Circulação</dt>\s*<dd>(\d{2})/(\d{2})/(\d{4})</dd>',
    re.DOTALL,
)
TOTAL = re.compile(r"de\s*<strong>(\d+)</strong>\s*mat[ée]rias", re.IGNORECASE)
BODY = re.compile(r'id="materia"[^>]*>(.*?)</div>\s*(?:<footer|</article>)', re.DOTALL)


def _plain(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn")


def search_url(slug: str, term: str, since: dt.date, until: dt.date, page: int) -> str:
    params = {
        "busca_avancada[titulo]": term,
        "busca_avancada[dataInicio]": since.isoformat(),
        "busca_avancada[dataFim]": until.isoformat(),
        "busca_avancada[ordenacao]": "recente",
        "busca_avancada[pagina]": page,
    }
    return f"{BASE}/{slug}/pesquisar?{urllib.parse.urlencode(params)}"


def materia_url(slug: str, code: str) -> str:
    return f"{BASE}/{slug}/materia/{code}"


def parse_search(page: str) -> dict:
    """Cartões de uma página de resultados e o total de matérias da busca."""
    cards = []
    for slug, code, title, entity, day, month, year in CARD.findall(page):
        cards.append({
            "slug": slug, "code": code, "title": html.unescape(re.sub(r"\s+", " ", title)).strip(),
            "entity": html.unescape(re.sub(r"\s+", " ", entity)).strip(), "date": f"{year}-{month}-{day}",
        })
    total = TOTAL.search(page)
    return {"cards": cards, "total": int(total.group(1)) if total else len(cards)}


def parse_materia(page: str, to_text: Callable[[str], str]) -> dict | None:
    """Texto, entidade e nome do diário de uma matéria; None se a página não tiver o corpo."""
    body = BODY.search(page)
    if not body:
        return None
    entity = re.search(r'documento__entidade">(.*?)<', page)
    gazette = re.search(r"<dt>Di[áa]rio</dt>\s*<dd>(.*?)</dd>", page, re.DOTALL)
    return {
        "text": to_text(body.group(1)),
        "entity": html.unescape(entity.group(1)).strip() if entity else "",
        "gazetteName": html.unescape(gazette.group(1)).strip() if gazette else "",
    }


def city_from_entity(entity: str) -> str | None:
    """"PREFEITURA MUNICIPAL DE QUATRO BARRAS" -> "Quatro Barras"; None para autarquias, consórcios etc."""
    m = ENTITY_PREFIX.match(entity or "")
    if not m:
        return None
    words = m.group(1).lower().split()
    return " ".join(w if i and w in LOWER_WORDS else w.capitalize() for i, w in enumerate(words))


def collect(
    since: dt.date,
    today: dt.date,
    *,
    fetch: Callable[[str], bytes],
    extract: Callable[[str, dict], list[dict]],
    to_text: Callable[[str], str],
    off: set[str] | None = None,
    slugs: dict[str, str] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    log: Callable[[str], None] = lambda msg: None,
) -> list[dict]:
    """Aberturas encontradas no Sigpub. Falha numa associação ou matéria é registrada e não derruba as outras."""
    off = off or set()
    items: list[dict] = []
    seen: set[str] = set()
    for slug, uf in (slugs or SLUGS).items():
        if f"sigpub:{slug}" in off:
            log(f"sigpub:{slug}: desligado em SOURCES_OFF")
            continue
        try:
            cards = _search_all(slug, since, today, fetch, sleep, log)
        except Exception as err:  # noqa: BLE001 - uma associação fora do ar não derruba as outras
            log(f"sigpub:{slug}: busca falhou: {err}")
            continue
        wanted = [c for c in cards if c["code"] not in seen and not SKIP_TITLE.search(c["title"])]
        log(f"sigpub:{slug}: {len(cards)} matérias, {len(wanted)} para ler")
        for card in wanted:
            seen.add(card["code"])
            try:
                sleep(DELAY)
                page = fetch(materia_url(slug, card["code"])).decode("utf-8", errors="replace")
            except Exception as err:  # noqa: BLE001
                log(f"  sigpub:{slug}/{card['code']}: {err}")
                continue
            items.extend(items_from_page(page, card, slug, uf, extract, to_text))
    return items


def _search_term(slug, term, since, until, fetch, sleep, log, cards: dict[str, dict]) -> None:
    """Todas as matérias de um termo no período. A busca devolve no máximo MAX_PAGES páginas; quando o teto é
    atingido o período é dividido ao meio e cada metade é pesquisada de novo, até caber ou chegar a um dia."""
    for page in range(1, MAX_PAGES + 1):
        sleep(DELAY)
        result = parse_search(fetch(search_url(slug, term, since, until, page)).decode("utf-8", errors="replace"))
        for card in result["cards"]:
            cards.setdefault(card["code"], card)
        if page * PAGE_SIZE >= result["total"] or not result["cards"]:
            return
    if since >= until:
        log(f"sigpub:{slug}: busca por {term!r} em {since} cortada em {MAX_PAGES} páginas")
        return
    middle = since + (until - since) // 2
    _search_term(slug, term, since, middle, fetch, sleep, log, cards)
    _search_term(slug, term, middle + dt.timedelta(days=1), until, fetch, sleep, log, cards)


def _search_all(slug, since, today, fetch, sleep, log) -> list[dict]:
    cards: dict[str, dict] = {}
    for term in TERMS:
        _search_term(slug, term, since, today, fetch, sleep, log, cards)
    return list(cards.values())


def items_from_page(page: str, card: dict, slug: str, uf: str, extract, to_text) -> list[dict]:
    materia = parse_materia(page, to_text)
    if not materia:
        return []
    city = city_from_entity(materia["entity"]) or city_from_entity(card["entity"])
    if not city:
        return []
    url = materia_url(slug, card["code"])
    gazette = {
        "territory_id": f"sigpub-{slug}-{_plain(city).lower().replace(' ', '-')}",
        "territory_name": city, "state_code": uf, "date": card["date"], "url": url, "txt_url": url,
    }
    name = materia["gazetteName"] or f"Diário Oficial dos Municípios ({slug})"
    published = dt.date.fromisoformat(card["date"]).strftime("%d/%m/%Y")
    out = []
    for index, item in enumerate(extract(materia["text"], gazette)):
        key = f"sigpub|{card['code']}" + (f"|{index}" if index else "")
        # O site do órgão no cabeçalho às vezes entra no nome ("Prefeitura Municipal de Lagoa Santa Www").
        item["title"] = re.sub(r"\s+Www\b.*?(?= - |$)", "", item["title"])
        item["id"] = hashlib.sha1(key.encode()).hexdigest()[:12]
        item["description"] = item["description"].replace(
            f"Fonte: Diário Oficial de {city}/{uf}, {published}.", f"Fonte: {name}, {published}.")
        item.update(source="diario_municipal", collector=f"sigpub:{slug}", sphere="municipal", gazetteName=name)
        out.append(item)
    return out
