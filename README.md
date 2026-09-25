# concursos-data

Feed JSON de concursos públicos municipais com inscrições abertas, usado pelo app iOS **ConcursosPublicos**.

- **Fonte:** diários oficiais municipais, pela API pública do [Querido Diário](https://queridodiario.ok.org.br) (Open Knowledge Brasil).
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
   - o link da banca
4. Por fim, junta com o `docs/concursos.json` anterior, remove duplicados e tira do feed:
   - os concursos com inscrições encerradas
   - os concursos sem prazo identificado publicados há mais de 45 dias

Cada item segue o formato do `EventModel` do app. Os campos extras (`city`, `published`, `registrationEnds`, `gazetteUrl`) são ignorados pelo app.

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

## Limitações conhecidas

- **Cobertura:** só entram os municípios que o Querido Diário indexa. Concursos estaduais e federais ainda não entram (o próximo passo seria o DOU).
- **Campos incompletos:** a extração é heurística. Quando o diário publica só um aviso resumido, os campos de prazo, salário e vagas podem sair vazios, e o app esconde campos vazios. A descrição sempre traz o trecho original e a fonte.
- **Instabilidade da API:** a API às vezes responde 503. O script tenta de novo com espera crescente e, se falhar, o workflow falha sem apagar o feed publicado.

## Rodar localmente

Precisa de Python 3.10 ou superior, sem nenhuma dependência.

```sh
python3 -m unittest discover -s tests -v    # testes do extrator
python3 scripts/build_feed.py --days 14     # atualiza docs/concursos.json
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
6. **Apontar o app para o feed:** confira se a URL em `EventListingService.swift` (projeto ConcursosPublicos) usa o seu usuário.

### Manutenção

- **Workflow desativado por inatividade:** o GitHub desativa workflows agendados em repositórios públicos sem atividade por 60 dias. Os commits diários do próprio feed contam como atividade. Mas, se ficar 60 dias sem nenhum concurso novo, reative em *Actions*.
- **Falhas:** o GitHub envia e-mail quando o workflow falha. Na maioria das vezes é a API fora do ar, e basta rodar de novo.
- **Falsos positivos ou negativos:** ajuste as expressões `OPENING`, `NEGATIVE` e `OFF_TOPIC` em `scripts/build_feed.py` e adicione um caso em `tests/test_build_feed.py`.

## Licença dos dados

Os diários oficiais são atos públicos, e a Lei 9.610/98, art. 8º, IV, não protege textos de atos oficiais. Os dados vêm do projeto Querido Diário; cite a fonte ao reutilizar.
