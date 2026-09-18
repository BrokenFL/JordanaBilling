/* Only maintainer-promoted releases are offered; installation requires a click. */
(() => {
  const $ = id => document.getElementById(id);
  const api = window.JordanaAPI.api;
  let offer = null, installing = false;
  function display(message, notes = "") {
    $("softwareUpdate").hidden = false;
    $("softwareUpdateDetails").hidden = false;
    $("softwareUpdateMessage").textContent = message;
    $("softwareUpdateNotes").textContent = notes;
  }
  async function check(manual = false) {
    try {
      const result = await api(manual ? "/api/updates/check" : "/api/updates", manual ? {method: "POST", body: "{}"} : {});
      offer = result.offer;
      const status = result.installation || {};
      if (status.state === "installing") {
        installing = true; display("Installing update. The app will restart automatically."); poll();
      } else if (status.state === "failed") {
        installing = false; display(status.message);
      } else if (offer && (manual || localStorage.getItem("jordana-update-later") !== offer.version)) {
        display(`Update ${offer.release_label} is available.`, offer.notes);
      } else if (manual) {
        display(result.unavailable ? result.message : "You’re up to date with the release offered for this app.");
      }
      $("installSoftwareUpdate").hidden = !offer || installing;
    } catch {
      if (manual) display("Could not check for updates. Try again later.");
    }
  }
  let polling = false;
  async function poll() {
    if (polling) return;
    polling = true;
    const started = Date.now();
    while (Date.now() - started < 20 * 60 * 1000) {
      await new Promise(resolve => setTimeout(resolve, 4000));
      try {
        const result = await api("/api/updates");
        if (result.installation?.state === "complete") { location.reload(); return; }
        if (result.installation?.state === "failed") {
          display(result.installation.message); installing = false; polling = false;
          $("installSoftwareUpdate").hidden = !offer; return;
        }
      } catch { /* Installer temporarily stops the local server. */ }
    }
    polling = false; installing = false;
    display("Update is taking longer than expected. Reopen Jordana Billing to check its status.");
  }
  $("checkSoftwareUpdate").onclick = () => check(true);
  $("laterSoftwareUpdate").onclick = () => {
    if (offer) localStorage.setItem("jordana-update-later", offer.version);
    $("softwareUpdateDetails").hidden = true;
    $("softwareUpdate").hidden = true;
  };
  $("installSoftwareUpdate").onclick = async () => {
    if (!offer || installing) return;
    if (!confirm("Save any unfinished edits before continuing. Update and restart Jordana Billing now?")) return;
    installing = true; $("installSoftwareUpdate").hidden = true;
    display("Preparing your backup and update…");
    try {
      await api("/api/updates/install", {method: "POST", body: JSON.stringify({version: offer.version})});
      display("Installing update. The app will restart automatically."); poll();
    } catch (error) {
      installing = false; $("installSoftwareUpdate").hidden = false; display(error.message);
    }
  };
  check();
  setInterval(() => check(), 24 * 60 * 60 * 1000);
})();
