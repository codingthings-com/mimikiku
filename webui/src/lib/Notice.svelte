<script>
  import { fly } from "svelte/transition";
  import { notice } from "./notice.svelte.js";
  import Icon from "./Icon.svelte";
</script>

{#if notice.text}
  <div class="notice" class:error={notice.tone === "error"} role="status" transition:fly={{ y: 12, duration: 160 }}>
    {#if notice.tone === "error"}<Icon name="alert" size={15} />{:else}<Icon name="check" size={15} />{/if}
    <span>{notice.text}</span>
    <button onclick={() => notice.dismiss()} aria-label="Dismiss"><Icon name="x" size={13} /></button>
  </div>
{/if}

<style>
  .notice {
    position: fixed;
    left: 50%;
    bottom: 24px;
    z-index: 50;
    transform: translateX(-50%);
    display: flex;
    align-items: center;
    gap: 10px;
    max-width: min(560px, calc(100vw - 32px));
    padding: 10px 12px 10px 14px;
    border: 1px solid var(--border-strong);
    border-radius: 10px;
    background: var(--surface);
    box-shadow: var(--shadow);
    color: var(--text);
    font-size: 13.5px;
  }

  .notice.error {
    color: var(--danger);
    border-color: color-mix(in oklab, var(--danger) 45%, var(--border));
  }

  span {
    flex: 1;
    color: var(--text);
  }

  button {
    display: grid;
    place-items: center;
    padding: 3px;
    border: none;
    border-radius: 5px;
    background: none;
    color: var(--text-3);
  }

  button:hover {
    color: var(--text);
  }
</style>
