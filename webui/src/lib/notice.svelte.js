// One transient message at a time, shown by <Notice /> at the bottom of the app.

class Notice {
  text = $state("");
  tone = $state("info"); // info | error
  #timer = 0;

  show(text, tone = "info", ms = 4000) {
    clearTimeout(this.#timer);
    this.text = text;
    this.tone = tone;
    this.#timer = setTimeout(() => this.dismiss(), ms);
  }

  error(text) {
    this.show(text, "error", 6000);
  }

  dismiss() {
    clearTimeout(this.#timer);
    this.text = "";
  }
}

export const notice = new Notice();
