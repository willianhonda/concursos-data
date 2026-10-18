import sys
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import build_feed as bf  # noqa: E402
import orgaos_uf  # noqa: E402
from test_build_feed import dou_article  # noqa: E402


class SedeUfTests(unittest.TestCase):
    def test_local_federal_bodies_from_the_feed(self):
        # Nomes reais do docs/concursos.json de 17/10/2026, todos publicados como "BR".
        cases = {
            "Fundação Universidade de Brasília": "DF",
            "Fundação Universidade Federal de Viçosa": "MG",
            "Instituto Federal de Educação, Ciência e Tecnologia Goiano": "GO",
            "Instituto Federal de Educação, Ciência e Tecnologia do Ceará": "CE",
            "Instituto Federal de Educação, Ciência e Tecnologia do Norte de Minas Gerais": "MG",
            "Universidade Federal da Integração Latino-Americana": "PR",
            "Universidade Tecnológica Federal do Paraná": "PR",
            "Universidade Federal do Delta do Parnaíba": "PI",
            "Colégio Pedro II": "RJ",
        }
        for org, uf in cases.items():
            self.assertEqual(orgaos_uf.sede_uf(org), uf, org)

    def test_case_accents_and_prepositions_do_not_matter(self):
        self.assertEqual(orgaos_uf.sede_uf("INSTITUTO FEDERAL DE EDUCACAO CIENCIA E TECNOLOGIA DE CEARA"), "CE")
        self.assertEqual(orgaos_uf.sede_uf("Universidade Federal de São João del-Rei"), "MG")
        self.assertEqual(orgaos_uf.sede_uf("Tribunal Regional Eleitoral do Rio Grande do Sul"), "RS")
        # "Mato Grosso" não pode virar "Mato Grosso do Sul" nem o contrário.
        self.assertEqual(orgaos_uf.sede_uf("Universidade Federal de Mato Grosso"), "MT")
        self.assertEqual(orgaos_uf.sede_uf("Fundação Universidade Federal de Mato Grosso do Sul"), "MS")

    def test_national_and_multi_state_bodies_stay_unmapped(self):
        for org in (
            "Fundação Escola Nacional de Administração Pública",
            "Conselho Nacional dos Direitos da Pessoa com Deficiência",
            "Secretaria Executiva",
            "Comando da Marinha",
            "Departamento de Polícia Federal",
            "Polícia Rodoviária Federal",
            "Empresa Brasileira de Serviços Hospitalares",
            "Universidade Federal da Fronteira Sul",
            "Fundação Universidade Federal do Vale do São Francisco",
            # Só o começo do nome não basta: a comparação é do nome inteiro.
            "Universidade Federal",
        ):
            self.assertIsNone(orgaos_uf.sede_uf(org), org)

    def test_fix_states_only_touches_br_items_from_the_dou(self):
        feed = [
            {"id": "a", "title": "Fundação Universidade de Brasília - Concurso Público (Edital nº 65/2026)", "state": "BR", "source": "dou"},
            {"id": "b", "title": "Conselho Nacional dos Direitos da Pessoa com Deficiência - Processo Seletivo", "state": "BR", "source": "dou"},
            {"id": "c", "title": "Universidade Federal de Viçosa - Concurso Público", "state": "SP", "source": "dou"},
            {"id": "d", "title": "Universidade Federal de Viçosa - Concurso Público", "state": "BR", "source": "querido_diario"},
        ]
        self.assertEqual(orgaos_uf.fix_states(feed), 1)
        self.assertEqual([i["state"] for i in feed], ["DF", "BR", "SP", "BR"])


class DouSedeTests(unittest.TestCase):
    def test_dou_item_gets_the_seat_state(self):
        item = bf.extract_dou_article(ET.fromstring(dou_article(
            "Edital", "EDITAL Nº 29/2026",
            "<p>O Reitor torna pública a abertura de inscrições do Processo Seletivo Simplificado para Professor "
            "Substituto. Inscrições de 01/10/2026 a 20/10/2026.</p>",
            category="Ministério da Educação/Instituto Federal de Educação, Ciência e Tecnologia Goiano",
        )))
        self.assertEqual(item["state"], "GO")

    def test_national_body_stays_br(self):
        item = bf.extract_dou_article(ET.fromstring(dou_article(
            "Edital", "EDITAL Nº 260/2026",
            "<p>A Presidente torna pública a realização de Concurso Público para o cargo de Analista.</p>",
            category="Ministério da Gestão e da Inovação em Serviços Públicos/Fundação Escola Nacional de Administração Pública",
        )))
        self.assertEqual(item["state"], "BR")


if __name__ == "__main__":
    unittest.main()
