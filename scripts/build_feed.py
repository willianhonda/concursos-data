#!/usr/bin/env python3
"""Monta docs/concursos.json com concursos e processos seletivos com inscrições abertas.

Fontes:
- diários oficiais municipais, pela API do Querido Diário;
- Diário Oficial da União (Seção 3), pelo INLABS da Imprensa Nacional. Precisa das
  variáveis INLABS_EMAIL e INLABS_PASSWORD (cadastro gratuito); sem elas, o DOU é pulado.

Só usa a biblioteca padrão do Python, então não precisa de `pip install`.

Fluxo:
1. Busca na API do Querido Diário os diários publicados nos últimos N dias que
   mencionam a abertura de inscrições de um concurso público ou processo seletivo,
   e baixa as edições do DOU dos mesmos dias.
2. Baixa o texto completo de cada diário e localiza os atos de abertura.
3. Extrai com regex os campos do `EventModel` do app: título, prazo de
   inscrição, UF, salário, vagas, descrição e link.
4. Junta com o feed anterior, remove duplicados e concursos com inscrições
   encerradas, e grava o JSON.
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import http.cookiejar
import io
import os
import hashlib
import json
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from validate_feed import item_errors

API = "https://api.queridodiario.org.br/gazettes"
USER_AGENT = "concursos-data/1.0 (+https://github.com/willianhonda/concursos-data)"
QUERY = (
    '("concurso público" | "processo seletivo") + ("abertas as inscrições" | "abertura das inscrições" | '
    '"inscrições estarão abertas" | "período de inscrições" | "período de inscrição" | '
    '"edital de abertura" | "torna pública a abertura")'
)
PAGE_SIZE = 50
MAX_PAGES = 20
# Sem prazo identificado, o concurso sai do feed depois deste número de dias.
UNDATED_TTL_DAYS = 45

MONTHS = {
    "janeiro": 1, "fevereiro": 2, "marco": 3, "abril": 4, "maio": 5, "junho": 6,
    "julho": 7, "agosto": 8, "setembro": 9, "outubro": 10, "novembro": 11, "dezembro": 12,
}

# Concurso público ou processo seletivo (simplificado, público etc.).
SEL = r"(?:concurso\s+p[úu]blico|processo\s+seletivo)"
SELECTION = re.compile(SEL, re.IGNORECASE)
CONCURSO = re.compile(r"concurso\s+p[úu]blico", re.IGNORECASE)
# Frases que indicam a abertura de um concurso ou processo seletivo (e não convocação ou resultado).
OPENING = re.compile(
    r"torna(?:r|m)?\s+p[úu]blic[ao]\s+(?:[^.;]{0,120}?\s)?(?:a\s+)?(?:abertura|realiza[çc][ãa]o)[^.;]{0,160}?" + SEL
    + r"|torna(?:r|m)?\s+p[úu]blico\s+o\s+edital\s+(?:normativo|de\s+abertura)[^.;]{0,80}?" + SEL
    + r"|(?:estar[ãa]o|ficam|est[ãa]o)\s+abertas\s+as\s+inscri[çc][õo]es"
    r"|abertura\s+(?:das|de)\s+inscri[çc][õo]es[^.;]{0,120}?" + SEL,
    re.IGNORECASE,
)
# Retificações também indicam um concurso em andamento, mas não trazem os dados completos.
RECTIFICATION = re.compile(r"retifica[çc][ãa]o", re.IGNORECASE)
# Temas que usam as mesmas frases, mas não são vagas de emprego público
# (matrícula escolar, conselhos, bolsas, pós-graduação, vestibular).
OFF_TOPIC = re.compile(
    r"conselh[oe]|elei[çc][ãa]o|matr[íi]cula|ano\s+letivo|bolsa|est[áa]gio|credenciamento|chamamento\s+p[úu]blico|"
    r"mestrado|doutorado|p[óo]s-gradua|resid[êe]ncia\s+(?:m[ée]dica|multiprofissional)|vestibular|monitoria|aluno|discente|"
    r"curso\s+(?:de\s+)?(?:licenciatura|gradua[çc][ãa]o|bacharelado|especializa[çc][ãa]o|t[ée]cnico)",
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
    r"(?:concurso\s+p[úu]blico|processo\s+seletivo(?:\s+simplificado)?|edital)[^\n]{0,40}?n[.ºo°]*\s*[:.]?\s*(\d{1,4}\s*[/-]\s*\d{2,4})",
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
GAZETTE_URL_HINTS = ("diario", "diário", "imprensa", "in.gov.br", "dom.", "doe.", "queridodiario", "iomat", "iom",
                     "jus.br", "inss", "receita", "tse.", "esocial", "planalto", "caixa.gov")
# Domínios de bancas organizadoras conhecidas: o edital e a inscrição costumam estar no site delas.
# "concurso" pega bancas menores (abconcursospublicos.org, cmmconcursos.com.br); sites .gov.br ficam de fora.
BANCA_HOSTS = (
    "concurso", "consesp", "consulpam", "institutoiacp", "avalia.org", "selecao.net.br", "indepac", "vunesp", "fgv", "cebraspe", "cespe", "cesgranrio", "ibfc", "aocp", "fundatec", "consulplan", "idecan", "quadrix",
    "ibam", "ipell", "concursosfcc", "fcc.org", "objetivas", "legalle", "avancasp", "institutomais", "omniconcursos",
    "ibade", "selecon", "fafipa", "gestaoconcursos", "fundep", "rboconcursos", "nossorumo", "ibgp", "itame",
    "fepese", "furb", "faurgs", "fumarc", "cpcon", "conscamweb", "metodoesolucoes", "shdias", "igecs", "agirh",
    "publiconsult", "ameosc", "unoesc", "acafe", "institutoconsulplan", "excelenciaconcursos", "noroesteconcursos",
)
# Frase que nomeia a banca: "será executado pela IPELL CONSULTORIA LTDA".
BANCA_NAME = re.compile(
    r"(?:executad[oa]|realizad[oa]|organizad[oa]|operacionalizad[oa]|aplicad[oa])\s+(?:pel[oa]|por)\s+"
    r"(?:empresa\s+|institui[çc][ãa]o\s+)?([A-ZÀ-Ú][\w&À-ú-]{2,})",
)
# Contexto logo antes do link que indica o local de inscrição ou do edital.
LINK_CONTEXT = re.compile(r"inscri|endere[çc]o\s+eletr[ôo]nico|s[íi]tio|site|portal|edital", re.IGNORECASE)
LINK_PATH = re.compile(r"concurso|sele[cçt]|inscri|edital", re.IGNORECASE)
# Páginas de serviço que citam o concurso, mas não são o edital (restituição da taxa, isenção, recurso).
LINK_PATH_OTHER = re.compile(r"restitui|isen[cç]|recurso|gabarito|resultado", re.IGNORECASE)
# Continuação de um link quebrado no fim da linha: "…/processo-\nseletivo-2026".
URL_TAIL = re.compile(r"\s*\n\s*([\w./%?=&#-]+)")


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


def url_host(link: str) -> str:
    host = urllib.parse.urlsplit(link if "://" in link else f"https://{link}").hostname or ""
    return host.removeprefix("www.")


def find_link(text: str, fallback: str) -> tuple[str, str]:
    """Escolhe o link do edital no texto do ato e diz o que ele é.

    Devolve (url, tipo), com tipo "edital", "banca", "orgao" ou "diario" (o próprio diário, quando
    nenhum link serve). O site que publica o diário aparece no cabeçalho de cada página, então ele
    perde pontos: o link útil costuma ser o da banca, citado no capítulo das inscrições.
    """
    found = []
    for m in URL.finditer(text):
        link = m.group(0)
        tail = URL_TAIL.match(text, m.end()) if link.endswith(("-", "/", "_")) else None
        found.append((m.start(), (link + tail.group(1) if tail else link).rstrip(".,;)")))
    gazette_hosts = {url_host(l) for _, l in found if any(h in l.lower() for h in GAZETTE_URL_HINTS)}
    banca = BANCA_NAME.search(text)
    banca_token = strip_accents(banca.group(1)).lower() if banca else ""

    best, best_score = None, None
    for pos, link in found:
        lower = link.lower()
        if any(h in lower for h in GAZETTE_URL_HINTS):
            continue
        host = url_host(link)
        path = urllib.parse.urlsplit(link if "://" in link else f"https://{link}").path.strip("/")
        is_banca = (any(b in host for b in BANCA_HOSTS) and not host.endswith(".gov.br")) or (
            len(banca_token) >= 3 and banca_token in host
        )
        score = 0
        score += 5 if is_banca else 0
        score += 3 if LINK_PATH.search(path) and not LINK_PATH_OTHER.search(path) else 0
        score -= 3 if LINK_PATH_OTHER.search(path) else 0
        score += 2 if LINK_CONTEXT.search(text[max(0, pos - 150): pos]) else 0
        score -= 6 if host in gazette_hosts else 0
        if best_score is None or score > best_score:
            best, best_score = (link, is_banca, path), score
    if best is None or best_score < 0:
        return fallback, "diario"
    link, is_banca, path = best
    url = link if link.lower().startswith("http") else f"https://{link}"
    if (LINK_PATH.search(path) and not LINK_PATH_OTHER.search(path)) or path.lower().endswith(".pdf"):
        return url, "edital"
    return url, "banca" if is_banca else "orgao"


# Do link mais útil para o menos útil.
LINK_RANK = {"diario": 0, "orgao": 1, "banca": 2, "edital": 3}


def link_is_broken(url: str) -> bool:
    """Só 404 e 410 contam como link quebrado: sites de prefeitura costumam ter certificado
    vencido ou recusar HEAD, e isso não quer dizer que o link esteja errado."""
    try:
        req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": USER_AGENT})
        urllib.request.urlopen(req, timeout=15).close()
    except urllib.error.HTTPError as err:
        return err.code in (404, 410)
    except Exception:  # noqa: BLE001 - erro de rede ou SSL não prova que o link está quebrado
        return False
    return False


KIND_LABEL = {"concurso": "Concurso Público", "processo_seletivo": "Processo Seletivo"}


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
        if not SELECTION.search(context):
            continue
        kind = "concurso" if CONCURSO.search(text[max(0, m.start() - 300): m.end() + 200]) else "processo_seletivo"
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

        label = KIND_LABEL[kind]
        title = org + (f" - {label} nº {number_str}" if number_str else f" - {label}")
        if is_rectification:
            title += " (retificação)"
        snippet = squash(text[max(start, m.start() - 250): min(end, m.end() + 650)])
        # O ato inteiro, e não só a janela, porque o site da banca costuma vir no capítulo das inscrições.
        url, link_kind = find_link(text[m.start(): min(act_end, m.end() + 30000)], gazette.get("url") or gazette.get("txt_url", ""))
        description = (
            f"{snippet}\n\nFonte: Diário Oficial de {city}/{uf}, {published.strftime('%d/%m/%Y')}. "
            "Informações extraídas automaticamente; confira sempre o edital oficial."
        )
        key = (
            f"{gazette['territory_id']}|{kind}|{number_str}" if number_str
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
            "url": url,
            "linkKind": link_kind,
            # Campos extras: o app ignora, mas o script usa na junção.
            "kind": kind,
            "source": "querido_diario",
            "city": city,
            "published": published.isoformat(),
            "registrationStarts": period[0].isoformat() if period else None,
            "registrationEnds": period[1].isoformat() if period else None,
            "gazetteUrl": gazette.get("url", ""),
        })
    return items


# ---------------------------------------------------------------- DOU (INLABS)

INLABS_LOGIN = "https://inlabs.in.gov.br/logar.php"
INLABS_DOWNLOAD = "https://inlabs.in.gov.br/index.php?p={day}&dl={day}-{section}.zip"
DOU_SECTIONS = ("DO3", "DO3E")
DOU_OPENING = re.compile(
    r"torna(?:r|m)?\s+p[úu]blic[ao]s?\s+(?:[^.;]{0,160}?\s)?(?:a\s+)?(?:abertura|realiza[çc][ãa]o)"
    r"|(?:estar[ãa]o|ficam|est[ãa]o)\s+abertas\s+as\s+inscri[çc][õo]es"
    r"|abertura\s+(?:das|de)\s+inscri[çc][õo]es|edital\s+de\s+abertura",
    re.IGNORECASE,
)
# No cabeçalho do ato, indicam que não é a abertura (convocação, resultado, alteração).
DOU_NEGATIVE = re.compile(
    r"convoca|nomea|homolog|resultado|classifica[çc][ãa]o|retifica|altera[çc][ãa]o|aditivo|prorroga|"
    r"reabertura|gabarito|recurso|cancela|suspen|revoga|torna\s+sem\s+efeito",
    re.IGNORECASE,
)
UF_BY_NAME = {
    "acre": "AC", "alagoas": "AL", "amapa": "AP", "amazonas": "AM", "bahia": "BA", "ceara": "CE",
    "distrito federal": "DF", "espirito santo": "ES", "goias": "GO", "maranhao": "MA", "mato grosso do sul": "MS",
    "mato grosso": "MT", "minas gerais": "MG", "para": "PA", "paraiba": "PB", "parana": "PR", "pernambuco": "PE",
    "piaui": "PI", "rio de janeiro": "RJ", "rio grande do norte": "RN", "rio grande do sul": "RS", "rondonia": "RO",
    "roraima": "RR", "santa catarina": "SC", "sao paulo": "SP", "sergipe": "SE", "tocantins": "TO",
}
UFS = "|".join(sorted(set(UF_BY_NAME.values())))
# Sede de cada região da Justiça do Trabalho e da Justiça Federal.
TRT_UF = {1: "RJ", 2: "SP", 3: "MG", 4: "RS", 5: "BA", 6: "PE", 7: "CE", 8: "PA", 9: "PR", 10: "DF", 11: "AM", 12: "SC",
          13: "PB", 14: "RO", 15: "SP", 16: "MA", 17: "ES", 18: "GO", 19: "AL", 20: "SE", 21: "RN", 22: "PI", 23: "MT", 24: "MS"}
TRF_UF = {1: "DF", 2: "RJ", 3: "SP", 4: "RS", 5: "PE", 6: "MG"}
REGION = re.compile(r"tribunal\s+regional\s+(do\s+trabalho|federal)\s+da\s+(\d{1,2})[ªa]\s+regi[ãa]o", re.IGNORECASE)
CITY_UF = re.compile(rf"\b[A-ZÀ-Ú][\wÀ-ú' ]{{2,40}}\s*[/-]\s*({UFS})\b")
# "(?!\w)" evita que "Pará" case com "Paraná" e "Mato Grosso" com "Mato Grosso do Sul".
STATE_NAME = re.compile(
    r"\b(?:estado|universidade\s+(?:federal\s+)?|instituto\s+federal)\s*(?:do|da|de)?\s+("
    + "|".join(sorted(UF_BY_NAME, key=len, reverse=True)) + r")(?!\w)",
    re.IGNORECASE,
)


def html_to_text(raw: str) -> str:
    text = re.sub(r"<\s*(?:br|/p|/tr|/li|/h\d)\s*/?>", "\n", raw, flags=re.IGNORECASE)
    text = html.unescape(re.sub(r"<[^>]+>", " ", text))
    return re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n", text)).strip()


def dou_org(category: str) -> str:
    """artCategory vem como "Ministério da Educação/Universidade Federal do Ceará/Pró-Reitoria…"."""
    parts = [p.strip() for p in category.split("/") if p.strip()]
    if not parts:
        return "Governo Federal"
    return parts[1] if len(parts) > 1 else parts[0]


def dou_uf(org: str, text: str) -> str:
    region = REGION.search(org)
    if region:
        table = TRT_UF if "trabalho" in region.group(1).lower() else TRF_UF
        if int(region.group(2)) in table:
            return table[int(region.group(2))]
    for source in (org, text[:3000]):
        m = STATE_NAME.search(strip_accents(source))
        if m:
            return UF_BY_NAME[m.group(1).lower()]
    m = CITY_UF.search(text[:6000])
    return m.group(1) if m else "BR"


def extract_dou_article(article: ET.Element) -> dict | None:
    """Converte um <article> do XML do INLABS num item do feed, se for abertura de seleção."""
    body = article.find("body")
    if body is None:
        return None
    identifica = squash(html_to_text(body.findtext("Identifica") or ""))
    text = html_to_text(body.findtext("Texto") or "")
    header = f"{article.get('artType', '')} {identifica} {text[:700]}"
    if not SELECTION.search(header) or not DOU_OPENING.search(text[:3000]):
        return None
    if DOU_NEGATIVE.search(f"{article.get('artType', '')} {identifica} {text[:250]}") or OFF_TOPIC.search(header):
        return None
    try:
        published = dt.datetime.strptime(article.get("pubDate", ""), "%d/%m/%Y").date()
    except ValueError:
        return None

    kind = "concurso" if CONCURSO.search(header) else "processo_seletivo"
    org = dou_org(article.get("artCategory", ""))
    number = re.search(r"n[º°o.]*\s*(\d{1,4}(?:\s*/\s*\d{2,4})?)", identifica, re.IGNORECASE)
    number_str = re.sub(r"\s+", "", number.group(1)) if number else ""
    if number_str and "/" not in number_str:
        number_str += f"/{published.year}"
    title = f"{org} - {KIND_LABEL[kind]}" + (f" (Edital nº {number_str})" if number_str else "")
    period = find_inscription_period(text, published)
    gazette_url = article.get("pdfPage", "")
    section = article.get("pubName", "DO3").upper().replace("DO", "Seção ").replace("E", " (extra)")
    snippet = squash(text[:900])
    url, link_kind = find_link(text, gazette_url)
    return {
        "id": hashlib.sha1(f"dou|{article.get('idMateria') or article.get('id')}".encode()).hexdigest()[:12],
        "title": title,
        "deadline": (
            f"Inscrições: {period[0].strftime('%d/%m/%Y')} a {period[1].strftime('%d/%m/%Y')}"
            if period else "Inscrições: veja o edital"
        ),
        "state": dou_uf(org, text),
        "salary": find_salary(text[:12000]),
        "vacancies": find_vacancies(text[:12000]),
        "description": (
            f"{snippet}\n\nFonte: Diário Oficial da União, {section}, {published.strftime('%d/%m/%Y')}. "
            "Informações extraídas automaticamente; confira sempre o edital oficial."
        ),
        "url": url,
        "linkKind": link_kind,
        "kind": kind,
        "source": "dou",
        "city": None,
        "published": published.isoformat(),
        "registrationStarts": period[0].isoformat() if period else None,
        "registrationEnds": period[1].isoformat() if period else None,
        "gazetteUrl": gazette_url,
    }


def extract_dou_zip(data: bytes) -> list[dict]:
    items = []
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        for name in zf.namelist():
            if not name.lower().endswith(".xml"):
                continue
            try:
                root = ET.fromstring(zf.read(name))
            except ET.ParseError as err:
                log(f"  XML inválido {name}: {err}")
                continue
            for article in root.iter("article"):
                item = extract_dou_article(article)
                if item:
                    items.append(item)
    return items


def fetch_dou(since: dt.date, today: dt.date) -> list[dict]:
    email, password = os.environ.get("INLABS_EMAIL"), os.environ.get("INLABS_PASSWORD")
    if not email or not password:
        log("DOU: INLABS_EMAIL/INLABS_PASSWORD não definidos; pulando o DOU")
        return []
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    login = urllib.request.Request(
        INLABS_LOGIN,
        data=urllib.parse.urlencode({"email": email, "password": password}).encode(),
        headers={"User-Agent": USER_AGENT, "Content-Type": "application/x-www-form-urlencoded"},
    )
    opener.open(login, timeout=60).read()
    cookie = next((c.value for c in jar if c.name == "inlabs_session_cookie"), None)
    if not cookie:
        raise RuntimeError("INLABS: login falhou (confira INLABS_EMAIL e INLABS_PASSWORD)")

    items: list[dict] = []
    day = since
    while day <= today:
        for section in DOU_SECTIONS:
            url = INLABS_DOWNLOAD.format(day=day.isoformat(), section=section)
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "origem": "736372697074"})
            try:
                with opener.open(req, timeout=120) as resp:
                    data = resp.read()
            except urllib.error.HTTPError as err:
                if err.code != 404:  # 404 = sem edição (fim de semana, feriado, sem extra)
                    log(f"DOU {day} {section}: HTTP {err.code}")
                continue
            if not data.startswith(b"PK"):
                log(f"DOU {day} {section}: resposta não é ZIP; pulando")
                continue
            found = extract_dou_zip(data)
            log(f"DOU {day} {section}: {len(found)} abertura(s)")
            items.extend(found)
            time.sleep(1)
        day += dt.timedelta(days=1)
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
            # Fica o link de melhor tipo; no empate, o da extração mais nova. Feeds antigos não têm linkKind.
            if item.get("url") and LINK_RANK.get(item.get("linkKind"), -1) >= LINK_RANK.get(prev.get("linkKind"), -1):
                prev["url"], prev["linkKind"] = item["url"], item["linkKind"]
            if item.get("registrationEnds"):
                prev["registrationEnds"], prev["deadline"] = item["registrationEnds"], item["deadline"]
                prev["registrationStarts"] = item.get("registrationStarts")
        else:
            by_id[item["id"]] = item
    # Itens anteriores ao campo `source` usavam outro id: some o antigo quando o mesmo ato foi extraído de novo.
    sourced = {(i["title"], i["published"]) for i in by_id.values() if i.get("source")}
    current = [
        i for i in by_id.values()
        if is_current(i, today) and (i.get("source") or (i["title"], i["published"]) not in sourced)
    ]
    return sorted(current, key=lambda i: (i["published"], i["id"]), reverse=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--days", type=int, default=7, help="dias para trás na busca (padrão: 7)")
    parser.add_argument("--output", type=Path, default=Path("docs/concursos.json"))
    args = parser.parse_args()

    today = dt.date.today()
    old = json.loads(args.output.read_text()) if args.output.exists() else []
    since = today - dt.timedelta(days=args.days)
    try:
        gazettes = search_gazettes(since)
    except Exception as err:  # noqa: BLE001 - API fora do ar: segue com o DOU e limpa os vencidos
        log(f"Querido Diário: {err}")
        gazettes = []

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

    try:
        new.extend(fetch_dou(since, today))
    except Exception as err:  # noqa: BLE001 - falha no DOU não impede publicar os municipais
        log(f"DOU: {err}")

    for item in new:
        if item["linkKind"] != "diario" and link_is_broken(item["url"]):
            log(f"  link quebrado, usando o diário: {item['url']}")
            item["url"], item["linkKind"] = item["gazetteUrl"], "diario"

    feed = []
    for item in merge(old, new, today):
        # Um item fora do formato do app sai do feed em vez de derrubar a lista de quem está na 2.2.x.
        if problems := item_errors(item):
            log(f"  descartado {item.get('id', '?')}: {'; '.join(problems)}")
        else:
            feed.append(item)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(feed, ensure_ascii=False, indent=1) + "\n")
    log(f"feed: {len(feed)} concursos ({len(new)} encontrados nesta execução)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
