<script>
  import { link } from "svelte-spa-router";
  import Icon from "../lib/Icon.svelte";
</script>

<div class="home">
  <span class="logo"><Icon name="wave" size={26} /></span>
  <h1>Mimikiku</h1>
  <p>Live transcription that tells speakers apart. Everything you record is saved to your transcripts.</p>
  <div class="actions">
    <a class="btn primary" href="/new" use:link><Icon name="mic" /> New transcript</a>
    <a class="btn" href="/transcripts" use:link>Your transcripts</a>
  </div>
</div>

<style>
  .home {
    flex: 1;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 14px;
    padding: 24px 16px;
    text-align: center;
  }

  .logo {
    display: grid;
    place-items: center;
    width: 52px;
    height: 52px;
    border-radius: 14px;
    background: var(--accent);
    color: var(--accent-contrast);
  }

  h1 {
    margin: 0;
    font-size: 26px;
    font-weight: 650;
    letter-spacing: -0.02em;
  }

  p {
    max-width: 420px;
    margin: 0;
    color: var(--text-2);
    line-height: 1.5;
  }

  .actions {
    display: flex;
    flex-wrap: wrap;
    justify-content: center;
    gap: 8px;
    margin-top: 8px;
  }
</style>
