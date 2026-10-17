"""Coletores das bancas organizadoras: IBGP, ProSeleta (Avança SP, Objetivas…) e Cebraspe.

Todas publicam uma lista pública de inscrições abertas. Lemos só os fatos da lista (órgão, tipo, número do
edital, datas de inscrição, vagas, salário máximo) e apontamos o link para a página da banca; nada do edital é copiado.
Usamos o User-Agent identificável do projeto (vem do `fetch`) e 1 s entre requisições.

- `ibgp`: JSON em /rest/concurso/inscricaoAberta (UF e município no próprio nome do concurso);
- `proseleta:<slug>`: HTML ISO-8859-1 em <site>/index/abertos/?pp=100, um site por cliente da plataforma;
- `cebraspe`: JSON em apis.cebraspe.org.br; só eventos estaduais, a UF vem do código do evento (federais já saem
  pelo DOU) e, sem UF reconhecida, o evento é ignorado.

Fora do escopo do app: vestibular, residência, especialização e hospitais particulares.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import html
import json
import re
import time
import unicodedata
from typing import Callable

IBGP_URL = "https://www.ibgpconcursos.com.br/rest/concurso/inscricaoAberta"
IBGP_HOME = "https://www.ibgpconcursos.com.br/"
CEBRASPE_LIST = "https://apis.cebraspe.org.br/cebraspe/eventos/tipo/concursos/"
CEBRASPE_EVENT = "https://apis.cebraspe.org.br/cebraspe/eventos/{url}"
CEBRASPE_PAGE = "https://www.cebraspe.org.br/concursos/{url}"
# slug -> (site, UF padrão quando o título não traz a UF, banca)
PROSELETA = {
    "avancasp": ("https://avancasp.org.br", "SP", "Instituto Avança SP"),
    "objetivas": ("https://concursos.objetivas.com.br", None, "Objetivas"),
    "igeduc": ("https://www.igeduc.org.br", None, "IGEDUC"),
}
DELAY = 1.0

UFS = {
    "AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA", "MT", "MS", "MG", "PA", "PB", "PR", "PE", "PI",
    "RJ", "RN", "RS", "RO", "RR", "SC", "SP", "SE", "TO",
}
SKIP_KIND = re.compile(r"vestibular|resid[êe]ncia|especializa|fellowship|p[óo]s-gradua", re.IGNORECASE)
PRIVATE = re.compile(r"santa\s+casa|hospital\s+(?!municipal|estadual|regional|universit)|benefic[êe]ncia", re.IGNORECASE)
LOWER_WORDS = {"de", "da", "do", "das", "dos", "e", "em"}


def _fold(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn")


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def _title_case(text: str) -> str:
    words = text.lower().split()
    return " ".join("-".join(part.capitalize() for part in w.split("-")) if not i or w not in LOWER_WORDS else w
                    for i, w in enumerate(words))


SELETIVO = re.compile(r"seletivo|sele[çc][ãa]o", re.IGNORECASE)


def kind_of(label: str) -> str:
    return "processo_seletivo" if SELETIVO.search(label) else "concurso"


def kind_label(kind: str) -> str:
    return "Processo Seletivo" if kind == "processo_seletivo" else "Concurso Público"


def br_date(text: str) -> str | None:
    """"16/10/2026" ou "16/10/26" -> "2026-10-16"; None se não for uma data."""
    m = re.search(r"(\d{2})/(\d{2})/(\d{2,4})", text or "")
    if not m:
        return None
    day, month, year = m.groups()
    try:
        return dt.date(int(year) + 2000 if len(year) == 2 else int(year), int(month), int(day)).isoformat()
    except ValueError:
        return None


def _br(iso: str) -> str:
    return dt.date.fromisoformat(iso).strftime("%d/%m/%Y")


def _brl(value: float) -> str:
    return "R$ " + f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def build_item(*, key: str, org: str, label_kind: str, number: str, state: str, city: str | None, sphere: str,
               banca: str, collector: str, url: str, starts: str | None, ends: str | None, vacancies: int | None,
               salary: float | None, today: dt.date, source_name: str) -> dict:
    kind = kind_of(label_kind)
    suffix = f" nº {number}" if number else ""
    deadline = (f"Inscrições: {_br(starts)} a {_br(ends)}" if starts and ends
                else f"Inscrições até {_br(ends)}" if ends else "Inscrições: veja o edital")
    return {
        "id": hashlib.sha1(key.encode()).hexdigest()[:12],
        "title": f"{org} - {kind_label(kind)}{suffix}",
        "deadline": deadline,
        "state": state,
        "salary": f"Salário até {_brl(salary)}" if salary else "",
        "vacancies": f"Vagas {vacancies}" if vacancies else "",
        "description": (f"{org}: {kind_label(kind).lower()}{suffix} com inscrições abertas, organizado por {banca}. "
                        f"Cargos, requisitos e valores no edital.\n\nFonte: {source_name}, {_br(today.isoformat())}. "
                        "Informações extraídas automaticamente; confira sempre o edital oficial."),
        "url": url,
        "linkKind": "banca",
        "kind": kind,
        "source": "banca",
        "city": city,
        "published": today.isoformat(),
        "registrationStarts": starts,
        "registrationEnds": ends,
        "banca": banca,
        "sphere": sphere,
        "collector": collector,
    }


def _accepted(label_kind: str, org: str) -> bool:
    return not (SKIP_KIND.search(label_kind) or SKIP_KIND.search(org) or PRIVATE.search(org))


# ---------------------------------------------------------------- IBGP

# "…DA CÂMARA MUNICIPAL DE DORES DO INDAIÁ/MG - EDITAL Nº 01/2026": órgão, município, UF e número do edital.
# Autarquias ("…/MG - DAMAE - EDITAL…") não casam e ficam de fora.
IBGP_NAME = re.compile(
    r"\b(?P<org>munic[íi]pio|c[âa]mara\s+municipal|prefeitura\s+municipal|guarda\s+civil\s+municipal)\s+d[eo]\s+"
    r"(?P<city>[^/]+?)\s*/\s*(?P<uf>[A-Z]{2})\s*-\s*edital",
    re.IGNORECASE,
)


def parse_ibgp(raw: bytes, today: dt.date) -> list[dict]:
    items = []
    for row in json.loads(raw.decode("utf-8")):
        label = (row.get("tipo") or {}).get("nome", "")
        name = _clean(row.get("nome", ""))
        m = IBGP_NAME.search(name)
        if not m or not row.get("statusInscricao", True) or not _accepted(label, name):
            continue
        uf = m.group("uf").upper()
        if uf not in UFS:
            continue
        city = _title_case(m.group("city"))
        org = f"Câmara de {city}" if re.match(r"(?i)c[âa]mara", m.group("org")) else f"Prefeitura de {city}"
        number = re.sub(r"(?i)^edital\s+n[º°o.]*\s*", "", _clean(row.get("edital", "")))
        items.append(build_item(
            key=f"ibgp|{row['id']}", org=org, label_kind=name if SELETIVO.search(name) else label, number=number,
            state=uf, city=city, sphere="municipal", banca="IBGP", collector="ibgp", url=IBGP_HOME,
            starts=br_date(row.get("inicioInscricaoFormatado", "")), ends=br_date(row.get("fimInscricaoFormatado", "")),
            vacancies=row.get("totalVagas") or None, salary=None, today=today, source_name="IBGP Concursos",
        ))
    return items


# ---------------------------------------------------------------- ProSeleta

CARD = re.compile(r'<li class="item">(.*?)</li>', re.DOTALL)


def parse_proseleta(page: str, base: str, default_uf: str | None, banca: str, slug: str, today: dt.date) -> list[dict]:
    items = []
    for block in CARD.findall(page):
        title_m = re.search(r'<h3><a href="(/informacoes/(\d+)/)"[^>]*>(.*?)</a>', block, re.DOTALL)
        if not title_m:
            continue
        path, code, title = title_m.group(1), title_m.group(2), _clean(re.sub(r"<[^>]+>", "", title_m.group(3)))
        label = _clean(re.sub(r"<[^>]+>", "", (re.search(r'class="tipo">(.*?)</p>', block, re.DOTALL) or [0, ""])[1]))
        dates = [br_date(d) for d in re.findall(r"<b>(\d{2}/\d{2}/\d{4})</b>", block)]
        if not _accepted(label, title) or len(dates) < 2 or not dates[0] or not dates[1]:
            continue
        number_m = re.search(r"(\d+/\d{4})", title)
        number = number_m.group(1) if number_m else ""
        vac_m = re.search(r'class="qtde"[^>]*>\s*(\d+)', block)
        # Dois formatos: "CIDADE/UF - Município - Concurso Público 01/2026" e "Concurso Público - 01/2026 - ÓRGÃO"
        head = re.match(r"^\s*([^/]+?)\s*/\s*([A-Z]{2})\s*-\s*(?:Munic[íi]pio|Estado|Prefeitura|C[âa]mara)", title)
        if head:
            city, uf = _title_case(head.group(1)), head.group(2)
            org = f"Prefeitura de {city}"
        else:
            entity = title.split(" - ")[-1]
            uf = default_uf or ""
            if re.match(r"(?i)prefeitura\s+municipal\s+d[aoe]s?\s+", entity):
                city = _title_case(re.sub(r"(?i)^prefeitura\s+municipal\s+d[aoe]s?\s+", "", entity))
                org = f"Prefeitura de {city}"
            elif re.match(r"(?i)c[âa]mara\s+municipal\s+d[aoe]s?\s+", entity):
                city = _title_case(re.sub(r"(?i)^c[âa]mara\s+municipal\s+d[aoe]s?\s+", "", entity))
                org = f"Câmara de {city}"
            else:
                continue  # autarquias e outras entidades: sem município seguro, não publicamos
        if uf not in UFS:
            continue
        items.append(build_item(
            key=f"proseleta|{slug}|{code}", org=org, label_kind=label, number=number, state=uf, city=city,
            sphere="municipal", banca=banca, collector=f"proseleta:{slug}", url=base + path,
            starts=dates[0], ends=dates[1], vacancies=int(vac_m.group(1)) if vac_m else None, salary=None,
            today=today, source_name=banca,
        ))
    return items


# ---------------------------------------------------------------- Cebraspe

# Siglas de órgãos municipais: sem cidade no código, ficam de fora.
CEBRASPE_MUNICIPAL = {"PREF", "PM", "CM", "CAMARA", "PMS"}


def cebraspe_uf(code: str) -> str | None:
    tokens = [t for t in re.split(r"[_\s]+", _fold(code).upper()) if t]
    if not tokens or tokens[0] in CEBRASPE_MUNICIPAL:
        return None
    found = [t for t in tokens if t in UFS]
    return found[0] if len(found) == 1 else None


def cebraspe_org(code: str, uf: str) -> str:
    tokens = [t for t in re.split(r"[_\s]+", code.upper()) if t and not re.fullmatch(r"\d{2,4}", t) and t != uf]
    return f"{' '.join(tokens)} {uf}".strip()


def parse_cebraspe_list(raw: bytes) -> list[dict]:
    """Eventos da fase "Inscrições Abertas" com UF reconhecida."""
    out = []
    for group in json.loads(raw.decode("utf-8")):
        if group.get("faseEvento") != "Inscrições Abertas":
            continue
        for event in group.get("eventos", []):
            if cebraspe_uf(event.get("eventoURL", "")):
                out.append(event)
    return out


def item_from_cebraspe(event: dict, detail: dict | None, today: dt.date) -> dict | None:
    code = event["eventoURL"]
    uf = cebraspe_uf(code)
    if not uf:
        return None
    period = (detail or {}).get("periodoInscricao") or ""
    dates = [br_date(d) for d in re.findall(r"\d{2}/\d{2}/\d{4}", period)]
    starts, ends = (dates + [None, None])[:2]
    if starts and ends and starts > ends:
        starts = None
    vac = event.get("eventoTotalVagas")
    vacancies = int(vac) if str(vac or "").isdigit() else None
    salary = event.get("eventoSalarioMaximo") or None
    return build_item(
        key=f"cebraspe|{code}", org=cebraspe_org(code, uf), label_kind="Concurso Público", number="", state=uf,
        city=None, sphere="estadual", banca="Cebraspe", collector="cebraspe", url=CEBRASPE_PAGE.format(url=code),
        starts=starts, ends=ends, vacancies=vacancies, salary=float(salary) if salary else None, today=today,
        source_name="Cebraspe",
    )


# ---------------------------------------------------------------- coleta


def _safe(name: str, log: Callable[[str], None], run: Callable[[], list[dict]]) -> list[dict]:
    try:
        found = run()
    except Exception as err:  # noqa: BLE001 - uma banca fora do ar não derruba as outras
        log(f"{name}: falhou: {err}")
        return []
    log(f"{name}: {len(found)} abertura(s) lida(s)")
    return found


def collect(
    since: dt.date,
    today: dt.date,
    *,
    fetch: Callable[[str], bytes],
    off: set[str] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    log: Callable[[str], None] = lambda msg: None,
    only: str | None = None,
    **_: object,
) -> list[dict]:
    """Aberturas das três bancas. `only` limita a coleta a uma delas (ibgp, proseleta, cebraspe)."""
    off = off or set()
    items: list[dict] = []

    def wanted(name: str) -> bool:
        if name in off:
            log(f"{name}: desligado em SOURCES_OFF")
        return name not in off and only in (None, name.split(":")[0])

    if wanted("ibgp"):
        items += _safe("ibgp", log, lambda: parse_ibgp(fetch(IBGP_URL), today))
    for slug, (base, default_uf, banca) in PROSELETA.items():
        if not wanted(f"proseleta:{slug}"):
            continue
        sleep(DELAY)
        items += _safe(f"proseleta:{slug}", log, lambda: parse_proseleta(
            fetch(f"{base}/index/abertos/?pp=100").decode("iso-8859-1"), base, default_uf, banca, slug, today))
    if wanted("cebraspe"):
        def cebraspe() -> list[dict]:
            out = []
            for event in parse_cebraspe_list(fetch(CEBRASPE_LIST)):
                sleep(DELAY)
                try:
                    detail = json.loads(fetch(CEBRASPE_EVENT.format(url=event["eventoURL"])).decode("utf-8"))
                except Exception as err:  # noqa: BLE001
                    log(f"  cebraspe/{event['eventoURL']}: {err}")
                    detail = None
                item = item_from_cebraspe(event, detail, today)
                if item:
                    out.append(item)
            return out
        items += _safe("cebraspe", log, cebraspe)
    return items
