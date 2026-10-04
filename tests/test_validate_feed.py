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

    def test_published_feed_is_valid(self):
        self.assertEqual(vf.feed_errors(json.loads(FEED.read_text())), [])


if __name__ == "__main__":
    unittest.main()
