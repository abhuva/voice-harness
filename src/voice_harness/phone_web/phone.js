const byId = (id) => document.getElementById(id);
const status = byId("status");
const form = byId("connect");
const talk = byId("talk");
const reply = byId("reply");
const enableAudio = byId("enable-audio");
let current = null;

const codeFromLink = new URLSearchParams(location.hash.slice(1)).get("code");
if (codeFromLink) {
  byId("code").value = codeFromLink;
  history.replaceState(null, "", location.pathname);
}

function signal(call, type, values = {}) {
  if (call.socket?.readyState === WebSocket.OPEN) call.socket.send(JSON.stringify({ type, ...values }));
}

function clearPlayback(call) {
  for (const source of call.sources) source.stop();
  call.sources.clear();
  call.playAt = 0;
}

function end(call, text) {
  if (current !== call) return;
  current = null;
  clearPlayback(call);
  call.socket?.close();
  call.peer?.close();
  call.stream?.getTracks().forEach((track) => track.stop());
  call.context?.close().catch(() => {});
  reply.srcObject = null;
  enableAudio.hidden = true;
  talk.disabled = true;
  talk.textContent = "Hold to talk";
  talk.setAttribute("aria-pressed", "false");
  byId("call").hidden = true;
  form.hidden = false;
  byId("start").disabled = false;
  status.textContent = text;
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

function transcript(call, message) {
  let row = call.messages.get(message.id);
  if (!row) {
    row = document.createElement("li");
    row.append(document.createElement("strong"), document.createElement("p"), document.createElement("small"));
    call.messages.set(message.id, row);
    byId("chat").append(row);
    if (call.messages.size > 100) {
      const first = call.messages.keys().next().value;
      call.messages.get(first).remove();
      call.messages.delete(first);
    }
  }
  row.children[0].textContent = message.speaker;
  row.children[1].textContent = message.body;
  row.children[2].textContent = message.status;
}

async function receive(call, data) {
  if (current !== call) return;
  if (data instanceof ArrayBuffer) {
    if (call.mode === "ptt") play(call, data);
    return;
  }
  const message = JSON.parse(data);
  switch (message.type) {
    case "answer":
      await call.peer.setRemoteDescription({ type: "answer", sdp: message.sdp });
      break;
    case "ready":
      talk.disabled = false;
      break;
    case "status":
      status.textContent = message.text;
      break;
    case "message":
      transcript(call, message);
      break;
    case "flush":
      clearPlayback(call);
      break;
    case "ended":
      end(call, message.text);
      break;
  }
}

function wsConnect(call, offer) {
  const socket = new WebSocket(new URL("/session", location.origin.replace(/^http/, "ws")));
  call.socket = socket;
  socket.binaryType = "arraybuffer";
  socket.onopen = () => signal(call, "call", { code: byId("code").value.trim(), mode: call.mode, offer });
  socket.onmessage = ({ data }) => receive(call, data).catch(() => end(call, "Phone audio could not be connected."));
  socket.onclose = () => end(call, "Disconnected from the laptop.");
  socket.onerror = () => end(call, "Could not reach Voice Harness. Check Tailscale and the laptop.");
}

async function prepareRtc(call) {
  const peer = new RTCPeerConnection({ iceServers: [] });
  call.peer = peer;
  peer.ontrack = ({ track, streams }) => {
    reply.srcObject = streams[0] || new MediaStream([track]);
    reply.play().catch(() => {
      if (current === call) enableAudio.hidden = false;
    });
  };
  peer.onconnectionstatechange = () => {
    if (peer.connectionState === "failed") end(call, "WebRTC failed to connect. Check the laptop’s Tailscale address.");
  };
  peer.addTrack(call.stream.getAudioTracks()[0], call.stream);
  await peer.setLocalDescription(await peer.createOffer());
  // The laptop sends its reachable ICE address. The phone initiates checks from its own network interfaces.
  return peer.localDescription.sdp;
}

async function prepareTalk(call) {
  await call.context.audioWorklet.addModule("/capture.js");
  const capture = new AudioWorkletNode(call.context, "talk-capture", { channelCount: 1, channelCountMode: "explicit" });
  call.capture = capture;
  const mute = call.context.createGain();
  mute.gain.value = 0;
  call.context.createMediaStreamSource(call.stream).connect(capture).connect(mute).connect(call.context.destination);
  capture.port.onmessage = ({ data }) => {
    if (current !== call || call.socket?.readyState !== WebSocket.OPEN) return;
    if (typeof data === "string") {
      signal(call, data);
      call.finishing = false;
    } else if (call.socket.bufferedAmount < 128 * 1024) {
      call.socket.send(data);
    } else {
      end(call, "The connection fell behind. Start a new call when it is stable.");
    }
  };
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (current) return;
  const mode = byId("mode").value;
  const call = { mode, sources: new Set(), messages: new Map(), playAt: 0 };
  current = call;
  byId("chat").replaceChildren();
  byId("start").disabled = true;
  byId("call").hidden = false;
  talk.hidden = mode !== "ptt";
  reply.hidden = mode !== "rtc";
  status.textContent = "Waiting for microphone permission";
  try {
    if (mode === "ptt") {
      call.context = new AudioContext({ latencyHint: "interactive" });
      await call.context.resume();
    }
    if (current !== call) return;
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, channelCount: 1 },
    });
    if (current !== call) {
      stream.getTracks().forEach((track) => track.stop());
      return;
    }
    call.stream = stream;
    stream.getAudioTracks()[0].onended = () => end(call, "The phone microphone ended.");
    form.hidden = true;
    status.textContent = "Connecting to the laptop";
    const offer = mode === "rtc" ? await prepareRtc(call) : await prepareTalk(call);
    if (current === call) wsConnect(call, offer);
  } catch {
    end(call, "Could not open the microphone. Check permission and use the private HTTPS address.");
  }
});

function press() {
  const call = current;
  if (!call || talk.disabled || call.holding || call.finishing) return;
  call.holding = true;
  talk.textContent = "Release to send";
  talk.setAttribute("aria-pressed", "true");
  signal(call, "press");
  call.capture.port.postMessage("press");
}

function release(type = "release") {
  const call = current;
  if (!call?.holding) return;
  call.holding = false;
  call.finishing = true;
  talk.textContent = "Hold to talk";
  talk.setAttribute("aria-pressed", "false");
  call.capture.port.postMessage(type);
}

talk.addEventListener("pointerdown", (event) => {
  if (event.button !== 0) return;
  talk.setPointerCapture(event.pointerId);
  press();
});
talk.addEventListener("pointerup", () => release());
talk.addEventListener("pointercancel", () => release("cancel"));
talk.addEventListener("lostpointercapture", () => release("cancel"));
talk.addEventListener("contextmenu", (event) => event.preventDefault());
talk.addEventListener("keydown", (event) => {
  if (event.key !== " " && event.key !== "Enter") return;
  event.preventDefault();
  press();
});
talk.addEventListener("keyup", (event) => {
  if (event.key !== " " && event.key !== "Enter") return;
  event.preventDefault();
  release();
});
talk.addEventListener("click", (event) => {
  if (event.detail === 0) {
    if (current?.holding) release();
    else press();
  }
});
talk.addEventListener("blur", () => release("cancel"));
reply.addEventListener("playing", () => { enableAudio.hidden = true; });
enableAudio.addEventListener("click", () => {
  reply.play().catch(() => { status.textContent = "Could not play audio. Check the phone’s browser permissions."; });
});
document.addEventListener("visibilitychange", () => {
  if (document.hidden) release("cancel");
});
byId("stop").addEventListener("click", () => {
  if (!current) return;
  signal(current, "hangup");
  end(current, "Call ended");
});
window.addEventListener("pagehide", () => {
  if (current) end(current, "Call ended");
});
