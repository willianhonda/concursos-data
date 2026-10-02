import datetime as dt
import io
import sys
import unittest
import xml.etree.ElementTree as ET
import zipfile
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


ITAPOLIS = {
    "territory_id": "3522703",
    "territory_name": "Itápolis",
    "state_code": "SP",
    "date": "2026-09-15",
    "url": "https://data.queridodiario.ok.org.br/3522703/2026-09-15/x.pdf",
    "txt_url": "https://data.queridodiario.ok.org.br/3522703/2026-09-15/x.txt",
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
        self.assertEqual(item["registrationStarts"], "2026-10-01")
        self.assertEqual(item["state"], "SP")
        self.assertEqual(item["url"], "https://www.fumes.sp.gov.br")
        self.assertEqual(item["kind"], "concurso")

    def test_banca_site_beats_gazette_header(self):
        # O cabeçalho de cada página traz o site da prefeitura; o edital é da banca (IPELL).
        text = (FIXTURES / "itapolis_2026-09-15.txt").read_text()
        [item] = bf.extract_openings(text, ITAPOLIS)
        self.assertEqual(item["title"], "Câmara Municipal de Itápolis - Concurso Público nº 01/2026")
        self.assertEqual(item["url"], "https://www.ipell.com.br")
        self.assertEqual(item["linkKind"], "banca")

    def test_convocation_is_ignored(self):
        text = (
            "EDITAL DE CONVOCAÇÃO DO CONCURSO PÚBLICO 026/2024. Considerando o que consta do Edital de Abertura "
            "do Concurso Público nº 026/2024, CONVOCA os candidatos abaixo relacionados."
        )
        self.assertEqual(bf.extract_openings(text, GAZETTE), [])

    def test_temporary_selection_is_included_as_processo_seletivo(self):
        text = (
            "O Município, no uso de suas atribuições, TORNA PÚBLICO que estarão abertas as inscrições do "
            "Processo Seletivo Simplificado nº 03/2026 para contratação temporária de Professor."
        )
        [item] = bf.extract_openings(text, GAZETTE)
        self.assertEqual(item["kind"], "processo_seletivo")
        self.assertEqual(item["title"], "Município de Marília - Processo Seletivo nº 03/2026")

    def test_internship_selection_is_ignored(self):
        text = (
            "O Município TORNA PÚBLICO que estarão abertas as inscrições do Processo Seletivo "
            "para estágio remunerado de estudantes."
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


def dou_article(art_type, identifica, texto, category="Ministério da Educação/Universidade Federal do Ceará"):
    return (
        f'<article id="1" idMateria="{abs(hash(identifica))}" pubName="DO3" artType="{art_type}" pubDate="25/09/2026" '
        f'artCategory="{category}" pdfPage="https://pesquisa.in.gov.br/imprensa/jsp/visualiza/index.jsp?data=25/09/2026&amp;jornal=530&amp;pagina=45">'
        f"<body><Identifica><![CDATA[{identifica}]]></Identifica><Texto><![CDATA[{texto}]]></Texto></body></article>"
    )


class DouTests(unittest.TestCase):
    def parse(self, xml):
        return bf.extract_dou_article(ET.fromstring(xml))

    def test_concurso_opening(self):
        item = self.parse(dou_article(
            "Edital de Concurso Público", "EDITAL Nº 48/2026",
            "<p>EDITAL Nº 48/2026</p><p>A Reitora da Universidade Federal do Ceará torna pública a abertura de "
            "inscrições para o Concurso Público para provimento de cargos de Professor do Magistério Superior.</p>"
            "<p>2. DAS INSCRIÇÕES 2.1 As inscrições serão realizadas de 01/10/2026 a 30/10/2026, no site "
            "https://concursos.ufc.br. Remuneração: R$ 10.481,64. Total de 12 vagas.</p>",
        ))
        self.assertEqual(item["title"], "Universidade Federal do Ceará - Concurso Público (Edital nº 48/2026)")
        self.assertEqual(item["state"], "CE")
        self.assertEqual(item["kind"], "concurso")
        self.assertEqual(item["source"], "dou")
        self.assertEqual(item["registrationEnds"], "2026-10-30")
        self.assertEqual(item["url"], "https://concursos.ufc.br")
        self.assertEqual(item["vacancies"], "Vagas 12")
        self.assertIn("Diário Oficial da União, Seção 3, 25/09/2026", item["description"])

    def test_substitute_teacher_is_processo_seletivo(self):
        item = self.parse(dou_article(
            "Edital", "EDITAL Nº 12",
            "<p>O Reitor do Instituto Federal de Goiás torna pública a abertura de inscrições do Processo Seletivo "
            "Simplificado para contratação de Professor Substituto. Inscrições de 29/09/2026 a 08/10/2026.</p>",
            category="Ministério da Educação/Instituto Federal de Educação, Ciência e Tecnologia de Goiás",
        ))
        self.assertEqual(item["kind"], "processo_seletivo")
        self.assertEqual(item["state"], "GO")
        self.assertEqual(item["title"], "Instituto Federal de Educação, Ciência e Tecnologia de Goiás - Processo Seletivo (Edital nº 12/2026)")

    def test_convocation_and_graduate_selection_are_ignored(self):
        self.assertIsNone(self.parse(dou_article(
            "Edital de Convocação", "EDITAL DE CONVOCAÇÃO",
            "<p>CONCURSO PÚBLICO EDITAL Nº 1/2023. O Conselho Regional convoca o candidato aprovado.</p>",
        )))
        self.assertIsNone(self.parse(dou_article(
            "Aviso", "EDITAL DE 14 DE SETEMBRO DE 2026",
            "<p>CONCURSO PÚBLICO DE SELEÇÃO E ADMISSÃO - CURSOS DE MESTRADO E DOUTORADO. Torna pública a abertura "
            "das inscrições.</p>",
        )))

    def test_regional_court_uses_seat_state(self):
        item = self.parse(dou_article(
            "Edital", "EDITAL Nº 20, DE 23 DE SETEMBRO DE 2026",
            "<p>O Presidente torna pública a realização de Concurso Público para formação de cadastro de reserva.</p>",
            category="Poder Judiciário/Tribunal Regional do Trabalho da 1ª Região",
        ))
        self.assertEqual(item["state"], "RJ")

    def test_student_selection_is_ignored(self):
        self.assertIsNone(self.parse(dou_article(
            "Extrato", "EDITAL Nº 17/2026 - UFPI",
            "<p>PROCESSO SELETIVO PARA O CURSO DE LICENCIATURA EM EDUCAÇÃO DO CAMPO. Torna pública a abertura "
            "das inscrições.</p>",
        )))

    def test_zip_with_several_articles(self):
        xml_ok = "<xml>" + dou_article("Edital", "EDITAL Nº 5", "<p>Torna pública a abertura do Concurso Público.</p>") + "</xml>"
        xml_skip = "<xml>" + dou_article("Extrato de Contrato", "EXTRATO", "<p>Contratação de serviços.</p>") + "</xml>"
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("a.xml", xml_ok)
            zf.writestr("b.xml", xml_skip)
            zf.writestr("imagem.jpg", b"...")
        items = bf.extract_dou_zip(buf.getvalue())
        self.assertEqual([i["title"] for i in items], ["Universidade Federal do Ceará - Concurso Público (Edital nº 5/2026)"])


class MergeTests(unittest.TestCase):
    def item(self, id_, published, ends=None, **extra):
        return {"id": id_, "title": id_, "published": published, "registrationEnds": ends, "deadline": "", "salary": "", **extra}

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

    def test_legacy_item_is_replaced_by_the_same_act_with_source(self):
        today = dt.date(2026, 9, 25)
        old = [self.item("legacy", "2026-09-15", "2026-10-02", title="Câmara - Concurso Público nº 01/2026")]
        new = [self.item("new", "2026-09-15", "2026-10-02", title="Câmara - Concurso Público nº 01/2026", source="querido_diario")]
        self.assertEqual([i["id"] for i in bf.merge(old, new, today)], ["new"])

    def test_better_link_replaces_worse_one(self):
        today = dt.date(2026, 9, 25)
        old = [self.item("a", "2026-09-20", url="https://cidade.sp.gov.br", linkKind="orgao")]
        new = [
            self.item("a", "2026-09-24", url="https://www.ipell.com.br", linkKind="banca"),
            self.item("a", "2026-09-24", url="https://x.pdf", linkKind="diario"),
        ]
        [merged] = bf.merge(old, new, today)
        self.assertEqual((merged["url"], merged["linkKind"]), ("https://www.ipell.com.br", "banca"))



class FindLinkTests(unittest.TestCase):
    PDF = "https://data.queridodiario.ok.org.br/1/x.pdf"

    def test_edital_page_is_preferred(self):
        text = "Site www.prefeitura.sp.gov.br. Inscrições pelo site https://www.vunesp.com.br/concurso/PMXX2601."
        self.assertEqual(bf.find_link(text, self.PDF), ("https://www.vunesp.com.br/concurso/PMXX2601", "edital"))

    def test_only_gazette_site_falls_back_to_pdf(self):
        text = ("www.cidade.sp.gov.br https://www.cidade.sp.gov.br/diario-oficial/10/ Edital de abertura. "
                "www.cidade.sp.gov.br")
        self.assertEqual(bf.find_link(text, self.PDF), (self.PDF, "diario"))

    def test_no_link_falls_back_to_pdf(self):
        self.assertEqual(bf.find_link("Edital sem endereço eletrônico.", self.PDF), (self.PDF, "diario"))

    def test_link_broken_across_lines_is_joined(self):
        text = "Inscrições no site http://www.juatuba.mg.gov.br/processo-\nseletivo-2026 até 10/10."
        self.assertEqual(bf.find_link(text, self.PDF), ("http://www.juatuba.mg.gov.br/processo-seletivo-2026", "edital"))

    def test_fee_refund_page_is_not_the_edital(self):
        text = ("Edital no site www.cidade.rs.gov.br. Restituição em "
                "https://cidade.atende.net/servicos/e-restituicao-taxa-de-inscricao-concurso-012026.")
        self.assertEqual(bf.find_link(text, self.PDF), ("https://www.cidade.rs.gov.br", "orgao"))

    def test_organization_site(self):
        text = "Informações no portal www.saae.sp.gov.br."
        self.assertEqual(bf.find_link(text, self.PDF), ("https://www.saae.sp.gov.br", "orgao"))


if __name__ == "__main__":
    unittest.main()
