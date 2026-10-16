import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import validate_feed as vf  # noqa: E402

FEED = Path(__file__).resolve().parents[1] / "docs" / "concursos.json"


def item(**changes):
    base = {
        "id": "abc123",
        "title": "Prefeitura de Exemplo - Concurso Público nº 1/2026",
        "deadline": "Inscrições: 01/10/2026 a 30/10/2026",
        "state": "SP",
        "salary": "",
        "vacancies": "",
        "description": "Trecho",
        "url": "https://example.com",
        "city": "Exemplo",
        "published": "2026-09-30",
        "registrationStarts": "2026-10-01",
        "registrationEnds": "2026-10-30",
        "gazetteUrl": "https://example.com/diario.pdf",
        "kind": "concurso",
        "source": "querido_diario",
        "linkKind": "orgao",
    }
    base.update(changes)
    return {k: v for k, v in base.items() if v is not ...}


class ItemTests(unittest.TestCase):
    def test_valid_item(self):
        self.assertEqual(vf.item_errors(item()), [])

    def test_optional_fields_may_be_null_or_missing(self):
        self.assertEqual(vf.item_errors(item(city=None, registrationStarts=None, kind=..., linkKind=...)), [])

    def test_new_fields_are_allowed(self):
        self.assertEqual(vf.item_errors(item(cargo="Enfermeiro", escolaridade="superior")), [])

    def test_career_fields(self):
        valid = item(banca="Vunesp", roles=["Enfermeiro"], education=["superior"], areas=["saude"])
        self.assertEqual(vf.item_errors(valid), [])
        self.assertEqual(vf.item_errors(item(banca=None, roles=None, education=None, areas=None)), [])
        self.assertIn("'roles' deve ser lista de texto ou null", vf.item_errors(item(roles="Enfermeiro")))
        self.assertIn("'roles' deve ser lista de texto ou null", vf.item_errors(item(roles=[1])))
        self.assertIn("'banca' deve ser texto ou null, veio list", vf.item_errors(item(banca=["Vunesp"])))
        self.assertEqual(vf.item_errors(item(areas=["astronomia"])), ["'areas' com valores desconhecidos: ['astronomia']"])
        self.assertEqual(vf.item_errors(item(education=["doutorado"])), ["'education' com valores desconhecidos: ['doutorado']"])

    def test_new_sources_and_optional_fields(self):
        for source in ("diario_municipal", "doe", "banca"):
            self.assertEqual(vf.item_errors(item(source=source)), [])
        new = item(source="diario_municipal", collector="sigpub:amp", sphere="municipal", gazetteName="Diário Oficial dos Municípios do Paraná")
        self.assertEqual(vf.item_errors(new), [])
        self.assertEqual(vf.item_errors(item(sphere=None, collector=None, gazetteName=None)), [])
        self.assertTrue(vf.item_errors(item(source="blog")))

    def test_sphere_and_collector_are_checked(self):
        self.assertTrue(vf.item_errors(item(sphere="conselho")))
        self.assertTrue(vf.item_errors(item(collector="Sigpub AMP")))
        self.assertIn("'gazetteName' deve ser texto ou null, veio int", vf.item_errors(item(gazetteName=1)))

    def test_missing_required_field(self):
        self.assertIn("falta o campo obrigatório 'title'", vf.item_errors(item(title=...)))

    def test_required_field_with_wrong_type(self):
        self.assertIn("'salary' deve ser texto, veio int", vf.item_errors(item(salary=3000)))
        self.assertIn("'vacancies' deve ser texto, veio NoneType", vf.item_errors(item(vacancies=None)))

    def test_dates_must_be_iso(self):
        self.assertTrue(vf.item_errors(item(registrationEnds="30/10/2026")))
        self.assertTrue(vf.item_errors(item(registrationEnds="2026-02-30")))
        self.assertTrue(vf.item_errors(item(registrationStarts="2026-11-01")))

    def test_unknown_values(self):
        self.assertTrue(vf.item_errors(item(kind="estagio")))
        self.assertTrue(vf.item_errors(item(state="XX")))
        self.assertTrue(vf.item_errors(item(url="www.example.com")))
        self.assertEqual(vf.item_errors(item(state="BR")), [])

    def test_not_an_object(self):
        self.assertEqual(vf.item_errors(42), ["não é um objeto: int"])


class FeedTests(unittest.TestCase):
    def test_feed_must_be_an_array(self):
        self.assertTrue(vf.feed_errors({"items": []}))

    def test_repeated_id(self):
        self.assertIn("item abc123: 'id' repetido", vf.feed_errors([item(), item()]))

    def test_shrinking_by_half_blocks_publication(self):
        feed = [item(id=str(n)) for n in range(4)]
        self.assertTrue(vf.feed_errors(feed, previous_count=20))
        self.assertEqual(vf.feed_errors(feed, previous_count=8), [])  # feed pequeno: sem checagem

    def test_sources_off_parsing(self):
        self.assertEqual(vf.sources_off({"SOURCES_OFF": " Sigpub, doe ,,"}), {"sigpub", "doe"})
        self.assertEqual(vf.sources_off({}), set())

    def test_kill_switch_rejects_items_from_disabled_sources(self):
        feed = [item(id="a"), item(id="b", source="diario_municipal", collector="sigpub:amp")]
        self.assertEqual(vf.feed_errors(feed, off=set()), [])
        self.assertEqual(vf.feed_errors(feed, off={"sigpub"}), [
            "item b: vem de uma fonte desligada em SOURCES_OFF (sigpub:amp)"])
        self.assertEqual(len(vf.feed_errors(feed, off={"sigpub:amp"})), 1)
        self.assertEqual(len(vf.feed_errors(feed, off={"diario_municipal"})), 1)
        self.assertEqual(vf.feed_errors(feed, off={"sigpub:famurs"}), [])

    def test_published_feed_is_valid(self):
        self.assertEqual(vf.feed_errors(json.loads(FEED.read_text())), [])


if __name__ == "__main__":
    unittest.main()
