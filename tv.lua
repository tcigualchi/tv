-- CC:Tweaked TV for server.py. Usage: tv [YouTube URL] [server URL]
local args = {...}
local monitor = peripheral.find('monitor')
if not monitor then error('Conecte um monitor avancado ao computador.', 0) end
if not http then error('Ative HTTP na configuracao do CC:Tweaked.', 0) end
local server = (args[2] or 'http://127.0.0.1:8765'):gsub('/+$', '')
local url = args[1]
local fps = tonumber(args[3]) or 8
if args[1] and args[1]:match('^https://[%w%-]+%.ngrok[%w%.%-]*$') and not args[2] then
  server, url = args[1], nil
end
if not server:match('^https?://[%w%.%-]+:?%d*$') then error('URL do servidor invalida.', 0) end
write('Chave de acesso exibida no PC: ')
local token = read('*')
if not token or token == '' then error('Chave vazia.', 0) end
local headers = {['Authorization']='Bearer ' .. token, ['ngrok-skip-browser-warning']='1'}
if not url or url == '' then
  write('Link do YouTube: ')
  url = read()
end
if not url or url == '' then error('Link vazio.', 0) end

monitor.setTextScale(0.5)
local width, height = monitor.getSize()
if not monitor.isColor() then error('Use um monitor avancado para ver cores.', 0) end
print(('Monitor: %d x %d; %d FPS; servidor: %s'):format(width, height, fps, server))
local function jsonResponse(handle)
  local raw = handle.readAll()
  local code = handle.getResponseCode()
  handle.close()
  local data = textutils.unserializeJSON(raw)
  if code >= 400 then error((data and data.error) or raw, 0) end
  if not data then error('Resposta JSON invalida.', 0) end
  return data
end
local function request(path, body, binary)
  local h, err, errorHandle
  if body then
    h, err, errorHandle = http.post(server .. path, textutils.serializeJSON(body),
      {['Content-Type']='application/json', ['Authorization']=headers.Authorization,
       ['ngrok-skip-browser-warning']='1'})
  else
    h, err, errorHandle = http.get(server .. path, headers, binary)
  end
  if not h then
    if errorHandle then
      local code = errorHandle.getResponseCode()
      local detail = errorHandle.readAll()
      errorHandle.close()
      local parsed = textutils.unserializeJSON(detail)
      error(('HTTP %d: %s'):format(code, (parsed and parsed.error) or detail:sub(1, 180)), 0)
    end
    error('Falha de conexao: ' .. tostring(err) .. ' | ' .. server, 0)
  end
  return h
end
local check = jsonResponse(request('/status'))
print('Conectado ao servidor. Estado: ' .. tostring(check.state))
local result = jsonResponse(request('/start', {url=url, width=width, height=height, fps=fps}))
print('Preparando video no PC...')
local status
repeat
  sleep(2)
  status = jsonResponse(request('/status'))
  if status.state == 'error' then error(status.message or 'Erro ao converter video.', 0) end
until status.state == 'ready'
print(('Pronto: %.1f segundos. Aperte Ctrl+T para parar.'):format(status.frames / status.fps))

local speakers = {peripheral.find('speaker')}
local function audioThread()
  if not status.audio or #speakers == 0 then return end
  local decoder = require('cc.audio.dfpwm').make_decoder()
  local n = 0
  while true do
    local h = request('/audio?n=' .. n .. '&count=8', nil, true)
    local batch = h.readAll()
    h.close()
    if #batch == 0 then return end
    for offset = 1, #batch, 6144 do
      local chunk = batch:sub(offset, offset + 6143)
      local samples = decoder(chunk)
      local tasks = {}
      for _, speaker in ipairs(speakers) do
        local sp = speaker
        tasks[#tasks+1] = function()
          while not sp.playAudio(samples) do os.pullEvent('speaker_audio_empty') end
        end
      end
      parallel.waitForAll(table.unpack(tasks))
    end
    n = n + 8
  end
end
local function videoThread()
  local blank = string.rep(' ', width)
  local fg = string.rep('0', width)
  local start = os.epoch('utc')
  monitor.setBackgroundColor(colors.black)
  monitor.clear()
  local frameSize = width * height
  for first = 0, status.frames - 1, 8 do
    local h = request('/frame?n=' .. first .. '&count=8', nil, true)
    local batch = h.readAll()
    h.close()
    local count = math.min(8, status.frames - first)
    if #batch ~= count * frameSize then error('Lote de quadros incompleto: ' .. first, 0) end
    for i = 0, count - 1 do
      local n = first + i
      local offset = i * frameSize
      for y = 1, height do
        monitor.setCursorPos(1, y)
        local left = offset + (y-1)*width+1
        monitor.blit(blank, fg, batch:sub(left, left+width-1))
      end
      local delay = (start + (n+1)*1000/status.fps - os.epoch('utc')) / 1000
      if delay > 0 then sleep(delay) else sleep(0) end
    end
  end
end
local previousPalette = {}
if status.palette then
  for i = 1, 16 do
    local colour = 2^(i-1)
    previousPalette[i] = {monitor.getPaletteColor(colour)}
    monitor.setPaletteColor(colour, tonumber(status.palette[i]:sub(2), 16))
  end
end
local ok, err = pcall(function() parallel.waitForAll(videoThread, audioThread) end)
for i, rgb in ipairs(previousPalette) do
  monitor.setPaletteColor(2^(i-1), table.unpack(rgb))
end
for _, speaker in ipairs(speakers) do pcall(speaker.stop) end
if not ok then error(err, 0) end
print('Video concluido.')
