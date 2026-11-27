function clearPlayback(call) {
  for (const source of call.sources) source.stop();
  call.sources.clear();
  call.playAt = 0;
}

function play(call, bytes) {
  const raw = new DataView(bytes);
  const audio = call.context.createBuffer(1, bytes.byteLength / 2, 24000);
  const channel = audio.getChannelData(0);
  for (let index = 0; index < channel.length; index++) channel[index] = raw.getInt16(2 * index, true) / 32768;
  const source = call.context.createBufferSource();
  source.buffer = audio;
  source.connect(call.context.destination);
  const now = call.context.currentTime;
  if (call.playAt > now + .35) clearPlayback(call);
  if (call.playAt < now) call.playAt = now + .05;
  source.start(call.playAt);
  call.playAt += audio.duration;
  call.sources.add(source);
  source.onended = () => call.sources.delete(source);
}
