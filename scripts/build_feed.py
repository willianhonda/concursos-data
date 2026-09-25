#!/usr/bin/env python3
"""Monta docs/concursos.json a partir dos diários oficiais municipais do Querido Diário.

Só usa a biblioteca padrão do Python, então não precisa de `pip install`.

Fluxo:
1. Busca na API do Querido Diário os diários publicados nos últimos N dias que
   mencionam a abertura de inscrições de um concurso público.
2. Baixa o texto completo de cada diário e localiza os trechos de abertura.
3. Extrai com regex os campos do `EventModel` do app: título, prazo de
   inscrição, UF, salário, vagas, descrição e link.
4. Junta com o feed anterior, remove duplicados e concursos com inscrições
   encerradas, e grava o JSON.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://api.queridodiario.org.br/gazettes"
USER_AGENT = "concursos-data/1.0 (+https://github.com/willianhonda/concursos-data)"
QUERY = (
    '"concurso público" + ("abertas as inscrições" | "abertura das inscrições" | '
    '"inscrições estarão abertas" | "período de inscrições" | "período de inscrição" | '
    '"edital de abertura" | "torna pública a abertura")'
)
PAGE_SIZE = 50
MAX_PAGES = 10
# Sem prazo identificado, o concurso sai do feed depois deste número de dias.
UNDATED_TTL_DAYS = 45

MONTHS = {
    "janeiro": 1, "fevereiro": 2, "marco": 3, "abril": 4, "maio": 5, "junho": 6,
    "julho": 7, "agosto": 8, "setembro": 9, "outubro": 10, "novembro": 11, "dezembro": 12,
}

# Frases que indicam a abertura de um concurso (e não convocação ou resultado).
OPENING = re.compile(
    r"torna(?:r|m)?\s+p[úu]blic[ao]\s+(?:[^.;]{0,120}?\s)?(?:a\s+)?(?:abertura|realiza[çc][ãa]o)[^.;]{0,160}?concurso\s+p[úu]blico"
    r"|torna(?:r|m)?\s+p[úu]blico\s+o\s+edital\s+(?:normativo|de\s+abertura)[^.;]{0,80}?concurso\s+p[úu]blico"
    r"|(?:estar[ãa]o|ficam|est[ãa]o)\s+abertas\s+as\s+inscri[çc][õo]es"
    r"|abertura\s+(?:das|de)\s+inscri[çc][õo]es[^.;]{0,120}?concurso\s+p[úu]blico",
    re.IGNORECASE,
)
# Retificações também indicam um concurso em andamento, mas não trazem os dados completos.
RECTIFICATION = re.compile(r"retifica[çc][ãa]o", re.IGNORECASE)
# Temas que usam as mesmas frases, mas não são concursos (matrícula escolar, conselhos, bolsas).
OFF_TOPIC = re.compile(
    r"processo\s+seletivo\s+simplificado|contrata[çc][ãa]o\s+tempor[áa]ria|conselh[oe]|elei[çc][ãa]o|"
    r"matr[íi]cula|ano\s+letivo|bolsa|est[áa]gio|credenciamento|chamamento\s+p[úu]blico",
    re.IGNORECASE,
)
# Trechos que, logo antes da frase de abertura, indicam outro tipo de ato.
NEGATIVE = re.compile(
    r"convoca|nomea|nomear|homolog|classifica[çc][ãa]o|resultado|posse|desclassifica|investiga[çc][ãa]o\s+social|"
    r"indeferid|recurso|isen[çc][ãa]o|gabarito|"
    r"conforme\s+(?:previsto|prev[êe])|nos\s+termos\s+do\s+edital|constantes?\s+(?:no|do)\s+edital",
    re.IGNORECASE,
)
# Listas de candidatos logo depois da frase indicam resultado, não abertura.
LISTING = re.compile(r"lista\s+de\s+classifica|classifica[çc][ãa]o\s+(?:final|geral)|rela[çc][ãa]o\s+(?:dos\s+)?candidatos", re.IGNORECASE)
# Cabeçalhos que marcam o início do próximo ato no diário.
NEXT_ACT = re.compile(
    r"\n\s*(?:EDITAL\s+DE\s+(?:CONVOCA|RESULTADO|HOMOLOGA|CLASSIFICA)|PORTARIA\s|DECRETO\s|LEI\s+(?:COMPLEMENTAR\s+)?N|"
    r"EXTRATO\s|AVISO\s+DE\s+LICITA|RESOLU[ÇC][ÃA]O\s|ATA\s+D[AE]\s|TERMO\s+DE\s)",
)
ORG = re.compile(
    r"(prefeitura\s+(?:municipal\s+|do\s+munic[íi]pio\s+|da\s+est[âa]ncia\s+[\wÀ-ú]+\s+(?:de\s+)?)?(?:de|do|da)\s+[A-ZÀ-Úa-zà-ú' -]{3,60}?"
    r"|c[âa]mara\s+municipal\s+(?:de|do|da)\s+[A-ZÀ-Úa-zà-ú' -]{3,60}?"
    r"|funda[çc][ãa]o\s+[A-ZÀ-Úa-zà-ú' -]{3,80}?"
    r"|servi[çc]o\s+aut[ôo]nomo\s+[A-ZÀ-Úa-zà-ú' -]{3,80}?"
    r"|instituto\s+(?:de\s+previd[êe]ncia|municipal)\s+[A-ZÀ-Úa-zà-ú' -]{3,80}?"
    r"|autarquia\s+[A-ZÀ-Úa-zà-ú' -]{3,80}?)"
    r"(?=[,.;\n]|\s+(?:torna|no\s+uso|estado|-|–|/|CNPJ|usando|por\s+meio|atrav[ée]s|mediante))",
    re.IGNORECASE,
)
EDITAL_NUMBER = re.compile(
    r"(?:concurso\s+p[úu]blico|edital)[^\n]{0,40}?n[.ºo°]*\s*[:.]?\s*(\d{1,4}\s*[/-]\s*\d{2,4})",
    re.IGNORECASE,
)
DATE_NUM = r"\d{1,2}/\d{1,2}/\d{2,4}"
DATE_TEXT = r"\d{1,2}(?:º|°)?\s+de\s+[a-zç]+(?:\s+de\s+\d{4})?"
DATE_RANGE = re.compile(
    rf"({DATE_NUM}|{DATE_TEXT})\s*(?:\(?[\w\s-]{{0,20}}\)?)?\s*(?:,\s*)?(?:a|at[ée]|e|à|às|-|–)\s+"
    rf"(?:\d{{1,2}}h\d{{0,2}}\s*(?:min)?\s*(?:do\s+dia\s+)?)?({DATE_NUM}|{DATE_TEXT})",
    re.IGNORECASE,
)
DAY_RANGE_TEXT = re.compile(r"\b(\d{1,2})\s+(?:a|e|até)\s+(\d{1,2})\s+de\s+([a-zç]+)(?:\s+de\s+(\d{4}))?", re.IGNORECASE)
MONEY = re.compile(r"R\$\s*(\d{1,3}(?:\.\d{3})*,\d{2})")
VACANCIES_TOTAL = re.compile(r"total\s+de\s+(\d{1,4})\s*(?:\([^)]*\)\s*)?vagas", re.IGNORECASE)
VACANCIES = re.compile(r"\b(\d{1,4})\s*(?:\([a-zà-ú\s]+\)\s*)?vagas?\b", re.IGNORECASE)
URL = re.compile(r"(?:https?://|www\.)[\w.-]+\.[a-z]{2,}(?:/[\w./%?=&#-]*)?", re.IGNORECASE)
GAZETTE_URL_HINTS = ("diario", "diário", "imprensa", "dom.", "doe.", "queridodiario", "iomat", "iom",
                     "jus.br", "inss", "receita", "tse.", "esocial", "planalto", "caixa.gov")


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def http_get(url: str, *, attempts: int = 5, timeout: int = 60) -> bytes:
    """GET com nova tentativa: a API do Querido Diário às vezes responde 503."""
    delay = 10
    for attempt in range(1, attempts + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except (urllib.error.URLError, TimeoutError) as err:
            if attempt == attempts:
                raise
            log(f"  tentativa {attempt} falhou ({err}); nova tentativa em {delay}s")
            time.sleep(delay)
            delay *= 2
    raise RuntimeError("unreachable")


def search_gazettes(since: dt.date) -> list[dict]:
    gazettes: list[dict] = []
    for page in range(MAX_PAGES):
        params = {
            "querystring": QUERY,
            "published_since": since.isoformat(),
            "size": PAGE_SIZE,
            "offset": page * PAGE_SIZE,
            "excerpt_size": 100,
            "number_of_excerpts": 1,
            "sort_by": "descending_date",
        }
        data = json.loads(http_get(f"{API}?{urllib.parse.urlencode(params)}"))
        batch = data.get("gazettes", [])
        gazettes.extend(batch)
        log(f"página {page + 1}: {len(batch)} diários (total na API: {data.get('total_gazettes')})")
        if len(batch) < PAGE_SIZE:
            break
        time.sleep(1)
    return gazettes


# ---------------------------------------------------------------- extração

def squash(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def strip_accents(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn")


def parse_date(raw: str, default_year: int) -> dt.date | None:
    raw = raw.strip().lower()
    m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{2,4})", raw)
    if m:
        d, mo, y = (int(x) for x in m.groups())
        y = y + 2000 if y < 100 else y
    else:
        m = re.fullmatch(r"(\d{1,2})(?:º|°)?\s+de\s+([a-zç]+)(?:\s+de\s+(\d{4}))?", raw)
        if not m:
            return None
        mo = MONTHS.get(strip_accents(m.group(2)))
        if not mo:
            return None
        d, y = int(m.group(1)), int(m.group(3) or default_year)
    try:
        return dt.date(y, mo, d)
    except ValueError:
        return None


def find_inscription_period(text: str, published: dt.date) -> tuple[dt.date, dt.date] | None:
    """Procura um intervalo de datas logo depois da palavra 'inscrição' ou 'inscrições'."""
    for m in re.finditer(r"inscri[çc](?:[ãa]o|[õo]es)", text, re.IGNORECASE):
        chunk = text[m.end(): m.end() + 420]
        for rng in DATE_RANGE.finditer(chunk):
            end = parse_date(rng.group(2), published.year)
            start = parse_date(rng.group(1), end.year if end else published.year)
            if start and end and start <= end and (end - start).days <= 120 and end >= published - dt.timedelta(days=7):
                return start, end
        rng = DAY_RANGE_TEXT.search(chunk)
        if rng:
            year = int(rng.group(4) or published.year)
            mo = MONTHS.get(strip_accents(rng.group(3).lower()))
            if mo:
                try:
                    start, end = dt.date(year, mo, int(rng.group(1))), dt.date(year, mo, int(rng.group(2)))
                    if start <= end:
                        return start, end
                except ValueError:
                    pass
    return None


def find_salary(text: str) -> str:
    values = []
    for m in MONEY.finditer(text):
        before = text[max(0, m.start() - 25): m.start()].lower()
        if re.search(r"taxa|inscri[çc]|isen[çc]|multa", before):
            continue
        value = float(m.group(1).replace(".", "").replace(",", "."))
        if 1_000 <= value <= 60_000:
            values.append(value)
    if not values:
        return ""

    def brl(v: float) -> str:
        return "R$ " + f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

    lo, hi = min(values), max(values)
    return f"Salário: {brl(lo)}" if lo == hi else f"Faixa salarial: {brl(lo)} - {brl(hi)}"


def find_vacancies(text: str) -> str:
    total = VACANCIES_TOTAL.search(text)
    if total:
        return f"Vagas {int(total.group(1))}"
    found = {int(m.group(1)) for m in VACANCIES.finditer(text) if 0 < int(m.group(1)) < 5000}
    if len(found) == 1:
        return f"Vagas {found.pop()}"
    return ""


def find_org(pre: str, window: str, city: str) -> str:
    candidates = list(ORG.finditer(pre)) or list(ORG.finditer(window[:1500]))
    if candidates:
        org = squash(candidates[-1].group(1))
        org = re.sub(r"\s+estado\b.*$", "", org, flags=re.IGNORECASE)
        if 8 <= len(org) <= 90:
            small = {"de", "do", "da", "dos", "das", "e"}
            return " ".join(w if w.lower() in small else w.capitalize() for w in org.lower().split())
    return f"Município de {city}"


def find_link(window: str, fallback: str) -> str:
    links = []
    for m in URL.finditer(window):
        link = m.group(0).rstrip(".,;)")
        if any(h in link.lower() for h in GAZETTE_URL_HINTS):
            continue
        links.append(link)
    if not links:
        return fallback
    best = next((l for l in links if re.search(r"concurso|selec|inscri|edital", l, re.IGNORECASE)), links[0])
    return best if best.lower().startswith("http") else f"https://{best}"


def extract_openings(text: str, gazette: dict) -> list[dict]:
    published = dt.date.fromisoformat(gazette["date"])
    city, uf = gazette["territory_name"], gazette["state_code"]
    items, last_end = [], -1
    for m in OPENING.finditer(text):
        if m.start() < last_end:
            continue
        around = text[max(0, m.start() - 160): m.end() + 160]
        if NEGATIVE.search(around) or OFF_TOPIC.search(around):
            continue
        context = text[max(0, m.start() - 600): m.end() + 400]
        if not re.search(r"concurso\s+p[úu]blico", context, re.IGNORECASE):
            continue
        is_rectification = bool(RECTIFICATION.search(text[max(0, m.start() - 300): m.end()]))
        start = max(0, m.start() - 600)
        nxt = NEXT_ACT.search(text, m.end() + 150)
        act_end = nxt.start() if nxt else len(text)
        end = min(act_end, m.end() + 5000)
        window = text[start:end]
        if LISTING.search(text[m.end(): m.end() + 1500]):
            continue
        # Editais completos trazem o capítulo "Das inscrições" bem depois do preâmbulo.
        full_act = text[start: min(act_end, m.end() + 30000)]
        last_end = end

        period = find_inscription_period(full_act, published)
        number = EDITAL_NUMBER.search(window)
        number_str = re.sub(r"\s+", "", number.group(1)).replace("-", "/") if number else ""
        org = find_org(text[max(0, m.start() - 600): m.end()], window, city)

        title = org + (f" - Concurso Público nº {number_str}" if number_str else " - Concurso Público")
        if is_rectification:
            title += " (retificação)"
        snippet = squash(text[max(start, m.start() - 250): min(end, m.end() + 650)])
        description = (
            f"{snippet}\n\nFonte: Diário Oficial de {city}/{uf}, {published.strftime('%d/%m/%Y')}. "
            "Informações extraídas automaticamente; confira sempre o edital oficial."
        )
        key = (
            f"{gazette['territory_id']}|{number_str}" if number_str
            else f"{gazette['territory_id']}|{strip_accents(org.lower())}|{published.isoformat()}"
        )
        items.append({
            "id": hashlib.sha1(key.encode()).hexdigest()[:12],
            "title": title,
            "deadline": (
                f"Inscrições: {period[0].strftime('%d/%m/%Y')} a {period[1].strftime('%d/%m/%Y')}"
                if period else "Inscrições: veja o edital"
            ),
            "state": uf,
            "salary": find_salary(full_act[:12000]),
            "vacancies": find_vacancies(full_act[:12000]),
            "description": description,
            "url": find_link(window, gazette.get("url") or gazette.get("txt_url", "")),
            # Campos extras: o app ignora, mas o script usa na junção.
            "city": city,
            "published": published.isoformat(),
            "registrationEnds": period[1].isoformat() if period else None,
            "gazetteUrl": gazette.get("url", ""),
        })
    return items


# ---------------------------------------------------------------- feed

def is_current(item: dict, today: dt.date) -> bool:
    if item.get("registrationEnds"):
        return dt.date.fromisoformat(item["registrationEnds"]) >= today
    return dt.date.fromisoformat(item["published"]) >= today - dt.timedelta(days=UNDATED_TTL_DAYS)


def merge(old: list[dict], new: list[dict], today: dt.date) -> list[dict]:
    by_id = {i["id"]: i for i in old}
    for item in new:
        prev = by_id.get(item["id"])
        if prev:
            # Mantém a primeira publicação, mas preenche campos que antes vieram vazios.
            for k, v in item.items():
                if v and not prev.get(k):
                    prev[k] = v
            if item.get("registrationEnds"):
                prev["registrationEnds"], prev["deadline"] = item["registrationEnds"], item["deadline"]
        else:
            by_id[item["id"]] = item
    current = [i for i in by_id.values() if is_current(i, today)]
    return sorted(current, key=lambda i: (i["published"], i["id"]), reverse=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--days", type=int, default=7, help="dias para trás na busca (padrão: 7)")
    parser.add_argument("--output", type=Path, default=Path("docs/concursos.json"))
    args = parser.parse_args()

    today = dt.date.today()
    old = json.loads(args.output.read_text()) if args.output.exists() else []
    gazettes = search_gazettes(today - dt.timedelta(days=args.days))

    new: list[dict] = []
    for g in gazettes:
        try:
            text = http_get(g["txt_url"]).decode("utf-8", errors="replace")
        except Exception as err:  # noqa: BLE001 - um diário com erro não derruba o job
            log(f"  pulando {g['territory_name']} {g['date']}: {err}")
            continue
        found = extract_openings(text, g)
        log(f"{g['territory_name']}/{g['state_code']} {g['date']}: {len(found)} abertura(s)")
        new.extend(found)
        time.sleep(0.5)

    feed = merge(old, new, today)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(feed, ensure_ascii=False, indent=1) + "\n")
    log(f"feed: {len(feed)} concursos ({len(new)} encontrados nesta execução)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
