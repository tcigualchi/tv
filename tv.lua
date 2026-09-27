-- CC:Tweaked TV: playlist/controls on the computer, video on the monitor.
-- tv [video URL] [server URL] [fps]  OR  tv [ngrok URL]
local args = {...}
local monitor = peripheral.find('monitor')
if not monitor then error('Conecte um monitor avancado.', 0) end
if not monitor.isColor() then error('Use um monitor avancado para video colorido.', 0) end
if not http then error('Ative HTTP no CC:Tweaked.', 0) end
local server = (args[2] or 'http://127.0.0.1:8765'):gsub('/+$', '')
local initialURL = args[1]
if args[1] and args[1]:match('^https://[%w%-]+%.ngrok[%w%.%-]*$') and not args[2] then
  server, initialURL = args[1], nil
end
if not server:match('^https?://[%w%.%-]+:?%d*$') then error('URL do servidor invalida.', 0) end
local fps = tonumber(args[3]) or 8
if fps < 1 or fps > 10 then error('FPS deve ser entre 1 e 10.', 0) end
monitor.setTextScale(0.5)
local width, height = monitor.getSize()

local configFile = 'tv_config.json'
local token = nil
if fs.exists(configFile) then
  local f = fs.open(configFile, 'r')
  if f then
    local saved = textutils.unserializeJSON(f.readAll())
    f.close()
    if type(saved)=='table' and saved.server==server and type(saved.token)=='string' then
      token = saved.token
    end
  end
end
local function askKey()
  write('Chave do servidor (somente uma vez): ')
  local value=read('*')
  if not value or value=='' then error('Chave vazia.',0) end
  return value
end
if not token then token=askKey() end
local headers = {Authorization='Bearer ' .. token, ['ngrok-skip-browser-warning']='1'}
local function jsonResponse(handle)
  local raw, code = handle.readAll(), handle.getResponseCode()
  handle.close()
  local value = textutils.unserializeJSON(raw)
  if code >= 400 then error((value and (value.error or value.detail)) or raw, 0) end
  if not value then error('Resposta invalida do servidor.', 0) end
  return value
end
local function request(path, body, binary)
  local h, err, failure
  if body then
    h, err, failure = http.post(server .. path, textutils.serializeJSON(body), {
      ['Content-Type']='application/json', Authorization=headers.Authorization,
      ['ngrok-skip-browser-warning']='1'})
  else
    h, err, failure = http.get(server .. path, headers, binary)
  end
  if not h then
    if failure then
      local raw, code = failure.readAll(), failure.getResponseCode()
      failure.close()
      local value = textutils.unserializeJSON(raw)
      error(('HTTP %s: %s'):format(tostring(code), (value and (value.error or value.detail)) or raw:sub(1, 140)), 0)
    end
    error('Conexao: ' .. tostring(err), 0)
  end
  return h
end
local ok, checkError=pcall(function() jsonResponse(request('/status')) end)
if not ok and tostring(checkError):find('401',1,true) then
  print('Chave antiga nao funciona. Digite a nova chave:')
  token=askKey()
  headers.Authorization='Bearer '..token
  ok,checkError=pcall(function() jsonResponse(request('/status')) end)
end
if not ok then error(checkError,0) end
local cfg=fs.open(configFile,'w')
if cfg then cfg.write(textutils.serializeJSON({server=server,token=token}));cfg.close() end


local playlistFile = 'tv_playlist.json'
local playlist = {}
if fs.exists(playlistFile) then
  local f = fs.open(playlistFile, 'r')
  if f then
    local saved = textutils.unserializeJSON(f.readAll())
    f.close()
    if type(saved) == 'table' then
      for _, item in ipairs(saved) do
        if type(item) == 'string' and #item < 1900 then playlist[#playlist+1] = item end
      end
    end
  end
end
local function save()
  local f = fs.open(playlistFile, 'w')
  if not f then return false end
  f.write(textutils.serializeJSON(playlist)); f.close()
  return true
end
if initialURL and initialURL ~= '' then playlist[#playlist+1] = initialURL; save() end
local selected = initialURL and #playlist or 1
local scroll = 0
local state = {volume=1, status='Pronto', playing=false, abort=false,
               intent=nil, quit=false, modal=false, current=nil, paused=false, progress=0, duration=0, title='', url=''}
local speakers = {peripheral.find('speaker')}
local buttons = {}
local function row(y, label, bg, fg)
  local w, h = term.getSize()
  if y < 1 or y > h then return end
  term.setBackgroundColor(bg)
  term.setTextColor(fg)
  term.setCursorPos(1, y)
  local text = tostring(label):sub(1, w)
  term.write(text .. string.rep(' ', w-#text))
end
local function button(y, label, action, x)
  local w, h = term.getSize()
  if y < 1 or y > h or x > w then return x end
  local text = ('[%s]'):format(label)
  if x + #text - 1 > w then return x end
  term.setCursorPos(x, y)
  term.setBackgroundColor(colors.gray)
  term.setTextColor(colors.white)
  term.write(text)
  buttons[#buttons+1] = {y=y, first=x, last=x+#text-1, action=action}
  return x + #text + 2
end
local function draw()
  if state.modal then return end
  local w, h = term.getSize()
  local last = math.max(3, h-5)
  local visible = math.max(1,last-3)
  if selected < scroll+1 then scroll = selected-1 end
  if selected > scroll+visible then scroll = selected-visible end
  scroll = math.max(0, scroll)
  buttons = {}
  row(1, ' CC TV  |  Monitor: '..width..'x'..height..'  '..fps..' FPS', colors.blue, colors.white)
  row(2, (' %s  |  Volume %d%%'):format(state.status, math.floor(state.volume*100+0.5)), colors.black, colors.lime)
  row(3, (' Lista (%d videos) - clique ou use as setas'):format(#playlist), colors.lightGray, colors.black)
  for y=4,last do
    local index = scroll + y-3
    local label = playlist[index] and (('%d. %s'):format(index, playlist[index])) or ''
    if index == state.current then label = '> '..label end
    row(y, label, index==selected and colors.cyan or colors.black,
        index==selected and colors.black or colors.white)
  end
  row(h-4, '', colors.black, colors.white)
  row(h-3, '', colors.black, colors.white)
  local x=2
  x=button(h-3, 'A Adicionar', 'add', x)
  x=button(h-3, 'P Tocar', 'play', x)
  button(h-3, 'D Apagar', 'delete', x)
  row(h-2, '', colors.black, colors.white)
  x=2
  x=button(h-2, '- Vol', 'down', x)
  x=button(h-2, '+ Vol', 'up', x)
  x=button(h-2, 'S Stop', 'stop', x)
  button(h-2, 'N Next', 'next', x)
  row(h-1, ' Enter: tocar | A: adicionar | Q: sair', colors.gray, colors.white)
  row(h, '', colors.black, colors.white)
end
local function status(message)
  state.status = tostring(message):sub(1, 90)
  draw()
end
local function stopSpeakers()
  for _, sp in ipairs(speakers) do pcall(sp.stop) end
end
local function playOne(index)
  state.current = index
  state.playing = true
  state.abort = false
  state.paused = false
  state.progress = 0
  state.duration = 0
  state.url = playlist[index]
  state.title = 'Video '..index
  status('Preparando video '..index..'...')
  jsonResponse(request('/start', {url=playlist[index], width=width, height=height, fps=fps}))
  local info
  repeat
    if state.abort then return false end
    sleep(2)
    if state.abort then return false end
    info = jsonResponse(request('/status'))
    if info.state == 'error' then error(info.message or 'Erro na conversao.', 0) end
  until info.state == 'ready'
  if state.abort then return false end
  state.duration = info.frames / info.fps
  state.title = info.title or state.title
  if info.palette then
    for i=1,16 do monitor.setPaletteColor(2^(i-1), tonumber(info.palette[i]:sub(2),16)) end
  end
  status(('Tocando %d/%d'):format(index, #playlist))
  local function video()
    local blank = string.rep(' ', width)
    local fg = string.rep('0', width)
    local size = width*height
    local batchSize = math.max(1, math.min(8, math.floor(96000/size)))
    local start = os.epoch('utc')
    local pausedAt = nil
    monitor.setBackgroundColor(colors.black)
    monitor.clear()
    for first=0,info.frames-1,batchSize do
      if state.abort then return end
      local h = request('/frame?n='..first..'&count='..batchSize, nil, true)
      local batch = h.readAll(); h.close()
      local count = math.min(batchSize, info.frames-first)
      if #batch ~= count*size then error('Lote de video incompleto.', 0) end
      for i=0,count-1 do
        if state.abort then return end
        if state.paused then
          pausedAt = os.epoch('utc')
          while state.paused and not state.abort do sleep(0.1) end
          if state.abort then return end
          start = start + os.epoch('utc') - pausedAt
          pausedAt = nil
        end
        local offset = i*size
        for y=1,height do
          monitor.setCursorPos(1,y)
          local pos=offset+(y-1)*width+1
          monitor.blit(blank,fg,batch:sub(pos,pos+width-1))
        end
        state.progress=(first+i+1)/info.fps
        local remaining=(start+(first+i+1)*1000/info.fps-os.epoch('utc'))/1000
        if remaining>0 then sleep(remaining) else sleep(0) end
      end
    end
  end
  local function audio()
    if not info.audio or #speakers==0 then return end
    local decoder=require('cc.audio.dfpwm').make_decoder()
    local n=0
    while not state.abort do
      local h=request('/audio?n='..n..'&count=8',nil,true)
      local batch=h.readAll(); h.close()
      if #batch==0 then return end
      for offset=1,#batch,6144 do
        if state.abort then return end
        while state.paused and not state.abort do sleep(0.1) end
        if state.abort then return end
        local samples=decoder(batch:sub(offset,offset+6143))
        local tasks={}
        for _,sp in ipairs(speakers) do
          local speaker=sp
          tasks[#tasks+1]=function()
            while not state.abort and not state.paused and not speaker.playAudio(samples,state.volume) do sleep(0.05) end
          end
        end
        parallel.waitForAll(table.unpack(tasks))
      end
      n=n+8
    end
  end
  parallel.waitForAll(video,audio)
  return not state.abort
end
local function nextIndex(index)
  if #playlist==0 then return nil end
  return (index % #playlist)+1
end
local function control(action, value)
  if action=='add' then
    state.modal=true
    local _,h=term.getSize()
    row(h,'Cole o link do video: ',colors.black,colors.yellow)
    term.setCursorPos(22,h)
    local value=read()
    state.modal=false
    if value and value:match('^https?://') then
      playlist[#playlist+1]=value
      selected=#playlist
      save()
      status('Video adicionado a lista')
    else status('Link invalido ou vazio') end
  elseif action=='add_url' then
    if type(value)=='string' and #value<=1900 and value:match('^https?://[%w%.%-]+') then
      playlist[#playlist+1]=value
      selected=#playlist
      if save() then
        status('Musica adicionada a lista')
        return true
      end
      table.remove(playlist)
      selected=math.max(1,#playlist)
    end
    status('Nao foi possivel salvar o link')
    return false
  elseif action=='delete' then
    if #playlist>0 then
      table.remove(playlist,selected)
      selected=math.max(1,math.min(selected,#playlist))
      save(); status('Removido da lista')
    end
  elseif action=='play' then
    if #playlist>0 then
      state.intent=selected; state.abort=true
      status('Abrindo video '..selected..'...')
    end
  elseif action=='stop' then
    state.intent=nil; state.abort=true; state.paused=false
    stopSpeakers()
    pcall(function() jsonResponse(request('/cancel', {})) end)
    status('Parado')
  elseif action=='next' then
    local next=nextIndex(state.current or selected)
    if next then
      selected=next; state.intent=next; state.abort=true
      stopSpeakers(); status('Proximo video')
    end
  elseif action=='pause' then
    if state.playing and not state.paused then
      state.paused=true; stopSpeakers(); status('Pausado')
    end
  elseif action=='resume' then
    if state.playing and state.paused then state.paused=false; status('Tocando') end
  elseif action=='volume' then
    if type(value)=='number' then state.volume=math.max(0,math.min(3,value)); draw() end
  elseif action=='up' then state.volume=math.min(3, state.volume+0.25); draw()
  elseif action=='down' then state.volume=math.max(0, state.volume-0.25); draw()
  elseif action=='quit' then state.quit=true; state.abort=true; stopSpeakers() end
end
local function inputLoop()
  draw()
  while not state.quit do
    local event,a,b,c=os.pullEvent()
    if event=='key' then
      if a==keys.up then selected=math.max(1,selected-1);draw()
      elseif a==keys.down then selected=math.min(#playlist,selected+1);draw()
      elseif a==keys.enter then control('play') end
    elseif event=='char' then
      local ch=a:lower()
      if ch=='a' then control('add')
      elseif ch=='p' then control('play')
      elseif ch=='s' then control('stop')
      elseif ch=='n' then control('next')
      elseif ch=='d' then control('delete')
      elseif ch=='+' or ch=='=' then control('up')
      elseif ch=='-' then control('down')
      elseif ch==' ' then control(state.paused and 'resume' or 'pause')
      elseif ch=='q' then control('quit') end
    elseif event=='mouse_click' and a==1 then
      local x,y=b,c
      local _,h=term.getSize()
      if y>=4 and y<=math.max(3,h-5) then
        local index=scroll+y-3
        if index<=#playlist then selected=index;draw() end
      else
        for _,btn in ipairs(buttons) do
          if y==btn.y and x>=btn.first and x<=btn.last then control(btn.action);break end
        end
      end
    elseif event=='mouse_scroll' then
      if a>0 then selected=math.min(#playlist,selected+1)
      else selected=math.max(1,selected-1) end
      draw()
    elseif event=='term_resize' then draw() end
  end
end
local function playbackLoop()
  while not state.quit do
    if state.intent and #playlist>0 then
      local index=state.intent
      state.intent=nil
      local ok, finished=pcall(playOne,index)
      stopSpeakers()
      state.playing=false
      state.paused=false
      state.current=nil
      state.progress=0; state.duration=0; state.url=''; state.title=''
      if not ok and state.abort then status('Parado')
      elseif not ok then status('Erro: '..tostring(finished))
      elseif not state.abort and finished and not state.intent then
        if index < #playlist then state.intent=index+1;selected=index+1
        else status('Lista concluida') end
      elseif not state.intent and state.abort then
        monitor.setBackgroundColor(colors.black)
        monitor.clear()
      end
      state.abort=false
      draw()
    else sleep(0.1) end
  end
end
local function remoteLoop()
  local wsURL=server:gsub('^http','ws')..'/ws'
  while not state.quit do
    local ok,ws=pcall(function() return http.websocket({url=wsURL,headers=headers,timeout=10}) end)
    if ok and ws then
      while not state.quit do
        local packet=textutils.serializeJSON({type='state',playing=state.playing,
          paused=state.paused,status=state.status,title=state.title,url=state.url,
          progress=state.progress,duration=state.duration,volume=state.volume})
        if not pcall(ws.send,packet) then break end
        local message,reason=ws.receive(1)
        if message then
          local data=textutils.unserializeJSON(message)
          if type(data)=='table' and data.type=='control' then
            if data.action=='add_url' then
              local saved, result=pcall(control,data.action,data.value)
              if not pcall(ws.send,textutils.serializeJSON({type='add_ack',id=data.id,
                  ok=saved and result==true})) then break end
            else
              control(data.action,data.value)
            end
          end
        elseif reason and reason~='Timed out' then break end
      end
      pcall(ws.close)
    end
    if not state.quit then sleep(3) end
  end
end
local original={}
for i=1,16 do original[i]={monitor.getPaletteColor(2^(i-1))} end
local ok,err=pcall(function() parallel.waitForAll(inputLoop,playbackLoop,remoteLoop) end)
stopSpeakers()
for i,rgb in ipairs(original) do monitor.setPaletteColor(2^(i-1),table.unpack(rgb)) end
monitor.setBackgroundColor(colors.black);monitor.clear()
term.setBackgroundColor(colors.black);term.setTextColor(colors.white);term.clear();term.setCursorPos(1,1)
if not ok then error(err,0) end
