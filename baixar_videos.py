#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
baixar_videos.py - descarrega ficheiros MP4 a partir de uma lista de URLs.

Uso rápido:
    python baixar_videos.py --limit 1          (teste com 1 vídeo)
    python baixar_videos.py                    (descarrega todos)
    python baixar_videos.py --input falhados.txt

Usa apenas a biblioteca standard (Python 3.8+). Ver LEIA-ME.md.
"""

import argparse
import errno
import http.client
import logging
import os
import re
import socket
import sys
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote, urlparse
from urllib.request import Request, urlopen

# ---------------------------------------------------------------------------
# Configuração
# ---------------------------------------------------------------------------

# User-Agent de um browser normal (muitos servidores WordPress bloqueiam o do Python)
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
TAMANHO_BLOCO = 1024 * 1024             # lê 1 MB de cada vez (nunca o ficheiro inteiro)
TIMEOUT = 30                            # segundos sem resposta até considerar a ligação perdida
NOVAS_TENTATIVAS = 4                    # tentativas extra por ficheiro (5 no total)
ESPERA_BASE = 5                         # espera crescente: 5 s, 10 s, 20 s, 40 s
ESPERA_MAX = 120                        # nunca espera mais do que isto
MARCO_PERCENT = 10                      # mostra o progresso a cada 10 %
MARCO_SEM_TAMANHO = 50 * 1024 * 1024    # sem Content-Length: mostra a cada 50 MB

FICHEIRO_LOG = "download.log"
FICHEIRO_FALHADOS = "falhados.txt"

# Erros em que não vale a pena tentar de novo
ERROS_SEM_REPETIR = {
    401: "401 Não autorizado - o link exige login.",
    403: "403 Proibido - o servidor recusou o acesso (link protegido ou bloqueio).",
    404: "404 Não encontrado - o link está errado ou o ficheiro foi removido.",
    410: "410 Removido - o ficheiro já não existe no servidor.",
}

# Caracteres proibidos em nomes de ficheiro no Windows
CARACTERES_INVALIDOS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
NOMES_RESERVADOS = (
    {"CON", "PRN", "AUX", "NUL"}
    | {"COM%d" % i for i in range(1, 10)}
    | {"LPT%d" % i for i in range(1, 10)}
)

# ---------------------------------------------------------------------------
# Estado partilhado entre threads
# ---------------------------------------------------------------------------

lock_print = threading.Lock()   # impede que as linhas de várias threads se misturem
parar = threading.Event()       # ligado com Ctrl+C ou disco cheio
log = logging.getLogger("baixar_videos")


class ErroDefinitivo(Exception):
    """Erro em que não vale a pena tentar de novo."""


class ErroIncompleto(Exception):
    """Transferência cortada ou inconsistente: vale a pena tentar de novo."""


class Interrompido(Exception):
    """Pedido para parar (Ctrl+C ou disco cheio)."""


class Contador:
    """Soma os bytes descarregados por todas as threads."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.bytes = 0

    def somar(self, n: int) -> None:
        with self._lock:
            self.bytes += n


# ---------------------------------------------------------------------------
# Utilitários
# ---------------------------------------------------------------------------

def mostrar(msg: str) -> None:
    """Imprime uma linha inteira de cada vez (seguro com várias threads)."""
    with lock_print:
        print(msg, flush=True)


def mb(n: int) -> str:
    return "{:.0f} MB".format(n / (1024 * 1024))


def duracao(segundos: float) -> str:
    segundos = int(segundos)
    h, resto = divmod(segundos, 3600)
    m, s = divmod(resto, 60)
    if h:
        return "{} h {} min {} s".format(h, m, s)
    if m:
        return "{} min {} s".format(m, s)
    return "{} s".format(s)


def preparar_consola() -> None:
    # Evita erros com acentos/caracteres estranhos na consola do Windows
    for fluxo in (sys.stdout, sys.stderr):
        try:
            fluxo.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass


def configurar_log() -> None:
    handler = logging.FileHandler(FICHEIRO_LOG, mode="a", encoding="utf-8")
    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
    log.addHandler(handler)
    log.setLevel(logging.INFO)


def url_para_pedido(url: str) -> str:
    # Codifica espaços e acentos (o urllib não os aceita); mantém o que já vem com %xx
    return quote(url, safe="%/:=&?~#+!$,;'@()*[]")


# ---------------------------------------------------------------------------
# Leitura da lista de links
# ---------------------------------------------------------------------------

def ler_links(caminho: Path) -> List[str]:
    links = []  # type: List[str]
    vistos = set()
    # utf-8-sig: aceita o BOM que o Bloco de Notas do Windows põe no início
    with caminho.open("r", encoding="utf-8-sig", errors="replace") as f:
        for num, linha in enumerate(f, start=1):
            linha = linha.strip()
            if not linha or linha.startswith("#"):
                continue
            partes = urlparse(linha)
            if partes.scheme.lower() not in ("http", "https") or not partes.netloc:
                mostrar("AVISO: linha {} ignorada (não é um link http/https): {}".format(
                    num, linha[:100]))
                log.warning("Linha %d ignorada (inválida): %s", num, linha)
                continue
            if linha in vistos:
                mostrar("AVISO: linha {} ignorada (link repetido).".format(num))
                continue
            vistos.add(linha)
            links.append(linha)
    return links


# ---------------------------------------------------------------------------
# Nomes dos ficheiros
# ---------------------------------------------------------------------------

def nome_a_partir_do_url(url: str, indice: int) -> str:
    """Nome = fim do URL, descodificado e limpo para o Windows."""
    ultimo = urlparse(url).path.rstrip("/").split("/")[-1]
    nome = unquote(ultimo)
    nome = CARACTERES_INVALIDOS.sub("_", nome).strip().rstrip(". ")
    if not nome:
        nome = "video_{}".format(indice)
    if not nome.lower().endswith(".mp4"):
        nome += ".mp4"
    raiz = nome[:-4]
    # Nomes como CON, NUL, COM1... são proibidos no Windows
    if raiz.split(".")[0].upper() in NOMES_RESERVADOS:
        raiz = "_" + raiz
    # Evita caminhos demasiado longos no Windows
    if len(raiz) > 150:
        raiz = raiz[:150].rstrip(". ")
    return raiz + nome[-4:]


def atribuir_nomes(links: List[str]) -> List[str]:
    """Dá um nome único a cada link (sufixo _2, _3... se houver repetidos)."""
    usados = set()
    nomes = []
    for i, url in enumerate(links, start=1):
        base = nome_a_partir_do_url(url, i)
        raiz, ext = base[:-4], base[-4:]
        nome = base
        n = 2
        # O Windows não distingue maiúsculas de minúsculas
        while nome.lower() in usados:
            nome = "{}_{}{}".format(raiz, n, ext)
            n += 1
        usados.add(nome.lower())
        nomes.append(nome)
    return nomes


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------

def ler_content_range(valor: Optional[str]) -> Tuple[Optional[int], Optional[int]]:
    """'bytes 100-999/1000' -> (100, 1000). Total None se for '*'."""
    if not valor:
        return None, None
    m = re.match(r"\s*bytes\s+(\d+)-\d+/(\d+|\*)", valor)
    if not m:
        return None, None
    total = int(m.group(2)) if m.group(2) != "*" else None
    return int(m.group(1)), total


def total_do_416(valor: Optional[str]) -> Optional[int]:
    """'bytes */1000' -> 1000."""
    if not valor:
        return None
    m = re.match(r"\s*bytes\s+\*/(\d+)", valor)
    return int(m.group(1)) if m else None


def ler_content_length(resposta) -> Optional[int]:
    valor = resposta.headers.get("Content-Length")
    try:
        return int(valor) if valor is not None else None
    except ValueError:
        return None


def apagar(caminho: Path) -> None:
    try:
        caminho.unlink()
    except FileNotFoundError:
        pass


def transferir(url: str, parte: Path, etiqueta: str, contador: Contador) -> int:
    """Descarrega para o .part (retomando se possível). Devolve o tamanho final."""
    ja_tenho = parte.stat().st_size if parte.exists() else 0
    cabecalhos = {
        "User-Agent": USER_AGENT,
        "Accept": "*/*",
        "Accept-Encoding": "identity",
    }
    if ja_tenho > 0:
        cabecalhos["Range"] = "bytes={}-".format(ja_tenho)

    try:
        resposta = urlopen(Request(url_para_pedido(url), headers=cabecalhos), timeout=TIMEOUT)
    except HTTPError as e:
        if e.code == 416 and ja_tenho > 0:
            # 416: o servidor diz que não há mais nada a partir daqui
            total = total_do_416(e.headers.get("Content-Range"))
            e.close()
            if total is not None and total == ja_tenho:
                return ja_tenho  # o .part já estava completo
            apagar(parte)
            raise ErroIncompleto("o servidor rejeitou a retoma (416); a recomeçar do zero")
        raise

    with resposta:
        tipo = (resposta.headers.get("Content-Type") or "").lower()
        if tipo.startswith("text/html"):
            raise ErroDefinitivo(
                "o servidor devolveu uma página web em vez do vídeo "
                "(link errado, protegido ou bloqueado)")

        if ja_tenho > 0 and resposta.getcode() == 206:
            inicio, total = ler_content_range(resposta.headers.get("Content-Range"))
            if inicio != ja_tenho:
                apagar(parte)
                raise ErroIncompleto("retoma desalinhada; a recomeçar do zero")
            modo = "ab"
            mostrar("{} - a retomar a partir de {}".format(etiqueta, mb(ja_tenho)))
        else:
            if ja_tenho > 0:
                # Resposta 200 em vez de 206: o servidor não suporta retoma
                mostrar("{} - o servidor não permite retomar; a recomeçar do zero".format(etiqueta))
            ja_tenho = 0
            modo = "wb"
            total = ler_content_length(resposta)

        recebido = ja_tenho
        if total:
            mostrar("{} - a descarregar ({})".format(etiqueta, mb(total)))
            proximo_marco = (recebido * 100 // total) // MARCO_PERCENT * MARCO_PERCENT + MARCO_PERCENT
        else:
            mostrar("{} - a descarregar (tamanho desconhecido)".format(etiqueta))
            proximo_marco = recebido + MARCO_SEM_TAMANHO

        with parte.open(modo) as f:
            while True:
                if parar.is_set():
                    raise Interrompido()
                bloco = resposta.read(TAMANHO_BLOCO)
                if not bloco:
                    break
                f.write(bloco)
                recebido += len(bloco)
                contador.somar(len(bloco))

                # Progresso só em marcos, para não inundar o terminal
                if total:
                    pct = recebido * 100 // total
                    if pct >= proximo_marco and pct < 100:
                        mostrar("{} - {}% ({} / {})".format(etiqueta, pct, mb(recebido), mb(total)))
                        proximo_marco = pct // MARCO_PERCENT * MARCO_PERCENT + MARCO_PERCENT
                elif recebido >= proximo_marco:
                    mostrar("{} - {} descarregados".format(etiqueta, mb(recebido)))
                    proximo_marco = recebido + MARCO_SEM_TAMANHO

    # Só aceita o ficheiro se o tamanho bater certo com o Content-Length
    if total is not None:
        if recebido < total:
            raise ErroIncompleto("transferência incompleta ({} de {})".format(mb(recebido), mb(total)))
        if recebido > total:
            apagar(parte)
            raise ErroIncompleto("tamanho maior do que o esperado; a recomeçar do zero")
    return recebido


def disco_cheio(e: BaseException) -> bool:
    if not isinstance(e, OSError):
        return False
    return e.errno == errno.ENOSPC or getattr(e, "winerror", None) in (39, 112)


def explicar_erro(e: BaseException) -> Tuple[str, bool, Optional[float]]:
    """Devolve (mensagem clara, vale a pena repetir?, espera sugerida)."""
    if isinstance(e, ErroDefinitivo):
        return str(e), False, None
    if isinstance(e, ErroIncompleto):
        return str(e), True, None
    if isinstance(e, HTTPError):
        if e.code in ERROS_SEM_REPETIR:
            return ERROS_SEM_REPETIR[e.code], False, None
        if e.code == 429:
            espera = None
            try:
                espera = float(e.headers.get("Retry-After", ""))
            except (TypeError, ValueError):
                pass
            return ("429 Demasiados pedidos - o servidor pediu para abrandar "
                    "(experimenta --workers 1)"), True, espera
        if e.code >= 500 or e.code == 408:
            return "{} erro temporário no servidor".format(e.code), True, None
        return "HTTP {} {}".format(e.code, e.reason), False, None
    if isinstance(e, URLError):
        if isinstance(e.reason, socket.timeout):
            return "o servidor não respondeu a tempo (timeout)", True, None
        return "sem ligação ao servidor: {}".format(e.reason), True, None
    if isinstance(e, socket.timeout):
        return "a ligação parou de responder (timeout)", True, None
    if isinstance(e, (ConnectionError, http.client.HTTPException)):
        return "a ligação caiu: {}".format(e.__class__.__name__), True, None
    if isinstance(e, OSError):
        return "erro de ficheiro/sistema: {}".format(e), True, None
    return "erro inesperado: {!r}".format(e), False, None


def descarregar(indice: int, total: int, url: str, destino: Path,
                contador: Contador) -> Tuple[str, str]:
    """Trata um ficheiro. Devolve (estado, motivo).
    estado: concluido | saltado | falhado | interrompido"""
    etiqueta = "[{}/{}] {}".format(indice, total, destino.name)

    # Se já existe, salta (permite correr o script outra vez)
    if destino.exists():
        mostrar("{} - já existe, saltado".format(etiqueta))
        log.info("Saltado (já existe): %s", destino.name)
        return "saltado", ""

    parte = destino.with_name(destino.name + ".part")
    for tentativa in range(1, NOVAS_TENTATIVAS + 2):
        if parar.is_set():
            return "interrompido", ""
        try:
            tamanho = transferir(url, parte, etiqueta, contador)
            os.replace(str(parte), str(destino))  # só agora passa a .mp4
            mostrar("{} - concluído ({})".format(etiqueta, mb(tamanho)))
            log.info("Concluído: %s (%d bytes) <- %s", destino.name, tamanho, url)
            return "concluido", ""
        except Interrompido:
            return "interrompido", ""
        except Exception as e:  # noqa: BLE001 - queremos explicar qualquer erro
            if disco_cheio(e):
                motivo = "disco cheio - liberta espaço e volta a correr o script"
                mostrar("{} - FALHOU: {}".format(etiqueta, motivo))
                log.error("%s: %s (%s)", destino.name, motivo, url)
                parar.set()  # não adianta continuar os outros
                return "falhado", motivo

            motivo, repetir, espera = explicar_erro(e)
            log.error("%s (tentativa %d): %s | %s", destino.name, tentativa, motivo, url)

            if not repetir or tentativa > NOVAS_TENTATIVAS:
                mostrar("{} - FALHOU: {}".format(etiqueta, motivo))
                return "falhado", motivo

            if espera is None:
                espera = ESPERA_BASE * (2 ** (tentativa - 1))
            espera = min(espera, ESPERA_MAX)
            mostrar("{} - erro: {}. Nova tentativa {}/{} daqui a {:.0f} s".format(
                etiqueta, motivo, tentativa, NOVAS_TENTATIVAS, espera))
            # wait() em vez de sleep(): acorda logo se carregares em Ctrl+C
            if parar.wait(espera):
                return "interrompido", ""
    return "falhado", "esgotadas as tentativas"


# ---------------------------------------------------------------------------
# Programa principal
# ---------------------------------------------------------------------------

def inteiro_positivo(valor: str) -> int:
    try:
        n = int(valor)
    except ValueError:
        raise argparse.ArgumentTypeError("tem de ser um número inteiro")
    if n < 1:
        raise argparse.ArgumentTypeError("tem de ser 1 ou mais")
    return n


def ler_argumentos(argv: Optional[List[str]]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Descarrega ficheiros MP4 a partir de uma lista de links.")
    p.add_argument("--input", default="links.txt",
                   help="ficheiro com os links, um por linha (por defeito: links.txt)")
    p.add_argument("--output", default="downloads",
                   help="pasta onde guardar os vídeos (por defeito: downloads)")
    p.add_argument("--workers", type=inteiro_positivo, default=3,
                   help="quantos downloads em simultâneo (por defeito: 3)")
    p.add_argument("--limit", type=inteiro_positivo, default=None,
                   help="descarrega só os primeiros N links (para testar)")
    return p.parse_args(argv)


def escrever_falhados(caminho: Path, falhados: List[Tuple[str, str]],
                      por_fazer: List[str]) -> None:
    """Escreve no mesmo formato do input, para usar com --input falhados.txt."""
    if not falhados and not por_fazer:
        if caminho.exists():
            apagar(caminho)
            mostrar("(Não houve falhas: {} antigo removido.)".format(caminho.name))
        return
    agora = datetime.now().strftime("%Y-%m-%d %H:%M")
    linhas = [
        "# Links que não ficaram concluídos ({}).".format(agora),
        "# Para repetir só estes: python baixar_videos.py --input {}".format(caminho.name),
        "",
    ]
    for url, motivo in falhados:
        linhas.append("# falhou: {}".format(motivo))
        linhas.append(url)
    if por_fazer:
        linhas.append("")
        linhas.append("# interrompidos antes de terminar:")
        linhas.extend(por_fazer)
    caminho.write_text("\n".join(linhas) + "\n", encoding="utf-8")


def main(argv: Optional[List[str]] = None) -> int:
    preparar_consola()
    args = ler_argumentos(argv)
    configurar_log()

    entrada = Path(args.input)
    if not entrada.is_file():
        mostrar("ERRO: não encontrei o ficheiro '{}'. Estás na pasta certa?".format(entrada))
        return 2

    links = ler_links(entrada)
    if not links:
        mostrar("Não há links válidos em '{}'. Cola os links, um por linha, e grava.".format(entrada))
        return 1

    # Os nomes são atribuídos sobre a lista toda, para serem sempre os mesmos
    tarefas = list(zip(links, atribuir_nomes(links)))
    if args.limit:
        tarefas = tarefas[:args.limit]

    pasta = Path(args.output)
    pasta.mkdir(parents=True, exist_ok=True)
    total = len(tarefas)

    mostrar("{} vídeo(s) a tratar, {} em simultâneo. Destino: {}".format(
        total, args.workers, pasta.resolve()))
    mostrar("Carrega em Ctrl+C para parar (podes retomar depois).\n")
    log.info("=== Início: %d links de %s, workers=%d, destino=%s ===",
             total, entrada, args.workers, pasta.resolve())

    contador = Contador()
    inicio = time.time()
    resultados = {}  # type: Dict[int, Tuple[str, str]]
    interrompido = False

    executor = ThreadPoolExecutor(max_workers=args.workers)
    futuros = {}
    try:
        for i, (url, nome) in enumerate(tarefas, start=1):
            f = executor.submit(descarregar, i, total, url, pasta / nome, contador)
            futuros[f] = i
        pendentes = set(futuros)
        while pendentes:
            # timeout curto: deixa o Ctrl+C ser apanhado também no Windows
            feitos, pendentes = wait(pendentes, timeout=0.5, return_when=FIRST_COMPLETED)
            for f in feitos:
                resultados[futuros[f]] = f.result()
    except KeyboardInterrupt:
        interrompido = True
        parar.set()
        mostrar("\nCtrl+C recebido - a parar... (os ficheiros .part ficam guardados para retomar)")
        log.warning("Interrompido pelo utilizador (Ctrl+C)")
        for f in futuros:
            f.cancel()
    finally:
        executor.shutdown(wait=True)

    # Recolhe o que terminou entretanto
    for f, i in futuros.items():
        if i in resultados or f.cancelled() or not f.done():
            continue
        try:
            resultados[i] = f.result()
        except Exception as e:  # noqa: BLE001
            resultados[i] = ("falhado", repr(e))

    # Resumo
    concluidos = saltados = 0
    falhados = []   # type: List[Tuple[str, str]]
    por_fazer = []  # type: List[str]
    for i, (url, _nome) in enumerate(tarefas, start=1):
        estado, motivo = resultados.get(i, ("interrompido", ""))
        if estado == "concluido":
            concluidos += 1
        elif estado == "saltado":
            saltados += 1
        elif estado == "falhado":
            falhados.append((url, motivo))
        else:
            por_fazer.append(url)

    tempo = time.time() - inicio
    gb = contador.bytes / (1024 ** 3)
    mostrar("")
    mostrar("================ RESUMO ================")
    mostrar("Concluídos:    {}".format(concluidos))
    mostrar("Saltados:      {} (já existiam)".format(saltados))
    mostrar("Falhados:      {}".format(len(falhados)))
    if por_fazer:
        mostrar("Por terminar:  {} (interrompidos)".format(len(por_fazer)))
    mostrar("Tempo total:   {}".format(duracao(tempo)))
    mostrar("Descarregado:  {:.2f} GB".format(gb))
    mostrar("Vídeos em:     {}".format(pasta.resolve()))

    caminho_falhados = Path(FICHEIRO_FALHADOS)
    escrever_falhados(caminho_falhados, falhados, por_fazer)
    if falhados or por_fazer:
        mostrar("\nLinks por concluir guardados em {}".format(caminho_falhados.resolve()))
        mostrar("Para repetir só esses: python baixar_videos.py --input {}".format(FICHEIRO_FALHADOS))
    if interrompido or por_fazer:
        mostrar("Ou volta a correr o mesmo comando: o que já está feito é saltado e os .part são retomados.")
    mostrar("Registo detalhado em {}".format(Path(FICHEIRO_LOG).resolve()))

    log.info("=== Fim: %d concluídos, %d saltados, %d falhados, %d por terminar, %s, %.2f GB ===",
             concluidos, saltados, len(falhados), len(por_fazer), duracao(tempo), gb)

    if interrompido:
        return 130
    return 1 if falhados else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        # Ctrl+C antes de os downloads começarem
        print("\nInterrompido. Podes voltar a correr o script quando quiseres.")
        sys.exit(130)
