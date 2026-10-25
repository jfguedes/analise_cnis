#!/usr/bin/env python3
"""
cnis_aposentadoria.py - Analise de perspectivas de aposentadoria no RGPS (INSS)
a partir do PDF do CNIS ("Extrato Previdenciario" baixado no Meu INSS).

O que faz
  1. Le o PDF do CNIS: dados pessoais, vinculos, remuneracoes e contribuicoes.
  2. Calcula tempo de contribuicao e carencia (hoje e em 13/11/2019).
  3. Verifica, mes a mes, quando cada regra fica cumprida:
       - Direito adquirido ate 13/11/2019 (tempo de contribuicao e idade)
       - Transicao por pontos (EC 103, art. 15)
       - Transicao por idade minima progressiva (art. 16)
       - Transicao com pedagio de 50% (art. 17)
       - Transicao da aposentadoria por idade (art. 18)
       - Transicao com pedagio de 100% (art. 20)
       - Regra permanente (art. 19), para quem filiou apos a reforma
  4. Estima o valor de cada beneficio em moeda de hoje (media corrigida pelo
     INPC, coeficientes, fator previdenciario e descarte de contribuicoes).

Uso
  pip install pdfplumber requests
  python cnis_aposentadoria.py CNIS.pdf --sexo M
  python cnis_aposentadoria.py CNIS.pdf --sexo F --salario-futuro 4500
  python cnis_aposentadoria.py CNIS.pdf --sexo F --parar        (sem novas contribuicoes)
  python cnis_aposentadoria.py CNIS.pdf --sexo M --saida relatorio.txt
  python cnis_aposentadoria.py CNIS.pdf --sexo M --cenarios     (compara cenarios; ver cnis_config.json)
  python cnis_aposentadoria.py CNIS.pdf --sexo M --cores nunca  (sem cores; padrao: cores so no terminal)

Avisos na saida
  [!] vermelho: situacao que pode mudar o direito ou o valor (o INSS hoje provavelmente nao conta)
  [*] amarelo:  ponto que merece conferencia (pendencia, premissa do calculo)

Limites (leia antes de confiar no numero)
  - Nao converte tempo especial, nao trata professor, PcD, rural, RPPS nem
    aposentadoria proporcional pre-1998. Se houver, o resultado sera conservador.
  - Datas com precisao de mes. Indicadores de pendencia (PEXT, PREM-EXT etc.)
    podem fazer o INSS desconsiderar periodos ate serem acertados.
  - Projecao futura em moeda de hoje (sem inflacao), com o salario informado.
  - E uma simulacao para planejamento, nao substitui a analise do INSS ou de
    um advogado previdenciarista.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import re
import sys
import textwrap
from collections import defaultdict
from pathlib import Path

try:
    import pdfplumber
except ImportError:
    sys.exit("Falta a biblioteca pdfplumber. Instale com:  pip install pdfplumber requests")

REFORMA = dt.date(2019, 11, 13)
INICIO_PBC = (1994, 7)  # periodo basico de calculo comeca em 07/1994

# Teto do RGPS (vigencia: ano, mes -> valor). Usado para limitar remuneracoes.
TETO = [
    ((1994, 7), 582.86), ((1995, 5), 832.66), ((1996, 5), 957.56), ((1997, 6), 1031.87),
    ((1998, 6), 1081.50), ((1998, 12), 1200.00), ((1999, 6), 1255.32), ((2000, 6), 1328.25),
    ((2001, 6), 1430.00), ((2002, 6), 1561.56), ((2003, 6), 1869.34), ((2004, 1), 2400.00),
    ((2004, 5), 2508.72), ((2005, 5), 2668.15), ((2006, 4), 2801.56), ((2006, 8), 2801.82),
    ((2007, 4), 2894.28), ((2008, 3), 3038.99), ((2009, 2), 3218.90), ((2010, 1), 3467.40),
    ((2011, 1), 3691.74), ((2012, 1), 3916.20), ((2013, 1), 4159.00), ((2014, 1), 4390.24),
    ((2015, 1), 4663.75), ((2016, 1), 5189.82), ((2017, 1), 5531.31), ((2018, 1), 5645.80),
    ((2019, 1), 5839.45), ((2020, 1), 6101.06), ((2021, 1), 6433.57), ((2022, 1), 7087.22),
    ((2023, 1), 7507.49), ((2024, 1), 7786.02), ((2025, 1), 8157.41), ((2026, 1), 8475.55),
]
# Salario minimo (vigencia). Competencia abaixo do minimo nao conta: desde 07/1994 para
# recolhimentos proprios (contribuinte individual / facultativo) e, apos a reforma, para todos.
SAL_MIN = [
    ((1994, 7), 64.79), ((1994, 9), 70.00), ((1995, 5), 100.00), ((1996, 5), 112.00), ((1997, 5), 120.00),
    ((1998, 5), 130.00), ((1999, 5), 136.00), ((2000, 4), 151.00), ((2001, 4), 180.00), ((2002, 4), 200.00),
    ((2003, 4), 240.00), ((2004, 5), 260.00), ((2005, 5), 300.00), ((2006, 4), 350.00), ((2007, 4), 380.00),
    ((2008, 3), 415.00), ((2009, 2), 465.00), ((2010, 1), 510.00), ((2011, 1), 540.00), ((2011, 3), 545.00),
    ((2012, 1), 622.00), ((2013, 1), 678.00), ((2014, 1), 724.00), ((2015, 1), 788.00), ((2016, 1), 880.00),
    ((2017, 1), 937.00), ((2018, 1), 954.00), ((2019, 1), 998.00), ((2020, 1), 1039.00), ((2020, 2), 1045.00), ((2021, 1), 1100.00),
    ((2022, 1), 1212.00), ((2023, 1), 1302.00), ((2023, 5), 1320.00), ((2024, 1), 1412.00),
    ((2025, 1), 1518.00), ((2026, 1), 1621.00),
]
# Expectativa de sobrevida, ambos os sexos - Tabua IBGE 2024 (usada no fator
# previdenciario para beneficios com inicio de dez/2025 a nov/2026).
ES_IBGE_2024 = {
    40: 39.8, 41: 38.9, 42: 38.0, 43: 37.1, 44: 36.2, 45: 35.3, 46: 34.4, 47: 33.5,
    48: 32.6, 49: 31.8, 50: 30.9, 51: 30.0, 52: 29.2, 53: 28.3, 54: 27.5, 55: 26.6,
    56: 25.8, 57: 25.0, 58: 24.2, 59: 23.4, 60: 22.6, 61: 21.8, 62: 21.0, 63: 20.3,
    64: 19.5, 65: 18.8, 66: 18.0, 67: 17.3, 68: 16.6, 69: 15.9, 70: 15.2, 71: 14.6,
    72: 13.9, 73: 13.2, 74: 12.5, 75: 11.9, 76: 11.3, 77: 10.7, 78: 10.1, 79: 9.5, 80: 9.0,
}
# Tabua IBGE 2017 (publicada em 29/11/2018): vigente em 13/11/2019, usada no direito adquirido.
ES_IBGE_2017 = {
    40: 39.3, 41: 38.4, 42: 37.5, 43: 36.6, 44: 35.7, 45: 34.8, 46: 34.0, 47: 33.1, 48: 32.2, 49: 31.4,
    50: 30.5, 51: 29.7, 52: 28.8, 53: 28.0, 54: 27.2, 55: 26.4, 56: 25.6, 57: 24.8, 58: 24.0, 59: 23.2,
    60: 22.4, 61: 21.6, 62: 20.9, 63: 20.1, 64: 19.4, 65: 18.7, 66: 18.0, 67: 17.3, 68: 16.6, 69: 15.9,
    70: 15.2, 71: 14.6, 72: 14.0, 73: 13.3, 74: 12.7, 75: 12.2, 76: 11.6, 77: 11.1, 78: 10.5, 79: 10.0, 80: 9.6,
}
# Especies de beneficio: incapacidade (contam tempo e carencia se intercaladas com contribuicoes,
# Lei 8.213 art. 55 II e STF Tema 1.125) e salario-maternidade (conta sempre).
ESPECIES_INCAPACIDADE = {31, 32, 91, 92}
ESPECIE_MATERNIDADE = 80
# Indicadores de pendencia mais comuns (legenda do CNIS)
DESC_IND = {
    "PEXT": "vinculo com informacao extemporanea, passivel de comprovacao",
    "PREM-EXT": "remuneracao informada fora do prazo, passivel de comprovacao",
    "PREC-MENOR-MIN": "recolhimento abaixo do valor minimo",
    "PREC-FACULTCONC": "facultativo concomitante com vinculo obrigatorio",
    "PREM-FVIN": "remuneracao apos o fim do vinculo",
    "PREM-BLOQ-EC103": "remuneracao bloqueada para ajuste entre competencias (EC 103)",
    "PREC-FBR": "facultativo baixa renda ainda nao validado (CadUnico)",
    "PADM-EMPR": "admissao anterior ao inicio da atividade do empregador",
}
# indicadores que ja geram aviso proprio (nao repetir na lista de pendencias)
IND_COM_AVISO = {"PREC-MENOR-MIN", "PREC-FACULTCONC", "PREM-FVIN", "PREM-BLOQ-EC103"}

DATA_RE = r"\d{2}/\d{2}/\d{4}"
VALOR_RE = r"\d{1,3}(?:\.\d{3})*,\d{2}"
INDICADOR_RE = re.compile(
    r"\b(PEXT|P(?:REC|REM|VIN|ADM|RPPS|SC|MEN)[A-Z0-9\-]*|I(?:REC|REM|VIN|CON)[A-Z0-9\-]+|A(?:VRC|EXT|DM|CRE)[A-Z0-9\-]*)\b"
)


# ----------------------------------------------------------------------------- utilidades
def vigente(tabela, ym):
    valor = tabela[0][1]
    for inicio, v in tabela:
        if inicio <= ym:
            valor = v
    return valor


def money(txt: str) -> float:
    return float(txt.replace(".", "").replace(",", "."))


def brl(v: float) -> str:
    if abs(v) < 0.005:
        v = 0.0
    s = f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {s}"


def parse_data(txt):
    return dt.datetime.strptime(txt, "%d/%m/%Y").date() if txt else None


def ym_add(ym, n):
    y, m = ym
    t = y * 12 + (m - 1) + n
    return (t // 12, t % 12 + 1)


def ym_range(a, b):
    """Meses de a ate b, inclusive."""
    cur = a
    while cur <= b:
        yield cur
        cur = ym_add(cur, 1)


def fim_do_mes(ym):
    prox = ym_add(ym, 1)
    return dt.date(prox[0], prox[1], 1) - dt.timedelta(days=1)


def idade_em(nasc: dt.date, d: dt.date) -> float:
    anos = d.year - nasc.year - ((d.month, d.day) < (nasc.month, nasc.day))
    ultimo = nasc.replace(year=nasc.year + anos) if not (nasc.month == 2 and nasc.day == 29) else dt.date(nasc.year + anos, 3, 1)
    return anos + (d - ultimo).days / 365.25


def fmt_tempo(anos: float) -> str:
    dias = round(max(anos, 0) * 365.25)
    a = int(dias // 365.25)
    resto = dias - a * 365.25
    m = int(resto // 30.4375)
    d = int(round(resto - m * 30.4375))
    if d >= 30:
        m, d = m + 1, 0
    if m >= 12:
        a, m = a + 1, 0
    return f"{a}a {m}m {d}d"


def fmt_idade(anos: float) -> str:
    a = int(anos)
    m = int((anos - a) * 12 + 1e-9)
    return f"{a}a {m}m"


def meses_entre(a, b):
    return (b[0] - a[0]) * 12 + (b[1] - a[1])


def fmt_meses(meses, max_faixas=6) -> str:
    """Lista de competencias em faixas: '11/2005 a 07/2008, 09/2008'."""
    faixas = []
    for ym in sorted(set(meses)):
        if faixas and meses_entre(faixas[-1][1], ym) == 1:
            faixas[-1][1] = ym
        else:
            faixas.append([ym, ym])
    txt = [f"{a[1]:02d}/{a[0]}" + (f" a {b[1]:02d}/{b[0]}" if b != a else "") for a, b in faixas[:max_faixas]]
    return ", ".join(txt) + (" ..." if len(faixas) > max_faixas else "")


# ----------------------------------------------------------------------------- cores e avisos
class Cor:
    ativo = False
    CODIGOS = {"negrito": "1", "vermelho": "31", "verde": "32", "amarelo": "33", "azul": "34",
               "ciano": "36", "cinza": "90"}


ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def cor(txt: str, *estilos) -> str:
    if not Cor.ativo or not estilos:
        return txt
    return "\x1b[" + ";".join(Cor.CODIGOS[e] for e in estilos) + "m" + txt + "\x1b[0m"


ALERTA, ATENCAO, INFO = "alerta", "atencao", "info"
_ORDEM_NIVEL = {ALERTA: 0, ATENCAO: 1, INFO: 2}


def aviso(avisos: list, texto: str, nivel: str = ATENCAO):
    avisos.append((nivel, texto))


def fmt_avisos(avisos) -> list[str]:
    """Avisos ordenados por gravidade, quebrados em linhas e coloridos."""
    linhas = []
    for nivel, texto in sorted(avisos, key=lambda a: _ORDEM_NIVEL[a[0]]):
        marca = {ALERTA: "[!]", ATENCAO: "[*]", INFO: " * "}[nivel]
        estilo = {ALERTA: ("vermelho", "negrito"), ATENCAO: ("amarelo",), INFO: ()}[nivel]
        for l in textwrap.wrap(texto, width=74, initial_indent=f"  {marca} ", subsequent_indent="      "):
            linhas.append(cor(l, *estilo))
    return linhas


# ----------------------------------------------------------------------------- leitura do CNIS
def classificar(desc: str) -> str:
    d = desc.lower()
    if "benef" in d:
        return "BEN"
    # "RECOLHIMENTO Facultativo": testar facultativo antes de "recolhimento"
    if "facultativo" in d:
        return "FAC"
    if ("contribuinte individual" in d or "recolhimento" in d or "mei" in d.split()
            or re.search(r"aut[oô]nomo|empres[aá]rio|empregador", d)):
        return "CI"
    if "doméstic" in d or "domestic" in d:
        return "DOM"
    return "EMP"


MESES_ABREV = ["Jan", "Fev", "Mar", "Abr", "Mai", "Jun", "Jul", "Ago", "Set", "Out", "Nov", "Dez"]


def ler_consolidados(pdf) -> dict:
    """Tabela 'Valores Consolidados por Ano Civil' (competencias pos-reforma): {(ano, mes): valor}.
    A extracao de texto perde as celulas vazias, entao o mes e obtido pela posicao horizontal."""
    dados = {}
    for pg in pdf.pages:
        if "Valores Consolidados" not in (pg.extract_text() or ""):
            continue
        ws = pg.extract_words()
        tit = [w for w in ws if w["text"] == "Consolidados"]
        if not tit:
            continue
        cab = [w for w in ws if w["text"] in MESES_ABREV and w["top"] > tit[0]["top"]][:12]
        if len(cab) < 12:
            continue
        colunas = [((w["x0"] + w["x1"]) / 2, MESES_ABREV.index(w["text"]) + 1) for w in cab]
        leg = [w["top"] for w in ws if w["text"] == "Legenda" and w["top"] > cab[0]["top"]]
        limite = min(leg) if leg else float("inf")
        linhas = defaultdict(list)
        for w in ws:
            if cab[0]["top"] + 2 < w["top"] < limite:
                linhas[round(w["top"])].append(w)
        for _, ws_l in sorted(linhas.items()):
            ws_l.sort(key=lambda w: w["x0"])
            if not re.fullmatch(r"\d{4}", ws_l[0]["text"]):
                continue
            ano = int(ws_l[0]["text"])
            for w in ws_l[1:]:
                if re.fullmatch(VALOR_RE, w["text"]):
                    x = (w["x0"] + w["x1"]) / 2
                    mes = min(colunas, key=lambda c: abs(c[0] - x))[1]
                    dados[(ano, mes)] = money(w["text"])
    return dados


IND_GRUPO = r"((?:[ \t]+[A-Z][A-Z0-9\-]*,?)*)"   # indicadores apos o valor da competencia


def ler_cnis(caminho: str, debug: bool = False) -> dict:
    with pdfplumber.open(caminho) as pdf:
        texto = "\n".join((p.extract_text() or "") for p in pdf.pages)
        consolidados = ler_consolidados(pdf)
    if debug:
        Path(caminho).with_suffix(".txt").write_text(texto, encoding="utf-8")
    if len(texto.strip()) < 50:
        sys.exit("O PDF nao tem texto extraivel (pode ser imagem escaneada). Baixe o CNIS pelo Meu INSS.")

    info = {"nome": None, "nascimento": None, "vinculos": [], "consolidados": consolidados}
    # o que vem depois da tabela de valores consolidados / legenda nao pertence a nenhum vinculo
    m = re.search(r"Valores Consolidados por Ano Civil|Legenda de Indicadores", texto)
    texto_vinc = texto[:m.start()] if m else texto
    m = re.search(r"Data de [Nn]ascimento:?\s*(" + DATA_RE + ")", texto)
    if m:
        info["nascimento"] = parse_data(m.group(1))
    m = re.search(r"\bNome:\s*(.+?)(?:\s{2,}|\s+Data de|\s+Nome da|\s+CPF|\n)", texto)
    if m:
        info["nome"] = m.group(1).strip()

    linhas = texto_vinc.splitlines()
    cab = [i for i, l in enumerate(linhas) if re.match(r"\s*Seq\.?\s", l)]
    for k, i0 in enumerate(cab):
        i1 = cab[k + 1] if k + 1 < len(cab) else len(linhas)
        bloco = linhas[i0 + 1:i1]
        desc_linhas, resto = [], []
        alvo = desc_linhas
        for l in bloco:
            if alvo is desc_linhas and re.search(r"Remunera|Contribui[cç][oõ]es|Compet[eê]ncia", l):
                alvo = resto
            alvo.append(l)
        desc = " ".join(desc_linhas)
        mseq = re.match(r"\s*(\d+)\s", desc)
        datas = re.findall(DATA_RE, desc)
        v = {
            "seq": int(mseq.group(1)) if mseq else len(info["vinculos"]) + 1,
            "tipo": classificar(desc),
            "origem": re.sub(r"\s+", " ", desc)[:90],
            "inicio": parse_data(datas[0]) if datas else None,
            "fim": parse_data(datas[1]) if len(datas) > 1 else None,
            "comp": {},
            "pg": {},             # data de pagamento (recolhimentos proprios)
            "ind_comp": defaultdict(set),  # indicadores por competencia
            "simpl": set(),       # competencias no plano simplificado (LC 123 / MEI / baixa renda)
            "indicadores": set(),
            "especie": None,      # beneficios: codigo da especie (31, 32, 80, 91 ...)
        }
        if v["tipo"] == "BEN":
            me = re.search(r"\b(\d{2})\s*-\s*[A-ZÀ-Ú]", desc)
            v["especie"] = int(me.group(1)) if me else None
        corpo = "\n".join(resto)

        def guarda(mm, aa, val, ind):
            ym = (int(aa), int(mm))
            v["comp"][ym] = v["comp"].get(ym, 0) + money(val)
            v["ind_comp"][ym] |= set(INDICADOR_RE.findall(ind))
            return ym

        # contribuinte individual / facultativo: competencia, data pgto, contribuicao, salario
        for mm, aa, pg, _contrib, sal, ind in re.findall(
            r"(?<![\d/])(\d{2})/(\d{4})\s+(" + DATA_RE + r")\s+(" + VALOR_RE + r")\s+(" + VALOR_RE + ")" + IND_GRUPO,
            corpo
        ):
            ym = guarda(mm, aa, sal, ind)
            pago = parse_data(pg)
            v["pg"][ym] = min(v["pg"].get(ym, pago), pago)
            if "LC123" in ind or "IREC-MEI" in ind or "FBR" in ind:
                v["simpl"].add(ym)
        if not v["comp"]:
            # cooperativas / contratantes: competencia, CNPJ contratante, [CNPJ/CEI tomador], forma, remuneracao
            for mm, aa, val, ind in re.findall(
                r"(?m)^\s*(\d{2})/(\d{4})\s+(?:[\d./-]{14,}\s+){1,2}[^\d\n]*?(" + VALOR_RE + ")" + IND_GRUPO, corpo
            ):
                guarda(mm, aa, val, ind)
        if not v["comp"]:
            for mm, aa, val, ind in re.findall(r"(?<![\d/])(\d{2})/(\d{4})\s+(" + VALOR_RE + ")" + IND_GRUPO, corpo):
                guarda(mm, aa, val, ind)
        ind = set(INDICADOR_RE.findall(corpo)) | set(INDICADOR_RE.findall(desc[-80:]))
        # indicador quebrado entre linhas ("PREM-" ... "BLOQ-EC103"): descarta os pedacos e remonta
        v["indicadores"] = {i for i in ind if not i.endswith("-") and i != "PREM"}
        if re.search(r"(?m)^\s*BLOQ-EC103\b", corpo):
            v["indicadores"].add("PREM-BLOQ-EC103")
        if v["inicio"] or v["comp"]:
            info["vinculos"].append(v)
    return info


# ----------------------------------------------------------------------------- INPC
INPC_CACHE = Path(__file__).resolve().with_name("inpc_cache.json")
INPC_RECHECAR_HORAS = 24  # se o cache estiver atrasado, tenta baixar de novo no maximo a cada 24h


def ultimo_inpc_esperado(hoje: dt.date):
    """Ultima competencia que ja deve estar publicada (o IBGE divulga o INPC do mes ~dia 10 do mes seguinte)."""
    return ym_add((hoje.year, hoje.month), -1 if hoje.day >= 12 else -2)


def ler_cache_inpc():
    """Retorna (dados, verificado_em, fonte) ou None. Aceita o formato antigo (dict plano, sem metadados)."""
    if not INPC_CACHE.exists():
        return None
    try:
        raw = json.loads(INPC_CACHE.read_text())
        if "dados" in raw:
            verificado = dt.datetime.fromisoformat(raw["verificado_em"])
            fonte, raw = raw.get("fonte", "?"), raw["dados"]
        else:
            verificado = dt.datetime.fromtimestamp(INPC_CACHE.stat().st_mtime)
            fonte = "?"
        dados = {(int(k[:4]), int(k[5:])): v for k, v in raw.items()}
        return (dados, verificado, fonte) if dados else None
    except (ValueError, KeyError, OSError):
        return None


def baixar_inpc():
    """Baixa a serie mensal do INPC (%). Tenta o IBGE (1 requisicao) e, se falhar, o BCB."""
    import requests
    # o firewall do BCB rejeita requisicoes sem User-Agent de navegador (e as vezes mesmo com ele)
    headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                             "(KHTML, like Gecko) Chrome/130.0 Safari/537.36",
               "Accept": "application/json"}

    def baixar_ibge():
        # SIDRA tabela 1736, variavel 44 = INPC variacao mensal (%)
        r = requests.get("https://apisidra.ibge.gov.br/values/t/1736/n1/all/v/44/p/all/f/c",
                         headers=headers, timeout=60)
        r.raise_for_status()
        dados = {}
        for item in r.json()[1:]:  # primeira linha e o cabecalho
            per, v = item["D3C"], item["V"]
            if int(per[:4]) >= 1994 and re.fullmatch(r"-?[\d.]+", v):
                dados[(int(per[:4]), int(per[4:]))] = float(v)
        return dados

    def baixar_bcb():
        dados = {}
        hoje = dt.date.today()
        for ini in range(1994, hoje.year + 1, 10):
            fim = min(ini + 9, hoje.year)
            url = (
                "https://api.bcb.gov.br/dados/serie/bcdata.sgs.188/dados?formato=json"
                f"&dataInicial=01/01/{ini}&dataFinal=31/12/{fim}"
            )
            r = requests.get(url, headers=headers, timeout=30)
            r.raise_for_status()
            for item in r.json():  # pagina de bloqueio vem em HTML -> JSONDecodeError
                d = parse_data(item["data"])
                dados[(d.year, d.month)] = float(item["valor"])
        return dados

    try:
        dados, fonte = baixar_ibge(), "IBGE (SIDRA 1736)"
    except Exception as e_ibge:
        dados = baixar_bcb()
        fonte = f"Banco Central (SGS 188) - IBGE falhou: {e_ibge.__class__.__name__}"
    if not dados:
        raise ValueError("serie INPC vazia")
    return dados, fonte


def carregar_inpc(arq: str | None, forcar: bool = False):
    """Retorna ({(ano, mes): variacao_percentual}, descricao_da_fonte).

    Usa o cache local enquanto ele tiver a ultima competencia ja publicada; so baixa
    se estiver desatualizado (no maximo uma tentativa a cada INPC_RECHECAR_HORAS) ou com forcar=True.
    """
    if arq:
        dados = {}
        for l in Path(arq).read_text(encoding="utf-8").splitlines():
            m = re.match(r"\s*(?:\d{2}/)?(\d{2})/(\d{4})\s*[;,\t]\s*(-?[\d.,]+)", l)
            if m:
                dados[(int(m.group(2)), int(m.group(1)))] = float(m.group(3).replace(",", "."))
        return dados, f"arquivo {arq}"

    agora = dt.datetime.now()
    cache = ler_cache_inpc()
    if cache and not forcar:
        dados, verificado, fonte = cache
        em_dia = max(dados) >= ultimo_inpc_esperado(agora.date())
        recente = agora - verificado < dt.timedelta(hours=INPC_RECHECAR_HORAS)
        if em_dia or recente:
            return dados, f"{fonte}, cache de {verificado:%d/%m/%Y}"
    try:
        dados, fonte = baixar_inpc()
    except Exception as e:  # sem internet: usa o cache, mesmo desatualizado
        if cache:
            return cache[0], f"{cache[2]}, cache de {cache[1]:%d/%m/%Y} (falha ao atualizar: {e.__class__.__name__})"
        return None, f"indisponivel ({e.__class__.__name__})"
    if cache and max(cache[0]) > max(dados):  # nunca troca por uma serie mais curta
        dados = {**dados, **cache[0]}
    INPC_CACHE.parent.mkdir(parents=True, exist_ok=True)
    INPC_CACHE.write_text(json.dumps({
        "verificado_em": agora.isoformat(timespec="seconds"),
        "fonte": fonte,
        "dados": {f"{y}-{m:02d}": v for (y, m), v in sorted(dados.items())},
    }))
    return dados, f"{fonte}, baixado agora"


def fatores_correcao(inpc, ref):
    """fator[m] = INPC acumulado da competencia m ate o mes anterior a ref."""
    fat = {}
    acc = 1.0
    cur = ym_add(ref, -1)
    while cur >= INICIO_PBC:
        if inpc:
            acc *= 1 + inpc.get(cur, 0.0) / 100
        fat[cur] = acc
        cur = ym_add(cur, -1)
    return fat


# ----------------------------------------------------------------------------- linha do tempo
class Historico:
    """Por competencia: tempo (anos), carencia (0/1) e salario de contribuicao."""

    def __init__(self, cnis, avisos, licenca=False):
        self.tempo = defaultdict(float)
        self.carencia = {}
        self.salario = {}
        self.avisos = avisos
        dias_pre = set()          # dias cobertos ate 30/11/2019 (contagem por dia)
        cob_emp = set()           # meses com vinculo de empregado/domestico ativo
        sem_rem = set()           # meses de vinculo sem remuneracao informada
        licenca_meses = set()     # meses tratados como licenca nao remunerada (--licenca-sem-remuneracao)
        soma = defaultdict(float)
        corte = dt.date(2019, 11, 30)
        hoje = dt.date.today()
        vinculos = cnis["vinculos"]

        # recolhimentos proprios (CI/facultativo) abaixo do minimo antes da reforma nao contam
        soma_ci = defaultdict(float)
        for v in vinculos:
            if v["tipo"] in ("CI", "FAC"):
                for ym, val in v["comp"].items():
                    soma_ci[ym] += val
        ci_menor = {ym for ym, val in soma_ci.items()
                    if INICIO_PBC <= ym <= (2019, 11) and val < vigente(SAL_MIN, ym) - 0.005}

        fora = []                 # remuneracoes de empregado fora do periodo do vinculo (ex.: PREM-FVIN)
        beneficios = []
        for v in vinculos:
            if v["tipo"] == "BEN":
                if v["inicio"]:
                    beneficios.append(v)
                continue
            proprio = v["tipo"] in ("CI", "FAC")
            periodo = None
            if not proprio and v["inicio"] and v["fim"]:
                periodo = ((v["inicio"].year, v["inicio"].month), (v["fim"].year, v["fim"].month))
            for ym, val in v["comp"].items():
                if periodo and not periodo[0] <= ym <= periodo[1]:
                    fora.append((ym, v["seq"], val))
                    continue
                if not (proprio and ym in ci_menor):
                    soma[ym] += val
            if proprio:
                for ym in v["comp"]:
                    if ym in ci_menor:
                        continue
                    for d in range(1, fim_do_mes(ym).day + 1):
                        dia = dt.date(ym[0], ym[1], d)
                        if dia <= corte:
                            dias_pre.add(dia.toordinal())
                if not v["comp"] and v["inicio"]:
                    aviso(avisos, f"Seq. {v['seq']}: recolhimentos sem competencias legiveis - periodo ignorado.")
                continue
            ini, fim = v["inicio"], v["fim"]
            if ini is None:
                continue
            if fim is None:
                if v["comp"]:
                    fim = fim_do_mes(max(v["comp"]))
                else:
                    aviso(avisos, f"Seq. {v['seq']}: vinculo sem data fim e sem remuneracoes - so a data de "
                                  "inicio foi considerada.", INFO)
                    fim = ini
            fim = min(fim, hoje)
            d = ini
            while d <= fim:
                ym = (d.year, d.month)
                # licenca nao remunerada: mes sem remuneracao dentro de vinculo que tem remuneracoes
                if licenca and v["comp"] and ym not in v["comp"] and ym >= INICIO_PBC:
                    licenca_meses.add(ym)
                    d += dt.timedelta(days=1)
                    continue
                if d <= corte:
                    dias_pre.add(d.toordinal())
                cob_emp.add(ym)
                if not v["comp"] or ym not in v["comp"]:
                    sem_rem.add(ym)
                d += dt.timedelta(days=1)

        pagos_ci = {ym for v in vinculos if v["tipo"] in ("CI", "FAC") for ym in v["comp"]} - ci_menor
        atrasos = self._atrasos_sem_carencia(vinculos, cob_emp, pagos_ci)
        ben_conta = self._beneficios(beneficios, cob_emp | pagos_ci, dias_pre, corte, hoje)

        for o in dias_pre:
            dia = dt.date.fromordinal(o)
            self.tempo[(dia.year, dia.month)] += 1 / 365.25

        abaixo = []
        todos = cob_emp | ben_conta | set(soma)
        for ym in sorted(todos):
            sal = soma.get(ym)
            contrib = ym in cob_emp or ym in pagos_ci
            if sal and (ym <= (2019, 11) or sal >= vigente(SAL_MIN, ym) - 0.005):
                self.salario[ym] = min(sal, vigente(TETO, ym)) if ym >= INICIO_PBC else sal
            car_ok = ym not in atrasos
            if ym <= (2019, 11):
                self.carencia[ym] = 1 if ((contrib and car_ok) or ym in ben_conta) else 0
            else:
                ok = (sal is not None and sal >= vigente(SAL_MIN, ym) - 0.005) or (ym in sem_rem and sal is None)
                if contrib and not ok:
                    abaixo.append(ym)
                conta = (ok and contrib) or ym in ben_conta
                self.tempo[ym] = 1 / 12 if conta else 0.0
                self.carencia[ym] = 1 if ((ok and contrib and car_ok) or ym in ben_conta) else 0
        if abaixo:
            aviso(avisos,
                  f"{len(abaixo)} competencia(s) pos-reforma abaixo do salario minimo NAO contam ({fmt_meses(abaixo)}). "
                  "Podem ser complementadas, agrupadas ou utilizadas excedentes (EC 103, art. 29).", ALERTA)
        # mes sem remuneracao num vinculo, mas remunerado em outro (ex.: mesma empresa com dois CNPJs), nao e lacuna
        sem_rem -= set(soma)
        licenca_meses -= set(soma)
        # empregado sem salario comprovado: o INSS usa o salario minimo na media (Decreto 3.048, art. 36)
        for ym in sem_rem:
            if ym >= INICIO_PBC and self.tempo.get(ym):
                self.salario[ym] = vigente(SAL_MIN, ym)
        if fora:
            lista = ", ".join(f"{m:02d}/{y} (seq. {sq}, {brl(val)})" for (y, m), sq, val in sorted(fora)[:8]) \
                + (" ..." if len(fora) > 8 else "")
            aviso(avisos, f"{len(fora)} remuneracao(oes) fora do periodo do vinculo ignorada(s): {lista}. "
                          "O INSS so as considera depois de acertar o vinculo (indicador PREM-FVIN).")
        # plano simplificado / MEI / baixa renda: conta para regras por idade, nao para as regras por tempo
        normais = {ym for v in vinculos if v["tipo"] in ("CI", "FAC") for ym in v["comp"] if ym not in v["simpl"]}
        self.simplificado = {ym for v in vinculos for ym in v["simpl"]} - cob_emp - normais - ci_menor
        self.tempo_tc = {ym: (0.0 if ym in self.simplificado else t) for ym, t in self.tempo.items()}
        if self.simplificado:
            aviso(avisos,
                  f"{len(self.simplificado)} competencia(s) no plano simplificado/MEI/baixa renda "
                  f"({fmt_meses(self.simplificado)}): contam para as regras por idade, mas nao para as regras por "
                  "tempo de contribuicao (salvo complementacao para 20%).")
        if ci_menor:
            lista = ", ".join(f"{m:02d}/{y} ({brl(soma_ci[(y, m)])})" for y, m in sorted(ci_menor)[:8]) \
                + (" ..." if len(ci_menor) > 8 else "")
            aviso(avisos,
                  f"{len(ci_menor)} recolhimento(s) proprio(s) abaixo do salario minimo da epoca NAO contam: {lista}. "
                  "Podem ser complementados (pagamento da diferenca com acrescimos).", ALERTA)
        if atrasos:
            aviso(avisos,
                  f"{len(atrasos)} recolhimento(s) em atraso anteriores ao primeiro pago em dia ({fmt_meses(atrasos)}): "
                  "contam como tempo, mas NAO como carencia (Lei 8.213, art. 27, II).", ALERTA)
        sem_pre = sorted(ym for ym in sem_rem if INICIO_PBC <= ym <= (2019, 11))
        sem_pos = sorted(ym for ym in sem_rem if ym > (2019, 11))
        if sem_pre:
            aviso(avisos,
                  f"{len(sem_pre)} competencia(s) de vinculo sem remuneracao no CNIS ({fmt_meses(sem_pre)}): contam "
                  "tempo e entram na media com o salario minimo (Decreto 3.048, art. 36). Se foram licenca nao "
                  "remunerada ou contrato suspenso, use --licenca-sem-remuneracao.")
        if sem_pos:
            aviso(avisos,
                  f"{len(sem_pos)} competencia(s) pos-reforma de vinculo SEM remuneracao no CNIS ({fmt_meses(sem_pos)}): "
                  "o calculo conta tempo e carencia com salario minimo na media, mas pela EC 103 (art. 29) o INSS so "
                  "reconhece competencia com remuneracao igual ou acima do minimo. Peca ao empregador a retificacao "
                  "(eSocial) ou comprove com holerites. Se foi licenca nao remunerada, use --licenca-sem-remuneracao.",
                  ALERTA)
        if licenca_meses:
            aviso(avisos, f"{len(licenca_meses)} mes(es) de licenca nao remunerada NAO contam tempo nem carencia "
                          f"({fmt_meses(licenca_meses)}).")

        self._avisos_cnis(cnis, avisos)

        meses = [ym for ym in set(self.tempo) | set(self.carencia) if self.tempo.get(ym) or self.carencia.get(ym)]
        self.primeiro = min(meses) if meses else None
        self.ultimo = max(meses) if meses else None
        self.tempo_reforma = len([o for o in dias_pre if o <= REFORMA.toordinal()]) / 365.25
        self.tempo_reforma_tc = len([o for o in dias_pre if o <= REFORMA.toordinal() and
                                     (dt.date.fromordinal(o).year, dt.date.fromordinal(o).month)
                                     not in self.simplificado]) / 365.25
        self.carencia_reforma = sum(c for ym, c in self.carencia.items() if ym <= (2019, 11))

    @staticmethod
    def _atrasos_sem_carencia(vinculos, cob_emp, pagos_ci):
        """Competencias proprias pagas em atraso antes do primeiro recolhimento em dia (Lei 8.213, art. 27, II).
        Uma interrupcao maior que o periodo de graca (12 meses), ou um pagamento feito depois da perda da
        qualidade de segurado, reinicia a exigencia de um primeiro pagamento em dia."""
        pg, simpl = {}, set()
        for v in vinculos:
            if v["tipo"] in ("CI", "FAC"):
                simpl |= v["simpl"]
                for ym, d in v["pg"].items():
                    if ym in pagos_ci and (ym not in pg or d < pg[ym]):
                        pg[ym] = d
        excluidos, em_dia, anterior = set(), False, None
        for ym in sorted(cob_emp | pagos_ci):
            if anterior is not None:
                perda = ym_add(anterior, 14)          # perda da qualidade: dia 16 do 2o mes apos a graca
                if meses_entre(anterior, ym) > 13 or (ym in pg and pg[ym] >= dt.date(perda[0], perda[1], 16)):
                    em_dia = False
            anterior = ym
            if ym in cob_emp or ym not in pg:   # empregado ou contribuicao presumida (cooperativa)
                em_dia = True
                continue
            prox = ym_add(ym, 1)
            venc = dt.date(prox[0], prox[1], 20 if ym in simpl else 15) + dt.timedelta(days=4)  # folga p/ dia util
            if pg[ym] <= venc:
                em_dia = True
            elif not em_dia:
                excluidos.add(ym)
        return excluidos

    def _beneficios(self, beneficios, contrib_meses, dias_pre, corte, hoje):
        """Meses de beneficio que contam como tempo e carencia."""
        conta_meses = set()
        for v in beneficios:
            esp = v["especie"]
            ini, fim = v["inicio"], min(v["fim"] or hoje, hoje)
            m_ini, m_fim = (ini.year, ini.month), (fim.year, fim.month)
            antes = any(ym < m_ini for ym in contrib_meses)
            depois = any(ym > m_fim for ym in contrib_meses)
            rotulo = f"Seq. {v['seq']} (beneficio especie {esp or '?'}, {ini:%d/%m/%Y} a " \
                     f"{v['fim']:%d/%m/%Y})" if v["fim"] else f"Seq. {v['seq']} (beneficio especie {esp or '?'}, " \
                     f"desde {ini:%d/%m/%Y}, ativo)"
            if esp == ESPECIE_MATERNIDADE:
                conta = True
                aviso(self.avisos, f"{rotulo}: salario-maternidade conta como tempo e carencia.", INFO)
            elif esp in ESPECIES_INCAPACIDADE:
                conta = antes and depois and v["fim"] is not None
                if conta:
                    aviso(self.avisos,
                          f"{rotulo}: incapacidade intercalada com contribuicoes, conta como tempo e carencia. O "
                          "salario de beneficio desse periodo deveria entrar na media (Lei 8.213, art. 29, par. 5), "
                          "mas o CNIS nao o informa: a media calculada fica sem esses meses.")
                else:
                    aviso(self.avisos,
                          f"{rotulo}: incapacidade NAO intercalada com contribuicoes (falta contribuicao "
                          f"{'antes' if not antes else 'depois'}): nao conta como tempo nem carencia. Uma contribuicao "
                          "apos o fim do beneficio passa a conta-lo.", ALERTA)
            else:
                conta = False
                aviso(self.avisos,
                      f"{rotulo}: esta especie (ex.: auxilio-acidente, pensao, assistencial) NAO conta como tempo de "
                      "contribuicao nem carencia." if esp else
                      f"{rotulo}: especie nao identificada no PDF - beneficio nao contado. Se for auxilio-doenca ou "
                      "invalidez intercalados, ou salario-maternidade, ele contaria.", ATENCAO)
            if not conta:
                continue
            for ym in ym_range(m_ini, m_fim):
                conta_meses.add(ym)
            d = ini
            while d <= fim:
                if d <= corte:
                    dias_pre.add(d.toordinal())
                d += dt.timedelta(days=1)
        return conta_meses

    def _avisos_cnis(self, cnis, avisos):
        """Avisos sobre indicadores do CNIS e a tabela de valores consolidados do INSS."""
        # facultativo concomitante com vinculo obrigatorio
        fc = sorted({ym for v in cnis["vinculos"] for ym, inds in v["ind_comp"].items() if "PREC-FACULTCONC" in inds})
        if fc:
            aviso(avisos,
                  f"{len(fc)} recolhimento(s) como facultativo concomitantes com vinculo obrigatorio ({fmt_meses(fc)}, "
                  "indicador PREC-FACULTCONC). Quem e segurado obrigatorio nao pode contribuir como facultativo: o INSS "
                  "pode desconsiderar esses recolhimentos e os salarios saem da media. Se o contrato de trabalho "
                  "estava suspenso (licenca, expatriacao), peca o acerto do vinculo para validar o facultativo.",
                  ALERTA)
        # tabela de valores consolidados: o que o INSS hoje considera em cada competencia pos-reforma
        cons = cnis.get("consolidados") or {}
        zerados = sorted(ym for ym, val in cons.items()
                         if self.carencia.get(ym) and val < vigente(SAL_MIN, ym) - 0.005)
        if zerados:
            aviso(avisos,
                  f"Na tabela de valores consolidados do CNIS, o INSS registra valor abaixo do minimo (em geral "
                  f"R$ 0,00) em {len(zerados)} competencia(s) que este calculo conta ({fmt_meses(zerados)}). Hoje o "
                  "INSS nao as reconhece como tempo nem carencia; a causa costuma ser pendencia no recolhimento ou "
                  "no vinculo (veja os indicadores). Confira no Meu INSS e peca o acerto.", ALERTA)
        # demais pendencias, por vinculo
        for v in cnis["vinculos"]:
            partes = []
            for ind in sorted(i for i in v["indicadores"] if i.startswith("P") and i not in IND_COM_AVISO):
                meses = [ym for ym, inds in v["ind_comp"].items() if ind in inds]
                desc = DESC_IND.get(ind, "indicador de pendencia")
                partes.append(f"{ind} ({desc})" + (f" em {fmt_meses(meses)}" if meses else ""))
            if partes:
                origem = re.sub(r"^\d+\s+(?:[\d.\-/]{6,}\s+)*", "", v["origem"])[:35].strip()
                aviso(avisos, f"Seq. {v['seq']} ({origem}): {'; '.join(partes)}. O calculo considera o que consta no "
                              "CNIS, mas o INSS pode exigir comprovacao (carteira de trabalho, holerites, guias).")


# ----------------------------------------------------------------------------- calculos de valor
def fator_prev(idade, tc, sexo, es_tabela, es_override=None):
    if sexo == "F":
        tc += 5
    es = es_override or es_tabela.get(max(40, min(80, int(idade))))
    a = 0.31
    return (tc * a / es) * (1 + (idade + tc * a) / 100), es


def melhor_com_descarte(sals, tempo, carencia, tempo_min, coef_fn, permite=True):
    """Media de 100% com descarte das menores contribuicoes (EC 103, art. 26, par. 6)."""
    if not sals:
        return 0.0, 0, 0.0
    s = sorted(sals)
    n = len(s)
    total = sum(s)
    best = (total / n * coef_fn(tempo), 0, coef_fn(tempo))
    if permite:
        pref = 0.0
        for k in range(1, n):
            pref += s[k - 1]
            t = tempo - k / 12
            if t < tempo_min - 1e-9 or carencia - k < 180:
                break
            c = coef_fn(t)
            val = (total - pref) / (n - k) * c
            if val > best[0] + 0.005:
                best = (val, k, c)
    return best


def coef_60_2(base):
    return lambda t: 0.60 + 0.02 * max(0, math.floor(t + 1e-9) - base)


# ----------------------------------------------------------------------------- analise
def analisar(cnis, sexo, nasc, args, inpc_pre=None):
    avisos = []
    h = Historico(cnis, avisos, args.licenca_sem_remuneracao)
    if h.primeiro is None:
        sys.exit("Nenhum periodo de contribuicao foi encontrado no PDF. Rode com --debug e confira o .txt gerado.")
    hoje = dt.date.today()
    mes_atual = (hoje.year, hoje.month)

    inpc, fonte_inpc = inpc_pre or carregar_inpc(args.inpc_csv, args.atualizar_inpc)
    if not inpc:
        aviso(avisos, "INPC indisponivel: salarios NAO corrigidos - valores ficam subestimados. Use --inpc-csv.", ALERTA)
    fat = fatores_correcao(inpc, mes_atual)

    # salario corrigido (moeda de hoje) por competencia
    sal_corr = {ym: v * fat.get(ym, 1.0) for ym, v in h.salario.items() if ym >= INICIO_PBC and ym < mes_atual}

    # projecao
    recentes = [ym for ym in h.carencia if h.carencia[ym] and ym >= ym_add(mes_atual, -6)]
    if args.parar:
        continuar = False
    elif args.continuar or args.salario_futuro:
        continuar = True
    else:
        continuar = bool(recentes)
    teto_hoje, sm_hoje = vigente(TETO, mes_atual), vigente(SAL_MIN, mes_atual)
    if args.salario_futuro:
        sal_fut = args.salario_futuro
    else:
        ult = sorted(ym for ym in sal_corr if ym >= ym_add(mes_atual, -12))
        sal_fut = sum(sal_corr[ym] for ym in ult) / len(ult) if ult else sm_hoje
    sal_fut = max(sm_hoje, min(teto_hoje, sal_fut))
    inicio_proj = max(ym_add(h.ultimo, 1), mes_atual)
    # sem salario informado e contribuindo hoje so como MEI/simplificado: projeta no mesmo plano (sobre o minimo)
    proj_simpl = bool(not args.salario_futuro and recentes and all(ym in h.simplificado for ym in recentes))
    if proj_simpl:
        sal_fut = sm_hoje
    horizonte = (nasc.year + 76, nasc.month)
    if continuar:
        fim_proj = horizonte
        if args.ate_idade:
            fim_proj = (nasc.year + int(args.ate_idade), nasc.month)
        for ym in ym_range(inicio_proj, fim_proj):
            h.tempo[ym] = 1 / 12
            h.tempo_tc[ym] = 0.0 if proj_simpl else 1 / 12
            h.carencia[ym] = 1
            sal_corr[ym] = sal_fut

    # acumulados mensais
    meses = list(ym_range(h.primeiro, horizonte))
    cum_t, cum_tc, cum_c, t, ttc, c = {}, {}, {}, 0.0, 0.0, 0
    for ym in meses:
        t += h.tempo.get(ym, 0.0)
        ttc += h.tempo_tc.get(ym, 0.0)
        c += h.carencia.get(ym, 0)
        cum_t[ym], cum_tc[ym], cum_c[ym] = t, ttc, c

    def estado(ym_data, tc=False):
        """Situacao no 1o dia do mes ym_data (contribuicoes ate o mes anterior).
        tc=True: tempo sem as competencias do plano simplificado (regras por tempo de contribuicao)."""
        ant = ym_add(ym_data, -1)
        ant = min(ant, meses[-1])
        tt = (cum_tc if tc else cum_t).get(ant, 0.0) if ant >= h.primeiro else 0.0
        cc = cum_c.get(ant, 0) if ant >= h.primeiro else 0
        d = dt.date(ym_data[0], ym_data[1], 1)
        return d, tt, cc, idade_em(nasc, d)

    def sals_ate(ym_data):
        return [v for ym, v in sal_corr.items() if ym < ym_data]

    base = 35 if sexo == "M" else 30
    tr = h.tempo_reforma_tc
    filiado_antes = h.primeiro <= (2019, 11)
    resultados = []

    def limita(v):
        return max(sm_hoje, min(teto_hoje, v))

    # --- direito adquirido em 13/11/2019
    if filiado_antes:
        idade_r = idade_em(nasc, REFORMA)
        sals_r = sorted((v for ym, v in sal_corr.items() if ym <= (2019, 10)), reverse=True)
        n80 = max(1, round(len(sals_r) * 0.8)) if sals_r else 0
        media80 = sum(sals_r[:n80]) / n80 if n80 else 0.0
        if tr >= base and h.carencia_reforma >= 180:
            pontos = idade_r + tr
            # fator com a tabua vigente na data do direito (IBGE 2017, valida de 12/2018 a 11/2019)
            f, es = fator_prev(idade_r, tr, sexo, ES_IBGE_2017)
            sem_fator = pontos >= (96 if sexo == "M" else 86)
            mult = max(f, 1.0) if sem_fator else f
            obs = f"pontos {pontos:.1f}: " + ("sem fator" if sem_fator else f"fator {f:.4f} (Es {es}, tabua 2017)")
            resultados.append(("Direito adquirido - tempo de contribuicao", REFORMA, idade_r, tr,
                               limita(media80 * mult), obs + "; media 80% maiores"))
        else:
            resultados.append(("Direito adquirido - tempo de contribuicao", None, None, None, None,
                               f"em 13/11/2019 tinha {fmt_tempo(tr)} (precisava {base} anos)"))
        idade_min = 65 if sexo == "M" else 60
        if idade_r >= idade_min and h.carencia_reforma >= 180:
            coef = min(1.0, 0.70 + 0.01 * (h.carencia_reforma // 12))
            # fator previdenciario e opcional na idade: so se aplica quando aumenta o valor (Lei 9.876, art. 7)
            f, es = fator_prev(idade_r, tr, sexo, ES_IBGE_2017)
            mult = max(f, 1.0)
            resultados.append(("Direito adquirido - idade", REFORMA, idade_r, tr, limita(media80 * mult * coef),
                               f"coef. {coef:.0%}; media 80% maiores"
                               + (f"; fator {f:.4f} (opcional, maior que 1)" if f > 1 else "")))
        else:
            resultados.append(("Direito adquirido - idade", None, None, None, None,
                               f"em 13/11/2019 tinha {fmt_idade(idade_r)} de idade e {h.carencia_reforma} meses de carencia"))

    def procurar(nome, cond, valor_fn, tc=False):
        for ym in ym_range((2019, 12), horizonte):
            d, tt, cc, idd = estado(ym, tc)
            if cond(d, tt, cc, idd):
                # o requisito pode ter sido cumprido durante o mes anterior (ex.: aniversario): procura o dia exato,
                # com o tempo e a carencia que ja existiam naquele mes
                ant = ym_add(ym, -1)
                if ant >= (2019, 12):
                    _, tt0, cc0, _ = estado(ant, tc)
                    for dia in range(2, fim_do_mes(ant).day + 1):
                        d0 = dt.date(ant[0], ant[1], dia)
                        i0 = idade_em(nasc, d0)
                        if cond(d0, tt0, cc0, i0):
                            ym, d, tt, cc, idd = ant, d0, tt0, cc0, i0
                            break
                v, obs = valor_fn(ym, tt, cc, idd)
                resultados.append((nome, d, idd, tt, v, obs))
                return
        resultados.append((nome, None, None, None, None, "nao cumpre ate os 76 anos com a projecao adotada"))

    def valor_media100(tempo_min, coef_fn, rotulo, pontos_fn=None):
        """pontos_fn(ano): pontuacao exigida. O tempo descartado tambem sai dos pontos (EC 103, art. 26, par. 6),
        entao o descarte nao pode deixar idade + tempo abaixo da pontuacao."""
        def f(ym, tt, cc, idd):
            tmin = tempo_min if pontos_fn is None else max(tempo_min, pontos_fn(ym[0]) - idd)
            media_val, k, c = melhor_com_descarte(sals_ate(ym), tt, cc, tmin, coef_fn)
            obs = f"{rotulo} {c:.0%}" + (f"; descarte de {k} menores contrib." if k else "")
            return limita(media_val), obs
        return f

    if filiado_antes:
        # art. 15 - pontos
        def pts(ano):
            return min(96 + ano - 2019, 105) if sexo == "M" else min(86 + ano - 2019, 100)
        procurar("Transicao por pontos (art. 15)",
                 lambda d, tt, cc, idd: tt >= base and cc >= 180 and idd + tt >= pts(d.year),
                 valor_media100(base, coef_60_2(20 if sexo == "M" else 15), "coef.", pontos_fn=pts), tc=True)

        # art. 16 - idade minima progressiva
        def idade_prog(ano):
            return min(61 + 0.5 * (ano - 2019), 65) if sexo == "M" else min(56 + 0.5 * (ano - 2019), 62)
        procurar("Idade minima progressiva (art. 16)",
                 lambda d, tt, cc, idd: tt >= base and cc >= 180 and idd >= idade_prog(d.year),
                 valor_media100(base, coef_60_2(20 if sexo == "M" else 15), "coef."), tc=True)

        # art. 17 - pedagio 50%
        lim = 33 if sexo == "M" else 28
        if tr > lim:
            req = base + 0.5 * max(0, base - tr)

            def v17(ym, tt, cc, idd):
                sals = sals_ate(ym)
                media = sum(sals) / len(sals) if sals else 0
                f, es = fator_prev(idd, tt, sexo, ES_IBGE_2024, args.es)
                fonte_es = "informada" if args.es else "tabua 2024; tabuas futuras tendem a reduzir o fator"
                return limita(media * f), f"fator {f:.4f} (Es {es}, {fonte_es}); exige {fmt_tempo(req)}"
            procurar("Pedagio de 50% (art. 17)", lambda d, tt, cc, idd: tt >= req - 1e-9 and cc >= 180, v17, tc=True)
        else:
            resultados.append(("Pedagio de 50% (art. 17)", None, None, None, None,
                               f"exige mais de {lim} anos em 13/11/2019 (tinha {fmt_tempo(tr)})"))

        # art. 20 - pedagio 100%
        req100 = base + max(0, base - tr)
        idade20 = 60 if sexo == "M" else 57
        procurar("Pedagio de 100% (art. 20)",
                 lambda d, tt, cc, idd: idd >= idade20 and tt >= req100 - 1e-9 and cc >= 180,
                 valor_media100(req100, lambda t: 1.0, f"exige {fmt_tempo(req100)}; coef."), tc=True)

        # art. 18 - idade (transicao)
        def idade18(ano):
            return 65 if sexo == "M" else min(60 + 0.5 * (ano - 2019), 62)
        procurar("Aposentadoria por idade - transicao (art. 18)",
                 lambda d, tt, cc, idd: idd >= idade18(d.year) and tt >= 15 and cc >= 180,
                 valor_media100(15, coef_60_2(20 if sexo == "M" else 15), "coef."))
    else:
        tmin = 20 if sexo == "M" else 15
        procurar("Regra permanente (art. 19)",
                 lambda d, tt, cc, idd: idd >= (65 if sexo == "M" else 62) and tt >= tmin and cc >= 180,
                 valor_media100(tmin, coef_60_2(20 if sexo == "M" else 15), "coef."))

    _, t_hoje, c_hoje, idade_hoje = estado(mes_atual)
    t_hoje_tc = estado(mes_atual, tc=True)[1]
    ctx = dict(h=h, avisos=avisos, fonte_inpc=fonte_inpc, continuar=continuar, sal_fut=sal_fut, proj_simpl=proj_simpl,
               inicio_proj=inicio_proj, t_hoje=t_hoje, t_hoje_tc=t_hoje_tc, c_hoje=c_hoje, idade_hoje=idade_hoje,
               teto_hoje=teto_hoje, sm_hoje=sm_hoje, ultimo_inpc=max(inpc) if inpc else None,
               inpc=(inpc, fonte_inpc))
    return resultados, ctx


# ----------------------------------------------------------------------------- relatorio
NOMES_TIPO = {"EMP": "Empregado", "DOM": "Domestico", "CI": "Contrib. individual",
              "FAC": "Facultativo", "BEN": "Beneficio"}


def titulo(txt):
    return cor(txt, "negrito", "ciano")


def relatorio(cnis, sexo, nasc, resultados, ctx):
    h = ctx["h"]
    L = []
    w = L.append
    w(cor("=" * 78, "ciano"))
    w(titulo("ANALISE DE APOSENTADORIA - RGPS (simulacao a partir do CNIS)"))
    w(cor("=" * 78, "ciano"))
    w(f"Segurado(a): {cor(cnis.get('nome') or '-', 'negrito')}    Sexo: {sexo}    Nascimento: {nasc:%d/%m/%Y}")
    w(f"Data da analise: {dt.date.today():%d/%m/%Y}    Idade hoje: {fmt_idade(ctx['idade_hoje'])}")
    w("")
    w(titulo("VINCULOS LIDOS DO CNIS"))
    w(cor(f"{'Seq':>3}  {'Tipo':<19} {'Inicio':<10} {'Fim':<10} {'Comp.':>5}  Indicadores / origem", "negrito"))
    for v in cnis["vinculos"]:
        ini = f"{v['inicio']:%d/%m/%Y}" if v["inicio"] else "-"
        fim = f"{v['fim']:%d/%m/%Y}" if v["fim"] else "-"
        inds = sorted(v["indicadores"] | {i for s in v["ind_comp"].values() for i in s})
        ind = ",".join(cor(i, "amarelo") if i.startswith("P") else i for i in inds)
        origem = re.sub(r"^\d+\s+(?:[\d.\-/]{6,}\s+)*", "", v["origem"])[:40]
        tipo = NOMES_TIPO[v["tipo"]] + (f" {v['especie']}" if v["tipo"] == "BEN" and v["especie"] else "")
        w(f"{v['seq']:>3}  {tipo:<19} {ini:<10} {fim:<10} {len(v['comp']):>5}  {ind + ' | ' if ind else ''}{origem}")
    w("")
    w(titulo("RESUMO"))
    w(f"  Tempo de contribuicao ate hoje ....... {cor(fmt_tempo(ctx['t_hoje']), 'negrito')}")
    w(f"  Carencia ate hoje .................... {cor(str(ctx['c_hoje']), 'negrito')} meses")
    if h.primeiro <= (2019, 11):
        w(f"  Tempo em 13/11/2019 (reforma) ........ {fmt_tempo(h.tempo_reforma)}")
        w(f"  Carencia em 13/11/2019 ............... {h.carencia_reforma} meses")
    if h.simplificado:
        w(f"  Tempo hoje sem plano simplificado .... {cor(fmt_tempo(ctx['t_hoje_tc']), 'negrito')}"
          "  (regras por tempo de contribuicao)")
    w(f"  Primeira / ultima competencia ........ {h.primeiro[1]:02d}/{h.primeiro[0]} / {h.ultimo[1]:02d}/{h.ultimo[0]}")
    w("")
    w(titulo("PREMISSAS"))
    if ctx["continuar"]:
        w(f"  Continua contribuindo a partir de {ctx['inicio_proj'][1]:02d}/{ctx['inicio_proj'][0]} "
          f"sobre {brl(ctx['sal_fut'])} (moeda de hoje)"
          + (", no plano simplificado/MEI como hoje (use --salario-futuro para simular o plano normal)." if ctx["proj_simpl"] else "."))
    else:
        w("  Sem novas contribuicoes (so idade avanca).")
    w(f"  Correcao monetaria: INPC - {ctx['fonte_inpc']}"
      + (f", ate {ctx['ultimo_inpc'][1]:02d}/{ctx['ultimo_inpc'][0]}" if ctx["ultimo_inpc"] else ""))
    w(f"  Valores em moeda de hoje, limitados entre {brl(ctx['sm_hoje'])} e {brl(ctx['teto_hoje'])}.")
    w("")
    w(titulo("REGRAS"))
    validos = [r for r in resultados if r[1]]
    maior = max(validos, key=lambda r: (r[4], -r[1].toordinal())) if validos else None
    primeira = min(validos, key=lambda r: r[1]) if validos else None
    for r in resultados:
        nome, data, idd, tt, val, obs = r
        if data:
            w(cor(f"- {nome}", "negrito"))
            if data <= dt.date.today():
                quando = cor("JA CUMPRIDA em " + f"{data:%d/%m/%Y}", "verde", "negrito")
            else:
                quando = "cumpre em " + cor(f"{data:%d/%m/%Y}", "verde")
            w(f"    {quando}  |  idade {fmt_idade(idd)}  |  tempo {fmt_tempo(tt)}")
            marca = cor("  <- maior valor", "verde", "negrito") if r is maior else ""
            w(f"    valor estimado: {cor(brl(val), 'verde', 'negrito')}  ({obs}){marca}")
        else:
            w(cor(f"- {nome}", "cinza"))
            w(cor(f"    nao se aplica: {obs}", "cinza"))
    if validos:
        w("")
        w(titulo("DESTAQUES"))
        w(f"  Mais cedo:   {primeira[0]} - {cor(f'{primeira[1]:%d/%m/%Y}', 'negrito')}, "
          f"{cor(brl(primeira[4]), 'verde', 'negrito')}")
        w(f"  Maior valor: {maior[0]} - {cor(f'{maior[1]:%d/%m/%Y}', 'negrito')}, "
          f"{cor(brl(maior[4]), 'verde', 'negrito')}")
        w("  Antes de decidir, compare tambem o valor de esperar alguns anos a mais em cada regra.")
    w("")
    w(titulo("ATENCAO"))
    avisos = list(ctx["avisos"]) + [
        (INFO, "Nao inclui tempo especial, professor, PcD, rural, aposentadoria proporcional nem RPPS."),
        (INFO, "Simulacao para planejamento; nao substitui a analise do INSS ou de um advogado."),
    ]
    L.extend(fmt_avisos(avisos))
    return "\n".join(L)


# ----------------------------------------------------------------------------- cenarios
CONFIG_PADRAO = {
    "taxa_desconto_real_aa": 5.0,
    "idade_final_recebimento": None,
    "pagamentos_por_ano": 13,
    "cenarios": [
        {"nome": "Sem novas contribuicoes", "salario": None},
        {"nome": "Minimo (20%)", "salario": "minimo", "aliquota": 0.20},
        {"nome": "Intermediario", "salario": 4500, "aliquota": 0.20},
        {"nome": "Teto (20%)", "salario": "teto", "aliquota": 0.20},
    ],
}


def carregar_config(arq: str | None):
    """Le o JSON de configuracao; chaves ausentes ficam com o padrao. Chaves iniciadas por '_' sao comentarios."""
    candidatos = [Path(arq)] if arq else [Path.cwd() / "cnis_config.json", Path(__file__).with_name("cnis_config.json")]
    for c in candidatos:
        if c.exists():
            dados = json.loads(c.read_text(encoding="utf-8"))
            cfg = {**CONFIG_PADRAO, **{k: v for k, v in dados.items() if not k.startswith("_")}}
            return cfg, str(c)
    if arq:
        sys.exit(f"Arquivo de configuracao nao encontrado: {arq}")
    return dict(CONFIG_PADRAO), "padrao embutido (cnis_config.json nao encontrado)"


def vp(fluxo, taxa_aa):
    """Valor presente de {indice_mes: valor} (mes 0 = mes atual), taxa real anual em %."""
    i = (1 + taxa_aa / 100) ** (1 / 12) - 1
    return sum(v / (1 + i) ** k for k, v in fluxo.items())


def tir_aa(fluxo):
    """TIR real anual (%) de um fluxo mensal; None se nao houver troca de sinal que a defina."""
    if not fluxo or min(fluxo.values()) >= 0 or max(fluxo.values()) <= 0:
        return None
    f = lambda t: vp(fluxo, t)
    lo, hi = -50.0, 100.0
    if f(lo) * f(hi) > 0:
        return None
    for _ in range(100):
        mid = (lo + hi) / 2
        if f(lo) * f(mid) <= 0:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2


def simular_cenarios(cnis, sexo, nasc, args, cfg):
    hoje = dt.date.today()
    mes_atual = (hoje.year, hoje.month)
    sm_hoje, teto_hoje = vigente(SAL_MIN, mes_atual), vigente(TETO, mes_atual)
    idade_hoje = idade_em(nasc, hoje)
    idade_final = cfg["idade_final_recebimento"] or idade_hoje + ES_IBGE_2024[max(40, min(80, int(idade_hoje)))]
    fim = ym_add((nasc.year, nasc.month), round(idade_final * 12))
    fator_ano = cfg["pagamentos_por_ano"] / 12
    taxa = cfg["taxa_desconto_real_aa"]

    inpc = None
    saida = []
    for cen in cfg["cenarios"]:
        a = argparse.Namespace(**vars(args))
        sal = cen.get("salario")
        aliq = cen.get("aliquota", 0.20)
        if sal is None:
            a.parar, a.continuar, a.salario_futuro, contrib_mes = True, False, None, 0.0
        else:
            valor = {"minimo": sm_hoje, "teto": teto_hoje}.get(sal, sal)
            valor = max(sm_hoje, min(teto_hoje, float(valor)))
            a.parar, a.continuar, a.salario_futuro, contrib_mes = False, True, valor, aliq * valor
        a.ate_idade = None
        resultados, ctx = analisar(cnis, sexo, nasc, a, inpc)
        inpc = ctx["inpc"]
        # plano simplificado (aliquota < 20%) nao da direito as regras por tempo de contribuicao
        simplificado = sal is not None and aliq < 0.20 - 1e-9
        validos = [r for r in resultados if r[1] and (not simplificado or "idade" in r[0].lower() or "art. 19" in r[0])]
        opcoes = []
        for nome, data, idd, tt, val, obs in validos:
            dib = max((data.year, data.month), mes_atual)
            ben = {meses_entre(mes_atual, ym): val * fator_ano for ym in ym_range(dib, fim)}
            con = {meses_entre(mes_atual, ym): contrib_mes for ym in ym_range(ctx["inicio_proj"], ym_add(dib, -1))} \
                if contrib_mes else {}
            opcoes.append(dict(regra=nome, dib=dib, idade=idd, tempo=tt, valor=val, obs=obs, ben=ben, con=con,
                               liquido=vp(ben, taxa) - vp(con, taxa)))
        melhor = max(opcoes, key=lambda o: o["liquido"]) if opcoes else None
        saida.append(dict(cen=cen, salario=a.salario_futuro, contrib_mes=contrib_mes, simplificado=simplificado,
                          melhor=melhor, ctx=ctx))

    base = next((s for s in saida if s["cen"].get("salario") is None), None)
    for s in saida:
        m = s["melhor"]
        if not m or not base or not base["melhor"] or s is base:
            continue
        b = base["melhor"]

        def incremental(ate):
            """- contribuicoes + (beneficio do cenario - beneficio sem contribuir), ate o mes 'ate'."""
            inc = defaultdict(float)
            for k, v in m["con"].items():
                inc[k] -= v
            for opc, sinal in ((m, 1), (b, -1)):
                for ym in ym_range(opc["dib"], ate):
                    inc[meses_entre(mes_atual, ym)] += sinal * opc["valor"] * fator_ano
            return dict(inc)

        inc = incremental(fim)
        # idade de equilibrio: primeiro mes, apos parar de contribuir, em que o VP acumulado fica >= 0
        i = (1 + taxa / 100) ** (1 / 12) - 1
        acc, equilibrio = 0.0, None
        longo = incremental(ym_add((nasc.year, nasc.month), 110 * 12))
        for k in sorted(longo):
            acc += longo[k] / (1 + i) ** k
            if acc >= 0 and k >= max(m["con"], default=0):
                equilibrio = idade_em(nasc, dt.date(*ym_add(mes_atual, k), 1))
                break
        s["comp"] = dict(
            pago=vp(m["con"], taxa),
            pago_total=sum(m["con"].values()),
            recebido=vp(m["ben"], taxa) - vp(b["ben"], taxa),
            liquido=vp(dict(inc), taxa),
            tir=tir_aa(dict(inc)),
            equilibrio=equilibrio,
        )
    return dict(cenarios=saida, idade_final=idade_final, fim=fim, taxa=taxa, idade_hoje=idade_hoje,
                sm_hoje=sm_hoje, teto_hoje=teto_hoje)


def relatorio_cenarios(cnis, sexo, nasc, sim, cfg, fonte_cfg):
    L = []
    w = L.append
    ctx0 = sim["cenarios"][0]["ctx"]
    w(cor("=" * 78, "ciano"))
    w(titulo("SIMULACAO DE CENARIOS DE CONTRIBUICAO - RGPS"))
    w(cor("=" * 78, "ciano"))
    w(f"Segurado(a): {cor(cnis.get('nome') or '-', 'negrito')}    Sexo: {sexo}    Nascimento: {nasc:%d/%m/%Y}")
    w(f"Data da analise: {dt.date.today():%d/%m/%Y}    Idade hoje: {fmt_idade(sim['idade_hoje'])}"
      f"    Tempo hoje: {fmt_tempo(ctx0['t_hoje'])}")
    w("")
    w(titulo("PREMISSAS"))
    w(f"  Configuracao: {fonte_cfg}")
    w(f"  Valores em moeda de hoje (reais constantes); correcao: INPC - {ctx0['fonte_inpc']}.")
    w(f"  Taxa de desconto real: {sim['taxa']:.2f}% a.a.  |  {cfg['pagamentos_por_ano']} pagamentos/ano")
    w(f"  Recebe o beneficio ate os {fmt_idade(sim['idade_final'])} ({sim['fim'][1]:02d}/{sim['fim'][0]})"
      + ("" if cfg["idade_final_recebimento"] else " - idade hoje + sobrevida IBGE"))
    w("  Contribui ate o mes anterior ao inicio do beneficio; em cada cenario e escolhida a regra")
    w("  com maior valor presente liquido (beneficios - contribuicoes).")
    w("")
    w(titulo("CENARIOS"))
    for s in sim["cenarios"]:
        m = s["melhor"]
        w(cor(f"- {s['cen']['nome']}", "negrito"))
        if s["salario"] is None:
            w("    sem contribuicoes")
        else:
            w(f"    contribui sobre {brl(s['salario'])}: {brl(s['contrib_mes'])}/mes"
              + (" (plano simplificado: so regras por idade)" if s["simplificado"] else ""))
        if not m:
            w("    nenhuma regra cumprida ate os 76 anos")
            continue
        dib_txt = cor(f"{m['dib'][1]:02d}/{m['dib'][0]}", "verde")
        w(f"    {m['regra']}: inicio {dib_txt}, idade {fmt_idade(m['idade'])}, "
          f"tempo {fmt_tempo(m['tempo'])}")
        w(f"    beneficio: {cor(brl(m['valor']), 'verde', 'negrito')}/mes  ({m['obs']})")
        c = s.get("comp")
        if c:
            w(f"    pago a mais (VP): {brl(c['pago'])}   [soma simples {brl(c['pago_total'])}]")
            w(f"    recebido a mais (VP): {brl(c['recebido'])}")
            w(f"    resultado liquido (VP): {cor(brl(c['liquido']), 'verde' if c['liquido'] >= 0 else 'vermelho', 'negrito')}"
              + (f"   |   TIR real: {c['tir']:.1f}% a.a." if c["tir"] is not None else "   |   TIR: n/a"))
            w("    se paga a partir dos " + (fmt_idade(c["equilibrio"]) if c["equilibrio"] else "- (nao se paga ate os 110 anos)"))

    comp = [s for s in sim["cenarios"] if s.get("comp")]
    if comp:
        w("")
        w(titulo(f"COMPARACAO COM 'SEM CONTRIBUICOES' (valor presente a {sim['taxa']:.1f}% a.a. real)"))
        w(cor(f"  {'Cenario':<26}{'Beneficio':>13}{'Pago a mais':>14}{'Recebe a mais':>15}{'Liquido':>14}{'TIR':>7}{'Equilib.':>10}", "negrito"))
        b = next(s for s in sim["cenarios"] if s["cen"].get("salario") is None)
        if b["melhor"]:
            w(f"  {b['cen']['nome'][:25]:<26}{brl(b['melhor']['valor']):>13}{'-':>14}{'-':>15}{'-':>14}{'-':>7}{'-':>10}")
        for s in comp:
            c = s["comp"]
            tir = f"{c['tir']:.1f}%" if c["tir"] is not None else "n/a"
            eq = fmt_idade(c["equilibrio"]) if c["equilibrio"] else "nunca"
            liq = cor(f"{brl(c['liquido']):>14}", "verde" if c["liquido"] >= 0 else "vermelho")
            w(f"  {s['cen']['nome'][:25]:<26}{brl(s['melhor']['valor']):>13}{brl(c['pago']):>14}"
              f"{brl(c['recebido']):>15}{liq}"
              f"{tir:>7}{eq:>10}")
        w("")
        w("  Liquido > 0: contribuir compensa a essa taxa. TIR: rendimento real que as contribuicoes")
        w("  'pagam' na forma de beneficio maior. Equilib.: idade a partir da qual o que foi pago a mais")
        w("  ja voltou (em valor presente) - se viver menos que isso, contribuir nao compensa.")

    w("")
    w(titulo("ATENCAO"))
    L.extend(fmt_avisos(list(ctx0["avisos"]) + [
        (INFO, "Nao considera imposto de renda (deducao das contribuicoes nem IR sobre o beneficio), ganho real "
               "do salario minimo, pensao por morte nem a cobertura de auxilio-doenca/invalidez enquanto contribui."),
        (INFO, "Simulacao para planejamento; nao substitui a analise do INSS ou de um advogado."),
    ]))
    return "\n".join(L)


def main():
    p = argparse.ArgumentParser(description="Analise de aposentadoria (RGPS) a partir do PDF do CNIS.")
    p.add_argument("cnis", help="PDF do CNIS (Extrato Previdenciario do Meu INSS)")
    p.add_argument("--sexo", required=True, choices=["M", "F", "m", "f"], help="O CNIS nao informa o sexo")
    p.add_argument("--nascimento", help="dd/mm/aaaa (se o PDF nao trouxer)")
    p.add_argument("--salario-futuro", type=float, help="salario de contribuicao projetado (padrao: media dos ultimos 12 meses)")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--continuar", action="store_true", help="projeta contribuicoes futuras mesmo se parou")
    g.add_argument("--parar", action="store_true", help="nao projeta novas contribuicoes")
    p.add_argument("--ate-idade", type=float, help="contribui ate essa idade e para")
    p.add_argument("--es", type=float, help="expectativa de sobrevida para o fator previdenciario (sobrepoe a tabua embutida)")
    p.add_argument("--licenca-sem-remuneracao", action="store_true",
                   help="meses sem remuneracao dentro de um vinculo foram licenca nao remunerada: nao contam tempo nem carencia")
    p.add_argument("--cenarios", action="store_true",
                   help="simula os cenarios de contribuicao do arquivo de configuracao e compara custo x beneficio")
    p.add_argument("--config", help="arquivo JSON de configuracao (padrao: cnis_config.json na pasta atual ou do script)")
    p.add_argument("--inpc-csv", help="arquivo 'MM/AAAA;variacao%%' se nao houver internet")
    p.add_argument("--atualizar-inpc", action="store_true",
                   help="baixa o INPC de novo, mesmo que o cache local esteja em dia")
    p.add_argument("--saida", help="salva o relatorio em arquivo de texto")
    p.add_argument("--debug", action="store_true", help="salva o texto extraido do PDF ao lado do arquivo")
    p.add_argument("--cores", choices=["auto", "sempre", "nunca"], default="auto",
                   help="cores na saida (auto: so no terminal e sem a variavel NO_COLOR)")
    args = p.parse_args()
    Cor.ativo = args.cores == "sempre" or (args.cores == "auto" and sys.stdout.isatty()
                                           and "NO_COLOR" not in os.environ)

    cnis = ler_cnis(args.cnis, args.debug)
    nasc = parse_data(args.nascimento) if args.nascimento else cnis["nascimento"]
    if not nasc:
        sys.exit("Data de nascimento nao encontrada no PDF. Informe com --nascimento dd/mm/aaaa")
    sexo = args.sexo.upper()
    if args.cenarios:
        cfg, fonte_cfg = carregar_config(args.config)
        txt = relatorio_cenarios(cnis, sexo, nasc, simular_cenarios(cnis, sexo, nasc, args, cfg), cfg, fonte_cfg)
    else:
        resultados, ctx = analisar(cnis, sexo, nasc, args)
        txt = relatorio(cnis, sexo, nasc, resultados, ctx)
    print(txt)
    if args.saida:
        Path(args.saida).write_text(ANSI_RE.sub("", txt) + "\n", encoding="utf-8")
        print(f"\nRelatorio salvo em {args.saida}")


if __name__ == "__main__":
    main()
