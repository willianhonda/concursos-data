import datetime as dt
import json
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import bancas  # noqa: E402
import build_feed as bf  # noqa: E402
import sources  # noqa: E402
import validate_feed as vf  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures" / "bancas"
TODAY = dt.date(2026, 10, 16)


def raw(name):
    return (FIXTURES / name).read_bytes()


def find(items, needle):
    return next(i for i in items if needle in i["title"])


class IBGPTests(unittest.TestCase):
    def setUp(self):
        self.items = bancas.parse_ibgp(raw("ibgp.json"), TODAY)

    def test_only_prefeituras_and_camaras_in_scope(self):
        self.assertEqual(len(self.items), 12)  # sem vestibular, residência, Santa Casa/hospitais e autarquia
        self.assertEqual({i["collector"] for i in self.items}, {"ibgp"})
        self.assertFalse([i for i in self.items if "Hospital" in i["title"] or "Santa" in i["title"]])

    def test_city_org_number_and_dates(self):
        item = find(self.items, "Dores do Indaiá")
        self.assertEqual(item["title"], "Câmara de Dores do Indaiá - Concurso Público nº 01/2026")
        self.assertEqual((item["state"], item["city"], item["sphere"]), ("MG", "Dores do Indaiá", "municipal"))
        self.assertEqual((item["registrationStarts"], item["registrationEnds"]), ("2026-10-05", "2026-11-03"))
        self.assertEqual(item["deadline"], "Inscrições: 05/10/2026 a 03/11/2026")
        self.assertEqual(item["vacancies"], "Vagas 5")

    def test_hyphenated_city_and_processo_seletivo(self):
        self.assertEqual(find(self.items, "Processo Seletivo nº 01/2026")["kind"], "processo_seletivo")
        self.assertTrue(any("São João Del-Rei" in i["title"] for i in self.items))

    def test_items_are_valid_and_stable(self):
        for item in self.items:
            self.assertEqual(vf.item_errors(item), [], item["title"])
            self.assertEqual((item["source"], item["linkKind"], item["banca"]), ("banca", "banca", "IBGP"))
        again = bancas.parse_ibgp(raw("ibgp.json"), TODAY)
        self.assertEqual([i["id"] for i in self.items], [i["id"] for i in again])
        self.assertEqual(len({i["id"] for i in self.items}), len(self.items))


class ProSeletaTests(unittest.TestCase):
    def avancasp(self):
        page = raw("proseleta_avancasp.html").decode("iso-8859-1")
        return bancas.parse_proseleta(page, "https://avancasp.org.br", "SP", "Instituto Avança SP", "avancasp", TODAY)

    def objetivas(self):
        page = raw("proseleta_objetivas.html").decode("iso-8859-1")
        return bancas.parse_proseleta(page, "https://concursos.objetivas.com.br", None, "Objetivas", "objetivas", TODAY)

    def test_avancasp_skips_vestibular_and_uses_default_uf(self):
        items = self.avancasp()
        self.assertEqual(len(items), 12)
        self.assertFalse([i for i in items if "Uniregistral" in i["title"].title()])
        item = find(items, "Cabreúva")
        self.assertEqual(item["title"], "Prefeitura de Cabreúva - Concurso Público nº 02/2026")
        self.assertEqual((item["state"], item["registrationStarts"], item["registrationEnds"]), ("SP", "2026-10-09", "2026-11-09"))
        self.assertEqual(item["url"], "https://avancasp.org.br/informacoes/" + item["url"].rsplit("/", 2)[-2] + "/")
        self.assertEqual(item["collector"], "proseleta:avancasp")

    def test_objetivas_takes_uf_from_the_title(self):
        items = self.objetivas()
        self.assertEqual(len(items), 5)
        self.assertEqual(find(items, "Chapecó")["state"], "SC")
        self.assertEqual(find(items, "Tio Hugo")["state"], "RS")

    def test_without_default_uf_and_without_uf_in_title_the_item_is_dropped(self):
        page = raw("proseleta_avancasp.html").decode("iso-8859-1")
        self.assertEqual(bancas.parse_proseleta(page, "https://x.br", None, "X", "x", TODAY), [])

    def test_valid_items(self):
        for item in self.avancasp() + self.objetivas():
            self.assertEqual(vf.item_errors(item), [], item["title"])


class CebraspeTests(unittest.TestCase):
    def test_uf_from_event_code(self):
        self.assertEqual(bancas.cebraspe_uf("PC_PE_26"), "PE")
        self.assertEqual(bancas.cebraspe_uf("TJ_CE_25_NOTARIOS"), "CE")
        self.assertIsNone(bancas.cebraspe_uf("CGU_26_AFFC"))
        self.assertIsNone(bancas.cebraspe_uf("CD_26_ANALISTA"))
        self.assertIsNone(bancas.cebraspe_uf("PREF_SP_26"))

    def test_list_keeps_only_open_events_with_uf(self):
        codes = [e["eventoURL"] for e in bancas.parse_cebraspe_list(raw("cebraspe_lista.json"))]
        self.assertEqual(codes, ["CBM_AL_26", "PC_PE_26", "SEFAZ_AL_26", "TCE_AC_26"])

    def test_item_with_detail(self):
        event = next(e for e in bancas.parse_cebraspe_list(raw("cebraspe_lista.json")) if e["eventoURL"] == "PC_PE_26")
        item = bancas.item_from_cebraspe(event, json.loads(raw("cebraspe_evento.json")), TODAY)
        self.assertEqual(item["title"], "PC PE - Concurso Público")
        self.assertEqual((item["state"], item["sphere"], item["banca"]), ("PE", "estadual", "Cebraspe"))
        self.assertEqual((item["registrationStarts"], item["registrationEnds"]), ("2026-10-15", "2026-12-16"))
        self.assertEqual(item["salary"], "Salário até R$ 13.560,00")
        self.assertEqual(item["vacancies"], "Vagas 1315")
        self.assertEqual(item["url"], "https://www.cebraspe.org.br/concursos/PC_PE_26")
        self.assertEqual(vf.item_errors(item), [])

    def test_full_name_when_the_event_has_one(self):
        event = next(e for e in bancas.parse_cebraspe_list(raw("cebraspe_lista.json")) if e["eventoURL"] == "PC_PE_26")
        detail = {**json.loads(raw("cebraspe_evento.json")), "eventoNomeCompleto": "Polícia Civil do Estado de Pernambuco"}
        self.assertEqual(bancas.item_from_cebraspe(event, detail, TODAY)["title"],
                         "Polícia Civil do Estado de Pernambuco - Concurso Público")
        # Código repetido ou frase no lugar do nome: fica a sigla do código.
        for name in ("PC PE 26", "Concurso para provimento do cargo efetivo de Conselheiro Substituto (Auditor) do TCE/AC", ""):
            self.assertIsNone(bancas.cebraspe_name({"eventoNomeCompleto": name}), name)

    def test_item_without_detail_still_valid(self):
        event = bancas.parse_cebraspe_list(raw("cebraspe_lista.json"))[0]
        item = bancas.item_from_cebraspe(event, None, TODAY)
        self.assertEqual(item["deadline"], "Inscrições: veja o edital")
        self.assertEqual(vf.item_errors(item), [])


class CollectTests(unittest.TestCase):
    def fetch(self, url):
        if url == bancas.IBGP_URL:
            return raw("ibgp.json")
        if url == bancas.CEBRASPE_LIST:
            return raw("cebraspe_lista.json")
        if "cebraspe" in url:
            return raw("cebraspe_evento.json")
        if "avancasp" in url:
            return raw("proseleta_avancasp.html")
        if "objetivas" in url:
            return raw("proseleta_objetivas.html")
        raise OSError("fora do ar")

    def run_collect(self, **kw):
        logs = []
        items = bancas.collect(TODAY - dt.timedelta(days=7), TODAY, fetch=self.fetch, sleep=lambda s: None,
                               log=logs.append, **kw)
        return items, logs

    def test_all_three(self):
        items, logs = self.run_collect()
        self.assertEqual(len(items), 12 + 12 + 5 + 4)
        self.assertTrue(any("igeduc" in line and "falhou" in line for line in logs))  # um site fora do ar não derruba

    def test_only_limits_to_one_banca(self):
        items, _ = self.run_collect(only="cebraspe")
        self.assertEqual({i["collector"] for i in items}, {"cebraspe"})

    def test_off_switches_a_single_site(self):
        items, logs = self.run_collect(off={"proseleta:avancasp"})
        self.assertFalse([i for i in items if i["collector"] == "proseleta:avancasp"])
        self.assertTrue(any("desligado" in line for line in logs))

    def test_one_failing_banca_does_not_stop_the_others(self):
        def fetch(url):
            if url == bancas.IBGP_URL:
                raise OSError("503")
            return self.fetch(url)
        items = bancas.collect(TODAY, TODAY, fetch=fetch, sleep=lambda s: None)
        self.assertEqual({i["collector"] for i in items}, {"proseleta:avancasp", "proseleta:objetivas", "cebraspe"})


class WiringTests(unittest.TestCase):
    def test_registered_and_known(self):
        for name in ("ibgp", "proseleta", "cebraspe"):
            self.assertIn(name, bf.COLLECTOR_FUNCS)
            self.assertIn(name, sources.KNOWN_COLLECTORS)

    def test_run_collectors_in_shadow_never_touches_the_feed(self):
        env = {"SOURCES_SHADOW": "ibgp,cebraspe"}
        fetch = CollectTests().fetch
        live, shadow = bf.run_collectors(TODAY - dt.timedelta(days=7), TODAY, [], env, fetch)
        self.assertEqual(live, [])
        self.assertEqual({i["collector"] for i in shadow}, {"ibgp", "cebraspe"})

    def test_run_collectors_live_goes_to_the_feed(self):
        env = {"SOURCES_ON": "ibgp,proseleta,cebraspe", "SOURCES_SHADOW": "sigpub", "SOURCES_OFF": "sigpub"}
        live, shadow = bf.run_collectors(TODAY - dt.timedelta(days=7), TODAY, [], env, CollectTests().fetch)
        self.assertEqual(shadow, [])
        self.assertEqual({i["collector"].split(":")[0] for i in live}, {"ibgp", "proseleta", "cebraspe"})
        self.assertTrue(all(i["source"] == "banca" and not vf.item_errors(i) for i in live))

    def test_workflow_defaults_promote_the_bancas_and_keep_sigpub_in_shadow(self):
        workflow = (Path(__file__).resolve().parents[1] / ".github/workflows/atualizar-concursos.yml").read_text()
        on = re.search(r"SOURCES_ON: \$\{\{ vars\.SOURCES_ON \|\| '([^']*)' \}\}", workflow)
        shadow = re.search(r"SOURCES_SHADOW: \$\{\{ vars\.SOURCES_SHADOW \|\| '([^']*)' \}\}", workflow)
        env = {"SOURCES_ON": on.group(1), "SOURCES_SHADOW": shadow.group(1)}
        self.assertEqual({n: sources.collector_mode(n, env) for n in sources.KNOWN_COLLECTORS},
                         {"sigpub": "shadow", "ibgp": "live", "proseleta": "live", "cebraspe": "live"})
        self.assertEqual(sources.unknown_names(env), [])


if __name__ == "__main__":
    unittest.main()
