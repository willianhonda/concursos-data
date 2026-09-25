import datetime as dt
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import build_feed as bf  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"
GAZETTE = {
    "territory_id": "3529005",
    "territory_name": "Marília",
    "state_code": "SP",
    "date": "2026-09-24",
    "url": "https://data.queridodiario.ok.org.br/3529005/2026-09-24/x.pdf",
    "txt_url": "https://data.queridodiario.ok.org.br/3529005/2026-09-24/x.txt",
}


class ExtractOpeningsTests(unittest.TestCase):
    def test_real_gazette_excerpt(self):
        text = (FIXTURES / "marilia_2026-09-24.txt").read_text()
        items = bf.extract_openings(text, GAZETTE)
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item["title"], "Fundação Municipal de Ensino Superior de Marília - Concurso Público nº 06/2026")
        self.assertEqual(item["deadline"], "Inscrições: 01/10/2026 a 20/10/2026")
        self.assertEqual(item["registrationEnds"], "2026-10-20")
        self.assertEqual(item["state"], "SP")
        self.assertEqual(item["url"], "https://www.fumes.sp.gov.br")

    def test_convocation_is_ignored(self):
        text = (
            "EDITAL DE CONVOCAÇÃO DO CONCURSO PÚBLICO 026/2024. Considerando o que consta do Edital de Abertura "
            "do Concurso Público nº 026/2024, CONVOCA os candidatos abaixo relacionados."
        )
        self.assertEqual(bf.extract_openings(text, GAZETTE), [])

    def test_temporary_selection_is_ignored(self):
        text = (
            "O Município, no uso de suas atribuições, TORNA PÚBLICO que estarão abertas as inscrições do "
            "Processo Seletivo Simplificado para contratação temporária."
        )
        self.assertEqual(bf.extract_openings(text, GAZETTE), [])

    def test_full_edital_with_textual_dates_salary_and_vacancies(self):
        text = (
            "A PREFEITURA MUNICIPAL DE EXEMPLO, Estado de São Paulo, torna pública a abertura do Concurso Público "
            "nº 02/2026 para o total de 12 vagas. 3. DAS INSCRIÇÕES 3.1 As inscrições serão realizadas "
            "no período de 5 de outubro de 2026 a 4 de novembro de 2026, pela internet. "
            "Cargo: Agente Administrativo - Vencimento: R$ 2.500,00 - Taxa de inscrição: R$ 60,00. "
            "Cargo: Engenheiro - Vencimento: R$ 9.100,50."
        )
        [item] = bf.extract_openings(text, GAZETTE)
        self.assertEqual(item["title"], "Prefeitura Municipal de Exemplo - Concurso Público nº 02/2026")
        self.assertEqual(item["deadline"], "Inscrições: 05/10/2026 a 04/11/2026")
        self.assertEqual(item["salary"], "Faixa salarial: R$ 2.500,00 - R$ 9.100,50")
        self.assertEqual(item["vacancies"], "Vagas 12")


class MergeTests(unittest.TestCase):
    def item(self, id_, published, ends=None, **extra):
        return {"id": id_, "published": published, "registrationEnds": ends, "deadline": "", "salary": "", **extra}

    def test_drops_closed_and_stale_items(self):
        today = dt.date(2026, 9, 25)
        feed = bf.merge(
            [],
            [
                self.item("open", "2026-09-20", "2026-10-10"),
                self.item("closed", "2026-09-01", "2026-09-20"),
                self.item("undated-recent", "2026-09-10"),
                self.item("undated-old", "2026-07-01"),
            ],
            today,
        )
        self.assertEqual({i["id"] for i in feed}, {"open", "undated-recent"})

    def test_new_run_fills_missing_fields_without_losing_first_publication(self):
        today = dt.date(2026, 9, 25)
        old = [self.item("a", "2026-09-20")]
        new = [self.item("a", "2026-09-24", "2026-10-30", deadline="Inscrições: 01/10/2026 a 30/10/2026", salary="Salário: R$ 3.000,00")]
        [merged] = bf.merge(old, new, today)
        self.assertEqual(merged["published"], "2026-09-20")
        self.assertEqual(merged["registrationEnds"], "2026-10-30")
        self.assertEqual(merged["salary"], "Salário: R$ 3.000,00")


if __name__ == "__main__":
    unittest.main()
