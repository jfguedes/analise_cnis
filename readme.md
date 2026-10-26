# Análise de aposentadoria a partir do CNIS

O script `cnis_aposentadoria.py` lê o PDF do CNIS (Extrato Previdenciário baixado no Meu INSS) e simula a aposentadoria no INSS. Ele calcula o tempo de contribuição e a carência, verifica quando cada regra da Reforma da Previdência (EC 103/2019) fica cumprida e estima o valor do benefício em reais de hoje.

É uma simulação para planejamento. Não substitui a análise do INSS ou de um advogado previdenciarista.

**Atenção** Esta ferramenta foi feita em um dia com a ajuda do Claudinho (Claude Code). Não sou advogado nem especialista em previdência, e tinha pouquíssimos exemplos para estudar e validar os resultados. Veja a seção de limitações abaixo, mas considere que certamente existem diversos casos que esse script não deve considerar, e alguns erros também!!

## Instalação

Precisa do Python 3.10 ou mais recente. Para conferir a versão:

```sh
python3 --version   # macOS e Linux
py --version        # Windows
```

Se não tiver o Python, baixe em [python.org](https://www.python.org/downloads/). No Windows, marque a opção "Add python.exe to PATH" durante a instalação.

A pasta do projeto precisa ter estes arquivos:

- `cnis_aposentadoria.py`: o script.
- `requirements.txt`: lista das bibliotecas.
- `cnis_config.json`: cenários de contribuição usados por `--cenarios`. Opcional; sem ele o script usa um padrão embutido.

### 1. Criar o ambiente virtual

O ambiente virtual é uma pasta `.venv` com um Python separado, onde as bibliotecas ficam instaladas sem mexer no resto do sistema. Crie uma vez, dentro da pasta do projeto.

macOS e Linux:

```sh
cd caminho/da/pasta
python3 -m venv .venv
```

Windows (PowerShell):

```powershell
cd caminho\da\pasta
py -m venv .venv
```

### 2. Ativar o ambiente

Ative em cada terminal novo, antes de rodar o script.

macOS e Linux:

```sh
source .venv/bin/activate
```

Windows (PowerShell):

```powershell
.venv\Scripts\Activate.ps1
```

Se o PowerShell bloquear o script de ativação, libere uma vez com `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` e tente de novo. No Prompt de Comando (cmd), use `.venv\Scripts\activate.bat`.

O prompt passa a mostrar `(.venv)`. Com o ambiente ativo, `python` e `pip` apontam para o Python do ambiente, em qualquer sistema. Para sair:

```sh
deactivate
```

### 3. Instalar as bibliotecas

Com o ambiente ativo:

```sh
python -m pip install -r requirements.txt
```

Isso instala o `pdfplumber`, que lê o PDF, e o `requests`, que baixa o índice INPC.

### 4. Conferir

```sh
python cnis_aposentadoria.py --help
```

Se aparecer a ajuda, está tudo pronto. Se aparecer "Falta a biblioteca pdfplumber", o ambiente não está ativo ou o passo 3 não foi feito.

### Usar no VSCode

Rode "Python: Select Interpreter" na paleta de comandos e escolha o Python da pasta `.venv`. Assim o terminal integrado e o botão de executar usam o ambiente certo.

## Como rodar

Com o ambiente ativado:

```sh
python cnis_aposentadoria.py CAMINHO_DO_CNIS.pdf --sexo M|F [opções]
```

Para obter o PDF, entre no Meu INSS e baixe o Extrato Previdenciário (CNIS) completo, com as remunerações. O CNIS não informa o sexo, por isso `--sexo` é obrigatório. A data de nascimento é lida do PDF.

### Exemplos

```sh
# análise padrão: projeta contribuições se houver recolhimento nos últimos 6 meses
python cnis_aposentadoria.py CNIS.pdf --sexo M

# sem novas contribuições
python cnis_aposentadoria.py CNIS.pdf --sexo M --parar

# contribuindo sobre R$ 4.500 até os 62 anos
python cnis_aposentadoria.py CNIS.pdf --sexo F --salario-futuro 4500 --ate-idade 62

# compara cenários de contribuição (custo x benefício), definidos em cnis_config.json
python cnis_aposentadoria.py CNIS.pdf --sexo F --cenarios

# salva o relatório em arquivo, sem códigos de cor
python cnis_aposentadoria.py CNIS.pdf --sexo M --cenarios --saida relatorio.txt
```

### Opções

| Opção | O que faz |
|---|---|
| `cnis` | Caminho do PDF do CNIS. Obrigatório. |
| `--sexo M\|F` | Sexo do segurado. Obrigatório. |
| `--nascimento dd/mm/aaaa` | Data de nascimento, se o PDF não trouxer. |
| `--salario-futuro VALOR` | Salário de contribuição projetado, em reais de hoje. Padrão: média dos últimos 12 meses. Ativa a projeção de contribuições. |
| `--continuar` | Projeta contribuições futuras mesmo que a pessoa tenha parado de contribuir. |
| `--parar` | Não projeta novas contribuições. Não pode ser usada com `--continuar`. |
| `--ate-idade ANOS` | Contribui até essa idade e para. |
| `--es ANOS` | Expectativa de sobrevida usada no fator previdenciário das regras futuras. Substitui a tábua IBGE 2024 embutida. |
| `--licenca-sem-remuneracao` | Trata meses sem remuneração dentro de um vínculo como licença não remunerada. Esses meses deixam de contar tempo e carência. |
| `--cenarios` | Simula os cenários do arquivo de configuração e compara contribuições pagas com benefício recebido. |
| `--config ARQUIVO` | Arquivo JSON de configuração dos cenários. Padrão: `cnis_config.json` na pasta atual ou na pasta do script. |
| `--inpc-csv ARQUIVO` | Série do INPC em arquivo, no formato `MM/AAAA;variação%`, para usar sem internet. |
| `--atualizar-inpc` | Baixa o INPC de novo, mesmo com o cache local em dia. |
| `--saida ARQUIVO` | Salva o relatório em arquivo de texto, além de mostrar na tela. O arquivo nunca leva códigos de cor. |
| `--debug` | Salva o texto extraído do PDF num `.txt` ao lado do PDF, para conferir a leitura. |
| `--cores auto\|sempre\|nunca` | Cores na saída. `auto`, o padrão, só usa cores no terminal e respeita a variável `NO_COLOR`. |

Para ver a ajuda completa:

```sh
python cnis_aposentadoria.py --help
```

## Como ler a saída

O relatório mostra os vínculos lidos do CNIS, um resumo de tempo e carência, as premissas da projeção, o resultado de cada regra e os avisos.

Os avisos vêm ordenados por gravidade:

- **`[!]` em vermelho:** situação que pode mudar o direito ou o valor. Em geral é algo que o INSS hoje provavelmente não conta.
- **`[*]` em amarelo:** ponto que merece conferência, como uma pendência no CNIS ou uma premissa do cálculo.
- **`*` sem cor:** informação.

No modo `--cenarios`, cada cenário é comparado com o de "sem novas contribuições". A comparação usa uma taxa de juros real que fica no arquivo de configuração. "Se paga a partir dos" indica a idade em que o benefício a mais recupera o que foi pago a mais, em valor presente.

## Arquivo de configuração

O `cnis_config.json` define os cenários e as premissas da comparação:

| Campo | Significado |
|---|---|
| `taxa_desconto_real_aa` | Taxa de juros real, acima da inflação, em % ao ano. Padrão: 5. |
| `idade_final_recebimento` | Idade até a qual o benefício é recebido. `null` usa a expectativa de vida do IBGE. |
| `pagamentos_por_ano` | 13 inclui o 13º salário. |
| `cenarios` | Lista de cenários. `salario` aceita `null` (não contribui), `"minimo"`, `"teto"` ou um valor em reais. `aliquota` é 0.20 no plano normal ou 0.11 no plano simplificado. |

Chaves que começam com `_` são comentários.

## INPC

Os salários são corrigidos pelo INPC. A série é baixada do IBGE, ou do Banco Central se o IBGE falhar, e guardada em `inpc_cache.json`. O script só baixa de novo quando o cache estiver desatualizado, no máximo uma vez a cada 24 horas. Sem internet, ele usa o cache existente.

## Limitações

- Não trata tempo especial, professor, pessoa com deficiência, trabalhador rural, aposentadoria proporcional nem regimes próprios.
- As datas de cumprimento das regras são calculadas por dia, mas a contagem de tempo após 11/2019 é feita por mês.
- O valor do auxílio-doença intercalado não aparece no CNIS e fica fora da média.
- Os cenários não consideram imposto de renda, ganho real do salário mínimo nem a cobertura de incapacidade e pensão enquanto a pessoa contribui.
