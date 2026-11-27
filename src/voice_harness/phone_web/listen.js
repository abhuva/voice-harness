const byId = (id) => document.getElementById(id);
const form = byId("connect");
const start = byId("start");
const stop = byId("stop");
const deviceName = byId("name");
const code = byId("code");
const status = byId("status");
let ident = crypto.randomUUID();
let current = null;

try {
  ident = localStorage.getItem("voice-harness-receiver-id") || ident;
  localStorage.setItem("voice-harness-receiver-id", ident);
  deviceName.value = localStorage.getItem("voice-harness-receiver-name") || "";
} catch {
  // Pairing also works when browser storage is disabled.
}

function disconnect(receiver, message) {
  if (current !== receiver) return;
  current = null;
  clearPlayback(receiver);
  receiver.socket?.close();
  receiver.context?.close().catch(() => {});
  start.disabled = false;
  form.hidden = false;
  stop.hidden = true;
  status.textContent = message;
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (current) return;
  const receiver = { sources: new Set(), playAt: 0 };
  current = receiver;
  start.disabled = true;
  status.textContent = "Connecting playback…";
  try {
    receiver.context = new AudioContext({ latencyHint: "interactive" });
    await receiver.context.resume();
    if (current !== receiver) return;
    const socket = new WebSocket(new URL("/session", location.origin.replace(/^http/, "ws")));
    receiver.socket = socket;
    socket.binaryType = "arraybuffer";
    socket.onopen = () => socket.send(JSON.stringify({
      type: "receiver", code: code.value.trim(), id: ident, name: deviceName.value.trim(),
    }));
    socket.onmessage = ({ data }) => {
      if (current !== receiver) return;
      if (data instanceof ArrayBuffer) {
        play(receiver, data);
        return;
      }
      const message = JSON.parse(data);
      if (message.type === "ready") {
        try { localStorage.setItem("voice-harness-receiver-name", deviceName.value.trim()); } catch {}
        code.value = "";
        form.hidden = true;
        stop.hidden = false;
        status.textContent = "Playback ready. Select this device in Voice Harness on the host.";
      } else if (message.type === "flush") {
        clearPlayback(receiver);
      } else if (message.type === "ended") {
        disconnect(receiver, message.text);
      }
    };
    receiver.context.addEventListener("statechange", () => {
      if (receiver.context.state !== "running") {
        disconnect(receiver, "Playback was suspended. Connect playback again.");
      }
    });
    socket.onclose = () => disconnect(receiver, "Playback disconnected. Connect again to receive replies.");
    socket.onerror = () => disconnect(receiver, "Could not connect playback. Check Tailscale and the host.");
  } catch {
    disconnect(receiver, "Could not start playback. Check the browser's audio settings.");
  }
});

stop.addEventListener("click", () => {
  if (current) disconnect(current, "Playback disconnected.");
});
window.addEventListener("pagehide", () => {
  if (current) disconnect(current, "Playback disconnected.");
});
