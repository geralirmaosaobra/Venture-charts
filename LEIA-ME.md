# Como descarregar os vídeos (Windows)

Guia passo a passo. Copia os comandos exatamente como estão.

> ⚠️ **Antes de começar: verifica o espaço livre no disco.**
> Cada vídeo tem mais de 30 MB e são cerca de 80. Isso é **no mínimo 2,5 GB**, mas se os vídeos forem maiores pode chegar a **20 GB ou mais**.
> Abre o Explorador → **Este PC** e vê quanto espaço livre tem o disco. Depois do teste do passo 4, vê o tamanho desse vídeo e multiplica por 80 para teres uma estimativa.

---

## 1. Confirmar que o Python está instalado

1. Abre a linha de comandos (tecla Windows → escreve `cmd` → Enter).
2. Escreve:

   ```
   python --version
   ```

3. Se aparecer algo como `Python 3.12.4`, está tudo bem. Usa `python` nos comandos deste guia.
4. Se der erro, experimenta:

   ```
   py --version
   ```

   Se este funcionar, usa **`py`** em vez de `python` em todos os comandos deste guia (por exemplo `py baixar_videos.py --limit 1`).

**Se nenhum funcionar** (ou se abrir a Microsoft Store):

1. Vai a <https://www.python.org/downloads/> e descarrega a versão mais recente.
2. Ao instalar, **ativa a caixa "Add Python to PATH"** (fica em baixo, no primeiro ecrã do instalador).
3. Fecha a linha de comandos, abre uma nova e repete o `python --version`.

A versão tem de ser **3.8 ou mais recente**.

## 2. Abrir a linha de comandos na pasta certa

1. Abre no Explorador a pasta onde está o `baixar_videos.py`.
2. Clica na **barra de endereço** (onde aparece o caminho da pasta, em cima).
3. Apaga o que lá está, escreve `cmd` e carrega em **Enter**.

Abre-se uma janela preta já nessa pasta. Para confirmar, escreve `dir`: deves ver `baixar_videos.py` e `links.txt` na lista.

## 3. Preencher o links.txt

1. Abre o `links.txt` (duplo clique abre no Bloco de Notas).
2. Cola os links, **um por linha**, por baixo da linha que começa por `#`. Exemplo:

   ```
   # cola aqui os links, um por linha
   https://venture-charts.com/wp-content/uploads/2025/06/video1.mp4
   https://venture-charts.com/wp-content/uploads/2025/06/video2.mp4
   ```

3. Grava (**Ctrl+S**).

Linhas vazias e linhas que começam por `#` são ignoradas. Linhas que não sejam links `http`/`https` dão um aviso, mas o script continua.

## 4. Fazer um teste com 1 vídeo (faz isto primeiro!)

```
python baixar_videos.py --limit 1
```

Isto descarrega só o primeiro link. Vês linhas como:

```
[1/1] nome.mp4 - a descarregar (265 MB)
[1/1] nome.mp4 - 40% (106 MB / 265 MB)
[1/1] nome.mp4 - concluído (265 MB)
```

Se no fim aparecer **Concluídos: 1**, está tudo a funcionar. Abre a pasta `downloads` e confirma que o vídeo abre.

## 5. Descarregar tudo

```
python baixar_videos.py
```

- Os vídeos ficam na pasta **`downloads`**, dentro da pasta do script.
- Enquanto um vídeo não acaba, aparece com o nome `nome.mp4.part`. Só passa a `.mp4` quando está completo.
- No fim aparece um resumo: concluídos, saltados, falhados, tempo total e GB descarregados.
- Tudo fica registado em **`download.log`** (com data e hora), útil se algo correr mal.

Para guardar noutra pasta: `python baixar_videos.py --output D:\Videos`

## 6. Retomar se falhar ou se fechares a janela

**Volta simplesmente a correr o mesmo comando:**

```
python baixar_videos.py
```

- Vídeos que já estão completos são **saltados**.
- Vídeos a meio (`.part`) **continuam de onde pararam** (se o servidor permitir; se não, recomeçam esse vídeo do zero).

Para parar a meio de propósito, carrega em **Ctrl+C**. O script pára com calma e guarda o que já foi descarregado.

**Repetir só os que falharam:** no fim, os links que não ficaram concluídos são guardados em `falhados.txt`. Para tentar só esses:

```
python baixar_videos.py --input falhados.txt
```

## 7. Ajustar quantos downloads em simultâneo (--workers)

Por defeito descarrega **3 vídeos ao mesmo tempo**.

- Internet rápida e estável → podes subir para 4 ou 5:

  ```
  python baixar_videos.py --workers 5
  ```

- Erros **429** (“Demasiados pedidos”), ligações a cair ou internet lenta → baixa para 1 ou 2:

  ```
  python baixar_videos.py --workers 1
  ```

Mais workers não é sempre mais rápido: o servidor pode começar a recusar pedidos.

## 8. Resolução de problemas

| Problema | O que fazer |
|---|---|
| **"python" não é reconhecido como comando** | Usa `py` em vez de `python`. Se também não der, reinstala o Python de python.org com **"Add Python to PATH"** ativado e abre uma nova janela de cmd. |
| **Erro 403 (Proibido)** | O servidor recusou o acesso. Abre o link no browser: se não abrir lá também, o link está protegido ou expirou. Se abrir no browser mas não no script, experimenta `--workers 1` mais tarde. O script não repete estes erros (não vale a pena). |
| **Erro 404 (Não encontrado)** | O link está errado ou o ficheiro foi removido. Confirma se o link ficou completo ao colar. |
| **Disco cheio** | O script pára sozinho. Liberta espaço (ou usa `--output` para outro disco) e volta a correr: o que já está feito é saltado e os `.part` são retomados. |
| **Ligação a cair / timeout** | O script tenta de novo até 4 vezes por vídeo, com esperas cada vez maiores. Se continuar, usa `--workers 1` e depois `--input falhados.txt`. |
| **Erro 429** | O servidor pediu para abrandar. Usa `--workers 1`. |

---

> ⚠️ **Lembrete final: confirma o espaço livre em disco antes de correr tudo.**
> ~80 vídeos com mais de 30 MB cada = pelo menos 2,5 GB, possivelmente 20 GB ou mais. Faz primeiro o teste com `--limit 1` e multiplica o tamanho desse vídeo por 80.
