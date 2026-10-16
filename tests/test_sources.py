import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import sources  # noqa: E402


def item(id, title="Prefeitura de Exemplo - Concurso Público nº 01/2026", **extra):
    base = {"id": id, "title": title, "state": "MG", "city": "Lagoa Santa", "kind": "concurso", "published": "2026-10-01"}
    base.update(extra)
    return base


class ModeTests(unittest.TestCase):
    def test_default_is_not_running(self):
        self.assertEqual(sources.collector_mode("sigpub", {}), sources.SKIP)

    def test_shadow_and_live(self):
        self.assertEqual(sources.collector_mode("sigpub", {"SOURCES_SHADOW": "sigpub"}), sources.SHADOW)
        self.assertEqual(sources.collector_mode("sigpub", {"SOURCES_ON": " Sigpub "}), sources.LIVE)

    def test_live_wins_over_shadow(self):
        env = {"SOURCES_ON": "sigpub", "SOURCES_SHADOW": "sigpub"}
        self.assertEqual(sources.collector_mode("sigpub", env), sources.LIVE)

    def test_kill_switch_beats_everything(self):
        env = {"SOURCES_ON": "sigpub", "SOURCES_SHADOW": "sigpub", "SOURCES_OFF": "x,sigpub"}
        self.assertEqual(sources.collector_mode("sigpub", env), sources.OFF)

    def test_off_for_one_association_keeps_the_collector_running(self):
        env = {"SOURCES_ON": "sigpub", "SOURCES_OFF": "sigpub:amp"}
        self.assertEqual(sources.collector_mode("sigpub", env), sources.LIVE)

    def test_typos_are_reported(self):
        self.assertEqual(sources.unknown_names({"SOURCES_ON": "sigpub,sigpup"}), ["sigpup"])


class DropDisabledTests(unittest.TestCase):
    def test_items_of_disabled_sources_leave_the_feed_even_if_already_published(self):
        feed = [
            item("old", source="querido_diario"),
            item("a", source="diario_municipal", collector="sigpub:amp"),
            item("b", source="diario_municipal", collector="sigpub:famurs"),
        ]
        self.assertEqual([i["id"] for i in sources.drop_disabled(feed, {"sigpub"})], ["old"])
        self.assertEqual([i["id"] for i in sources.drop_disabled(feed, {"sigpub:amp"})], ["old", "b"])
        self.assertEqual([i["id"] for i in sources.drop_disabled(feed, {"diario_municipal"})], ["old"])
        self.assertEqual(sources.drop_disabled(feed, set()), feed)

    def test_legacy_items_without_collector_are_untouched(self):
        feed = [item("old"), item("dou", source="dou")]
        self.assertEqual(sources.drop_disabled(feed, {"sigpub", "doe"}), feed)


class DedupeTests(unittest.TestCase):
    def test_same_edital_in_another_source_is_dropped(self):
        qd = item("qd", source="querido_diario")
        sig = item("sig", "Município de Lagoa Santa - Concurso Público nº 001/2026", source="diario_municipal")
        self.assertEqual(sources.dedupe([sig], [qd]), [])

    def test_key_ignores_accents_case_and_leading_zeros(self):
        a = item("a", city="São João", title="X - Concurso Público nº 01/2026")
        b = item("b", city="SAO JOAO", title="Y - Concurso Público nº 001/26")
        self.assertEqual(sources.match_key(a), sources.match_key(b))

    def test_different_state_city_kind_or_number_is_kept(self):
        base = item("qd")
        for other in (item("a", state="PR"), item("b", city="Outra"), item("c", kind="processo_seletivo"),
                      item("d", title="X - Concurso Público nº 02/2026")):
            self.assertEqual(sources.dedupe([other], [base]), [other])

    def test_items_without_number_are_never_deduplicated_by_content(self):
        no_number = item("a", title="Câmara - Concurso Público")
        self.assertIsNone(sources.match_key(no_number))
        self.assertEqual(len(sources.dedupe([no_number, item("b", title="Câmara - Concurso Público")], [])), 2)

    def test_duplicates_within_the_new_items_and_same_id(self):
        a = item("a")
        b = item("b", title="Outro - Concurso Público nº 1/2026")
        self.assertEqual(sources.dedupe([a, b], []), [a])
        self.assertEqual(sources.dedupe([item("qd", city="Zzz")], [item("qd")]), [])


if __name__ == "__main__":
    unittest.main()
