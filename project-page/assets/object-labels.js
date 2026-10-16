/* Display labels only: keep the source object ID as the attribution identity. */
(() => {
  window.grepObjectLabel = value => {
    const id = String(value ?? "");
    const label = id.replace(/^room_\d+__/, "").replace(/[_-]+/g, " ").replace(/\s+/g, " ").trim();
    return label ? label[0].toUpperCase() + label.slice(1) : id;
  };
})();
