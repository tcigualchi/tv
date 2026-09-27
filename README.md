# YouTube na TV do CC:Tweaked

Cole um link no programa Lua `tv.lua`. O servidor Python no seu PC baixa o video, escolhe 16 cores para cada video e converte os quadros com dithering e converte o audio para DFPWM. O computador do Minecraft solicita cada quadro e pequenos trechos de audio por HTTP; o video **nao ocupa o disco do computador do jogo**.

## Instalar no Windows

1. Instale Python 3.10+ e FFmpeg. Deixe `ffmpeg` e `ffprobe` no PATH.
2. Abra PowerShell na pasta deste projeto e execute:

   ```powershell
   py -m pip install -r requirements.txt
   py server.py
   ```

3. Deixe o terminal do servidor aberto durante o uso.

`yt-dlp` e instalado pelo arquivo de requisitos. Caso o YouTube mude, atualize com `py -m pip install --upgrade yt-dlp`.

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

4. Digite a **CHAVE DE ACESSO** mostrada no servidor Python e cole o link de video quando solicitado. A chave e pedida a cada execucao e nao fica salva no jogo.

Tambem pode fornecer o link diretamente: `tv https://youtu.be/ID_DO_VIDEO https://exemplo.ngrok-free.app`. Para escolher FPS, passe um terceiro argumento de 1 a 10: `tv https://youtu.be/ID_DO_VIDEO https://exemplo.ngrok-free.app 10`. FPS maior gera mais trafego e pode engasgar em monitores grandes.

Se o Minecraft estiver no mesmo PC, pode usar apenas `tv` e manter o endereco local padrao. Nesse caso, [libere o acesso a IPs locais no CC:Tweaked](https://tweaked.cc/guide/local_ips.html).

## Limites e problemas comuns

- A URL do ngrok e publica: mantenha a chave privada. Ela muda sempre que o servidor Python reinicia, a menos que voce defina `CC_TV_TOKEN` no ambiente.
- O prototipo aceita videos de ate 2 minutos e roda a 8 FPS por padrao; nao suporta lives em andamento. O servidor faz a conversao inteira antes de tocar. Conforme monitor e computador, os quadros podem atrasar e o som perder sincronia.
- O programa ajusta automaticamente a resolucao ao tamanho real do monitor em escala de texto 0.5 e preserva a proporcao do video (com faixas pretas quando preciso). Se o monitor for muito grande (mais de 12.000 caracteres), o servidor recusa para evitar sobrecarga; diminua o monitor ou aumente o limite no codigo.
- Se aparecer `Domain not permitted`, verifique se HTTP esta ativo e se o host ngrok e permitido. Se aparecer `Servidor inacessivel`, confirme se ngrok e Python continuam ligados e se a URL HTTPS esta correta. Se aparecer `Chave de acesso incorreta`, copie a chave mostrada pelo Python, nao o authtoken do ngrok.
- Alguns videos nao podem ser baixados (restricao regional, login, DRM, mudancas no YouTube). Tente um video publico curto e atualize `yt-dlp`.
- O servidor e o conversor devem ser usados somente com conteudo para o qual voce tem permissao de acesso e uso.

Codigo independente; nao usa os arquivos `.mcanim` nem as bibliotecas Lua do projeto NeuGoga.
