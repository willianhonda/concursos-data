#!/usr/bin/env python3
"""Monta docs/previstos.json: concursos AUTORIZADOS no DOU que ainda não têm edital (spike CA-007).

O arquivo é separado de docs/concursos.json: as versões antigas do app não o leem.

Fonte: Diário Oficial da União, Seção 1 (DO1 e DO1E), pelo INLABS da Imprensa Nacional. Precisa de
INLABS_EMAIL e INLABS_PASSWORD (os mesmos do feed); sem elas, nada é buscado e o arquivo só passa
pela limpeza (saída de previstos), o que mantém `[]` como estado normal.

Regra de captura ESTRITA (0 falso positivo no spike): só entra o ato cuja ementa começa com
"Autoriza a realização de concurso(s) público(s)". Nunca autorização de nomeação ou provimento
adicional, nem ato que apenas cite uma autorização antiga, nem retificação. A republicação "(*)" do
mesmo ato conta uma vez só.

Um previsto SAI do arquivo quando:
- surge em docs/concursos.json um edital do mesmo órgão (normalizado), publicado a partir da autorização; ou
- `editalDeadline` passou há mais de 30 dias (o prazo pode ser prorrogado). Sem `editalDeadline`
  (atos fora do modelo do MGI), sai 365 dias depois da autorização.

Só usa a biblioteca padrão do Python.
"""

from __future__ import annotations

import argparse
import calendar
import datetime as dt
import http.cookiejar
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import build_feed as bf
from validate_previstos import forecast_id, forecasts_errors

SECTIONS = ("DO1", "DO1E")
DOU_ARTICLE_URL = "https://www.in.gov.br/web/dou/-/{slug}-{matter}"
EXPIRY_GRACE_DAYS = 30
NO_DEADLINE_TTL_DAYS = 365

STRICT = re.compile(r"^autoriza a realizacao de concursos? publicos?\b")
RECTIFICATION = re.compile(r"retifica")
REPUBLISHED = re.compile(r"\(\s*\*\s*\)")
ORG_IN_EMENTA = re.compile(r"quadro\s+de\s+pessoal\s+d(?:[oa]s?|e)\s+(.+?)\s*(?:[,;.]|$)", re.IGNORECASE)
EDITAL_DEADLINE = re.compile(
    r"prazo[^.]{0,80}?publica[c][a]o\s+do\s+edital[^.]{0,200}?ate\s+([a-z0-9 ]{1,40}?)\s*(?:\(\d+\)\s*)?(mes(?:es)?|dias?|anos?)\b"
)
EFFECTIVE_VACANCIES = re.compile(r"(\d{1,4})\s*(?:\([^)]*\)\s*)?vagas?\s+efetivas?")
ROLE_LINE = re.compile(r"^[IVXL]+\s*[-–]\s*(.+?)\s*(?:\(\s*c[óo]digo[^)]*\))?\s*[-–]\s*n[íi]vel\b", re.IGNORECASE)
LEVEL_BY_CODE = {"NA": "fundamental", "NI": "medio", "NS": "superior"}
LEVEL_ORDER = ("fundamental", "medio", "tecnico", "superior")

UNITS = {"um": 1, "uma": 1, "dois": 2, "duas": 2, "tres": 3, "quatro": 4, "cinco": 5, "seis": 6, "sete": 7, "oito": 8,
         "nove": 9, "dez": 10, "onze": 11, "doze": 12, "treze": 13, "quatorze": 14, "catorze": 14, "quinze": 15,
         "dezesseis": 16, "dezessete": 17, "dezoito": 18, "dezenove": 19}
TENS = {"vinte": 20, "trinta": 30, "quarenta": 40, "cinquenta": 50, "sessenta": 60, "setenta": 70, "oitenta": 80, "noventa": 90}
HUNDREDS = {"cem": 100, "cento": 100, "duzentos": 200, "trezentos": 300, "quatrocentos": 400, "quinhentos": 500,
            "seiscentos": 600, "setecentos": 700, "oitocentos": 800, "novecentos": 900}
WORDS = {**UNITS, **TENS, **HUNDREDS}


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def norm(text: str) -> str:
    """Minúsculas, sem acento e com espaços simples."""
    return bf.squash(bf.strip_accents(text).lower())


def words_to_int(text: str) -> int | None:
    """"cento e setenta" -> 170; "mil e duzentos" -> 1200; "12" -> 12. None se não for número."""
    text = norm(text)
    if text.isdigit():
        return int(text)
    total = current = 0
    seen = False
    for word in text.split():
        if word == "e":
            continue
        if word == "mil":
            total += (current or 1) * 1000
            current = 0
        elif word in WORDS:
            current += WORDS[word]
        else:
            return None
        seen = True
    return total + current if seen else None


def add_months(day: dt.date, months: int) -> dt.date:
    index = day.month - 1 + months
    year, month = day.year + index // 12, index % 12 + 1
    return dt.date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


# ---------------------------------------------------------------- extração de um ato

def slugify(identifica: str) -> str:
    """Slug da URL pública do DOU: "PORTARIA/MGI Nº 5.505, DE 3 DE JULHO DE 2026" -> portaria/mgi-n-5.505-de-3-de-julho-de-2026."""
    text = bf.strip_accents(identifica.lower())
    text = re.sub(r"[º°ª,]", "", text)
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9./]+", "-", text)).strip("-")


def act_url(article: ET.Element, identifica: str) -> str | None:
    matter = article.get("idMateria") or ""
    if not matter:
        return None
    return DOU_ARTICLE_URL.format(slug=slugify(REPUBLISHED.sub("", identifica)), matter=matter)


def find_edital_deadline(text: str, authorized: dt.date) -> dt.date | None:
    m = EDITAL_DEADLINE.search(norm(text))
    amount = words_to_int(m.group(1)) if m else None
    if not m or not amount:
        return None
    unit = m.group(2)
    if unit.startswith("mes"):
        return add_months(authorized, amount)
    if unit.startswith("ano"):
        return add_months(authorized, 12 * amount)
    return authorized + dt.timedelta(days=amount)


def table_rows(raw_html: str) -> list[list[str]]:
    rows = []
    for tr in re.findall(r"<tr\b[^>]*>(.*?)</tr>", raw_html, re.IGNORECASE | re.DOTALL):
        cells = [bf.squash(bf.html_to_text(td)) for td in re.findall(r"<td\b[^>]*>(.*?)</td>", tr, re.IGNORECASE | re.DOTALL)]
        if cells:
            rows.append(cells)
    return rows


def annex_fields(raw_html: str) -> tuple[list[tuple[str, str, int]], int | None]:
    """Linhas do Anexo do MGI (cargo, sigla de escolaridade, total de vagas) e o TOTAL."""
    roles, total = [], None
    for cells in table_rows(raw_html):
        last = cells[-1].replace(".", "")
        if cells[0].strip().upper() == "TOTAL" and last.isdigit():
            total = int(last)
        elif len(cells) >= 3 and cells[1].strip().upper() in LEVEL_BY_CODE and last.isdigit():
            roles.append((bf.tidy_name(cells[0]) if cells[0].isupper() else cells[0], cells[1].strip().upper(), int(last)))
    return roles, total


def article_one_vacancies(text: str) -> int | None:
    """"provimento de cento e setenta cargos" no art. 1º."""
    m = re.search(r"provimento\s+de\s+([a-zç0-9 ]{2,40}?)\s+cargos", norm(text))
    return words_to_int(m.group(1)) if m else None


def ordered_levels(levels) -> list[str]:
    return [level for level in LEVEL_ORDER if level in set(levels)]


def extract_forecast(article: ET.Element) -> dict | None:
    """Converte um <article> do INLABS num previsto, se for autorização de concurso (regra estrita)."""
    body = article.find("body")
    if body is None:
        return None
    identifica = bf.squash(bf.html_to_text(body.findtext("Identifica") or ""))
    raw = body.findtext("Texto") or ""
    ementa = bf.squash(bf.html_to_text(body.findtext("Ementa") or ""))
    if not ementa:
        m = re.search(r'<p[^>]*class="ementa"[^>]*>(.*?)</p>', raw, re.IGNORECASE | re.DOTALL)
        ementa = bf.squash(bf.html_to_text(m.group(1))) if m else ""
    republished = bool(REPUBLISHED.search(f"{identifica} {ementa}"))
    ementa = bf.squash(REPUBLISHED.sub("", ementa))
    if not STRICT.match(norm(ementa)):
        return None
    if RECTIFICATION.search(norm(f"{article.get('artType', '')} {identifica}")) or RECTIFICATION.match(norm(ementa)):
        return None
    try:
        authorized = dt.datetime.strptime(article.get("pubDate", ""), "%d/%m/%Y").date()
    except ValueError:
        return None
    url = act_url(article, identifica)
    if not url:
        return None

    text = bf.html_to_text(raw)
    org_match = ORG_IN_EMENTA.search(ementa)
    org = bf.squash(org_match.group(1)) if org_match else ""
    if not org:
        # Sem o órgão na ementa, só vale o órgão que publica o ato se ele autoriza o próprio concurso.
        category = article.get("artCategory", "")
        if re.search(r"gest[ãa]o e da inova|presid[êe]ncia", category, re.IGNORECASE):
            log(f"  descartado {identifica}: não identifiquei o órgão na ementa")
            return None
        org = bf.dou_org(category)

    annex, annex_total = annex_fields(raw)
    roles = [name for name, _, _ in annex]
    levels = [LEVEL_BY_CODE[code] for _, code, _ in annex]
    vacancies = None
    if annex:
        # Confere o total por extenso do art. 1º, o TOTAL do Anexo e a soma das linhas; se divergem, não afirma.
        counts = {n for n in (annex_total, article_one_vacancies(text), sum(v for _, _, v in annex)) if n}
        vacancies = counts.pop() if len(counts) == 1 else None
    else:
        lines = [line for line in re.split(r"\n", text) if ROLE_LINE.match(line.strip())]
        for line in lines:
            role = ROLE_LINE.match(line.strip()).group(1).strip(" -–")
            roles.append(role)
            levels += [level for level, pattern in bf.EDUCATION if pattern.search(line)]
        effective = [int(m.group(1)) for line in lines for m in EFFECTIVE_VACANCIES.finditer(line)]
        vacancies = sum(effective) or None
        if not levels:
            levels = [level for level, pattern in bf.EDUCATION if pattern.search(ementa)]

    act = bf.squash(REPUBLISHED.sub("", identifica))
    deadline = find_edital_deadline(text, authorized)
    item = {
        "id": forecast_id(url),
        "organization": org,
        "authorizedOn": authorized.isoformat(),
        "url": url,
        "vacancies": vacancies,
        "roles": roles[:bf.MAX_ROLES] or None,
        "education": ordered_levels(levels) or None,
        "editalDeadline": deadline.isoformat() if deadline else None,
        "sphere": "federal",
        "source": "dou",
        "act": act,
    }
    item = {k: v for k, v in item.items() if v is not None}
    item["_republished"] = republished  # só para a deduplicação; sai antes de gravar
    return item


def extract_articles(root: ET.Element) -> list[dict]:
    return [item for article in root.iter("article") if (item := extract_forecast(article))]


def extract_zip(data: bytes) -> list[dict]:
    items = []
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        for name in zf.namelist():
            if not name.lower().endswith(".xml"):
                continue
            try:
                items += extract_articles(ET.fromstring(zf.read(name)))
            except ET.ParseError as err:
                log(f"  XML inválido {name}: {err}")
    return items


# ---------------------------------------------------------------- INLABS

def fetch_dou(since: dt.date, today: dt.date) -> list[dict]:
    email, password = os.environ.get("INLABS_EMAIL"), os.environ.get("INLABS_PASSWORD")
    if not email or not password:
        log("DOU: INLABS_EMAIL/INLABS_PASSWORD não definidos; pulando o DOU")
        return []
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    login = urllib.request.Request(
        bf.INLABS_LOGIN,
        data=urllib.parse.urlencode({"email": email, "password": password}).encode(),
        headers={"User-Agent": bf.USER_AGENT, "Content-Type": "application/x-www-form-urlencoded"},
    )
    opener.open(login, timeout=60).read()
    if not any(c.name == "inlabs_session_cookie" for c in jar):
        raise RuntimeError("INLABS: login falhou (confira INLABS_EMAIL e INLABS_PASSWORD)")

    items: list[dict] = []
    day = since
    while day <= today:
        for section in SECTIONS:
            url = bf.INLABS_DOWNLOAD.format(day=day.isoformat(), section=section)
            req = urllib.request.Request(url, headers={"User-Agent": bf.USER_AGENT, "origem": "736372697074"})
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
            found = extract_zip(data)
            if found:
                log(f"DOU {day} {section}: {len(found)} autorização(ões)")
            items.extend(found)
            time.sleep(1)
        day += dt.timedelta(days=1)
    return items


# ---------------------------------------------------------------- ciclo de vida

def org_keys(organization: str) -> set[str]:
    """Chaves de comparação de um órgão: o nome inteiro e o nome sem a sigla final ("… - CGU")."""
    keys = {re.sub(r"[^a-z0-9]+", " ", norm(organization)).strip()}
    without_acronym = re.sub(r"\s+[-–]\s+[A-Za-z0-9/.-]{2,12}$", "", organization)
    keys.add(re.sub(r"[^a-z0-9]+", " ", norm(without_acronym)).strip())
    return {k for k in keys if k}


def feed_org(title: str) -> str:
    """O título do feed é "Órgão - Concurso Público (Edital nº …)"; devolve só o órgão."""
    return re.sub(r"\s+[-–]\s+(?:concurso\s+p[úu]blico|processo\s+seletivo)\b.*$", "", title, flags=re.IGNORECASE).strip()


def has_edital(forecast: dict, feed: list[dict]) -> bool:
    """Há no feed um edital do mesmo órgão, publicado a partir da autorização?"""
    keys = org_keys(forecast["organization"])
    for exam in feed:
        if not isinstance(exam, dict) or not isinstance(exam.get("title"), str):
            continue
        published = exam.get("published")
        if isinstance(published, str) and published < forecast["authorizedOn"]:
            continue  # concurso anterior do mesmo órgão
        if keys & org_keys(feed_org(exam["title"])):
            return True
    return False


def is_expired(forecast: dict, today: dt.date) -> bool:
    if forecast.get("editalDeadline"):
        return dt.date.fromisoformat(forecast["editalDeadline"]) + dt.timedelta(days=EXPIRY_GRACE_DAYS) < today
    return dt.date.fromisoformat(forecast["authorizedOn"]) + dt.timedelta(days=NO_DEADLINE_TTL_DAYS) < today


def dedupe(items: list[dict]) -> list[dict]:
    """Uma entrada por ato: a republicação "(*)" e o mesmo ato lido duas vezes não se repetem.
    Fica a publicação mais antiga e, no empate, a que não é republicação."""
    best: dict[str, dict] = {}
    for item in items:
        key = f"{norm(item['organization'])}|{norm(item.get('act') or item['id'])}"
        current = best.get(key)
        rank = (item["authorizedOn"], bool(item.get("_republished")))
        if current is None or rank < (current["authorizedOn"], bool(current.get("_republished"))):
            best[key] = item
    return list(best.values())


def update_forecasts(old: list[dict], new: list[dict], feed: list[dict], today: dt.date) -> list[dict]:
    """Junta o arquivo anterior com os atos novos, tira os que saíram e ordena por authorizedOn decrescente."""
    merged = dedupe([*old, *new])
    kept = []
    for item in merged:
        if has_edital(item, feed):
            log(f"  saiu (edital publicado): {item['organization']}")
        elif is_expired(item, today):
            log(f"  saiu (prazo vencido): {item['organization']}")
        else:
            kept.append({k: v for k, v in item.items() if not k.startswith("_")})
    return sorted(kept, key=lambda i: (i["authorizedOn"], i["id"]), reverse=True)


def load_json_list(path: Path) -> list[dict]:
    if not path.exists():
        return []
    data = json.loads(path.read_text())
    return data if isinstance(data, list) else []


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--days", type=int, default=7, help="dias para trás na busca (padrão: 7; use 365 na primeira carga)")
    parser.add_argument("--output", type=Path, default=Path("docs/previstos.json"))
    parser.add_argument("--feed", type=Path, default=Path("docs/concursos.json"), help="feed lido (nunca alterado) para saber se já saiu o edital")
    parser.add_argument("--xml", type=Path, nargs="*", default=[], help="XMLs do INLABS locais, em vez de baixar (testes e reprocessamento)")
    args = parser.parse_args()

    today = dt.date.today()
    try:
        old = load_json_list(args.output)
        feed = load_json_list(args.feed)
    except json.JSONDecodeError as err:
        log(f"arquivo ilegível: {err}")
        return 1

    new: list[dict] = []
    if args.xml:
        for path in args.xml:
            new += extract_articles(ET.fromstring(path.read_bytes()))
    else:
        try:
            new = fetch_dou(today - dt.timedelta(days=args.days), today)
        except Exception as err:  # noqa: BLE001 - INLABS fora do ar não apaga o que já foi capturado
            log(f"DOU: {err}")

    result = update_forecasts(old, new, feed, today)
    if problems := forecasts_errors(result, today):
        # Não grava um arquivo fora do contrato; o anterior continua no ar.
        for problem in problems:
            log(f"  inválido: {problem}")
        return 1
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n")
    log(f"previstos: {len(result)} ({len(new)} ato(s) lidos nesta execução)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
