import datetime as dt
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import build_feed as bf  # noqa: E402
import dosp  # noqa: E402
import validate_feed as vf  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures" / "dosp"


def fixture(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


CARD = {"url": "https://diariooficial.prefeitura.sp.gov.br/md_epubli_visualizar.php?abc,,", "number": "149615996",
        "type": "Edital de Concurso Público", "date": "2026-01-20", "organ": "Secretaria Municipal de Segurança Urbana"}


class ParseTests(unittest.TestCase):
    def test_search_page(self):
        result = dosp.parse_search(fixture("pesquisa.html"))
        self.assertEqual(result["total"], 48)
        self.assertTrue(result["cards"])
        for card in result["cards"]:
            self.assertTrue(card["url"].startswith("https://diariooficial.prefeitura.sp.gov.br/md_epubli_visualizar.php?"))
            self.assertRegex(card["number"], r"^\d+$")
            self.assertRegex(card["date"], r"^\d{4}-\d{2}-\d{2}$")
        self.assertIn("Edital de Concurso Público", [c["type"] for c in result["cards"]])

    def test_only_edital_types_can_be_openings(self):
        for kind in ("Edital", "Edital de Concurso Público"):
            self.assertTrue(dosp.is_opening_type({"type": kind}))
        for kind in ("Edital de Convocação para Exame Médico de Ingresso", "Comunicado", "Despacho deferido",
                     "Movimentação de Pessoal", "Convocação"):
            self.assertFalse(dosp.is_opening_type({"type": kind}))

    def test_search_form_filters_the_concursos_section_by_publication_date(self):
        form = dosp.search_form(dt.date(2026, 10, 1), dt.date(2026, 10, 31), 20)
        self.assertEqual(form["hdnVeiculoPublicacao"], "5")
        self.assertEqual(form["hdnDataInicioPesquisa"], "01/10/2026")
        self.assertEqual(form["hdnDataFimPesquisa"], "31/10/2026")
        self.assertEqual(form["hdnInicio"], "20")
        self.assertEqual(form["hdnTermoPesquisa"], "")


class ItemTests(unittest.TestCase):
    def items(self):
        return dosp.items_from_page(fixture("materia_gcm.html"), CARD, bf.extract_openings, bf.html_to_text)

    def test_opening_uses_the_prefeitura_and_the_registration_dates(self):
        (item,) = self.items()
        self.assertEqual(item["title"], "Prefeitura de São Paulo - Concurso Público")
        self.assertEqual(item["state"], "SP")
        self.assertEqual(item["city"], "São Paulo")
        self.assertEqual(item["registrationStarts"], "2026-01-29")
        self.assertEqual(item["registrationEnds"], "2026-03-03")
        self.assertEqual((item["source"], item["collector"], item["sphere"]), ("diario_municipal", "dosp", "municipal"))
        self.assertEqual(item["gazetteUrl"], CARD["url"])
        self.assertIn("Diário Oficial da Cidade de São Paulo", item["description"])

    def test_item_is_valid_for_the_app(self):
        (item,) = self.items()
        self.assertEqual(vf.item_errors(item), [])

    def test_id_comes_from_the_document_number(self):
        (item,) = self.items()
        again = dosp.items_from_page(fixture("materia_gcm.html"), dict(CARD, url="https://x/other,,"),
                                     bf.extract_openings, bf.html_to_text)[0]
        self.assertEqual(item["id"], again["id"])
        other = dosp.items_from_page(fixture("materia_gcm.html"), dict(CARD, number="1"),
                                     bf.extract_openings, bf.html_to_text)[0]
        self.assertNotEqual(item["id"], other["id"])

    def test_page_without_an_opening_gives_nothing(self):
        page = "<html><body><p>EDITAL DE CONVOCAÇÃO PARA EXAME MÉDICO DE INGRESSO. Ficam convocados os candidatos.</p></body></html>"
        self.assertEqual(dosp.items_from_page(page, CARD, bf.extract_openings, bf.html_to_text), [])


class CollectTests(unittest.TestCase):
    def collect(self, off=None, fail=False):
        sent, fetched = [], []

        def send(url, fields):
            sent.append(fields["hdnInicio"])
            return fixture("pesquisa.html").encode("latin-1")

        def fetch(url):
            fetched.append(url)
            if fail:
                raise OSError("fora do ar")
            return fixture("materia_gcm.html").encode()

        items = dosp.collect(dt.date(2026, 1, 10), dt.date(2026, 1, 31), fetch=fetch, extract=bf.extract_openings,
                             to_text=bf.html_to_text, off=off, send=send, sleep=lambda s: None)
        return items, sent, fetched

    def test_reads_only_the_edital_documents(self):
        items, sent, fetched = self.collect()
        self.assertEqual(sent, ["0", "10", "20", "30", "40"])
        self.assertEqual(len(fetched), 1)
        self.assertEqual(len(items), 1)

    def test_kill_switch(self):
        items, sent, fetched = self.collect(off={"dosp"})
        self.assertEqual((items, sent, fetched), ([], [], []))

    def test_a_page_that_fails_does_not_stop_the_run(self):
        items, _, fetched = self.collect(fail=True)
        self.assertEqual(items, [])
        self.assertTrue(fetched)


if __name__ == "__main__":
    unittest.main()
