# concursos-data

Feed JSON de concursos públicos e processos seletivos com inscrições abertas, usado pelo app iOS **ConcursosPublicos**.

- **Fontes:**
  - diários oficiais municipais, pela API pública do [Querido Diário](https://queridodiario.ok.org.br) (Open Knowledge Brasil);
  - Diário Oficial da União, Seção 3, pelo [INLABS](https://inlabs.in.gov.br) da Imprensa Nacional (precisa de cadastro gratuito; veja a configuração).
- **Atualização:** diária, por GitHub Actions.
- **Hospedagem:** GitHub Pages, em `https://<seu-usuario>.github.io/concursos-data/concursos.json`.
- **Custo:** zero. O repositório é público, sem servidor, banco de dados, chave de API nem LLM.

## Como funciona

1. `scripts/build_feed.py` busca na API os diários dos últimos 7 dias que mencionam a abertura de inscrições de um concurso público.
2. Para cada diário, o script baixa o texto completo e encontra os atos de abertura, ignorando convocações, nomeações, resultados, processos seletivos temporários etc.
3. Ele extrai com expressões regulares:
   - o órgão
   - o número do edital
   - o período de inscrições
   - a faixa salarial
   - o número de vagas
   - o link do edital: prefere o site da banca e descarta o site que publica o diário, que aparece no cabeçalho de cada página; sem link útil, usa o PDF do diário
4. Por fim, junta com o `docs/concursos.json` anterior, remove duplicados e tira do feed:
   - os concursos com inscrições encerradas
   - os concursos sem prazo identificado publicados há mais de 45 dias
5. Descarta os itens que o app não conseguiria ler, e o workflow só publica se `scripts/validate_feed.py` aprovar o feed inteiro (veja "Compatibilidade com o app").

Cada item segue o formato do `EventModel` do app, mais estes campos:

| Campo | Valores |
|---|---|
| `kind` | `concurso` ou `processo_seletivo` (seleções simplificadas e contratações temporárias) |
| `source` | `querido_diario` ou `dou` |
| `city` | cidade do diário municipal; `null` no DOU |
| `published` | data de publicação no diário (`AAAA-MM-DD`) |
| `registrationStarts`, `registrationEnds` | período de inscrições (`AAAA-MM-DD`), ou `null` quando não foi identificado |
| `gazetteUrl` | link do diário (PDF do município ou página do DOU) |
| `linkKind` | o que o `url` abre: `edital` (página do concurso), `banca`, `orgao` (site do órgão) ou `diario` (o próprio diário, quando o ato não traz um link útil) |
| `banca` | banca organizadora citada no ato ("será executado pela…") ou reconhecida pelo site do edital; `null` quando não identificada |
| `roles` | cargos citados por extenso ("cargos de Enfermeiro, Motorista e Guarda Municipal"), até 12; `null` quando o ato só traz uma tabela de cargos |
| `education` | níveis citados no ato: `fundamental`, `medio`, `tecnico`, `superior`; `null` quando não identificados |
| `areas` | áreas pelos cargos (ou pelos nomes de cargo do trecho): `saude`, `educacao`, `ti`, `juridica`, `seguranca`, `engenharia`, `administrativa`, `assistencia_social`, `operacional`; `null` sem pista |

`banca`, `roles`, `education` e `areas` são lidos pelo app a partir da 2.3 (filtro e alertas por área e escolaridade, Premium). A extração é por expressões regulares e cobre só parte dos atos; o app trata a ausência como "confira no edital".

No DOU, `state` vem da UF da instituição (ou da sede, nos TRTs e TRFs) e é `BR` quando não dá para saber.

```json
{
  "id": "3f1c9a0b2d4e",
  "title": "Prefeitura Municipal do Salvador - Concurso Público nº 02/2026",
  "deadline": "Inscrições: 21/09/2026 a 30/10/2026",
  "state": "BA",
  "salary": "Faixa salarial: R$ 1.773,21 - R$ 3.546,42",
  "vacancies": "Vagas 50",
  "description": "…trecho do diário…\n\nFonte: Diário Oficial de Salvador/BA, 18/09/2026. Informações extraídas automaticamente; confira sempre o edital oficial.",
  "url": "https://www.semge.salvador.ba.gov.br",
  "city": "Salvador",
  "published": "2026-09-18",
  "registrationEnds": "2026-10-30",
  "gazetteUrl": "https://data.queridodiario.ok.org.br/…pdf"
}
```

## Compatibilidade com o app

O feed chega no mesmo dia a todas as versões instaladas do app, sem revisão da App Store. As versões até a 2.2.x leem o array inteiro de uma vez: um único item fora do formato derruba a lista de todo mundo. Por isso:

- **Só acrescente campos, sempre opcionais.** Nunca remova, renomeie ou mude o tipo de `id`, `title`, `deadline`, `state`, `salary`, `vacancies`, `description` e `url`, que são obrigatórios e sempre texto. O app ignora chaves que não conhece.
- **`build_feed.py` descarta** os itens que não passam em `validate_feed.item_errors` e registra o motivo no log.
- **O workflow valida o feed antes de publicar.** Ele confere os campos obrigatórios e os tipos, as datas em `AAAA-MM-DD`, os valores de `kind`, `source`, `linkKind` e `state`, ids repetidos e se o feed encolheu mais da metade de um dia para o outro. Se falhar, nada é publicado e o feed anterior continua no ar; o GitHub avisa por e-mail.

Para conferir à mão: `python3 scripts/validate_feed.py docs/concursos.json`.

## Modo sombra: atualizações depois da inscrição

Experimento de 4 semanas (spike CA-006) para medir se dá para avisar quem acompanha um concurso quando sai o gabarito, o resultado, a homologação ou a convocação. **Nenhuma versão do app lê estes arquivos.** Eles ficam em `shadow/`, fora de `docs/`, então o GitHub Pages não os publica, e o `docs/concursos.json` não muda (o script só o lê). Como o repositório é público, `shadow/` pode ser visto no GitHub.

O workflow **Sombra - atualizações depois da inscrição** (`.github/workflows/sombra-atualizacoes.yml`) roda todo dia às 10:00 UTC, uma hora depois do feed, e faz o seguinte:

1. Roda os testes e `scripts/build_updates.py`:
   - **Índice de aberturas** (`shadow/aberturas.json`, 18 meses): junta o índice anterior com os itens do `docs/concursos.json` do dia. Cada abertura tem o mesmo `id` do item do feed, o município (`territoryId`), o tipo de órgão (`prefeitura`, `camara`, `autarquia`, `fundacao`, `instituto`, `outro`), o tipo de seleção e o número normalizado (`01/2026` e `001/2026` viram `1/2026`). O "Município de X" genérico conta como prefeitura.
   - **Atos no Querido Diário:** usa uma consulta própria (resultado, homologação, gabarito, convocação, nomeação). O tipo vem do cabeçalho do ato em caixa alta ("EDITAL DE HOMOLOGAÇÃO", "GABARITO OFICIAL PRELIMINAR") ou do verbo do ato ("Fica homologado", "CONVOCA os candidatos aprovados"). Cronogramas, comissões, estagiários, licitações, prorrogações de validade e convocações para prova ficam de fora. As repetições viram um ato por concurso, tipo e dia (Itajubá publica um termo de convocação por candidato).
   - **Atos no DOU (Seção 3):** pelo INLABS, com os mesmos secrets do feed. Sem eles, o DOU é pulado.
   - **Ligação com o concurso (`examId`):**
     - no município, só quando o município, o tipo de órgão e o número (e o tipo de seleção, quando o ato diz) apontam para uma única abertura, publicada antes do ato;
     - no DOU, quando o órgão e o número do edital de abertura citado no texto apontam para uma única abertura.

     O resto fica sem ligação: aparece no log do Actions com o motivo, mas não vai para o arquivo. Exemplos: o gabarito do SAAE de Itápolis não liga com o concurso nº 01/2026 da Câmara, e uma homologação sem número do concurso não liga com nada.
2. Valida `shadow/atualizacoes.json` com `scripts/validate_updates.py`.
3. Confere que nada fora de `shadow/` mudou e faz commit só de `shadow/`.

| Arquivo | Conteúdo |
|---|---|
| `shadow/aberturas.json` | índice de aberturas (estado do script, não é feed) |
| `shadow/atualizacoes.json` | o feed que o app leria: só atualizações ligadas, últimos 90 dias, no formato da seção 6(b) do spike (`id`, `examId`, `type`, `title`, `organization`, `edital`, `state`, `city`, `published`, `gazetteUrl`, `excerpt`, `source`) |
| `shadow/log.csv` | uma linha por execução: diários lidos, atos por tipo, ligados por tipo, não ligados, ligações novas, tamanho do índice |
| `shadow/revisao.csv` | a cada execução, até 10 ligações novas (alternando os tipos), com o concurso, a atualização, o trecho e o link do diário, e as colunas `correto` e `observacao` em branco |

Para preencher o índice com aberturas que já saíram do feed, rode o workflow uma vez à mão com `seed_days = 90`. A semente usa o próprio `extract_openings` do build_feed.

### Critério para liberar o app

Depois de 4 semanas, o app só passa a ler o arquivo se, nas revisões semanais:

- a **precisão das ligações for ≥ 90%** (`correto = sim` sobre as linhas revisadas);
- houver **≥ 3 atualizações ligadas por semana** (soma de `novos_ligados` no `log.csv`), em todas as 4 semanas.

Se passar, o arquivo vai para `docs/atualizacoes.json` com o mesmo contrato, como um arquivo separado: as versões antigas do app nunca o baixam, e o `concursos.json` não muda.

### Revisão semanal

1. Abra `shadow/revisao.csv` (no GitHub, ou baixe e abra numa planilha) e pegue as linhas da semana pela coluna `execucao`. Revise umas 30.
2. Para cada linha, abra o link da coluna `diario`, procure o trecho e confira:
   - se o ato é mesmo do tipo indicado (`tipo`);
   - se é do concurso da coluna `concurso`: mesmo órgão, mesmo número e mesmo tipo de seleção.
3. Preencha `correto` com `sim` ou `nao`. Em `observacao`, anote o motivo quando for `nao` (outro órgão, cronograma, outro concurso, tipo errado etc.).
4. Faça commit do arquivo. O workflow só acrescenta linhas no fim e não apaga o que você preencheu. Evite editar no horário em que ele roda (10:00 UTC).
5. Precisão da semana = `sim` ÷ (`sim` + `nao`). Ligações por semana = soma de `novos_ligados` no `log.csv`.

Para conferir à mão: `python3 scripts/validate_updates.py shadow/atualizacoes.json --index shadow/aberturas.json`.

### Rollback

Nada em `docs/`, no feed ou no app depende do modo sombra.

- **Pausar:** em *Settings → Secrets and variables → Actions → Variables*, crie a variável de repositório `UPDATES_SHADOW` com o valor `off`, ou desative o workflow em *Actions → Sombra - atualizações depois da inscrição → Disable workflow*. Para voltar, apague a variável ou reative o workflow.
- **Remover:** reverta o commit que trouxe o modo sombra para a `main` (`git revert -m 1 <merge>` ou, se entrou por squash, `git revert <commit>`), ou apague o workflow e os arquivos e faça commit:
  ```sh
  git rm -r .github/workflows/sombra-atualizacoes.yml shadow/ scripts/build_updates.py scripts/validate_updates.py tests/test_build_updates.py tests/fixtures/updates/
  ```
  Depois, apague esta seção do README.

## Limitações conhecidas

- **Cobertura:** só entram os municípios que o Querido Diário indexa (cerca de 500 dos 5.570) e os editais federais do DOU. Concursos estaduais ainda não entram.
- **Campos incompletos:** a extração é heurística. Quando o diário publica só um aviso resumido, os campos de prazo, salário e vagas podem sair vazios, e o app esconde campos vazios. A descrição sempre traz o trecho original e a fonte.
- **Instabilidade das fontes:** a API do Querido Diário às vezes responde 503. O script tenta de novo com espera crescente; se uma fonte falhar, ele segue com a outra e ainda tira do feed os concursos com inscrições encerradas.

## Rodar localmente

Precisa de Python 3.10 ou superior, sem nenhuma dependência.

```sh
python3 -m unittest discover -s tests -v    # testes do extrator
python3 scripts/build_feed.py --days 14     # atualiza docs/concursos.json (só municípios)
INLABS_EMAIL=... INLABS_PASSWORD=... python3 scripts/build_feed.py --days 3   # inclui o DOU
python3 scripts/build_updates.py --days 3 --seed-days 30   # modo sombra: grava só em shadow/
```

## Configuração (uma vez só)

1. **Criar o repositório no GitHub.com:**
   - Nome: `concursos-data`.
   - Visibilidade: **Public**. É isso que torna o GitHub Pages e o Actions gratuitos.
   - Não marque "Add a README".
2. **Enviar este diretório:**
   ```sh
   cd concursos-data
   git remote add origin https://github.com/<seu-usuario>/concursos-data.git
   git push -u origin main
   ```
3. **Ativar o GitHub Pages:**
   - Vá em *Settings → Pages → Build and deployment*.
   - Em *Source*, escolha **Deploy from a branch**.
   - Em *Branch*, escolha `main` e a pasta **`/docs`**, e clique em **Save**.
   - Em 1 a 2 minutos o feed fica disponível em `https://<seu-usuario>.github.io/concursos-data/concursos.json`.
4. **Dar permissão de escrita ao Actions:**
   - Vá em *Settings → Actions → General → Workflow permissions*.
   - Marque **Read and write permissions** e clique em **Save**.
5. **Rodar a primeira vez manualmente:**
   - Vá em *Actions → Atualizar concursos → Run workflow*.
   - Use `days = 30` para preencher o histórico inicial.
   - Depois disso, o workflow roda sozinho todo dia às 06:00 (horário de Brasília).
6. **Ativar o DOU (opcional, recomendado):**
   - Crie uma conta gratuita em [inlabs.in.gov.br](https://inlabs.in.gov.br) e confirme o e-mail.
   - Em *Settings → Secrets and variables → Actions → New repository secret*, crie `INLABS_EMAIL` e `INLABS_PASSWORD`.
   - Rode o workflow manualmente e confira no log as linhas `DOU <data> DO3: N abertura(s)`.
7. **Apontar o app para o feed:** confira se `AppLinks.eventsFeed` (projeto ConcursosPublicos) usa o seu usuário.

### Manutenção

- **Workflow desativado por inatividade:** o GitHub desativa workflows agendados em repositórios públicos sem atividade por 60 dias. Os commits diários do próprio feed contam como atividade. Mas, se ficar 60 dias sem nenhum concurso novo, reative em *Actions*.
- **Falhas:** o GitHub envia e-mail quando o workflow falha. Na maioria das vezes é a API fora do ar, e basta rodar de novo.
- **Falsos positivos ou negativos:** ajuste as expressões `OPENING`, `NEGATIVE` e `OFF_TOPIC` em `scripts/build_feed.py` e adicione um caso em `tests/test_build_feed.py`.

## Licença dos dados

Os diários oficiais são atos públicos, e a Lei 9.610/98, art. 8º, IV, não protege textos de atos oficiais. Os dados vêm do projeto Querido Diário; cite a fonte ao reutilizar.
