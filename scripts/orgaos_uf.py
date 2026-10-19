"""UF da sede dos órgãos federais locais (universidades, institutos federais, CEFETs, TREs…).

No DOU, `state` vinha "BR" quando o nome do órgão não trazia o nome de um estado: a Fundação Universidade de
Brasília, a Universidade Federal de Viçosa e o Instituto Federal Goiano ficavam como nacionais. Esta lista
curada corrige isso.

Regra conservadora: só entra o órgão cujo nome identifica uma única UF, ou seja, uma instituição com sede e
campi num só estado. Ficam como "BR" os órgãos nacionais (ministérios, agências, conselhos, ENAP, Polícia
Federal, PRF, Marinha, EBSERH) e as universidades com campi em mais de um estado (UFFS, Unilab, Univasf).

A comparação ignora caixa, acento, pontuação, o "Fundação" do início e as preposições: "Instituto Federal de
Educação, Ciência e Tecnologia do Ceará" e "Instituto Federal de Educação Ciência e Tecnologia de Ceará" casam.
"""

from __future__ import annotations

import re
import unicodedata

IF = "instituto federal de educacao ciencia e tecnologia "

# Nome do órgão (como vem no artCategory do DOU) -> UF da sede.
SEDE_UF = {
    # Universidades federais cujo nome não traz o nome do estado.
    "universidade de brasilia": "DF",
    "universidade federal de alfenas": "MG",
    "universidade federal de campina grande": "PB",
    "universidade federal de catalao": "GO",
    "universidade federal de ciencias da saude de porto alegre": "RS",
    "universidade federal de itajuba": "MG",
    "universidade federal de jatai": "GO",
    "universidade federal de juiz de fora": "MG",
    "universidade federal de lavras": "MG",
    "universidade federal de ouro preto": "MG",
    "universidade federal de pelotas": "RS",
    "universidade federal de rondonopolis": "MT",
    "universidade federal de santa maria": "RS",
    "universidade federal de sao carlos": "SP",
    "universidade federal de sao joao del rei": "MG",
    "universidade federal de uberlandia": "MG",
    "universidade federal de vicosa": "MG",
    "universidade federal do abc": "SP",
    "universidade federal do agreste de pernambuco": "PE",
    "universidade federal do cariri": "CE",
    "universidade federal do delta do parnaiba": "PI",
    "universidade federal do norte do tocantins": "TO",
    "universidade federal do oeste da bahia": "BA",
    "universidade federal do oeste do para": "PA",
    "universidade federal do pampa": "RS",
    "universidade federal do reconcavo da bahia": "BA",
    "universidade federal do rio grande": "RS",
    "universidade federal do sul da bahia": "BA",
    "universidade federal do sul e sudeste do para": "PA",
    "universidade federal do triangulo mineiro": "MG",
    "universidade federal dos vales do jequitinhonha e mucuri": "MG",
    "universidade federal fluminense": "RJ",
    "universidade federal rural da amazonia": "PA",
    "universidade federal rural de pernambuco": "PE",
    "universidade federal rural do rio de janeiro": "RJ",
    "universidade federal rural do semi arido": "RN",
    "universidade federal da integracao latino americana": "PR",
    "universidade tecnologica federal do parana": "PR",
    # Universidades federais com o nome do estado (o regex de dou_uf já pega a maioria; aqui por garantia).
    "universidade federal do acre": "AC",
    "universidade federal de alagoas": "AL",
    "universidade federal do amapa": "AP",
    "universidade federal do amazonas": "AM",
    "universidade federal da bahia": "BA",
    "universidade federal do ceara": "CE",
    "universidade federal do espirito santo": "ES",
    "universidade federal de goias": "GO",
    "universidade federal do maranhao": "MA",
    "universidade federal de mato grosso": "MT",
    "universidade federal de mato grosso do sul": "MS",
    "universidade federal de minas gerais": "MG",
    "universidade federal do para": "PA",
    "universidade federal da paraiba": "PB",
    "universidade federal do parana": "PR",
    "universidade federal de pernambuco": "PE",
    "universidade federal do piaui": "PI",
    "universidade federal do rio de janeiro": "RJ",
    "universidade federal do estado do rio de janeiro": "RJ",
    "universidade federal do rio grande do norte": "RN",
    "universidade federal do rio grande do sul": "RS",
    "universidade federal de rondonia": "RO",
    "universidade federal de roraima": "RR",
    "universidade federal de santa catarina": "SC",
    "universidade federal de sao paulo": "SP",
    "universidade federal de sergipe": "SE",
    "universidade federal do tocantins": "TO",
    # Institutos federais (38).
    IF + "do acre": "AC",
    IF + "de alagoas": "AL",
    IF + "do amapa": "AP",
    IF + "do amazonas": "AM",
    IF + "da bahia": "BA",
    IF + "baiano": "BA",
    IF + "de brasilia": "DF",
    IF + "do ceara": "CE",
    IF + "do espirito santo": "ES",
    IF + "de goias": "GO",
    IF + "goiano": "GO",
    IF + "do maranhao": "MA",
    IF + "de minas gerais": "MG",
    IF + "do norte de minas gerais": "MG",
    IF + "do sudeste de minas gerais": "MG",
    IF + "do sul de minas gerais": "MG",
    IF + "do triangulo mineiro": "MG",
    IF + "de mato grosso": "MT",
    IF + "de mato grosso do sul": "MS",
    IF + "do para": "PA",
    IF + "da paraiba": "PB",
    IF + "de pernambuco": "PE",
    IF + "do sertao pernambucano": "PE",
    IF + "do piaui": "PI",
    IF + "do parana": "PR",
    IF + "do rio de janeiro": "RJ",
    IF + "fluminense": "RJ",
    IF + "do rio grande do norte": "RN",
    IF + "do rio grande do sul": "RS",
    IF + "farroupilha": "RS",
    IF + "sul rio grandense": "RS",
    IF + "de rondonia": "RO",
    IF + "de roraima": "RR",
    IF + "de santa catarina": "SC",
    IF + "catarinense": "SC",
    IF + "de sao paulo": "SP",
    IF + "de sergipe": "SE",
    IF + "do tocantins": "TO",
    # CEFETs e escolas federais de um só endereço.
    "centro federal de educacao tecnologica de minas gerais": "MG",
    "centro federal de educacao tecnologica celso suckow da fonseca": "RJ",
    "colegio pedro ii": "RJ",
    "instituto benjamin constant": "RJ",
    "instituto nacional de educacao de surdos": "RJ",
    "hospital de clinicas de porto alegre": "RS",
}

# Tribunal Regional Eleitoral de <estado>: um por UF. TRTs e TRFs já saem pela região em build_feed.REGION.
UF_BY_NAME = {
    "acre": "AC", "alagoas": "AL", "amapa": "AP", "amazonas": "AM", "bahia": "BA", "ceara": "CE",
    "distrito federal": "DF", "espirito santo": "ES", "goias": "GO", "maranhao": "MA", "mato grosso do sul": "MS",
    "mato grosso": "MT", "minas gerais": "MG", "para": "PA", "paraiba": "PB", "parana": "PR", "pernambuco": "PE",
    "piaui": "PI", "rio de janeiro": "RJ", "rio grande do norte": "RN", "rio grande do sul": "RS", "rondonia": "RO",
    "roraima": "RR", "santa catarina": "SC", "sao paulo": "SP", "sergipe": "SE", "tocantins": "TO",
}

STOP = {"de", "do", "da", "dos", "das"}


def norm(name: str) -> str:
    """Sem acento, caixa, pontuação, "Fundação" inicial e preposições."""
    text = "".join(c for c in unicodedata.normalize("NFD", name or "") if unicodedata.category(c) != "Mn").lower()
    words = re.sub(r"[^a-z0-9]+", " ", text).split()
    if words[:1] == ["fundacao"]:
        words = words[1:]
    return " ".join(w for w in words if w not in STOP)


_SEDE = {norm(k): v for k, v in SEDE_UF.items()}
_TRE = {norm("tribunal regional eleitoral " + k): v for k, v in UF_BY_NAME.items()}


def sede_uf(org: str) -> str | None:
    """UF da sede de um órgão federal local; None quando o nome não identifica uma única UF."""
    key = norm(org)
    return _SEDE.get(key) or _TRE.get(key)


def org_of(item: dict) -> str:
    """O órgão de um item do DOU é o começo do título: "Universidade Federal de Viçosa - Concurso Público…"."""
    return (item.get("title") or "").split(" - ")[0]


def fix_states(feed: list[dict]) -> int:
    """Troca "BR" pela UF da sede nos itens do DOU, inclusive os já publicados. Devolve quantos mudaram."""
    changed = 0
    for item in feed:
        if item.get("state") == "BR" and item.get("source") == "dou":
            uf = sede_uf(org_of(item))
            if uf:
                item["state"] = uf
                changed += 1
    return changed
