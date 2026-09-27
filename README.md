# Videos da internet na TV do CC:Tweaked

Cole o link publico de um video no programa Lua `tv.lua`. O servidor Python no seu PC baixa o video, escolhe 16 cores para cada video e converte os quadros com dithering e converte o audio para DFPWM. O computador do Minecraft solicita lotes de ate 8 quadros e trechos de audio por HTTP; o video **nao ocupa o disco do computador do jogo**.

## Instalar no Windows

1. Instale Python 3.10+ e FFmpeg. Deixe `ffmpeg` e `ffprobe` no PATH.
2. Abra PowerShell na pasta deste projeto e execute:

   ```powershell
   py -m pip install -r requirements.txt
   py server.py
   ```

3. Deixe o terminal do servidor aberto durante o uso.

`yt-dlp` e instalado pelo arquivo de requisitos. Caso algum site mude, atualize com `py -m pip install --upgrade yt-dlp`.

## Acessar pelo ngrok (Windows)

1. Instale o [ngrok para Windows](https://ngrok.com/download/windows), crie uma conta e configure seu authtoken conforme o painel do ngrok (nao compartilhe esse token):

   ```powershell
   ngrok config add-authtoken SEU_AUTHTOKEN
   ```

2. Com `py server.py` aberto em um terminal, abra outro e inicie:

   ```powershell
   ngrok http 8765
   ```

3. Copie **somente** o endereco HTTPS `Forwarding` que aparecer, como `https://exemplo.ngrok-free.app`. Deixe ambos os terminais abertos. O servidor mostra uma **CHAVE DE ACESSO** ao iniciar; essa chave e diferente do authtoken da sua conta ngrok.

## No Minecraft

1. Coloque `tv.lua` em `.minecraft/saves/NOME_DO_MUNDO/computercraft/computer/ID/tv.lua`. Digite `id` no computador para descobrir o ID. Em servidor dedicado, a pasta pertence ao **servidor Minecraft**. Conecte um monitor avancado e speakers, se quiser audio.
2. Habilite HTTP no CC:Tweaked. Links HTTPS publicos do ngrok geralmente usam a regra publica padrao; nao e preciso desbloquear enderecos locais ao usar ngrok.
3. Inicie o Lua passando a URL HTTPS gerada pelo ngrok:

   ```lua
   tv https://exemplo.ngrok-free.app
   ```

4. Digite a **CHAVE DE ACESSO** mostrada no servidor Python e cole o link do video quando solicitado. A chave e pedida apenas na primeira execucao e fica salva em `tv_config.json` no computador do Minecraft. Se ela mudar, o Lua pede a nova.

Tambem pode fornecer o link diretamente: `tv https://exemplo.com/video https://exemplo.ngrok-free.app`. Para escolher FPS, passe um terceiro argumento de 1 a 10: `tv https://exemplo.com/video https://exemplo.ngrok-free.app 10`. FPS maior gera mais trafego e pode engasgar em monitores grandes.

Se o Minecraft estiver no mesmo PC, pode usar apenas `tv` e manter o endereco local padrao. Nesse caso, [libere o acesso a IPs locais no CC:Tweaked](https://tweaked.cc/guide/local_ips.html).

## Limites e problemas comuns

- A URL do ngrok e publica: mantenha a chave privada. Ela muda sempre que o servidor Python reinicia, a menos que voce defina `CC_TV_TOKEN` no ambiente.
- O prototipo aceita videos de ate 5 minutos e roda a 8 FPS por padrao. Em 5 minutos isso gera cerca de 300 pedidos de quadros, em vez de 2400; nao suporta lives em andamento. O servidor faz a conversao inteira antes de tocar. Conforme monitor e computador, os quadros podem atrasar e o som perder sincronia.
- O programa ajusta automaticamente a resolucao ao tamanho real do monitor em escala de texto 0.5 e preserva a proporcao do video (com faixas pretas quando preciso). O limite agora e de 32.000 caracteres por quadro, com largura ate 320 e altura ate 160. Monitores 8x6 cabem nesse limite; o Lua diminui automaticamente o numero de quadros por pedido para manter a transferencia abaixo de 96 KB. Em monitores grandes, a conversao demora mais; experimente 5 FPS se houver travamentos.
- Se aparecer `Domain not permitted`, verifique se HTTP esta ativo e se o host ngrok e permitido. Se aparecer `Servidor inacessivel`, confirme se ngrok e Python continuam ligados e se a URL HTTPS esta correta. Se aparecer `Chave de acesso incorreta`, copie a chave mostrada pelo Python, nao o authtoken do ngrok.
- Se o YouTube exigir `Sign in to confirm you're not a bot`, teste no Kali com `.venv/bin/python -m yt_dlp --cookies-from-browser firefox --simulate 'URL_DO_VIDEO'` (substitua `firefox` pelo navegador que esta logado). Se funcionar, acrescente `CC_TV_COOKIES_BROWSER=firefox` em `~/.config/cc-tv/server.env` e execute `systemctl --user restart cc-tv.service`. Alternativamente, defina `CC_TV_COOKIES_FILE=/caminho/absoluto/youtube-cookies.txt` para um arquivo Netscape exportado apenas do YouTube. As opcoes sao usadas so para links do YouTube; o arquivo tem prioridade sobre o navegador. Reinicie o servico apos alterar a configuracao. Nao coloque cookies na pasta publica nem compartilhe o arquivo; use preferencialmente uma conta separada, pois os downloads podem afetar a conta. Mesmo com cookies, alguns videos permanecem indisponiveis ou podem exigir ajustes futuros no yt-dlp.
- Apenas videos com duracao de ate 300 segundos sao aceitos. Se um download falhar, o Python mostra a causa enviada pelo `yt-dlp` (video acima do limite, bloqueio regional, login, DRM ou mudancas no site). Tente um link publico curto e atualize `yt-dlp`.
- O servidor e o conversor devem ser usados somente com conteudo para o qual voce tem permissao de acesso e uso.

Codigo independente; nao usa os arquivos `.mcanim` nem as bibliotecas Lua do projeto NeuGoga.

## Sites e links aceitos

Aceita links publicos HTTP/HTTPS de sites compativeis com `yt-dlp`, incluindo links diretos de arquivo de video quando o extrator generico conseguir identifica-los. Alguns sites exigem login, bloqueiam download ou mudam o formato; nao existe garantia para todos os links. Links para localhost, IPs privados e redes locais sao recusados. Quando a duracao nao esta disponivel nos metadados, o download pode ser feito, mas a reproducao e cortada em 5 minutos. Para consultar sites conhecidos: https://github.com/yt-dlp/yt-dlp/blob/master/supportedsites.md

## Interface do computador

O arquivo `tv.lua` agora abre um player no terminal do computador; o monitor mostra somente o video. Digite a chave uma vez na primeira execucao. A lista fica salva em `tv_playlist.json` dentro do computador. E necessario um **computador avancado** para clicar com o mouse; as teclas funcionam sem mouse.

- **A / Adicionar:** cole um link de video na lista. **Setas e Enter / Tocar:** selecione e reproduza. Um link fornecido como argumento e incluido na lista.
- **+ e - / Vol:** altere o volume do speaker em passos de 25%, de 0% a 300%. O novo volume vale para os proximos blocos de audio.
- **S / Stop:** interrompe video, audio e conversao no PC. **N / Next:** passa para o proximo item da lista. **D / Apagar:** remove o item selecionado. **Q:** fecha o player.
- Ao terminar um video, o proximo item da lista comeca automaticamente. No fim da lista o player para para evitar gasto continuo de banda. O botao Next pode voltar ao inicio manualmente.

Para abrir sem video predefinido: `tv https://SUA_URL.ngrok-free.dev`.

## Controle web no celular ou PC

Abra no navegador a mesma URL HTTPS do ngrok usada pelo `tv.lua`. A pagina `dashboard.html` mostra o video atual, o tempo de reproducao e o estado da TV. Digite a chave de acesso do servidor para entrar; a pagina oferece Play/Pause, Stop, Next, volume de 0% a 300% e um campo para adicionar links de musicas ou videos a lista do Minecraft. A interface se ajusta a telas pequenas. Os videos aparecem apenas no monitor do Minecraft. Links de audio sem video exibem barras animadas; para ouvir, conecte um speaker ao computador do jogo. Clique em Play para tocar o item selecionado apos adicionar; adicionar um link nao interrompe a musica em andamento.

A pagina usa uma interface inspirada em pixel art, com moldura de TV e botoes grandes para toque. Atualizacoes apenas do visual exigem substituir `dashboard.html` no Kali e atualizar a pagina no navegador; a lista e o player Lua nao precisam ser reinstalados.

O `tv.lua` precisa permanecer aberto no computador do Minecraft. Atualize **juntos** `server.py`, `requirements.txt`, `dashboard.html` no Kali e `tv.lua` no computador do jogo: a interface usa WebSocket para receber o estado e enviar comandos. A sessao do navegador usa cookie seguro com duracao de 24 horas; a chave nao fica salva no JavaScript nem na URL.

Ao adicionar um link pela pagina, a confirmacao aparece somente depois que o computador do Minecraft grava o item na lista. Se a TV nao confirmar em 12 segundos, o navegador mostrara uma falha e mantera o link no campo para nova tentativa. Nao repita o envio antes de conferir a lista caso haja demora na conexao.

Se voce ja instalou o servico em `~/Downloads/cc-tv-youtube`, copie os tres arquivos novos do servidor da pasta extraida para **essa pasta ativa** e execute:

```bash
cd ~/Downloads/cc-tv-youtube
.venv/bin/python -m pip install -r requirements.txt
systemctl --user restart cc-tv.service
curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8765/
```

O ultimo comando deve mostrar `200`. Atualize `tv.lua` no computador do Minecraft, reinicie o programa `tv` e acesse o endereco HTTPS do ngrok no navegador. A chave que voce ja configurou no servico continua valida. Se o servico mostrar erro, consulte `journalctl --user -u cc-tv.service -n 40 --no-pager`.

## Servidor Kali Linux 24 horas

Coloque a pasta extraida em um local permanente do notebook (por exemplo `~/cc-tv-youtube`). Instale Python, ambiente virtual e FFmpeg:

```bash
sudo apt update
sudo apt install python3 python3-venv python3-pip ffmpeg
```

Instale o [ngrok para Linux](https://ngrok.com/download/linux) e associe **sua conta** no Kali (o authtoken nao deve ser enviado a amigos):

```bash
ngrok config add-authtoken SEU_AUTHTOKEN
```

Confira no painel ngrok se `strength-ranging-buddhist.ngrok-free.dev` pertence a sua conta; use a URL HTTPS reservada da sua conta se for diferente. Na pasta deste projeto, execute:

```bash
bash install_kali.sh https://strength-ranging-buddhist.ngrok-free.dev
sudo loginctl enable-linger "$USER"
```

O instalador cria um ambiente virtual, instala os requisitos Python e configura os servicos de usuario `cc-tv.service` e `cc-tv-ngrok.service` para reiniciarem apos falhas. Ele gera uma chave permanente em `~/.config/cc-tv/server.env`; nao envie esse arquivo a ninguem. Copie **apenas a chave exibida ao final** e a URL HTTPS para os amigos. Eles usam `tv.lua` no computador do Minecraft deles e inserem a chave na primeira execucao. O Lua guarda a chave naquele computador em `tv_config.json`. Para ver a chave depois, use `sed -n 's/^CC_TV_TOKEN=//p' ~/.config/cc-tv/server.env` no Kali.

Verifique os servicos e acompanhe erros:

```bash
systemctl --user status cc-tv.service cc-tv-ngrok.service
journalctl --user -u cc-tv.service -f
journalctl --user -u cc-tv-ngrok.service -f
```

O notebook precisa ficar ligado, na tomada e sem suspender quando a tampa for fechada. Configure isso nas opcoes de energia do Kali. Se mover a pasta depois da instalacao, rode o instalador outra vez a partir do novo local. O servidor atual tem **uma reproducao/conversao ativa por vez**; se dois computadores enviarem videos diferentes, o segundo troca a reproducao do primeiro. O ngrok pode ter limites de requisicoes e transferencia de acordo com o plano da conta.
