class TalkCapture extends AudioWorkletProcessor {
  constructor() {
    super();
    this.capturing = false;
    this.frame = new Int16Array(320);
    this.port.onmessage = ({ data }) => {
      if (data === "press") {
        this.capturing = true;
        this.position = this.weight = this.total = 0;
      } else if (this.capturing) {
        this.capturing = false;
        if (data === "release" && this.position) this.send();
        this.port.postMessage(data);
      }
    };
  }

  send() {
    const buffer = this.frame.slice(0, this.position).buffer;
    this.port.postMessage(buffer, [buffer]);
    this.position = 0;
  }

  process(inputs) {
    if (!this.capturing || !inputs[0]?.[0]) return true;
    const width = sampleRate / 16000;
    for (const sample of inputs[0][0]) {
      const portion = Math.min(1, width - this.weight);
      this.total += sample * portion;
      this.weight += portion;
      if (this.weight + 1e-8 < width) continue;
      this.frame[this.position++] = Math.max(-32768, Math.min(32767, this.total / width * 32768));
      if (this.position === this.frame.length) this.send();
      this.weight = 1 - portion;
      this.total = sample * this.weight;
    }
    return true;
  }
}

registerProcessor("talk-capture", TalkCapture);
