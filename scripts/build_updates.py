#!/usr/bin/env python3
"""Modo sombra: atualizações depois da inscrição (gabarito, resultado, homologação, convocação).

Nenhum app lê estes arquivos. Eles existem para medir, por 4 semanas, se dá para avisar quem
acompanha um concurso quando sai o resultado (spike CA-006). Tudo é gravado em `shadow/`, fora
de `docs/` (o GitHub Pages), e o `docs/concursos.json` é só lido.

Fluxo:
1. Índice de aberturas (`shadow/aberturas.json`, 18 meses): junta o que já estava no índice com
   os itens do `docs/concursos.json` de hoje e, com `--seed-days N`, com as aberturas dos
   últimos N dias reconstruídas do Querido Diário pelo próprio `extract_openings` do build_feed.
   Cada abertura guarda o mesmo `id` do item do feed, o município, o tipo de órgão normalizado,
   o tipo de seleção e o número normalizado (`01/2026` e `001/2026` viram `1/2026`).
2. Atos pós-inscrição dos últimos `--days` dias:
   - Querido Diário, com uma consulta própria. O tipo vem do cabeçalho do ato ("EDITAL DE
     HOMOLOGAÇÃO", "GABARITO OFICIAL PRELIMINAR") ou do verbo do ato ("Fica homologado",
     "CONVOCA os candidatos aprovados"); cronogramas, comissões, estagiários, licitações e
     prorrogações de validade ficam de fora.
   - DOU (Seção 3) pelo INLABS, quando INLABS_EMAIL e INLABS_PASSWORD estão definidos.
3. Ligação ato → `examId`: no município, só quando município + tipo de órgão + número
   normalizado (e o tipo de seleção, quando o ato diz) apontam para uma única abertura; no DOU,
   órgão + número do edital de abertura citado no texto. O resto fica sem ligação: aparece no
   log, mas não vai para o arquivo.
4. Grava:
   - `shadow/atualizacoes.json`: o feed que o app leria, só com atualizações ligadas, 90 dias;
   - `shadow/log.csv`: uma linha por execução (atos, ligados e não ligados por tipo);
   - `shadow/revisao.csv`: até `--review-limit` ligações novas por execução, com as colunas
     `correto` e `observacao` em branco para a revisão manual da semana.

Reaproveita do build_feed (sem alterá-lo): http_get, a paginação do search_gazettes, find_org,
extract_openings, SELECTION/CONCURSO/OFF_TOPIC/NEXT_ACT e o download do INLABS (fetch_dou).
"""

from __future__ import annotations

import argparse
import contextlib
import csv
import datetime as dt
import hashlib
import io
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import build_feed as bf

TYPES = ("gabarito", "resultado", "homologacao", "convocacao")
# Título de cada tipo, no texto que o app usaria no aviso.
TYPE_TITLE = {
    "gabarito": "Gabarito publicado",
    "resultado": "Resultado publicado",
    "homologacao": "Concurso homologado",
    "convocacao": "Convocação publicada",
}
QUERY_UPDATES = (
    '("concurso público" | "processo seletivo") + ("homologação" | "homologa" | "resultado final" | '
    '"resultado preliminar" | "gabarito" | "convocação" | "convoca" | "nomeação" | "nomeia" | "classificação final")'
)
# Mais páginas que o build_feed: a semente de 90 dias passa de mil diários.
MAX_PAGES = 100
INDEX_DAYS = 548  # ~18 meses: o resultado sai de 1 a 6 meses depois da abertura, às vezes mais.
WINDOW_DAYS = 90  # janela do atualizacoes.json
REVIEW_LIMIT = 10
WORKERS = 4
EXCERPT_CHARS = 500

I = re.IGNORECASE

# ---------------------------------------------------------------- classificação do ato

# Palavras que podem marcar um ato pós-inscrição; o tipo de verdade vem de `classify`.
ANCHOR = re.compile(
    r"\bconvoca(?:[çc][ãa]o|r|m|dos?|das?)?\b|\bnomea(?:r|[çc][ãa]o|dos?|das?)?\b|\bnomeia(?:m)?\b|\bhomolog\w*|"
    r"\bresultado\s+(?:final|preliminar|definitivo|parcial|da|das|do|dos)\b|classifica[çc][ãa]o\s+(?:final|geral|preliminar)|"
    r"\bgabaritos?\b",
    I,
)
# Trechos que mostram que não é o ato em si: algo que "será publicado", comissões, estagiários,
# licitação (a contratação da banca também cita "concurso público"), prorrogação de validade.
NOT_ACT = re.compile(
    r"ser[áa]\s+(?:publicad|divulgad|homologad|convocad)|ser[ãa]o\s+(?:publicad|divulgad|convocad|chamad|nomead)|"
    r"ap[óo]s\s+a\s+homologa|homologad[oa]\s+(?:em|pel[oa]|atrav|por)|comiss[ãa]o|membros|"
    r"audi[êe]ncia\s+p[úu]blica|empreendimento\s+habitacional|estagi[áa]ri|apostil|licita[çc][ãa]o|preg[ãa]o|tomada\s+de\s+pre[çc]os|"
    r"prorrog\w+[^.]{0,60}validade|contrata[çc][ãa]o\s+de\s+empresa",
    I,
)
# "Considerando a homologação do processo…" cita um ato anterior, não é o ato.
CONSIDERING = re.compile(r"considerando\s+(?:a|o)\s+(?:homolog|resultado|gabarito)\w*", I)
# Cronograma do edital de abertura: datas previstas de resultado, gabarito e homologação.
SCHEDULE = re.compile(r"cronograma|calend[áa]rio\s+(?:de\s+eventos|previsto)|datas?\s+(?:abaixo\s+)?(?:s[ãa]o\s+)?prov[áa]ve", I)
DATE_SOON = re.compile(r"\d{1,2}/\d{1,2}(?:/\d{2,4})?")
# Data na linha do cabeçalho que não é de cronograma: "GABARITO … DAS PROVAS APLICADAS EM 27/09/2026".
DATE_OK = re.compile(r"aplicad|realizad", I)
NODATE = r"(?![^\n]{0,4}\d{1,2}/\d{1,2})"

# Os diários publicam o cabeçalho do ato em CAIXA ALTA; no corpo do edital de abertura as mesmas
# palavras aparecem em caixa mista ("a Homologação do Resultado Final será publicada…").
HEADERS = (
    ("gabarito", re.compile(
        r"GABARITOS?\s+(?:OFICIA|PRELIMINAR|DEFINITIV|PROVIS|FINAL)" + NODATE + "|"
        r"(?i:(?:torna(?:-se)?\s+p[úu]blic[oa]|divulga|publica)[^.;]{0,40}?\bo?s?\s*gabaritos?\b)" + NODATE)),
    ("homologacao", re.compile(
        r"EDITAL\s+DE\s+HOMOLOGA|HOMOLOGA[ÇC][ÃA]O\s+(?:DO\s+|DA\s+)?(?:RESULTADO|CONCURSO|PROCESSO|CLASSIFICA|FINAL)" + NODATE + "|"
        r"\bHOMOLOGA\b|(?i:fica(?:m)?\s+homologad[oa]s?|\bhomologa(?:r)?\s+o\s+resultado|resolve[^.;]{0,20}homologar)")),
    ("resultado", re.compile(
        r"EDITAL\s+DE\s+(?:DIVULGA[ÇC][ÃA]O\s+DO\s+)?(?:RESULTADO|CLASSIFICA)|LISTA\s+DE\s+CLASSIFICA[ÇC][ÃA]O|"
        r"RESULTADO\s+(?:FINAL|PRELIMINAR|DEFINITIVO|DA\s+PROVA|PROVIS)" + NODATE + "|"
        r"CLASSIFICA[ÇC][ÃA]O\s+(?:FINAL|GERAL|DEFINITIVA)" + NODATE + "|"
        r"(?i:(?:torna(?:-se)?\s+p[úu]blic[oa]|divulga)[^.;]{0,40}?\b(?:o\s+resultado|a\s+classifica[çc][ãa]o))")),
    # Convocação ou nomeação de aprovados. "Edital de Convocação" em caixa mista só conta no começo da linha.
    ("convocacao", re.compile(
        r"(?:EDITAL|TERMO|ATO|AVISO)\s+DE\s+CONVOCA[ÇC]|\d+\s*[ªa]\s*CONVOCA[ÇC]|"
        r"(?im:^\s*(?:edital|termo)\s+de\s+convoca[çc])|"
        r"(?i:\bconvoca(?:m)?\b[^.;]{0,40}?(?:candidat|aprovad|classificad|seguintes|abaixo|o\s+senhor|a\s+senhora|o\s+sr|a\s+sra))|"
        r"(?i:resolve[^.;]{0,30}?\b(?:convocar|nomear)\b)|\bNOMEAR\b|"
        r"(?i:\bnomeia(?:m)?\b[^.;]{0,160}?(?:aprovad|classificad|concurso|processo\s+seletivo|efetivo))")),
)
# Convocação para prova, entrevista ou heteroidentificação é etapa do concurso, não chamada de aprovados.
EXAM_STAGE = re.compile(r"\b(?:provas?|entrevistas?|testes?|TAF|heteroidentifica\w*|aferi[çc][ãa]o)\b", I)
HOMOLOGATION_NEAR = re.compile(r"homologa", I)
# Convocação de aprovados fala de candidatos ou do concurso; "EDITAL DE CONVOCAÇÃO DE AUDIÊNCIA PÚBLICA" não.
CANDIDATES = re.compile(r"candidat|aprovad|classificad|concurso|processo\s+seletivo", I)
# Cabeçalho em CAIXA ALTA que sempre abre um ato novo, mesmo logo depois de outro ato
# (o diário de Itápolis traz o gabarito do SAAE uma página depois de uma convocação).
NEW_ACT_HEADER = re.compile(
    r"EDITAL\s+DE\s+(?:HOMOLOGA|RESULTADO|CLASSIFICA|CONVOCA|DIVULGA[ÇC][ÃA]O\s+DO\s+RESULTADO)|"
    r"GABARITOS?\s+(?:OFICIA|PRELIMINAR|DEFINITIV)|TERMO\s+DE\s+CONVOCA|HOMOLOGA\s+(?:O\s+)?RESULTADO|LISTA\s+DE\s+CLASSIFICA"
)


def classify(excerpt: str, before: str = "") -> str | None:
    """Tipo do ato pelo cabeçalho ou pelo verbo; None quando o trecho só cita o ato.

    `excerpt` é o trecho em volta da âncora; `before` é o texto logo antes dele, usado para
    reconhecer um cronograma.
    """
    if NOT_ACT.search(excerpt):
        return None
    # Um cabeçalho em CAIXA ALTA vale mesmo depois de um ato que cita o cronograma do edital.
    if SCHEDULE.search(before[-600:] + excerpt[:250]) and not NEW_ACT_HEADER.search(excerpt):
        return None
    text = CONSIDERING.sub(" ", excerpt)
    best: tuple[int, str, re.Match] | None = None
    for name, pattern in HEADERS:
        m = pattern.search(text)
        if m and (best is None or m.start() < best[0]):
            best = (m.start(), name, m)
    if best is None:
        return None
    _, name, m = best
    line_rest = text[m.end(): m.end() + 80].split("\n", 1)[0]
    if name != "convocacao" and DATE_SOON.search(line_rest) and not DATE_OK.search(line_rest):
        return None  # "RESULTADO FINAL DAS PROVAS – 30/10/2026": linha de cronograma
    if name == "convocacao" and (
        EXAM_STAGE.search(text[m.start(): m.end() + 150]) or not CANDIDATES.search(text[m.start(): m.end() + 600])
    ):
        return None
    # "RESULTADO FINAL E HOMOLOGAÇÃO": a homologação diz mais (o resultado já é definitivo).
    if name == "resultado" and HOMOLOGATION_NEAR.search(text[m.start(): m.end() + 80]):
        return "homologacao"
    return name


# ---------------------------------------------------------------- números e órgãos

# Número logo depois de "concurso (público)" ou "processo seletivo": é o número do concurso, não o do ato.
SELECTION_NUMBER = re.compile(
    r"(concurso(?:\s+p[úu]blico)?|processo\s+seletivo(?:\s+simplificado)?)(?:\s*[-–]\s*edital)?"
    r"[\s,:]*(?:n[.ºo°]*\s*[:.]?\s*)?(\d{1,4}\s*[/-]\s*\d{2,4})(?!\d)",
    I,
)
# "Edital de Abertura nº 001/2026": o número do edital que abriu o concurso.
OPENING_EDITAL_NUMBER = re.compile(
    r"edital\s+(?:de\s+abertura|normativo)\s*(?:n[.ºo°]*\s*[:.]?\s*)?(\d{1,4}\s*[/-]\s*\d{2,4})(?!\d)", I
)
NUMBER_IN_TITLE = re.compile(r"n[º°]\s*(\d{1,4}\s*[/-]\s*\d{2,4})", I)
GAZETTE_TERRITORY = re.compile(r"queridodiario\.ok\.org\.br/(\d{7})/")
GENERIC_ORG = "Município de "


def norm_number(raw: str) -> str:
    """`01/2026`, `001/2026`, `1-2026` e `001/26` viram `1/2026`; texto sem número vira ""."""
    m = re.fullmatch(r"\s*(\d{1,4})\s*[/-]\s*(\d{2}|\d{4})\s*", raw or "")
    if not m:
        return ""
    year = m.group(2) if len(m.group(2)) == 4 else f"20{m.group(2)}"
    return f"{int(m.group(1))}/{year}"


def norm_text(text: str) -> str:
    return bf.squash(bf.strip_accents((text or "").lower()))


def org_type(org: str) -> str:
    """Tipo do órgão, para separar Câmara, SAAE e Prefeitura com o mesmo nº 01/2026.

    "Município de X" (o órgão genérico do find_org) conta como prefeitura.
    """
    n = norm_text(org)
    if re.search(r"\bcamara\b", n):
        return "camara"
    if re.search(r"servico\s+autonomo|\bsaae\w*|\bautarquia\b|departamento\s+(?:municipal\s+)?de\s+agua|\bdaae?\b", n):
        return "autarquia"
    if re.search(r"\bfundacao\b", n):
        return "fundacao"
    if re.search(r"\binstituto\b", n):
        return "instituto"
    if re.search(r"\bprefeitura\b|\bmunicipio\b", n):
        return "prefeitura"
    return "outro"


# Órgão no cabeçalho do ato quando o find_org não acha ("SAAEI – SERVIÇO AUTÔNOMO DE ÁGUA E ESGOTO
# DE ITÁPOLIS/ SP"). Fundações e institutos ficam de fora: costumam ser a banca, não o órgão.
HEADER_ORG = re.compile(
    r"(?:c[âa]mara\s+municipal\s+(?:de|do|da)\s+[A-ZÀ-Úa-zà-ú' ]{3,60}?|servi[çc]o\s+aut[ôo]nomo\s+[A-ZÀ-Úa-zà-ú' ]{3,80}?)"
    r"(?=\s*(?:[,.;:/–-]|\n|$))|\bSAAE\w*\b",
    I,
)


def act_org(pre: str, window: str, city: str) -> str:
    """find_org do build_feed, com dois ajustes para o cabeçalho de atos pós-inscrição:
    desconfia de prefeitura ou câmara de outra cidade (no Rio, o find_org devolveu "Prefeitura
    do Município de Osasco") e, quando sobra o genérico "Município de X", procura uma Câmara ou
    um SAAE logo antes da âncora."""
    org = bf.find_org(pre, window, city)
    if org_type(org) in ("prefeitura", "camara") and norm_text(city) not in norm_text(org):
        org = GENERIC_ORG + city
    if org.startswith(GENERIC_ORG):
        header = list(HEADER_ORG.finditer(pre[-400:]))
        if header:
            name = bf.squash(header[-1].group(0)).strip(" -")
            return "Serviço Autônomo de Água e Esgoto" if name.upper().startswith("SAAE") else bf.tidy_name(name)
    return org


def find_numbers(window: str, anchor: int) -> list[dict]:
    """Números que podem identificar o concurso, do mais provável para o menos provável.

    Primeiro o número citado depois de "Concurso Público"/"Processo Seletivo" mais perto da
    âncora (com o tipo de seleção); depois o do "Edital de Abertura", sem tipo. O número do
    próprio ato ("Edital de Convocação nº 579/2026") não entra.
    """
    found: list[dict] = []
    selections = sorted(SELECTION_NUMBER.finditer(window), key=lambda m: abs(m.start() - anchor))
    if selections:
        m = selections[0]
        kind = "concurso" if m.group(1).lower().startswith("concurso") else "processo_seletivo"
        found.append({"number": norm_number(m.group(2)), "raw": re.sub(r"\s+", "", m.group(2)), "kind": kind})
    for m in OPENING_EDITAL_NUMBER.finditer(window):
        found.append({"number": norm_number(m.group(1)), "raw": re.sub(r"\s+", "", m.group(1)), "kind": None})
    unique, seen = [], set()
    for n in found:
        if n["number"] and n["number"] not in seen:
            seen.add(n["number"])
            unique.append(n)
    return unique


# ---------------------------------------------------------------- B1: atos nos diários municipais

def extract_updates(text: str, gazette: dict) -> list[dict]:
    """Atos pós-inscrição de um diário municipal (ainda sem ligação com o feed)."""
    city, uf = gazette["territory_name"], gazette["state_code"]
    acts, last_end, last_anchor_end = [], -1, 0
    for m in ANCHOR.finditer(text):
        # Dentro do último ato, só um cabeçalho novo (depois da âncora daquele ato) abre outro ato.
        if m.start() < last_end and not NEW_ACT_HEADER.search(text, max(last_anchor_end, m.start() - 60), m.end() + 60):
            continue
        if not bf.SELECTION.search(text[max(0, m.start() - 400): m.end() + 400]):
            continue
        if bf.OFF_TOPIC.search(text[max(0, m.start() - 160): m.end() + 160]):
            continue
        excerpt_start = max(0, m.start() - 250)
        kind = classify(text[excerpt_start: m.end() + 350], text[max(0, excerpt_start - 600): excerpt_start])
        if not kind:
            continue
        nxt = bf.NEXT_ACT.search(text, m.end() + 150)
        act_end = nxt.start() if nxt else len(text)
        end = min(act_end, m.end() + 3000)
        # O resto do mesmo ato não vira outro ato (Bela Vista cita "convocação" depois da homologação).
        last_end, last_anchor_end = end, m.end()
        window_start = max(0, m.start() - 400)
        window = text[window_start: min(act_end, m.end() + 1500)]
        org = act_org(text[max(0, m.start() - 600): m.end()], text[max(0, m.start() - 500): end], city)
        acts.append({
            "type": kind,
            "source": "querido_diario",
            "published": gazette["date"],
            "territoryId": gazette["territory_id"],
            "city": city,
            "state": uf,
            "organization": org,
            "orgType": org_type(org),
            "numbers": find_numbers(window, m.start() - window_start),
            "excerpt": bf.squash(text[excerpt_start: min(end, m.end() + 450)])[:EXCERPT_CHARS],
            "gazetteUrl": gazette.get("url") or gazette.get("txt_url", ""),
        })
    return acts


# ---------------------------------------------------------------- B4: atos no DOU (INLABS)

# "Edital nº 30/2026" ou "Edital nº 30, de 12 de junho de 2026" citado no texto do ato.
DOU_EDITAL_REF = re.compile(
    r"edital\s+(?:n[º°o.]*\s*)?(\d{1,4})(?:\s*/\s*(\d{4})|[^\n,;]{0,20}?,?\s+de\s+\d{1,2}[º°]?\s+de\s+[a-zç]+\s+de\s+(\d{4}))", I
)
DOU_OWN_NUMBER = re.compile(r"n[º°o.]*\s*(\d{1,4}(?:\s*/\s*\d{2,4})?)", I)
# Verbos de ato em caixa mista, quando o DOU não traz o cabeçalho em caixa alta.
DOU_VERBS = (
    ("homologacao", re.compile(r"homolog\w*\s+(?:o\s+|a\s+)?(?:resultado|concurso|processo)", I)),
    ("resultado", re.compile(r"resultado\s+(?:final|preliminar|definitivo)|classifica[çc][ãa]o\s+final", I)),
    ("gabarito", re.compile(r"gabaritos?\s+(?:oficia|preliminar|definitiv)", I)),
    ("convocacao", re.compile(r"convoca\w*\s+(?:os\s+|as\s+)?candidat", I)),
)
DOU_SKIP = re.compile(r"retifica|torna\s+sem\s+efeito|cancela|suspen|revoga|prorroga", I)


def extract_dou_update(article: ET.Element) -> dict | None:
    """Converte um <article> do INLABS num ato pós-inscrição, se for um."""
    body = article.find("body")
    if body is None:
        return None
    identifica = bf.squash(bf.html_to_text(body.findtext("Identifica") or ""))
    text = bf.html_to_text(body.findtext("Texto") or "")
    art_type = article.get("artType", "")
    head = f"{identifica}\n{text[:600]}"
    if not bf.SELECTION.search(f"{art_type} {identifica} {text[:1500]}") or bf.OFF_TOPIC.search(head):
        return None
    # Retificação, cancelamento e prorrogação mexem num ato anterior; o edital de abertura não é atualização.
    if DOU_SKIP.search(f"{identifica} {text[:250]}") or bf.DOU_OPENING.search(text[:1500]):
        return None
    if NOT_ACT.search(head):
        return None
    kind = classify(head) or next((name for name, pattern in DOU_VERBS if pattern.search(head)), None)
    # Convocação para prova didática, entrevista etc. é etapa do concurso.
    if not kind or (kind == "convocacao" and EXAM_STAGE.search(head)):
        return None
    try:
        published = dt.datetime.strptime(article.get("pubDate", ""), "%d/%m/%Y").date()
    except ValueError:
        return None

    org = bf.dou_org(article.get("artCategory", ""))
    own = DOU_OWN_NUMBER.search(identifica)
    own_number = re.sub(r"\s+", "", own.group(1)) if own else ""
    if own_number and "/" not in own_number:
        own_number += f"/{published.year}"
    own_number = norm_number(own_number)
    numbers, seen = [], set()
    for m in DOU_EDITAL_REF.finditer(text):
        number = norm_number(f"{m.group(1)}/{m.group(2) or m.group(3)}")
        if number and number != own_number and number not in seen:
            seen.add(number)
            numbers.append({"number": number, "raw": number, "kind": None})
    return {
        "type": kind,
        "source": "dou",
        "published": published.isoformat(),
        "territoryId": None,
        "city": None,
        "state": bf.dou_uf(org, text),
        "organization": org,
        "orgType": None,
        "numbers": numbers,
        "excerpt": bf.squash(f"{identifica} {text[:EXCERPT_CHARS]}")[:EXCERPT_CHARS],
        "gazetteUrl": article.get("pdfPage", ""),
    }


def extract_dou_updates_zip(data: bytes) -> list[dict]:
    acts = []
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        for name in zf.namelist():
            if not name.lower().endswith(".xml"):
                continue
            try:
                root = ET.fromstring(zf.read(name))
            except ET.ParseError as err:
                bf.log(f"  XML inválido {name}: {err}")
                continue
            acts.extend(act for act in map(extract_dou_update, root.iter("article")) if act)
    return acts


@contextlib.contextmanager
def patched(**values):
    """Troca nomes do módulo build_feed só durante a chamada, sem alterar o arquivo.

    Assim a paginação do search_gazettes e o login/download do fetch_dou são os mesmos do feed.
    """
    old = {name: getattr(bf, name) for name in values}
    for name, value in values.items():
        setattr(bf, name, value)
    try:
        yield
    finally:
        for name, value in old.items():
            setattr(bf, name, value)


def fetch_dou_updates(since: dt.date, today: dt.date) -> list[dict] | None:
    """Atos pós-inscrição do DOU, ou None quando o INLABS não está configurado.

    Usa o fetch_dou do build_feed com outro extrator: o log dele diz "abertura(s)", mas aqui
    a contagem é de atos pós-inscrição.
    """
    if not os.environ.get("INLABS_EMAIL") or not os.environ.get("INLABS_PASSWORD"):
        bf.log("DOU: INLABS_EMAIL/INLABS_PASSWORD não definidos; pulando o DOU")
        return None
    with patched(extract_dou_zip=extract_dou_updates_zip):
        return bf.fetch_dou(since, today)


# ---------------------------------------------------------------- B2: índice de aberturas

def index_entry(item: dict, territory_id: str | None = None) -> dict | None:
    """Uma abertura do feed (ou do extract_openings) no formato do índice; None sem número."""
    source = item.get("source")
    if source not in ("querido_diario", "dou"):
        return None  # itens antigos, de antes do campo `source`
    title = re.sub(r"\s*\(retificação\)$", "", item.get("title", ""))
    organization, _, label = title.partition(" - ")
    number = NUMBER_IN_TITLE.search(label)
    if not number or not norm_number(number.group(1)):
        return None
    if source == "querido_diario" and not territory_id:
        m = GAZETTE_TERRITORY.search(item.get("gazetteUrl") or "")
        territory_id = m.group(1) if m else None
        if not territory_id:
            return None
    return {
        "id": item["id"],
        "source": source,
        "territoryId": territory_id if source == "querido_diario" else None,
        "city": item.get("city"),
        "state": item.get("state"),
        "organization": organization,
        "orgType": org_type(organization) if source == "querido_diario" else None,
        "kind": item.get("kind"),
        "number": norm_number(number.group(1)),
        "title": title,
        "published": item.get("published"),
    }


def merge_index(old: list[dict], new: list[dict], today: dt.date) -> list[dict]:
    """Junta as aberturas por (id, tipo de órgão) e tira as de mais de 18 meses."""
    by_key = {(e["id"], e.get("orgType")): e for e in old}
    for entry in new:
        key = (entry["id"], entry.get("orgType"))
        prev = by_key.get(key)
        if prev is None:
            by_key[key] = entry
            continue
        # Fica a primeira publicação (a abertura, não a retificação), com os campos que faltavam.
        if entry.get("published") and (not prev.get("published") or entry["published"] < prev["published"]):
            prev["published"] = entry["published"]
        for k, v in entry.items():
            if v and not prev.get(k):
                prev[k] = v
    cutoff = (today - dt.timedelta(days=INDEX_DAYS)).isoformat()
    kept = [e for e in by_key.values() if (e.get("published") or "") >= cutoff]
    return sorted(kept, key=lambda e: (e.get("published") or "", e["id"], e.get("orgType") or ""), reverse=True)


# ---------------------------------------------------------------- ligação ato → examId

class OpeningIndex:
    """Consultas no índice de aberturas."""

    def __init__(self, entries: list[dict]):
        self.by_territory: dict[str, list[dict]] = {}
        self.by_org: dict[str, list[dict]] = {}
        self.org_types_by_id: dict[str, set] = {}
        for e in entries:
            if e["source"] == "querido_diario":
                self.by_territory.setdefault(e["territoryId"], []).append(e)
                self.org_types_by_id.setdefault(e["id"], set()).add(e.get("orgType"))
            else:
                self.by_org.setdefault(norm_text(e["organization"]), []).append(e)

    def link(self, act: dict) -> tuple[dict | None, str]:
        """A abertura do ato e "ok", ou None e o motivo de não ligar."""
        if not act["numbers"]:
            return None, "sem número do concurso"
        if act["source"] == "dou":
            return self._link_dou(act)
        place = self.by_territory.get(act["territoryId"], [])
        if not place:
            return None, "sem abertura no município"
        hits: dict[str, dict] = {}
        reason = "sem abertura com esse número"
        for n in act["numbers"]:
            same_number = [e for e in place if e["number"] == n["number"]]
            if not same_number:
                continue
            same_org = [e for e in same_number if e.get("orgType") == act["orgType"]]
            if not same_org:
                reason = "tipo de órgão diferente"
                continue
            if n["kind"]:
                same_org = [e for e in same_org if e.get("kind") in (None, n["kind"])]
                if not same_org:
                    reason = "concurso/processo seletivo diferente"
                    continue
            for e in same_org:
                hits[e["id"]] = e
        if not hits:
            return None, reason
        if len(hits) > 1:
            return None, "ambíguo"
        opening = next(iter(hits.values()))
        # O build_feed dá o mesmo id a aberturas de órgãos diferentes com o mesmo número no
        # município (Câmara e Prefeitura de Itápolis, nº 01/2026): não dá para saber qual é.
        if len(self.org_types_by_id.get(opening["id"], ())) > 1:
            return None, "id compartilhado por órgãos diferentes"
        return self._check_date(act, opening)

    def _link_dou(self, act: dict) -> tuple[dict | None, str]:
        same_org = self.by_org.get(norm_text(act["organization"]), [])
        if not same_org:
            return None, "órgão sem abertura no índice"
        cited = {n["number"] for n in act["numbers"]}
        hits = {e["id"]: e for e in same_org if e["number"] in cited}
        if not hits:
            return None, "sem abertura com esse número"
        if len(hits) > 1:
            return None, "ambíguo"
        return self._check_date(act, next(iter(hits.values())))

    @staticmethod
    def _check_date(act: dict, opening: dict) -> tuple[dict | None, str]:
        if opening.get("published") and act["published"] < opening["published"]:
            return None, "ato anterior à abertura"
        return opening, "ok"


# ---------------------------------------------------------------- agrupamento e saída

def group_key(act: dict, opening: dict | None) -> tuple:
    """Um ato por (concurso, tipo, dia): Itajubá publica um termo de convocação por candidato."""
    if opening:
        return ("ligado", opening["id"], act["type"], act["published"])
    place = act.get("territoryId") or norm_text(act["organization"])
    number = act["numbers"][0]["number"] if act["numbers"] else norm_text(act["organization"])
    return ("sem_ligacao", place, number, act["type"], act["published"])


def group_acts(linked: list[tuple[dict, dict | None, str]]) -> list[tuple[dict, dict | None, str, int]]:
    """Junta as repetições; devolve (ato, abertura, motivo, repetições), na ordem em que apareceram."""
    groups: dict[tuple, list] = {}
    for act, opening, reason in linked:
        key = group_key(act, opening)
        if key in groups:
            groups[key][3] += 1
        else:
            groups[key] = [act, opening, reason, 1]
    return [tuple(g) for g in groups.values()]


def update_id(exam_id: str, kind: str, published: str) -> str:
    return "upd-" + hashlib.sha1(f"{exam_id}|{kind}|{published}".encode()).hexdigest()[:12]


def make_update(act: dict, opening: dict) -> dict:
    """Formato do spike CA-006, seção 6(b)."""
    _, _, edital = opening["title"].partition(" - ")
    organization = act["organization"]
    if organization.startswith(GENERIC_ORG) or act["source"] == "dou":
        organization = opening["organization"]
    return {
        "id": update_id(opening["id"], act["type"], act["published"]),
        "examId": opening["id"],
        "type": act["type"],
        "title": TYPE_TITLE[act["type"]],
        "organization": organization,
        "edital": edital or None,
        # No DOU, a UF do ato pode sair "BR"; a da abertura costuma ser mais precisa.
        "state": act.get("state") if act.get("state") not in (None, "BR") else (opening.get("state") or act.get("state")),
        "city": act.get("city"),
        "published": act["published"],
        "gazetteUrl": act["gazetteUrl"],
        "excerpt": act["excerpt"],
        "source": act["source"],
    }


def merge_updates(old: list[dict], new: list[dict], today: dt.date) -> list[dict]:
    """Mantém a primeira versão de cada atualização e só os últimos 90 dias."""
    by_id = {u["id"]: u for u in old}
    for u in new:
        by_id.setdefault(u["id"], u)
    cutoff = (today - dt.timedelta(days=WINDOW_DAYS)).isoformat()
    kept = [u for u in by_id.values() if u["published"] >= cutoff]
    return sorted(kept, key=lambda u: (u["published"], u["id"]), reverse=True)


LOG_FIELDS = (
    ["data", "dias", "diarios", "diarios_com_erro", "dou"]
    + [f"atos_{t}" for t in TYPES]
    + [f"ligados_{t}" for t in TYPES]
    + ["nao_ligados", "novos_ligados", "aberturas_no_indice"]
)
REVIEW_FIELDS = (
    "execucao", "publicado", "tipo", "fonte", "uf", "cidade", "concurso", "examId", "atualizacao",
    "orgao_no_ato", "trecho", "diario", "id", "correto", "observacao",
)


def append_csv(path: Path, fields, rows: list[dict]) -> None:
    is_new = not path.exists() or path.stat().st_size == 0
    with path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        if is_new:
            writer.writeheader()
        writer.writerows(rows)


def reviewed_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    with path.open(newline="", encoding="utf-8") as f:
        return {row.get("id", "") for row in csv.DictReader(f)}


def pick_for_review(updates: list[dict], limit: int) -> list[dict]:
    """Até `limit` atualizações, alternando os tipos para medir a precisão de cada um."""
    queues = {t: [u for u in updates if u["type"] == t] for t in ("homologacao", "resultado", "gabarito", "convocacao")}
    picked: list[dict] = []
    while len(picked) < limit and any(queues.values()):
        for t in queues:
            if queues[t] and len(picked) < limit:
                picked.append(queues[t].pop(0))
    return picked


def review_row(update: dict, opening: dict, act: dict, today: dt.date) -> dict:
    return {
        "execucao": today.isoformat(),
        "publicado": update["published"],
        "tipo": update["type"],
        "fonte": update["source"],
        "uf": update.get("state") or "",
        "cidade": update.get("city") or "",
        "concurso": opening["title"],
        "examId": update["examId"],
        "atualizacao": update["title"],
        "orgao_no_ato": act["organization"],
        "trecho": update["excerpt"][:300],
        "diario": update["gazetteUrl"],
        "id": update["id"],
        "correto": "",
        "observacao": "",
    }


# ---------------------------------------------------------------- execução

def read_gazettes(gazettes: list[dict], extract, workers: int) -> tuple[list, int]:
    """Baixa o texto de cada diário (em paralelo) e aplica `extract`; devolve (resultados, falhas)."""

    def work(g):
        try:
            text = bf.http_get(g["txt_url"], attempts=3).decode("utf-8", errors="replace")
            return extract(text, g)
        except Exception as err:  # noqa: BLE001 - um diário com erro não derruba a execução
            bf.log(f"  pulando {g.get('territory_name')} {g.get('date')}: {err}")
            return None

    results, failed = [], 0
    with ThreadPoolExecutor(max(1, workers)) as pool:
        for n, found in enumerate(pool.map(work, gazettes), 1):
            if found is None:
                failed += 1
            else:
                results.extend(found)
            if n % 50 == 0:
                bf.log(f"  {n}/{len(gazettes)} diários lidos")
    return results, failed


def search(query: str, since: dt.date) -> list[dict]:
    with patched(QUERY=query, MAX_PAGES=MAX_PAGES):
        return bf.search_gazettes(since)


def seed_openings(days: int, today: dt.date, workers: int) -> list[dict]:
    """Aberturas dos últimos `days` dias pelo extract_openings do build_feed."""
    gazettes = search(bf.QUERY, today - dt.timedelta(days=days))
    bf.log(f"semente: {len(gazettes)} diários com aberturas")

    def extract(text, g):
        return [e for e in (index_entry(i, g["territory_id"]) for i in bf.extract_openings(text, g)) if e]

    entries, _ = read_gazettes(gazettes, extract, workers)
    bf.log(f"semente: {len(entries)} aberturas com número")
    return entries


def load_json(path: Path, default):
    return json.loads(path.read_text()) if path.exists() else default


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--days", type=int, default=7, help="dias para trás na busca de atos (padrão: 7)")
    parser.add_argument("--seed-days", type=int, default=0, help="reconstrói as aberturas dos últimos N dias no índice")
    parser.add_argument("--feed", type=Path, default=Path("docs/concursos.json"), help="feed publicado (só leitura)")
    parser.add_argument("--shadow-dir", type=Path, default=Path("shadow"))
    parser.add_argument("--review-limit", type=int, default=REVIEW_LIMIT, help="ligações novas por execução na revisão")
    parser.add_argument("--workers", type=int, default=WORKERS, help="downloads de diários em paralelo")
    parser.add_argument("--today", type=dt.date.fromisoformat, default=None, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    today = args.today or dt.date.today()
    shadow = args.shadow_dir
    shadow.mkdir(parents=True, exist_ok=True)
    index_path, updates_path = shadow / "aberturas.json", shadow / "atualizacoes.json"
    log_path, review_path = shadow / "log.csv", shadow / "revisao.csv"

    # B2: índice de aberturas
    new_entries = [e for e in map(index_entry, load_json(args.feed, [])) if e]
    if args.seed_days > 0:
        try:
            new_entries += seed_openings(args.seed_days, today, args.workers)
        except Exception as err:  # noqa: BLE001 - sem a semente, segue com o índice e o feed
            bf.log(f"semente: {err}")
    index = merge_index(load_json(index_path, []), new_entries, today)
    write_json(index_path, index)
    bf.log(f"índice: {len(index)} aberturas")

    # B1: atos nos diários municipais
    since = today - dt.timedelta(days=args.days)
    try:
        gazettes = search(QUERY_UPDATES, since)
        search_failed = 0
    except Exception as err:  # noqa: BLE001 - API fora do ar: segue com o DOU
        bf.log(f"Querido Diário: {err}")
        gazettes, search_failed = [], 1
    acts, failed = read_gazettes(gazettes, extract_updates, args.workers)

    # B4: DOU
    dou = "não"
    try:
        dou_acts = fetch_dou_updates(since, today)
        if dou_acts is not None:
            dou = "sim"
            acts += dou_acts
            bf.log(f"DOU: {len(dou_acts)} atos pós-inscrição")
    except Exception as err:  # noqa: BLE001 - falha no DOU não impede os municipais
        bf.log(f"DOU: {err}")
        dou = "erro"

    # Ligação e agrupamento
    lookup = OpeningIndex(index)
    groups = group_acts([(act, *lookup.link(act)) for act in acts])
    found = {t: 0 for t in TYPES}
    linked = {t: 0 for t in TYPES}
    unlinked = 0
    new_updates, by_update = [], {}
    for act, opening, reason, count in groups:
        found[act["type"]] += 1
        number = act["numbers"][0]["raw"] if act["numbers"] else "-"
        if opening is None:
            unlinked += 1
            bf.log(f"  sem ligação ({reason}): {act['type']} {act['published']} {act.get('city') or act['organization']} nº {number}")
            continue
        linked[act["type"]] += 1
        update = make_update(act, opening)
        new_updates.append(update)
        by_update[update["id"]] = (opening, act)
        bf.log(f"  ligado: {act['type']} {act['published']} {opening['title']} ({opening['id']}, {count}x)")

    # B3: arquivos da sombra
    old_updates = load_json(updates_path, [])
    old_ids = {u["id"] for u in old_updates}
    merged = merge_updates(old_updates, new_updates, today)
    write_json(updates_path, merged)
    fresh = [u for u in new_updates if u["id"] not in old_ids and u["id"] in {m["id"] for m in merged}]

    already = reviewed_ids(review_path)
    to_review = pick_for_review([u for u in fresh if u["id"] not in already], args.review_limit)
    append_csv(review_path, REVIEW_FIELDS, [review_row(u, *by_update[u["id"]], today) for u in to_review])

    append_csv(log_path, LOG_FIELDS, [{
        "data": today.isoformat(),
        "dias": args.days,
        "diarios": len(gazettes),
        "diarios_com_erro": failed + search_failed,
        "dou": dou,
        **{f"atos_{t}": found[t] for t in TYPES},
        **{f"ligados_{t}": linked[t] for t in TYPES},
        "nao_ligados": unlinked,
        "novos_ligados": len(fresh),
        "aberturas_no_indice": len(index),
    }])
    bf.log(
        f"atos: {sum(found.values())} {found}; ligados: {sum(linked.values())} {linked}; "
        f"sem ligação: {unlinked}; novos no arquivo: {len(fresh)}; na revisão: {len(to_review)}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
