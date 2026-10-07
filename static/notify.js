// Browser notifications for deadlines and low attendance.
// Works while the site is open in a browser tab and the Flask server is running.
(function () {
  if (!("Notification" in window)) return;

  var button = document.getElementById("notify-btn");
  var STORE = "notified-keys";
  var CHECK_EVERY = 30 * 60 * 1000; // 30 minutes

  function loadSeen() {
    try { return JSON.parse(localStorage.getItem(STORE) || "[]"); } catch (e) { return []; }
  }

  function saveSeen(list) {
    try { localStorage.setItem(STORE, JSON.stringify(list.slice(-200))); } catch (e) {}
  }

  function updateButton() {
    if (!button) return;
    button.hidden = false;
    if (Notification.permission === "granted") {
      button.textContent = "Reminders on (send test)";
      button.title = "Click to send a test notification";
    } else if (Notification.permission === "denied") {
      button.textContent = "Reminders blocked";
      button.title = "Allow notifications for this site in your browser settings";
    } else {
      button.textContent = "Enable reminders";
      button.title = "Get notified about deadlines and low attendance";
    }
  }

  function check() {
    if (Notification.permission !== "granted") return;
    fetch("/api/reminders", { headers: { Accept: "application/json" } })
      .then(function (res) { return res.json(); })
      .then(function (data) {
        var seen = loadSeen();
        data.items.forEach(function (item) {
          if (seen.indexOf(item.key) === -1) {
            new Notification(item.title, { body: item.body, tag: item.key });
            seen.push(item.key);
          }
        });
        saveSeen(seen);
      })
      .catch(function () {});
  }

  if (button) {
    button.addEventListener("click", function () {
      if (Notification.permission === "granted") {
        new Notification("Student Reminder", { body: "Reminders are on." });
      } else if (Notification.permission === "default") {
        Notification.requestPermission().then(function () {
          updateButton();
          check();
        });
      }
    });
  }

  updateButton();
  check();
  setInterval(check, CHECK_EVERY);
})();
