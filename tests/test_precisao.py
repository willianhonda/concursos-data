"""Casos reais que o extrator classificava errado na avaliação do BL-057 (18/10/2026).

As fixtures em tests/fixtures/precisao/ são trechos dos diários e matérias originais: o Querido Diário de 13 a
16/10/2026 e as matérias do Sigpub de 05 a 17/10/2026. Nos editais muito longos, o meio foi cortado.
"""

import datetime as dt
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import build_feed as bf  # noqa: E402
import sigpub  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures" / "precisao"


def gazette(city, uf, date="2026-10-14"):
    return {"territory_id": "1", "territory_name": city, "state_code": uf, "date": date, "url": "https://example.com/d.pdf"}


def titles(name, city, uf, date="2026-10-14"):
    return [i["title"] for i in bf.extract_openings((FIXTURES / name).read_text(), gazette(city, uf, date))]


class NotAnOpeningTests(unittest.TestCase):
    def test_registration_approval_is_not_an_opening(self):
        # "TORNA PÚBLICO o Deferimento das Inscrições do Concurso Público n.º 01/2026"
        self.assertEqual(titles("sigpub_coronel_vivida_deferimento.txt", "Coronel Vivida", "PR"), [])
        self.assertEqual(titles("sigpub_cerro_azul_deferimento.txt", "Cerro Azul", "PR"), [])

    def test_exam_location_notice_is_not_an_opening(self):
        # "torna público que foi divulgado por meio do EDITAL 05 - DIVULGA OS LOCAIS E HORÁRIOS DAS PROVAS"
        self.assertEqual(titles("sigpub_piranhas_locais.txt", "Piranhas", "GO"), [])

    def test_list_of_registrations_is_not_an_opening(self):
        # "DIVULGAÇÃO DAS INSCRIÇÕES … resolve DIVULGAR AS INSCRIÇÕES … Lista de Inscritos por Cargo"
        self.assertEqual(titles("sigpub_campo_novo_inscritos.txt", "Campo Novo de Rondônia", "RO"), [])

    def test_school_director_selection_is_off_topic(self):
        # Seleção de Diretor(a) e Vice-Diretor(a) entre servidores da rede, não vaga nova.
        self.assertEqual(titles("sigpub_sao_bento_do_norte_diretores.txt", "São Bento do Norte", "RN"), [])

    def test_commission_appointment_is_not_an_opening(self):
        # "Instituir a Comissão Especial de Concurso Público … incumbida … providenciar a publicação do Edital de Abertura"
        self.assertEqual(titles("qd_campo_mourao_comissao.txt", "Campo Mourão", "PR", "2026-10-16"), [])

    def test_internship_schedule_change_is_off_topic(self):
        # "Fica alterado o item 1 do Processo Seletivo de Estagiário aberto pelo Edital nº 068/2026"
        self.assertEqual(titles("qd_arapongas_estagiario.txt", "Arapongas", "PR"), [])

    def test_sigpub_titles_that_are_never_openings(self):
        for title in (
            "DIVULGAÇÃO DAS INSCRIÇÕES DO PROCESSO SELETIVO SIMPLIFICADO 002-2026-SEMED PARA CONTRATAÇÃO POR TEMPO DETERMINADO.",
            "EDITAL Nº 003/2026 PROCESSO SELETIVO PARA DIRETORES(AS) E VICE-DIRETORES(AS) DAS UNIDADES ESCOLARES",
            "EDITAL 05 - LOCAIS E HORÁRIOS DAS PROVAS OBJETIVAS",
        ):
            self.assertTrue(sigpub.SKIP_TITLE.search(title), title)
        self.assertFalse(sigpub.SKIP_TITLE.search("PROCESSO SELETIVO SIMPLIFICADO 16/2026"))


class OneItemPerActTests(unittest.TestCase):
    def test_chapters_of_the_same_edital_give_one_item(self):
        # O capítulo "5. DAS INSCRIÇÕES … inscrições para o Concurso Público" é o mesmo edital nº 54/2026.
        self.assertEqual(titles("qd_apucarana_edital.txt", "Apucarana", "PR"), ["Município de Apucarana - Concurso Público nº 54/2026"])

    def test_one_item_per_sigpub_materia_without_number(self):
        # Os requisitos repetem "publicação do edital do processo seletivo público" quatro vezes na mesma matéria.
        found = bf.extract_openings((FIXTURES / "sigpub_sao_pedro_do_sul_abertura.txt").read_text(),
                                    gazette("São Pedro do Sul", "RS", "2026-10-16"))
        self.assertEqual(len(found), 1)
        self.assertEqual((found[0]["registrationStarts"], found[0]["registrationEnds"]), ("2026-10-19", "2026-10-23"))

    def test_materia_end_marks_the_act_even_after_cited_laws(self):
        # O edital cita "LEI Nº 495, DE 2011", que NEXT_ACT toma por outro ato; a matéria só termina no Código Identificador.
        self.assertEqual(len(titles("qd_cha_preta_edital.txt", "Chã Preta", "AL", "2026-10-13")), 1)

    def test_distinct_numbered_acts_in_the_same_gazette_are_kept(self):
        text = (
            "O MUNICÍPIO torna pública a abertura do Processo Seletivo Simplificado nº 01/2026 para Professor. "
            "Inscrições de 20/10/2026 a 30/10/2026.\n" + "x " * 300 + "\nEXTRATO DE EDITAL\n" +
            "O MUNICÍPIO torna pública a abertura do Processo Seletivo Simplificado nº 02/2026 para Motorista. "
            "Inscrições de 21/10/2026 a 31/10/2026.\n"
        )
        found = bf.extract_openings(text, gazette("Exemplo", "MG", "2026-10-16"))
        self.assertEqual([i["title"] for i in found], ["Município de Exemplo - Processo Seletivo nº 01/2026",
                                                       "Município de Exemplo - Processo Seletivo nº 02/2026"])


class OrganTests(unittest.TestCase):
    def test_banca_that_co_signs_is_not_the_organ(self):
        # "O MUNICÍPIO DE CHÃ-PRETA – AL … e a Fundação Vale do Piauí – FUNVAPI, tornam público"
        [title] = titles("qd_cha_preta_edital.txt", "Chã Preta", "AL", "2026-10-13")
        self.assertEqual(title, "Prefeitura Municipal de Chã Preta - Concurso Público nº 001/2026")

    def test_the_organ_itself_is_still_found(self):
        self.assertEqual(bf.find_org("A Fundação Municipal de Saúde de Exemplo, torna pública", "", "Exemplo"),
                         "Fundação Municipal de Saúde de Exemplo")


if __name__ == "__main__":
    unittest.main()
