<script>
  // Minimal stroke icon set (24x24, 1.8 stroke).
  let { name, size = 16 } = $props();

  const paths = {
    mic: "M12 3a3 3 0 0 0-3 3v6a3 3 0 0 0 6 0V6a3 3 0 0 0-3-3Z M19 11a7 7 0 0 1-14 0 M12 18v3",
    stop: "M7 7h10v10H7z",
    pause: "M9 6v12 M15 6v12",
    play: "M8 5.5v13l10.5-6.5z",
    copy: "M9 9h10v11H9z M5 15V4h10",
    download: "M12 4v11 M7 10.5l5 5 5-5 M5 20h14",
    trash: "M4 7h16 M9 7V4h6v3 M6 7l1 13h10l1-13",
    search: "M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14Z M20 20l-4-4",
    users: "M9 11a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7Z M2.5 20a6.5 6.5 0 0 1 13 0 M16 4.3a3.5 3.5 0 0 1 0 6.4 M18 14.5a6.5 6.5 0 0 1 3.5 5.5",
    down: "M12 5v14 M6 13l6 6 6-6",
    check: "M5 12.5l4.5 4.5L19 7",
    clock: "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18Z M12 7v5l3 2",
    x: "M6 6l12 12 M18 6L6 18",
    chevron: "M6 9l6 6 6-6",
    activity: "M3 12h4l3-8 4 16 3-8h4",
    wave: "M4 10v4 M8 7v10 M12 4v16 M16 8v8 M20 11v2",
    alert: "M12 4 2.5 20h19Z M12 10v4 M12 17h.01",
    pencil: "M4 20h4L19 9l-4-4L4 16z M13.5 6.5l4 4",
    user: "M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8Z M4 21a8 8 0 0 1 16 0",
    logout: "M15 4h4v16h-4 M10 8l-4 4 4 4 M6 12h10",
    list: "M8 6h12 M8 12h12 M8 18h12 M4 6h.01 M4 12h.01 M4 18h.01",
    menu: "M4 7h16 M4 12h16 M4 17h16",
    plus: "M12 5v14 M5 12h14",
  };
</script>

<svg
  width={size}
  height={size}
  viewBox="0 0 24 24"
  fill="none"
  stroke="currentColor"
  stroke-width="1.8"
  stroke-linecap="round"
  stroke-linejoin="round"
  aria-hidden="true"
>
  <path d={paths[name]} fill={name === "play" || name === "stop" ? "currentColor" : "none"} />
</svg>
