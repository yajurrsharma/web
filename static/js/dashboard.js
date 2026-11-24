/* Staff dashboard helpers. The server checks everything again; this only gives instant feedback. */
(function () {
  "use strict";

  function videoSeconds(file) {
    return new Promise(function (resolve) {
      var url = URL.createObjectURL(file);
      var v = document.createElement("video");
      v.preload = "metadata";
      v.onloadedmetadata = function () { URL.revokeObjectURL(url); resolve(v.duration); };
      v.onerror = function () { URL.revokeObjectURL(url); resolve(null); };
      v.src = url;
    });
  }

  // Multi-photo box: highlight on drag, and show what was chosen before saving.
  document.querySelectorAll("[data-dropzone]").forEach(function (zone) {
    var input = zone.querySelector("input[type=file]");
    var preview = zone.parentNode.querySelector("[data-dz-preview]");
    ["dragenter", "dragover"].forEach(function (ev) { zone.addEventListener(ev, function () { zone.classList.add("drag"); }); });
    ["dragleave", "drop"].forEach(function (ev) { zone.addEventListener(ev, function () { zone.classList.remove("drag"); }); });
    function render() {
      if (!preview) return;
      preview.innerHTML = "";
      var files = Array.prototype.slice.call(input.files || []);
      if (!files.length) return;
      var photos = files.filter(function (f) { return f.type.indexOf("video/") !== 0; }).length;
      var videos = files.length - photos;
      var count = document.createElement("div");
      count.className = "dz-count";
      count.textContent = "✓ " + photos + " photo" + (photos === 1 ? "" : "s") +
        (videos ? " and " + videos + " video" + (videos === 1 ? "" : "s") : "") + " selected — press Save to upload";
      preview.appendChild(count);
      files.slice(0, 16).forEach(function (f) {
        if (f.type.indexOf("video/") === 0) {
          var v = document.createElement("span");
          v.className = "dz-vid";
          v.textContent = "▶ video";
          preview.appendChild(v);
        } else {
          var img = document.createElement("img");
          img.alt = f.name;
          img.src = URL.createObjectURL(f);
          img.onload = function () { URL.revokeObjectURL(img.src); };
          preview.appendChild(img);
        }
      });
    }
    input.addEventListener("change", render);
    input.addEventListener("dz-cleared", render);
  });

  document.querySelectorAll("input[type=file][data-max-video-seconds]").forEach(function (input) {
    var max = parseFloat(input.dataset.maxVideoSeconds) || 0;
    var box = input.closest(".panel") || document;
    var msg = box.querySelector("[data-video-msg]");
    input.addEventListener("change", function () {
      if (msg) msg.textContent = "";
      var videos = Array.prototype.filter.call(input.files, function (f) { return f.type.indexOf("video/") === 0; });
      if (!max || !videos.length) return;
      Promise.all(videos.map(videoSeconds)).then(function (secs) {
        var bad = [];
        secs.forEach(function (s, i) { if (s && s > max + 0.5) bad.push(videos[i].name + " is " + Math.round(s) + " seconds"); });
        if (bad.length) {
          input.value = "";
          input.dispatchEvent(new Event("dz-cleared"));
          if (msg) msg.textContent = bad.join("; ") + ". Maximum is " + max + " seconds — please trim the video and choose again.";
        }
      });
    });
  });
})();
