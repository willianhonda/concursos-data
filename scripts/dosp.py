"""Coletor do Diário Oficial da Cidade de São Paulo (diariooficial.prefeitura.sp.gov.br).

O Querido Diário não tem spider de São Paulo (foi removido em 2025), então este coletor lê o portal da prefeitura:

1. pesquisa a seção "Concursos" no período (formulário POST, 10 matérias por página, sem termo);
2. fica só com os documentos do tipo "Edital" e "Edital de Concurso Público": o resto da seção são
   convocações, exames médicos, comunicados e movimentações de pessoal;
3. baixa a matéria e passa o texto pelo mesmo `extract_openings` do Querido Diário;
4. marca o item: id = sha1("dosp|<número do documento>"), source `diario_municipal`, collector `dosp`, sphere `municipal`.

O robots.txt do portal não existe (404). Usamos o User-Agent identificável do projeto e 1 s entre requisições.
Editais longos citam a banca e outros órgãos, e o extrator pode devolver mais de um item por matéria: ficamos
com um só por matéria (o que tem datas de inscrição) e o órgão é sempre a Prefeitura de São Paulo.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import html
import re
import time
import urllib.parse
import urllib.request
from typing import Callable

BASE = "https://diariooficial.prefeitura.sp.gov.br/md_epubli_controlador.php?acao=materias_pesquisar"
USER_AGENT = "concursos-data/1.0 (+https://github.com/willianhonda/concursos-data)"
SECAO_CONCURSOS = "5"
PAGE_SIZE = 10
MAX_PAGES = 30
DELAY = 1.0
CITY, UF, TERRITORY = "São Paulo", "SP", "3550308"
GAZETTE_NAME = "Diário Oficial da Cidade de São Paulo"

# Tipos de documento que podem ser a abertura de um concurso.
OPENING_TYPE = re.compile(r"^edital(?:\s+de\s+concurso\s+p[úu]blico)?$", re.IGNORECASE)
TOTAL = re.compile(r'Exibindo.*?de\s*<span class="nroResultados">(\d+)</span>', re.DOTALL)
DOC = re.compile(
    r'Documento:\s*</span>\s*<a href="([^"]*md_epubli_visualizar[^"]*)"[^>]*>(\d+)</a>\s*<span>\s*-\s*([^<]*)</span>',
    re.DOTALL,
)
PUBLISHED = re.compile(r"Publicado em (\d{2})/(\d{2})/(\d{4})")
ORGAO = re.compile(r'detalhesOrgao">\s*([^<\s][^<]*?)\s*<span class="tooltiptext">([^<]*)</span>', re.DOTALL)


def _text(raw: str) -> str:
    return html.unescape(re.sub(r"\s+", " ", raw)).strip()


def search_form(since: dt.date, until: dt.date, start: int) -> dict[str, str]:
    """Campos do formulário de pesquisa avançada: seção Concursos, sem termo, por data de publicação."""
    return {
        "hdnTermoPesquisa": "", "hdnModoPesquisa": "AVANCADA", "hdnTipoPesquisa": "Q", "hdnVersaoDiario": "A",
        "hdnTipoDataPesquisa": "P", "hdnDataInicioPesquisa": since.strftime("%d/%m/%Y"),
        "hdnDataFimPesquisa": until.strftime("%d/%m/%Y"), "hdnVeiculoPublicacao": SECAO_CONCURSOS,
        "hdnInicio": str(start), "hdnVisualizacao": "L",
    }


def parse_search(page: str) -> dict:
    """Cartões de uma página de resultados e o total de matérias da pesquisa."""
    cards = []
    for block in page.split('<div class="dadosDocumento">')[1:]:
        doc, published = DOC.search(block), PUBLISHED.search(block)
        if not doc or not published:
            continue
        organ = ORGAO.search(block)
        day, month, year = published.groups()
        cards.append({
            "url": html.unescape(doc.group(1)).replace("http://", "https://", 1),
            "number": doc.group(2), "type": _text(doc.group(3)), "date": f"{year}-{month}-{day}",
            "organ": _text(organ.group(2)) if organ else "",
        })
    total = TOTAL.search(page)
    return {"cards": cards, "total": int(total.group(1)) if total else len(cards)}


def post(url: str, fields: dict[str, str]) -> bytes:
    req = urllib.request.Request(
        url, data=urllib.parse.urlencode(fields).encode(), headers={"User-Agent": USER_AGENT}, method="POST")
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read()


def search_all(since: dt.date, until: dt.date, send: Callable[[str, dict], bytes], sleep, log) -> list[dict]:
    cards: dict[str, dict] = {}
    for page in range(MAX_PAGES):
        sleep(DELAY)
        result = parse_search(send(BASE, search_form(since, until, page * PAGE_SIZE)).decode("latin-1"))
        for card in result["cards"]:
            cards.setdefault(card["number"], card)
        if (page + 1) * PAGE_SIZE >= result["total"] or not result["cards"]:
            return list(cards.values())
    log(f"dosp: pesquisa de {since} a {until} cortada em {MAX_PAGES} páginas")
    return list(cards.values())


def is_opening_type(card: dict) -> bool:
    return bool(OPENING_TYPE.match(card["type"]))


def items_from_page(page: str, card: dict, extract, to_text) -> list[dict]:
    """O item da matéria: o que tem datas de inscrição (senão o primeiro), com o órgão trocado pela Prefeitura."""
    gazette = {
        "territory_id": TERRITORY, "territory_name": CITY, "state_code": UF, "date": card["date"],
        "url": card["url"], "txt_url": card["url"],
    }
    found = extract(to_text(page), gazette)
    if not found:
        return []
    item = next((i for i in found if i.get("registrationEnds")), found[0])
    published = dt.date.fromisoformat(card["date"]).strftime("%d/%m/%Y")
    # O órgão que o extrator acha num edital longo costuma ser a banca; o ato é sempre da Prefeitura.
    item["title"] = re.sub(r"^.*? - ", "Prefeitura de São Paulo - ", item["title"], count=1)
    item["id"] = hashlib.sha1(f"dosp|{card['number']}".encode()).hexdigest()[:12]
    item["description"] = item["description"].replace(
        f"Fonte: Diário Oficial de {CITY}/{UF}, {published}.", f"Fonte: {GAZETTE_NAME}, {published}.")
    item.update(source="diario_municipal", collector="dosp", sphere="municipal", gazetteName=GAZETTE_NAME,
                gazetteUrl=card["url"], city=CITY, state=UF)
    return [item]


def collect(
    since: dt.date,
    today: dt.date,
    *,
    fetch: Callable[[str], bytes],
    extract: Callable[[str, dict], list[dict]],
    to_text: Callable[[str], str],
    off: set[str] | None = None,
    send: Callable[[str, dict], bytes] = post,
    sleep: Callable[[float], None] = time.sleep,
    log: Callable[[str], None] = lambda msg: None,
) -> list[dict]:
    """Aberturas de concurso do Diário Oficial de São Paulo. Falha numa matéria é registrada e não derruba as outras."""
    if "dosp" in (off or set()):
        log("dosp: desligado em SOURCES_OFF")
        return []
    cards = search_all(since, today, send, sleep, log)
    wanted = [c for c in cards if is_opening_type(c)]
    log(f"dosp: {len(cards)} matérias na seção Concursos, {len(wanted)} para ler")
    items: list[dict] = []
    for card in wanted:
        try:
            sleep(DELAY)
            page = fetch(card["url"]).decode("utf-8", errors="replace")
        except Exception as err:  # noqa: BLE001
            log(f"  dosp/{card['number']}: {err}")
            continue
        items.extend(items_from_page(page, card, extract, to_text))
    return items
