"""Concursos previstos (spike CA-007).

As fixtures em tests/fixtures/previstos/ seguem o formato do INLABS. mgi_*.xml e cra_am.xml
são o texto real dos atos citados no spike (baixados de in.gov.br); mgi_anpd_republicada.xml e
fp_*.xml são sintéticos (republicação "(*)" e falsos positivos).
"""

import datetime as dt
import json
import sys
import tempfile
import unittest
import unittest.mock
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import build_previstos as bp  # noqa: E402
import validate_previstos as vp  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures" / "previstos"
TODAY = dt.date(2026, 10, 16)


def extract(name):
    return bp.extract_articles(ET.parse(FIXTURES / f"{name}.xml").getroot())


def one(name):
    found = extract(name)
    assert len(found) == 1, f"{name}: {found}"
    return found[0]


def forecast(**changes):
    base = {
        "id": "0123456789ab",
        "organization": "Banco Central do Brasil",
        "authorizedOn": "2026-07-03",
        "url": "https://www.in.gov.br/web/dou/-/x-1",
        "editalDeadline": "2027-01-03",
        "sphere": "federal",
        "source": "dou",
    }
    base.update(changes)
    return {k: v for k, v in base.items() if v is not ...}


def exam(title, published="2026-10-01"):
    return {"id": "e1", "title": title, "published": published}


class ExtractionTests(unittest.TestCase):
    def test_mgi_cgu(self):
        item = one("mgi_cgu")
        self.assertEqual(item["organization"], "Controladoria-Geral da União")
        self.assertEqual(item["authorizedOn"], "2026-06-24")
        self.assertEqual(item["vacancies"], 60)
        self.assertEqual(item["roles"], ["Auditor Federal de Finanças e Controle"])
        self.assertEqual(item["education"], ["superior"])
        self.assertEqual(item["editalDeadline"], "2026-12-24")
        self.assertEqual(item["url"], "https://www.in.gov.br/web/dou/-/portaria-mgi-n-5.093-de-22-de-junho-de-2026-714435116")
        self.assertEqual((item["sphere"], item["source"]), ("federal", "dou"))

    def test_mgi_anpd(self):
        item = one("mgi_anpd")
        self.assertEqual(item["organization"], "Agência Nacional de Proteção de Dados")
        self.assertEqual((item["vacancies"], item["editalDeadline"]), (50, "2026-12-24"))

    def test_mgi_bcb_has_three_roles_and_two_levels(self):
        item = one("mgi_bcb")
        self.assertEqual(item["organization"], "Banco Central do Brasil")
        self.assertEqual(item["vacancies"], 170)
        self.assertEqual(len(item["roles"]), 3)
        self.assertEqual(item["education"], ["medio", "superior"])
        self.assertEqual(item["editalDeadline"], "2027-01-03")
        self.assertTrue(item["url"].endswith("716755304"))

    def test_mgi_rfb_url_keeps_the_slash_of_the_title(self):
        item = one("mgi_rfb")
        self.assertEqual(item["organization"], "Secretaria Especial da Receita Federal do Brasil")
        self.assertEqual(item["vacancies"], 146)
        self.assertEqual(item["url"], "https://www.in.gov.br/web/dou/-/portaria/mgi-n-5.505-de-3-de-julho-de-2026-716754425")

    def test_cra_am_other_format(self):
        item = one("cra_am")
        self.assertEqual(item["organization"], "Conselho Regional de Administração do Amazonas - CRA-AM")
        self.assertEqual(item["authorizedOn"], "2026-05-27")
        self.assertEqual(item["vacancies"], 10)  # só as vagas efetivas; o cadastro de reserva não conta
        self.assertEqual(item["roles"], ["Assistente Administrativo", "Administrador", "Administrador Fiscal", "Analista de Recursos Humanos"])
        self.assertEqual(item["education"], ["medio", "superior"])
        self.assertNotIn("editalDeadline", item)  # o ato não fixa prazo

    def test_strict_rule_rejects_false_positives(self):
        for name in ("fp_nomeacao", "fp_homologacao", "fp_retificacao"):
            self.assertEqual(extract(name), [], name)

    def test_strict_rule_variants(self):
        for ementa, expected in [
            ("Autoriza a realização de concurso público para X do quadro de pessoal da Agência Y.", True),
            ("AUTORIZA A REALIZAÇÃO DE CONCURSOS PÚBLICOS para cargos do quadro de pessoal do Órgão Z.", True),
            ("(*) Autoriza a realização de concurso público para cargos do quadro de pessoal do Órgão Z.", True),
            ("Autoriza a nomeação de candidatos aprovados em concurso público.", False),
            ("Autoriza o provimento adicional de cargos, em razão de concurso público.", False),
            ("Dispõe sobre o concurso público que autoriza a realização de concurso público.", False),
            ("Retifica a portaria que autoriza a realização de concurso público.", False),
        ]:
            xml = (f'<article idMateria="1" artType="Portaria" pubDate="01/07/2026" artCategory="Ministério/Gabinete">'
                   f"<body><Identifica>PORTARIA Nº 1</Identifica><Ementa>{ementa}</Ementa><Texto>Art. 1º Texto.</Texto></body></article>")
            found = bp.extract_articles(ET.fromstring(f"<xml>{xml}</xml>"))
            self.assertEqual(bool(found), expected, ementa)

    def test_mgi_act_without_organization_in_the_ementa_is_discarded(self):
        xml = ('<article idMateria="1" artType="Portaria" pubDate="01/07/2026" '
               'artCategory="Ministério da Gestão e da Inovação em Serviços Públicos/Gabinete da Ministra">'
               "<body><Identifica>PORTARIA MGI Nº 1</Identifica><Ementa>Autoriza a realização de concurso público.</Ementa>"
               "<Texto>Art. 1º Texto.</Texto></body></article>")
        self.assertEqual(bp.extract_articles(ET.fromstring(f"<xml>{xml}</xml>")), [])

    def test_inconsistent_vacancy_totals_are_not_stated(self):
        article = ET.parse(FIXTURES / "mgi_cgu.xml").getroot().find("article")
        text = article.find("body/Texto")
        text.text = text.text.replace("sessenta cargos", "cinquenta cargos")
        item = bp.extract_forecast(article)
        self.assertNotIn("vacancies", item)

    def test_republication_counts_once(self):
        items = bp.dedupe([*extract("mgi_anpd_republicada"), *extract("mgi_anpd")])
        self.assertEqual(len(items), 1)
        self.assertTrue(items[0]["url"].endswith("714430536"))  # fica a publicação original
        self.assertEqual(len(bp.dedupe([*extract("mgi_anpd"), *extract("mgi_anpd_republicada")])), 1)

    def test_id_is_the_short_sha1_of_the_url(self):
        item = one("mgi_bcb")
        self.assertEqual(item["id"], vp.forecast_id(item["url"]))
        self.assertRegex(item["id"], r"^[0-9a-f]{12}$")


class HelperTests(unittest.TestCase):
    def test_words_to_int(self):
        self.assertEqual(bp.words_to_int("cento e setenta"), 170)
        self.assertEqual(bp.words_to_int("cento e quarenta e seis"), 146)
        self.assertEqual(bp.words_to_int("sessenta"), 60)
        self.assertEqual(bp.words_to_int("mil e duzentos"), 1200)
        self.assertEqual(bp.words_to_int("12"), 12)
        self.assertIsNone(bp.words_to_int("alguns"))

    def test_add_months_clamps_the_day(self):
        self.assertEqual(bp.add_months(dt.date(2026, 8, 31), 6), dt.date(2027, 2, 28))
        self.assertEqual(bp.add_months(dt.date(2026, 7, 3), 6), dt.date(2027, 1, 3))

    def test_edital_deadline_in_days(self):
        text = "O prazo para a publicação do edital de abertura será de até noventa dias, contados da publicação."
        self.assertEqual(bp.find_edital_deadline(text, dt.date(2026, 1, 1)), dt.date(2026, 4, 1))


class LifecycleTests(unittest.TestCase):
    def test_stays_while_deadline_is_open(self):
        kept = bp.update_forecasts([forecast()], [], [], TODAY)
        self.assertEqual(len(kept), 1)

    def test_leaves_30_days_after_the_deadline(self):
        old = forecast(editalDeadline="2026-09-16")
        self.assertEqual(len(bp.update_forecasts([old], [], [], dt.date(2026, 10, 16))), 1)  # exatamente 30 dias
        self.assertEqual(len(bp.update_forecasts([old], [], [], dt.date(2026, 10, 17))), 0)  # 31 dias

    def test_without_deadline_leaves_after_a_year(self):
        old = forecast(editalDeadline=..., authorizedOn="2025-10-01")
        self.assertEqual(bp.update_forecasts([old], [], [], TODAY), [])
        self.assertEqual(len(bp.update_forecasts([forecast(editalDeadline=..., authorizedOn="2026-05-27")], [], [], TODAY)), 1)

    def test_leaves_when_the_edital_shows_up_in_the_feed(self):
        feed = [exam("Banco Central do Brasil - Concurso Público (Edital nº 1/2026)")]
        self.assertEqual(bp.update_forecasts([forecast()], [], feed, TODAY), [])

    def test_edital_match_ignores_case_accent_and_spacing(self):
        feed = [exam("BANCO  CENTRAL do  Brasil - Concurso Publico")]
        self.assertEqual(bp.update_forecasts([forecast()], [], feed, TODAY), [])
        feed = [exam("Agência Nacional de Proteção de Dados - Concurso Público")]
        self.assertEqual(bp.update_forecasts([forecast(organization="AGENCIA NACIONAL DE PROTECAO DE DADOS")], [], feed, TODAY), [])

    def test_edital_match_ignores_the_acronym_suffix(self):
        feed = [exam("Controladoria-Geral da União - Concurso Público (Edital nº 1/2026)")]
        old = forecast(organization="Controladoria-Geral da União - CGU")
        self.assertEqual(bp.update_forecasts([old], [], feed, TODAY), [])

    def test_other_org_or_older_edital_does_not_remove(self):
        self.assertEqual(len(bp.update_forecasts([forecast()], [], [exam("Banco do Brasil - Concurso Público")], TODAY)), 1)
        older = [exam("Banco Central do Brasil - Concurso Público", published="2026-06-30")]  # antes da autorização
        self.assertEqual(len(bp.update_forecasts([forecast()], [], older, TODAY)), 1)

    def test_new_acts_are_merged_and_sorted_by_date_descending(self):
        new = [*extract("cra_am"), *extract("mgi_bcb"), *extract("mgi_cgu")]
        result = bp.update_forecasts([], new, [], TODAY)
        self.assertEqual([i["organization"][:6] for i in result], ["Banco ", "Contro", "Consel"])
        self.assertEqual(vp.forecasts_errors(result, TODAY), [])
        self.assertTrue(all(not k.startswith("_") for i in result for k in i))

    def test_known_act_is_not_duplicated_on_the_next_run(self):
        first = bp.update_forecasts([], extract("mgi_bcb"), [], TODAY)
        again = bp.update_forecasts(first, extract("mgi_bcb"), [], TODAY)
        self.assertEqual(first, again)

    def test_empty_stays_empty(self):
        self.assertEqual(bp.update_forecasts([], [], [], TODAY), [])


class MainTests(unittest.TestCase):
    def run_main(self, tmp, *extra):
        argv = ["build_previstos.py", "--output", str(tmp / "previstos.json"), "--feed", str(tmp / "concursos.json"), *extra]
        with unittest.mock.patch.object(sys, "argv", argv), unittest.mock.patch.object(bp, "log"):
            return bp.main()

    def test_end_to_end_from_xml_and_never_touches_the_feed(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            feed = tmp / "concursos.json"
            feed.write_text("[]\n")
            xmls = [str(FIXTURES / f"{n}.xml") for n in ("mgi_cgu", "mgi_anpd", "mgi_anpd_republicada", "fp_nomeacao")]
            with unittest.mock.patch.object(bp.dt, "date", wraps=dt.date) as fake:
                fake.today.return_value = TODAY
                self.assertEqual(self.run_main(tmp, "--xml", *xmls), 0)
            result = json.loads((tmp / "previstos.json").read_text())
            self.assertEqual(sorted(i["organization"] for i in result), ["Agência Nacional de Proteção de Dados", "Controladoria-Geral da União"])
            self.assertEqual(feed.read_text(), "[]\n")

    def test_without_credentials_keeps_the_file_and_empty_is_valid(self):
        with tempfile.TemporaryDirectory() as d, unittest.mock.patch.dict("os.environ", {}, clear=True):
            tmp = Path(d)
            self.assertEqual(self.run_main(tmp), 0)
            self.assertEqual(json.loads((tmp / "previstos.json").read_text()), [])


class ValidatorTests(unittest.TestCase):
    def test_empty_array_is_valid(self):
        self.assertEqual(vp.forecasts_errors([], TODAY), [])

    def test_valid_item_with_all_fields(self):
        item = forecast(vacancies=170, roles=["Auditor"], education=["medio", "superior"], act="PORTARIA MGI Nº 1")
        self.assertEqual(vp.forecast_errors(item, TODAY), [])

    def test_only_the_four_required_fields(self):
        item = {k: forecast()[k] for k in ("id", "organization", "authorizedOn", "url")}
        self.assertEqual(vp.forecast_errors(item, TODAY), [])

    def test_problems(self):
        cases = {
            "id": forecast(id="abc"),
            "organization": forecast(organization=""),
            "url": forecast(url="ftp://x"),
            "authorizedOn": forecast(authorizedOn="03/07/2026"),
            "futuro": forecast(authorizedOn="2026-12-01"),
            "deadline": forecast(editalDeadline="2026-01-01"),
            "vacancies": forecast(vacancies="170"),
            "vacancies bool": forecast(vacancies=True),
            "vacancies zero": forecast(vacancies=0),
            "roles": forecast(roles="Auditor"),
            "education": forecast(education=["doutorado"]),
            "sphere": forecast(sphere="lunar"),
            "source": forecast(source="querido_diario"),
        }
        for label, item in cases.items():
            self.assertTrue(vp.forecast_errors(item, TODAY), label)
        self.assertTrue(vp.forecast_errors("texto"))

    def test_unknown_keys_are_allowed(self):
        self.assertEqual(vp.forecast_errors(forecast(novo="x"), TODAY), [])

    def test_file_level_problems(self):
        self.assertTrue(vp.forecasts_errors({}, TODAY))
        a, b = forecast(id="aaaaaaaaaaaa", authorizedOn="2026-07-03"), forecast(id="bbbbbbbbbbbb", authorizedOn="2026-06-24")
        self.assertEqual(vp.forecasts_errors([a, b], TODAY), [])
        self.assertTrue(any("ordenado" in e for e in vp.forecasts_errors([b, a], TODAY)))
        self.assertTrue(any("repetido" in e for e in vp.forecasts_errors([a, a], TODAY)))

    def test_published_file_if_present_is_valid(self):
        path = Path(__file__).resolve().parents[1] / "docs" / "previstos.json"
        if path.exists():
            self.assertEqual(vp.forecasts_errors(json.loads(path.read_text()), TODAY), [])


if __name__ == "__main__":
    unittest.main()
