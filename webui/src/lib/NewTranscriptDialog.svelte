<script>
  import { workspace } from "./session.svelte.js";
  import Icon from "./Icon.svelte";

  let dialog;

  $effect(() => {
    if (workspace.confirmingNew && !dialog.open) dialog.showModal();
    else if (!workspace.confirmingNew && dialog.open) dialog.close();
  });
</script>

<dialog
  bind:this={dialog}
  onclose={() => (workspace.confirmingNew = false)}
  onclick={(e) => e.target === dialog && dialog.close()}
  aria-labelledby="new-transcript-title"
>
  <div class="body">
    <span class="mark"><Icon name="check" size={18} /></span>
    <div>
      <h2 id="new-transcript-title">This transcript is already saved</h2>
      <p>
        “{workspace.current.displayTitle}” is stored — you can reopen it anytime from Transcripts. Start a new
        one?
      </p>
    </div>
  </div>
  <div class="buttons">
    <button class="btn" onclick={() => dialog.close()}>Keep this one</button>
    <!-- svelte-ignore a11y_autofocus -->
    <button class="btn primary" onclick={() => workspace.confirmNew()} autofocus>
      <Icon name="plus" /> New transcript
    </button>
  </div>
</dialog>

<style>
  dialog {
    width: min(420px, calc(100vw - 32px));
    padding: 20px;
    border: 1px solid var(--border-strong);
    border-radius: var(--radius);
    background: var(--surface);
    color: var(--text);
    box-shadow: var(--shadow);
  }

  dialog::backdrop {
    background: color-mix(in oklab, #000 40%, transparent);
  }

  .body {
    display: flex;
    gap: 14px;
  }

  .mark {
    flex: none;
    display: grid;
    place-items: center;
    width: 34px;
    height: 34px;
    border-radius: 50%;
    color: var(--ok);
    background: color-mix(in oklab, var(--ok) 14%, transparent);
  }

  h2 {
    margin: 0 0 6px;
    font-size: 15px;
    font-weight: 600;
  }

  p {
    margin: 0;
    font-size: 13.5px;
    color: var(--text-2);
    overflow-wrap: anywhere;
  }

  .buttons {
    display: flex;
    justify-content: flex-end;
    gap: 8px;
    margin-top: 20px;
  }
</style>
