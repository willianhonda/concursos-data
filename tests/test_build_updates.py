"""Modo sombra das atualizações depois da inscrição (spike CA-006).

As fixtures em tests/fixtures/updates/ são trechos reais dos diários citados no spike, exceto
o XML do DOU, que segue o formato do INLABS (não há credenciais do INLABS fora do Actions).
"""

import csv
import datetime as dt
import hashlib
import io
import json
import os
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import build_feed as bf  # noqa: E402
import build_updates as bu  # noqa: E402
import validate_updates as vu  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"
UPDATES = FIXTURES / "updates"


def gazette(territory_id, city, uf, date):
    return {
        "territory_id": territory_id,
        "territory_name": city,
        "state_code": uf,
        "date": date,
        "url": f"https://data.queridodiario.ok.org.br/{territory_id}/{date}/x.pdf",
        "txt_url": f"https://data.queridodiario.ok.org.br/{territory_id}/{date}/x.txt",
    }


PORANGABA = gazette("3540507", "Porangaba", "SP", "2026-09-28")
NHANDEARA = gazette("3532603", "Nhandeara", "SP", "2026-09-29")
BELA_VISTA = gazette("5002100", "Bela Vista", "MS", "2026-09-30")
ITAPOLIS = gazette("3522703", "Itápolis", "SP", "2026-09-29")
ITAPOLIS_OPENING = gazette("3522703", "Itápolis", "SP", "2026-09-15")
IGARACU = gazette("3520004", "Igaraçu do Tietê", "SP", "2026-09-30")
ITAJUBA = gazette("3132404", "Itajubá", "MG", "2026-10-06")


def feed_item(g, org, kind, number, title_suffix=""):
    """Item do feed como o build_feed grava, com o id calculado pela mesma regra (território|tipo|nº)."""
    return {
        "id": hashlib.sha1(f"{g['territory_id']}|{kind}|{number}".encode()).hexdigest()[:12],
        "title": f"{org} - {bf.KIND_LABEL[kind]} nº {number}{title_suffix}",
        "state": g["state_code"],
        "city": g["territory_name"],
        "kind": kind,
        "source": "querido_diario",
        "published": g["date"],
        "gazetteUrl": g["url"],
    }


# Aberturas reais dos últimos 90 dias (reconstruídas no spike), no formato do feed.
OPENINGS = [
    feed_item(gazette("3540507", "Porangaba", "SP", "2026-08-14"), "Município de Porangaba", "concurso", "01/2026"),
    feed_item(gazette("3540507", "Porangaba", "SP", "2026-08-14"), "Município de Porangaba", "concurso", "02/2026"),
    feed_item(gazette("3540507", "Porangaba", "SP", "2026-08-17"), "Município de Porangaba", "concurso", "03/2026"),
    feed_item(gazette("3532603", "Nhandeara", "SP", "2026-08-24"), "Município de Nhandeara", "processo_seletivo", "001/2026"),
    feed_item(gazette("5002100", "Bela Vista", "MS", "2026-07-29"), "Prefeitura Municipal de Bela Vista", "processo_seletivo", "003/2026"),
]


def read(name):
    return (UPDATES / name).read_text()


def build_index(items, extra=()):
    return bu.OpeningIndex(bu.merge_index([], [e for e in map(bu.index_entry, items) if e] + list(extra), dt.date(2026, 10, 9)))


def link_all(acts, index):
    return bu.group_acts([(act, *index.link(act)) for act in acts])


class NumberTests(unittest.TestCase):
    def test_zeros_separators_and_short_years(self):
        for raw in ("01/2026", "001/2026", "1/2026", "001/26", "1-2026", " 01 / 2026 "):
            self.assertEqual(bu.norm_number(raw), "1/2026", raw)
        self.assertEqual(bu.norm_number("014/2026"), "14/2026")
        self.assertEqual(bu.norm_number("10/2026"), "10/2026")
        self.assertEqual(bu.norm_number(""), "")
        self.assertEqual(bu.norm_number("12/202"), "")

    def test_selection_number_beats_act_number(self):
        # "Edital de Convocação nº 579/2026 … Processo Seletivo nº 01/2026": o número do ato não serve.
        window = "EDITAL DE CONVOCAÇÃO Nº 579/2026\nA Prefeitura convoca os aprovados no Processo Seletivo nº 01/2026."
        numbers = bu.find_numbers(window, window.find("convoca"))
        self.assertEqual([(n["number"], n["kind"]) for n in numbers], [("1/2026", "processo_seletivo")])

    def test_org_types(self):
        self.assertEqual(bu.org_type("Município de Porangaba"), "prefeitura")
        self.assertEqual(bu.org_type("Prefeitura Municipal de Porangaba"), "prefeitura")
        self.assertEqual(bu.org_type("Câmara Municipal de Itápolis"), "camara")
        self.assertEqual(bu.org_type("Serviço Autônomo de Água e Esgoto de Itápolis"), "autarquia")
        self.assertEqual(bu.org_type("SAAE de Sorocaba"), "autarquia")
        self.assertEqual(bu.org_type("Fundação Municipal de Ensino Superior de Marília"), "fundacao")
        self.assertEqual(bu.org_type("Instituto de Previdência do Município de Bauru"), "instituto")

    def test_org_of_another_city_falls_back_to_the_municipality(self):
        # No diário do Rio, o find_org devolveu a Prefeitura de Osasco.
        pre = "PREFEITURA DO MUNICÍPIO DE OSASCO, no uso de suas atribuições. HOMOLOGO o resultado"
        self.assertEqual(bu.act_org(pre, pre, "Rio de Janeiro"), "Município de Rio de Janeiro")


class ClassifierTests(unittest.TestCase):
    def test_porangaba_homologations_link_by_municipality_and_number(self):
        acts = bu.extract_updates(read("porangaba_2026-09-28.txt"), PORANGABA)
        self.assertEqual([(a["type"], a["numbers"][0]["number"]) for a in acts],
                         [("homologacao", "1/2026"), ("homologacao", "2/2026"), ("homologacao", "3/2026")])
        # "Prefeitura Municipal de Porangaba" no ato, "Município de Porangaba" na abertura: mesmo tipo de órgão.
        groups = link_all(acts, build_index(OPENINGS))
        self.assertEqual([opening["id"] for _, opening, _, _ in groups], [o["id"] for o in OPENINGS[:3]])
        update = bu.make_update(groups[0][0], groups[0][1])
        self.assertEqual(update["examId"], OPENINGS[0]["id"])
        self.assertEqual(update["type"], "homologacao")
        self.assertEqual(update["title"], "Concurso homologado")
        self.assertEqual(update["edital"], "Concurso Público nº 01/2026")
        self.assertEqual(update["organization"], "Prefeitura Municipal de Porangaba")
        self.assertEqual(update["published"], "2026-09-28")
        self.assertEqual(update["source"], "querido_diario")
        self.assertIn("EDITAL DE HOMOLOGAÇÃO DO RESULTADO FINAL", update["excerpt"])
        self.assertEqual(vu.update_errors(update, dt.date(2026, 10, 9)), [])

    def test_schedule_inside_opening_edital_is_not_an_act(self):
        # O mesmo diário de Porangaba traz o edital de abertura, com "Edital de Convocação" no corpo
        # e o Anexo III – Cronograma ("Publicação do Edital de Homologação do Resultado Final").
        text = read("porangaba_2026-09-28_abertura.txt")
        self.assertIn("Edital de Homologação do Resultado Final", text)
        self.assertEqual(bu.extract_updates(text, PORANGABA), [])
        schedule = (
            "Concurso Público nº 04/2026\nANEXO III – CRONOGRAMA\n"
            "DIVULGAÇÃO DO GABARITO PRELIMINAR 05/10/2026\nRESULTADO FINAL 30/10/2026\n"
            "HOMOLOGAÇÃO DO RESULTADO FINAL 11/11/2026\n"
        )
        self.assertEqual(bu.extract_updates(schedule, PORANGABA), [])

    def test_nhandeara_final_classification(self):
        [act] = bu.extract_updates(read("nhandeara_2026-09-29.txt"), NHANDEARA)
        self.assertEqual(act["type"], "resultado")
        self.assertEqual(act["numbers"][0], {"number": "1/2026", "raw": "001/2026", "kind": "processo_seletivo"})
        opening, reason = build_index(OPENINGS).link(act)
        self.assertEqual(reason, "ok")
        self.assertEqual(opening["id"], OPENINGS[3]["id"])

    def test_bela_vista_result_and_homologation(self):
        # "EDITAL Nº 012/2026 - RESULTADO FINAL E HOMOLOGAÇÃO" do Processo Seletivo nº 003/2026,
        # que cita o "Edital de Abertura nº 001/2026".
        [act] = bu.extract_updates(read("bela_vista_2026-09-30.txt"), BELA_VISTA)
        self.assertEqual(act["type"], "homologacao")
        self.assertEqual([n["number"] for n in act["numbers"]], ["3/2026", "1/2026"])
        opening, reason = build_index(OPENINGS).link(act)
        self.assertEqual(reason, "ok")
        self.assertEqual(opening["id"], OPENINGS[4]["id"])

    def test_itapolis_saae_gabarito_does_not_link_to_the_camara(self):
        # A Câmara de Itápolis abriu o Concurso Público nº 01/2026; o gabarito é do SAAE, com o mesmo número.
        openings = bf.extract_openings((FIXTURES / "itapolis_2026-09-15.txt").read_text(), ITAPOLIS_OPENING)
        entries = [bu.index_entry(i, ITAPOLIS_OPENING["territory_id"]) for i in openings]
        self.assertEqual([(e["orgType"], e["number"]) for e in entries], [("camara", "1/2026")])
        acts = bu.extract_updates(read("itapolis_2026-09-29.txt"), ITAPOLIS)
        self.assertEqual([a["type"] for a in acts], ["convocacao", "gabarito"])
        gabarito = acts[1]
        self.assertEqual(gabarito["orgType"], "autarquia")
        self.assertEqual(gabarito["numbers"][0]["number"], "1/2026")
        index = bu.OpeningIndex(entries)
        self.assertEqual(index.link(gabarito), (None, "tipo de órgão diferente"))
        # A convocação é do Concurso 001/2023, que não está no índice.
        self.assertEqual(index.link(acts[0]), (None, "sem abertura com esse número"))

    def test_same_id_for_two_organizations_is_not_linked(self):
        # O spike reconstruiu "Câmara" e "Município de Itápolis" nº 01/2026 com o mesmo id do build_feed.
        camara = bu.index_entry(feed_item(ITAPOLIS_OPENING, "Câmara Municipal de Itápolis", "concurso", "01/2026"))
        generic = dict(camara, organization="Município de Itápolis", orgType="prefeitura")
        act = {"type": "resultado", "source": "querido_diario", "published": "2026-09-29", "territoryId": "3522703",
               "organization": "Prefeitura Municipal de Itápolis", "orgType": "prefeitura",
               "numbers": [{"number": "1/2026", "raw": "01/2026", "kind": "concurso"}]}
        self.assertEqual(bu.OpeningIndex([camara, generic]).link(act), (None, "id compartilhado por órgãos diferentes"))

    def test_igaracu_without_number_is_not_linked(self):
        # O diário real cita "EDITAL DE CONCURSO Nº 02/2026", que não está entre as aberturas conhecidas.
        [act] = bu.extract_updates(read("igaracu_do_tiete_2026-09-30.txt"), IGARACU)
        self.assertEqual(act["type"], "homologacao")
        self.assertEqual(build_index(OPENINGS).link(act), (None, "sem abertura no município"))
        # O trecho citado no spike, sem número nenhum: nunca liga, mesmo com abertura no município.
        text = ("DECRETO Nº 128, de 30 de setembro de 2026.\nArt. 1º - Fica homologado o Resultado Final do Concurso "
                "Público para os cargos de Professor de Educação Básica I.")
        [act] = bu.extract_updates(text, IGARACU)
        self.assertEqual(act["numbers"], [])
        igaracu = feed_item(gazette("3520004", "Igaraçu do Tietê", "SP", "2026-08-01"), "Município de Igaraçu do Tietê",
                            "concurso", "02/2026")
        self.assertEqual(build_index([igaracu]).link(act), (None, "sem número do concurso"))

    def test_itajuba_repeated_convocations_are_grouped(self):
        # Itajubá publica um "TERMO DE CONVOCAÇÃO" por candidato.
        acts = bu.extract_updates(read("itajuba_2026-10-06.txt"), ITAJUBA)
        self.assertEqual(len(acts), 6)
        self.assertTrue(all(a["type"] == "convocacao" for a in acts))
        groups = link_all(acts, build_index([]))
        self.assertEqual(
            [(act["numbers"][0]["number"], count) for act, _, _, count in groups],
            [("17/2026", 2), ("11/2026", 1), ("14/2026", 3)],
        )
        # Ligado, o grupo é por concurso, tipo e dia: o mesmo id de atualização.
        opening = feed_item(gazette("3132404", "Itajubá", "MG", "2026-08-01"), "Prefeitura Municipal de Itajubá",
                            "processo_seletivo", "014/2026")
        groups = link_all(acts, build_index([opening]))
        linked = [(o["id"], count) for _, o, _, count in groups if o]
        self.assertEqual(linked, [(opening["id"], 3)])

    def test_other_convocations_are_ignored(self):
        audiencia = ("Concurso Público nº 06/2026. Edital completo no site.\n\nEDITAL DE CONVOCAÇÃO DE AUDIÊNCIA PÚBLICA Nº 15/2026\n"
                     "CONVOCA, para os fins do disposto no art. 36 da Lei Complementar nº 141/2012.")
        self.assertEqual(bu.extract_updates(audiencia, ITAJUBA), [])
        prova = ("EDITAL DE CONVOCAÇÃO\nA Prefeitura convoca os candidatos inscritos no Concurso Público nº 02/2026 "
                 "para a realização das provas objetivas.")
        self.assertEqual(bu.extract_updates(prova, ITAJUBA), [])
        for noise in (
            "PORTARIA Nº 10. Nomeia os membros da Comissão Organizadora do Concurso Público nº 1/2026.",
            "Convoca os estagiários aprovados no processo seletivo nº 3/2026 de estágio.",
            "HOMOLOGO o resultado da licitação para contratação de empresa para realização de concurso público.",
            "Fica prorrogado por mais dois anos o prazo de validade do Concurso Público nº 1/2024, cujo resultado foi homologado em 2024.",
        ):
            self.assertEqual(bu.extract_updates(noise, ITAJUBA), [], noise)

    def test_act_before_the_opening_is_not_linked(self):
        act = bu.extract_updates(read("nhandeara_2026-09-29.txt"), dict(NHANDEARA, date="2026-08-01"))[0]
        self.assertEqual(build_index(OPENINGS).link(act), (None, "ato anterior à abertura"))


def dou_zip(xml: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("2026-09-25-DO3.xml", xml)
    return buffer.getvalue()


DOU_OPENING = {
    "id": "a1b2c3d4e5f6",
    "title": "Universidade Federal Rural do Semi-Árido - Concurso Público (Edital nº 30/2026)",
    "state": "RN",
    "city": None,
    "kind": "concurso",
    "source": "dou",
    "published": "2026-06-12",
    "gazetteUrl": "http://pesquisa.in.gov.br/imprensa/jsp/visualiza/index.jsp?data=12/06/2026&jornal=530&pagina=40",
}


class DouTests(unittest.TestCase):
    def test_inlabs_homologation_links_by_organization_and_cited_edital(self):
        acts = bu.extract_dou_updates_zip(dou_zip(read("dou_2026-09-25.xml")))
        # A abertura (Edital nº 41/2026) e a convocação para a prova didática ficam de fora.
        self.assertEqual(len(acts), 1)
        act = acts[0]
        self.assertEqual(act["type"], "homologacao")
        self.assertEqual(act["organization"], "Universidade Federal Rural do Semi-Árido")
        self.assertEqual([n["number"] for n in act["numbers"]], ["30/2026"])  # sem o nº 16 do próprio ato
        self.assertEqual(act["published"], "2026-09-25")
        opening, reason = build_index([DOU_OPENING]).link(act)
        self.assertEqual((opening["id"], reason), ("a1b2c3d4e5f6", "ok"))
        update = bu.make_update(act, opening)
        self.assertEqual(update["source"], "dou")
        self.assertEqual(update["edital"], "Concurso Público (Edital nº 30/2026)")
        self.assertEqual(update["state"], "RN")
        self.assertEqual(vu.update_errors(update, dt.date(2026, 10, 9)), [])

    def test_other_organization_with_the_same_number_is_not_linked(self):
        [act] = bu.extract_dou_updates_zip(dou_zip(read("dou_2026-09-25.xml")))
        other = dict(DOU_OPENING, id="ffffffffffff", title="Universidade Federal do Ceará - Concurso Público (Edital nº 30/2026)")
        self.assertEqual(build_index([other]).link(act), (None, "órgão sem abertura no índice"))

    def test_without_credentials_dou_is_skipped(self):
        with mock.patch.dict(os.environ, {"INLABS_EMAIL": "", "INLABS_PASSWORD": ""}), \
                mock.patch.object(bf, "fetch_dou", side_effect=AssertionError("não deveria acessar o INLABS")):
            self.assertIsNone(bu.fetch_dou_updates(dt.date(2026, 9, 25), dt.date(2026, 9, 25)))

    def test_fetch_dou_reuses_build_feed_with_the_updates_extractor(self):
        def fake_fetch(since, today):
            return bf.extract_dou_zip(dou_zip(read("dou_2026-09-25.xml")))

        with mock.patch.dict(os.environ, {"INLABS_EMAIL": "a@b.c", "INLABS_PASSWORD": "x"}), \
                mock.patch.object(bf, "fetch_dou", side_effect=fake_fetch):
            acts = bu.fetch_dou_updates(dt.date(2026, 9, 25), dt.date(2026, 9, 25))
        self.assertEqual([a["type"] for a in acts], ["homologacao"])
        # Depois da chamada, o build_feed volta a extrair aberturas.
        self.assertEqual(bf.extract_dou_zip.__module__, "build_feed")


class IndexTests(unittest.TestCase):
    def test_feed_items_become_index_entries(self):
        retification = feed_item(PORANGABA, "Município de Porangaba", "concurso", "01/2026", " (retificação)")
        entry = bu.index_entry(retification)
        self.assertEqual(entry["territoryId"], "3540507")
        self.assertEqual(entry["number"], "1/2026")
        self.assertEqual(entry["orgType"], "prefeitura")
        self.assertEqual(entry["title"], "Município de Porangaba - Concurso Público nº 01/2026")
        dou = bu.index_entry(DOU_OPENING)
        self.assertEqual((dou["number"], dou["territoryId"], dou["orgType"]), ("30/2026", None, None))
        no_number = dict(retification, title="Município de Porangaba - Concurso Público")
        self.assertIsNone(bu.index_entry(no_number))
        self.assertIsNone(bu.index_entry(dict(retification, source=None)))

    def test_merge_keeps_18_months_and_the_first_publication(self):
        today = dt.date(2026, 10, 9)
        old = bu.index_entry(feed_item(gazette("3540507", "Porangaba", "SP", "2025-03-01"), "Município de Porangaba",
                                       "concurso", "01/2025"))
        first = bu.index_entry(OPENINGS[0])
        later = dict(first, published="2026-09-21")
        merged = bu.merge_index([old, first], [later], today)
        self.assertEqual([(e["number"], e["published"]) for e in merged], [("1/2026", "2026-08-14")])


class OutputTests(unittest.TestCase):
    def run_main(self, shadow, feed, today="2026-10-09"):
        def fake_get(url, **_):
            if "api.queridodiario" in url:
                return json.dumps({"gazettes": [PORANGABA], "total_gazettes": 1}).encode()
            return read("porangaba_2026-09-28.txt").encode()

        with mock.patch.dict(os.environ, {"INLABS_EMAIL": "", "INLABS_PASSWORD": ""}), \
                mock.patch.object(bf, "http_get", side_effect=fake_get), mock.patch.object(bf.time, "sleep"):
            return bu.main(["--days", "3", "--feed", str(feed), "--shadow-dir", str(shadow), "--today", today,
                            "--review-limit", "2"])

    def test_shadow_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            feed, shadow = tmp / "concursos.json", tmp / "shadow"
            feed.write_text(json.dumps(OPENINGS))
            self.assertEqual(self.run_main(shadow, feed), 0)
            updates = json.loads((shadow / "atualizacoes.json").read_text())
            self.assertEqual(sorted(u["examId"] for u in updates), sorted(o["id"] for o in OPENINGS[:3]))
            index = json.loads((shadow / "aberturas.json").read_text())
            self.assertEqual(vu.updates_errors(updates, {e["id"] for e in index}, dt.date(2026, 10, 9)), [])

            with (shadow / "revisao.csv").open() as f:
                review = list(csv.DictReader(f))
            self.assertEqual(len(review), 2)  # --review-limit 2
            self.assertEqual({r["correto"] for r in review} | {r["observacao"] for r in review}, {""})
            self.assertTrue(review[0]["concurso"].startswith("Município de Porangaba - Concurso Público nº"))
            self.assertTrue(review[0]["diario"].startswith("https://data.queridodiario.ok.org.br/3540507/"))

            # Segunda execução no dia seguinte: nada novo, nada repetido na revisão.
            self.assertEqual(self.run_main(shadow, feed, "2026-10-10"), 0)
            self.assertEqual(len(json.loads((shadow / "atualizacoes.json").read_text())), 3)
            with (shadow / "revisao.csv").open() as f:
                self.assertEqual(len(list(csv.DictReader(f))), 2)
            with (shadow / "log.csv").open() as f:
                log = list(csv.DictReader(f))
            self.assertEqual([r["data"] for r in log], ["2026-10-09", "2026-10-10"])
            self.assertEqual((log[0]["atos_homologacao"], log[0]["ligados_homologacao"], log[0]["nao_ligados"]), ("3", "3", "0"))
            self.assertEqual((log[0]["novos_ligados"], log[1]["novos_ligados"]), ("3", "0"))
            self.assertEqual(log[0]["dou"], "não")
            # O feed publicado só é lido.
            self.assertEqual(json.loads(feed.read_text()), OPENINGS)

    def test_search_uses_its_own_query_and_restores_build_feed(self):
        urls = []

        def fake_get(url, **_):
            urls.append(url)
            return json.dumps({"gazettes": []}).encode()

        with mock.patch.object(bf, "http_get", side_effect=fake_get):
            bu.search(bu.QUERY_UPDATES, dt.date(2026, 10, 1))
        self.assertIn("homologa", urls[0])
        self.assertNotIn("abertas+as+inscri", urls[0])
        self.assertTrue(bf.QUERY.startswith('("concurso público" | "processo seletivo") + ("abertas'))
        self.assertEqual(bf.MAX_PAGES, 20)

    def test_updates_window_is_90_days(self):
        recent = {"id": "upd-000000000001", "published": "2026-10-01"}
        old = {"id": "upd-000000000002", "published": "2026-06-01"}
        self.assertEqual(bu.merge_updates([old], [recent], dt.date(2026, 10, 9)), [recent])


class ValidateUpdatesTests(unittest.TestCase):
    TODAY = dt.date(2026, 10, 9)

    def update(self, **changes):
        base = {
            "id": "upd-7c1e2a9b3f04", "examId": "3f1c9a0b2d4e", "type": "homologacao",
            "title": "Concurso homologado", "organization": "Prefeitura Municipal de Porangaba",
            "edital": "Concurso Público nº 01/2026", "state": "SP", "city": "Porangaba", "published": "2026-09-28",
            "gazetteUrl": "https://data.queridodiario.ok.org.br/3540507/2026-09-28/x.pdf",
            "excerpt": "EDITAL DE HOMOLOGAÇÃO", "source": "querido_diario",
        }
        base.update(changes)
        return base

    def test_valid(self):
        self.assertEqual(vu.updates_errors([self.update()], {"3f1c9a0b2d4e"}, self.TODAY), [])

    def test_contract_violations(self):
        self.assertTrue(vu.update_errors(self.update(examId=None), self.TODAY))
        self.assertTrue(vu.update_errors(self.update(type="nomeacao"), self.TODAY))
        self.assertTrue(vu.update_errors(self.update(published="28/09/2026"), self.TODAY))
        self.assertTrue(vu.update_errors(self.update(published="2026-05-01"), self.TODAY))
        self.assertTrue(vu.update_errors(self.update(gazetteUrl="diario.pdf"), self.TODAY))
        self.assertTrue(vu.update_errors(self.update(id="7c1e2a9b3f04"), self.TODAY))
        self.assertTrue(vu.update_errors(self.update(state="XX"), self.TODAY))
        self.assertTrue(vu.update_errors(self.update(city=3), self.TODAY))
        self.assertIn("'id' repetido", " ".join(vu.updates_errors([self.update(), self.update()], None, self.TODAY)))
        self.assertIn("não está no índice", " ".join(vu.updates_errors([self.update()], {"outro"}, self.TODAY)))
        self.assertTrue(vu.updates_errors({"id": "x"}))


if __name__ == "__main__":
    unittest.main()
