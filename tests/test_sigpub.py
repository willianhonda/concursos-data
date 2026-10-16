import datetime as dt
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import build_feed as bf  # noqa: E402
import sigpub  # noqa: E402
import validate_feed as vf  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures" / "sigpub"


def fixture(name):
    return (FIXTURES / name).read_text()


def items_from(name, code="ABC12345", slug="amp", uf="PR", entity=""):
    card = {"code": code, "entity": entity, "date": "2026-10-09"}
    return sigpub.items_from_page(fixture(name), card, slug, uf, bf.extract_openings, bf.html_to_text)


class ParseTests(unittest.TestCase):
    def test_search_page(self):
        result = sigpub.parse_search(fixture("amp_pesquisa.html"))
        self.assertEqual(result["total"], 128)
        first = result["cards"][0]
        self.assertEqual(first["code"], "3F3CAFDA")
        self.assertEqual(first["slug"], "amp")
        self.assertEqual(first["entity"], "Prefeitura Municipal de Jaguapitã")
        self.assertEqual(first["date"], "2026-10-16")
        self.assertIn("CONVOCAÇÃO", first["title"])

    def test_search_url_uses_the_title_search_and_the_period(self):
        url = sigpub.search_url("famurs", "concurso", dt.date(2026, 10, 1), dt.date(2026, 10, 16), 2)
        self.assertTrue(url.startswith("https://www.diariomunicipal.com.br/famurs/pesquisar?"))
        for part in ("titulo%5D=concurso", "dataInicio%5D=2026-10-01", "dataFim%5D=2026-10-16", "pagina%5D=2"):
            self.assertIn(part, url)

    def test_city_from_entity(self):
        self.assertEqual(sigpub.city_from_entity("PREFEITURA MUNICIPAL DE QUATRO BARRAS"), "Quatro Barras")
        self.assertEqual(sigpub.city_from_entity("Prefeitura Municipal de Boa Ventura de São Roque"), "Boa Ventura de São Roque")
        self.assertEqual(sigpub.city_from_entity("MUNICÍPIO DE PROFESSOR JAMIL"), "Professor Jamil")
        self.assertEqual(sigpub.city_from_entity("Câmara Municipal de Itajubá"), "Itajubá")
        self.assertIsNone(sigpub.city_from_entity("CONSAMU - Consórcio de Saúde"))
        self.assertIsNone(sigpub.city_from_entity(""))

    def test_title_filter(self):
        for title in ("EDITAL DE CONVOCAÇÃO Nº 049/2026 CONCURSO PUBLICO", "EDITAL Nº 013/2026 - RESULTADO FINAL E CLASSIFICAÇÃO",
                      "COMUNICADO – CHAMADA ENGENHEIRO AMBIENTAL CONCURSO PÚBLICO Nº. 01/2023", "HOMOLOGAÇÃO DO CONCURSO"):
            self.assertTrue(sigpub.SKIP_TITLE.search(title), title)
        for title in ("CONCURSO PÚBLICO Nº. 002/2026", "EXTRATO DE PUBLICAÇÃO DE EDITAL DE CONCURSO PÚBLICO",
                      "ABERTURA DE CONCURSO PÚBLICO 001/2026"):
            self.assertFalse(sigpub.SKIP_TITLE.search(title), title)


class ItemsFromRealPagesTests(unittest.TestCase):
    def test_opening_from_a_chamber(self):
        [item] = items_from("amp_quatro_barras_abertura.html", "E162CA98")
        self.assertEqual(item["title"], "Câmara Municipal de Quatro Barras - Concurso Público")
        self.assertEqual(item["registrationStarts"], "2026-10-09")
        self.assertEqual(item["registrationEnds"], "2026-11-03")
        self.assertEqual((item["state"], item["city"], item["kind"]), ("PR", "Quatro Barras", "concurso"))
        self.assertEqual(item["url"], "https://www.consulpam.com.br")

    def test_new_item_fields(self):
        [item] = items_from("amp_quatro_barras_abertura.html", "E162CA98")
        self.assertEqual(item["source"], "diario_municipal")
        self.assertEqual(item["collector"], "sigpub:amp")
        self.assertEqual(item["sphere"], "municipal")
        self.assertEqual(item["gazetteName"], "Diário Oficial dos Municípios do Paraná")
        self.assertEqual(item["gazetteUrl"], "https://www.diariomunicipal.com.br/amp/materia/E162CA98")
        self.assertIn("Fonte: Diário Oficial dos Municípios do Paraná, 09/10/2026.", item["description"])
        self.assertEqual(vf.item_errors(item), [])

    def test_id_comes_from_the_materia_code_and_is_stable(self):
        [a] = items_from("amp_quatro_barras_abertura.html", "E162CA98")
        [b] = items_from("amp_quatro_barras_abertura.html", "E162CA98")
        [c] = items_from("amp_quatro_barras_abertura.html", "FFFFFFFF")
        self.assertEqual(a["id"], b["id"])
        self.assertNotEqual(a["id"], c["id"])
        self.assertEqual(len(a["id"]), 12)

    def test_period_written_before_the_keyword(self):
        [item] = items_from("agm_professor_jamil_extrato.html", slug="agm", uf="GO")
        self.assertEqual(item["title"], "Prefeitura Municipal de Professor Jamil - Concurso Público nº 01/2026")
        self.assertEqual((item["registrationStarts"], item["registrationEnds"]), ("2026-11-09", "2026-11-30"))
        self.assertEqual(item["collector"], "sigpub:agm")

    def test_extract_with_the_period_only(self):
        [item] = items_from("famurs_tio_hugo_extrato.html", slug="famurs", uf="RS")
        self.assertEqual(item["city"], "Tio Hugo")
        self.assertEqual(item["registrationEnds"], "2026-11-16")
        self.assertEqual(item["linkKind"], "banca")

    def test_website_in_the_header_does_not_leak_into_the_title(self):
        [item] = items_from("amm-mg_lagoa_santa_abertura.html", slug="amm-mg", uf="MG")
        self.assertEqual(item["title"], "Prefeitura Municipal de Lagoa Santa - Concurso Público nº 001/2026")
        self.assertEqual(item["gazetteName"], "Diário Oficial dos Municípios Mineiros")

    def test_convocations_and_results_are_not_openings(self):
        for name in ("amp_jaguapita_convocacao.html", "amp_campina_grande_do_sul.html", "amp_vitorino.html",
                     "amp_guaira.html", "amp_bom_sucesso_do_sul.html", "amp_goioere_resultado.html"):
            self.assertEqual(items_from(name), [], name)

    def test_page_without_a_municipality_is_ignored(self):
        page = fixture("amp_quatro_barras_abertura.html").replace("PREFEITURA MUNICIPAL DE QUATRO BARRAS", "CONSAMU")
        card = {"code": "X", "entity": "CONSAMU", "date": "2026-10-09"}
        self.assertEqual(sigpub.items_from_page(page, card, "amp", "PR", bf.extract_openings, bf.html_to_text), [])

    def test_page_without_body(self):
        card = {"code": "X", "entity": "Prefeitura Municipal de Exemplo", "date": "2026-10-09"}
        self.assertEqual(sigpub.items_from_page("<html></html>", card, "amp", "PR", bf.extract_openings, bf.html_to_text), [])


class CollectTests(unittest.TestCase):
    SINCE, TODAY = dt.date(2026, 10, 9), dt.date(2026, 10, 16)

    def make_fetch(self, calls):
        search = fixture("amp_pesquisa.html")
        # Só a Quatro Barras de 15/10 vira abertura; o resto da lista é convocação ou resultado.
        pages = {"E162CA98": fixture("amp_quatro_barras_abertura.html"), "133E6D93": fixture("amp_quatro_barras_abertura.html")}

        def fetch(url):
            calls.append(url)
            if "/pesquisar?" in url:
                empty = "<p>Mostrando 0 de <strong>0</strong> matérias.</p>"
                return (search if "famurs" not in url and "titulo%5D=concurso" in url and "pagina%5D=1" in url else empty).encode()
            code = url.rsplit("/", 1)[1]
            return pages.get(code, fixture("amp_jaguapita_convocacao.html")).encode()
        return fetch

    def collect(self, calls, **kwargs):
        return sigpub.collect(self.SINCE, self.TODAY, fetch=self.make_fetch(calls), extract=bf.extract_openings,
                              to_text=bf.html_to_text, sleep=lambda _: None, slugs={"amp": "PR", "famurs": "RS"}, **kwargs)

    def test_collects_openings_and_skips_acts_by_title(self):
        calls = []
        items = self.collect(calls)
        self.assertEqual([i["collector"] for i in items], ["sigpub:amp"])
        self.assertEqual(items[0]["city"], "Quatro Barras")
        # Convocações e resultados da listagem não foram baixados.
        fetched = [u.rsplit("/", 1)[1] for u in calls if "/materia/" in u]
        self.assertNotIn("3F3CAFDA", fetched)
        self.assertIn("133E6D93", fetched)

    def test_kill_switch_for_a_single_association(self):
        calls = []
        self.collect(calls, off={"sigpub:amp"})
        self.assertFalse([u for u in calls if "/amp/" in u])
        self.assertTrue([u for u in calls if "/famurs/" in u])

    def test_failure_in_one_association_does_not_stop_the_others(self):
        calls = []
        inner = self.make_fetch(calls)

        def flaky(url):
            if "/amp/pesquisar" in url:
                raise OSError("fora do ar")
            return inner(url)

        logs = []
        items = sigpub.collect(self.SINCE, self.TODAY, fetch=flaky, extract=bf.extract_openings, to_text=bf.html_to_text,
                               sleep=lambda _: None, slugs={"amp": "PR", "famurs": "RS"}, log=logs.append)
        self.assertEqual(items, [])
        self.assertTrue(any("sigpub:amp: busca falhou" in m for m in logs))
        self.assertTrue([u for u in calls if "/famurs/" in u])

    def test_pagination_stops_at_the_total(self):
        calls = []
        self.collect(calls)
        # 128 matérias em páginas de 25: a página 1 devolve o total, então o coletor pede a 2 (e as seguintes) até o fim.
        amp_pages = [u for u in calls if "/amp/pesquisar" in u and "titulo%5D=concurso" in u]
        self.assertGreaterEqual(len(amp_pages), 2)
        self.assertLessEqual(len(amp_pages), sigpub.MAX_PAGES)


if __name__ == "__main__":
    unittest.main()
