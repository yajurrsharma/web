/* Small progressive enhancements. Every feature works without JavaScript. */
(function () {
  "use strict";

  // Register the service worker (installable app + offline page).
  if ("serviceWorker" in navigator && location.protocol === "https:") {
    navigator.serviceWorker.register("/sw.js").catch(function () {});
  }

  // Disable submit buttons after the first click to stop double orders.
  document.querySelectorAll("form[data-once]").forEach(function (form) {
    form.addEventListener("submit", function () {
      form.querySelectorAll("button[type=submit]").forEach(function (b) {
        b.disabled = true;
        if (b.dataset.busy) { b.textContent = b.dataset.busy; }
      });
    });
  });

  // Product gallery thumbnails.
  var main = document.querySelector("[data-gallery-main]");
  document.querySelectorAll("[data-gallery-thumb]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      if (!main) { return; }
      var kind = btn.dataset.kind, src = btn.dataset.src, alt = btn.dataset.alt || "";
      // Replace only the picture/video, so the zoom lens inside the frame survives.
      main.querySelectorAll("img, video, iframe").forEach(function (n) { n.remove(); });
      var el;
      if (kind === "youtube") {
        // Privacy-enhanced player, created only now (nothing loads from YouTube until the customer taps).
        el = document.createElement("iframe");
        el.src = "https://www.youtube-nocookie.com/embed/" + encodeURIComponent(src) + "?autoplay=1&rel=0";
        el.title = alt || "Product video";
        el.allow = "autoplay; encrypted-media; picture-in-picture";
        el.allowFullscreen = true;
        el.className = "yt-frame";
      } else if (kind === "video") {
        el = document.createElement("video");
        el.controls = true; el.preload = "metadata"; el.src = src;
      } else {
        el = document.createElement("img");
        el.src = src; el.alt = alt;
      }
      main.insertBefore(el, main.firstChild);
      document.querySelectorAll("[data-gallery-thumb]").forEach(function (b) { b.setAttribute("aria-current", "false"); });
      btn.setAttribute("aria-current", "true");
    });
  });

  // Variant selection updates price and stock text without a reload.
  document.querySelectorAll("[data-variant-input]").forEach(function (input) {
    input.addEventListener("change", function () {
      var d = input.dataset;
      var price = document.querySelector("[data-price]"), mrp = document.querySelector("[data-mrp]"),
          stock = document.querySelector("[data-stock]"), add = document.querySelectorAll("[data-add-button]");
      if (price) { price.textContent = d.price; }
      var short = document.querySelector("[data-price-short]"), off = document.querySelector("[data-off]"),
          mrpLine = document.querySelector("[data-mrp-line]");
      if (short && d.priceShort) {
        document.querySelectorAll("[data-price-short]").forEach(function (el) { el.textContent = d.priceShort; });
      }
      if (off) { off.textContent = d.off || ""; }
      var save = document.querySelector("[data-save]"), netqty = document.querySelector("[data-netqty]");
      if (save) { save.textContent = d.save || ""; }
      if (netqty && d.netqty) { netqty.textContent = d.netqty; }
      if (mrpLine) { mrpLine.hidden = d.mrp === d.price; }
      if (mrp) { mrp.textContent = d.mrp; if (!mrpLine) { mrp.hidden = d.mrp === d.price; } }
      if (stock) { stock.textContent = d.stockText; stock.className = d.stockClass; }
      add.forEach(function (b) { b.disabled = d.inStock !== "1"; });
      var qty = document.querySelector("[data-qty]");
      if (qty) { qty.max = d.max; }
      history.replaceState(null, "", "?v=" + input.value);
    });
  });

  // "Add to Cart" on product boxes: add without leaving the page (falls back to a normal post).
  var toast = document.querySelector("[data-toast]"), toastTimer;
  function showToast(html) {
    if (!toast) { return; }
    toast.innerHTML = html;
    toast.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { toast.hidden = true; }, 4000);
  }
  document.querySelectorAll("form[data-quick-add]").forEach(function (form) {
    form.addEventListener("submit", function (e) {
      if (!window.fetch) { return; }
      e.preventDefault();
      var btn = form.querySelector("button");
      btn.disabled = true;
      fetch(form.action, { method: "POST", body: new FormData(form), credentials: "same-origin",
                           headers: { "X-Requested-With": "XMLHttpRequest" } })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          var esc = document.createElement("span");
          esc.textContent = data.message || "";
          if (data.ok) {
            document.querySelectorAll("[data-cart-count]").forEach(function (el) { el.textContent = data.cart_count; });
            showToast('<span class="ok">✓</span><span>' + esc.innerHTML + '</span><a class="btn btn-sm btn-cta" href="/cart/">Go to Cart</a>');
          } else {
            showToast("<span>" + esc.innerHTML + "</span>");
          }
        })
        .catch(function () { form.submit(); })
        .finally(function () { btn.disabled = false; });
    });
  });

  // Recently viewed products: kept only in this browser (localStorage), never sent to the server.
  var RV_KEY = "lld_recent", RV_MAX = 10;
  function rvLoad() { try { return JSON.parse(localStorage.getItem(RV_KEY)) || []; } catch (e) { return []; } }
  function rvSave(list) { try { localStorage.setItem(RV_KEY, JSON.stringify(list)); } catch (e) { /* private mode */ } }
  var seen = document.querySelector("[data-rv-product]");
  if (seen) {
    var item = { url: seen.dataset.url, name: seen.dataset.name, img: seen.dataset.img, price: seen.dataset.price };
    rvSave([item].concat(rvLoad().filter(function (p) { return p.url !== item.url; })).slice(0, RV_MAX));
  }
  var rvBox = document.querySelector("[data-recently-viewed]");
  if (rvBox) {
    var rvList = rvBox.querySelector("[data-rv-list]"), recent = rvLoad();
    recent.forEach(function (p) {
      if (!p || typeof p.url !== "string" || p.url.charAt(0) !== "/") { return; }  // same-site links only
      var card = document.createElement("div"); card.className = "pcard";
      var a1 = document.createElement("a"); a1.className = "pimg"; a1.href = p.url; a1.tabIndex = -1;
      if (p.img && p.img.charAt(0) === "/") { var im = document.createElement("img"); im.src = p.img; im.alt = ""; im.loading = "lazy"; a1.appendChild(im); }
      var a2 = document.createElement("a"); a2.className = "ptitle"; a2.href = p.url; a2.textContent = p.name || "";
      var pr = document.createElement("div"); pr.className = "pprice"; pr.innerHTML = '<span class="amt"></span>';
      pr.firstChild.textContent = p.price || "";
      card.appendChild(a1); card.appendChild(a2); card.appendChild(pr);
      rvList.appendChild(card);
    });
    rvBox.hidden = !rvList.children.length;
    var clear = rvBox.querySelector("[data-rv-clear]");
    if (clear) { clear.addEventListener("click", function () { rvSave([]); rvBox.hidden = true; }); }
  }

  // Home slider: always auto-rotates (speed set in Store settings), with a pause button for accessibility.
  var hero = document.querySelector("[data-hero-carousel]");
  window.addEventListener("load", function () {
    if (!hero || !window.bootstrap) { return; }
    var slider = bootstrap.Carousel.getOrCreateInstance(hero, {
      interval: parseInt(hero.dataset.bsInterval, 10) || 4000, pause: false, ride: "carousel", wrap: true });
    slider.cycle();
    var btn = document.querySelector("[data-hero-pause]"), paused = false;
    if (btn) {
      btn.addEventListener("click", function () {
        paused = !paused;
        if (paused) { slider.pause(); } else { slider.cycle(); }
        btn.textContent = paused ? "▶" : "❚❚";
        btn.setAttribute("aria-label", paused ? "Play slideshow" : "Pause slideshow");
      });
    }
  });

  // Product photo zoom (mouse only): a lens over the photo and a large zoomed view beside it.
  var zoomMain = document.querySelector("[data-gallery-main]");
  var finePointer = window.matchMedia && window.matchMedia("(hover: hover) and (pointer: fine)").matches;
  if (zoomMain && finePointer) {
    var ZOOM = 2.5;
    var lens = document.createElement("div"); lens.className = "zoom-lens"; lens.hidden = true;
    var pane = document.createElement("div"); pane.className = "zoom-pane"; pane.hidden = true;
    pane.setAttribute("aria-hidden", "true");
    zoomMain.appendChild(lens);
    zoomMain.parentNode.appendChild(pane);
    var imageBox = function (img) {
      // Where the picture really sits inside its square frame (object-fit: contain leaves bars).
      var r = img.getBoundingClientRect(), nw = img.naturalWidth || r.width, nh = img.naturalHeight || r.height;
      var scale = Math.min(r.width / nw, r.height / nh), w = nw * scale, h = nh * scale;
      return { left: r.left + (r.width - w) / 2, top: r.top + (r.height - h) / 2, width: w, height: h };
    };
    var hide = function () { lens.hidden = true; pane.hidden = true; };
    zoomMain.addEventListener("mousemove", function (e) {
      var img = zoomMain.querySelector("img");
      if (!img || !img.complete) { hide(); return; }
      var box = imageBox(img), frame = zoomMain.getBoundingClientRect();
      var x = e.clientX - box.left, y = e.clientY - box.top;
      if (x < 0 || y < 0 || x > box.width || y > box.height) { hide(); return; }
      // Fit the zoom view between the photo and the buy box, so price and buttons stay visible.
      var gallery = zoomMain.parentNode.getBoundingClientRect(), buy = document.querySelector(".buybox");
      var room = (buy ? buy.getBoundingClientRect().left : window.innerWidth) - gallery.right - 32;
      var side = Math.max(200, Math.min(560, room, window.innerHeight - 40));
      pane.style.width = side + "px"; pane.style.height = side + "px";
      var paneW = side, paneH = side;
      var lw = Math.min(box.width, paneW / ZOOM), lh = Math.min(box.height, paneH / ZOOM);
      var lx = Math.max(0, Math.min(x - lw / 2, box.width - lw)), ly = Math.max(0, Math.min(y - lh / 2, box.height - lh));
      lens.style.width = lw + "px"; lens.style.height = lh + "px";
      lens.style.left = (box.left - frame.left + lx) + "px"; lens.style.top = (box.top - frame.top + ly) + "px";
      pane.style.backgroundImage = "url(\"" + img.currentSrc.replace(/"/g, "%22") + "\")";
      pane.style.backgroundSize = (box.width * ZOOM) + "px " + (box.height * ZOOM) + "px";
      pane.style.backgroundPosition = (-lx * ZOOM) + "px " + (-ly * ZOOM) + "px";
      lens.hidden = false; pane.hidden = false;
    });
    zoomMain.addEventListener("mouseleave", hide);
    // Like big shops: hovering a photo thumbnail shows it as the main picture.
    document.querySelectorAll("[data-gallery-thumb][data-kind=image]").forEach(function (t) {
      t.addEventListener("mouseenter", function () { t.click(); });
    });
  }

  // Report a problem: photos are optional only for "item missing" (nothing to photograph).
  document.querySelectorAll("[data-problem-form]").forEach(function (form) {
    var optional = (form.dataset.photoOptional || "").split(","), hint = form.querySelector("[data-photo-hint]");
    form.querySelectorAll("input[name=problem]").forEach(function (r) {
      r.addEventListener("change", function () {
        if (hint) { hint.textContent = optional.indexOf(r.value) >= 0 ? "(optional — a photo of the opened parcel helps)" : "(required — up to 4: the item, the damage, and the parcel label)"; }
      });
    });
    var files = form.querySelector("input[type=file]");
    if (files) {
      files.addEventListener("change", function () {
        files.setCustomValidity(files.files.length > 4 ? "Choose at most 4 photos." : "");
      });
    }
  });

  // Password boxes: a show/hide eye button, and a live "passwords match" check on the confirm box.
  document.querySelectorAll("input[type=password]").forEach(function (input) {
    var wrap = document.createElement("div");
    wrap.className = "pw-wrap";
    input.parentNode.insertBefore(wrap, input);
    wrap.appendChild(input);
    var eye = document.createElement("button");
    eye.type = "button"; eye.className = "pw-eye"; eye.textContent = "👁";
    eye.setAttribute("aria-label", "Show password"); eye.setAttribute("aria-pressed", "false");
    eye.addEventListener("click", function () {
      var show = input.type === "password";
      input.type = show ? "text" : "password";
      eye.setAttribute("aria-pressed", show ? "true" : "false");
      eye.setAttribute("aria-label", show ? "Hide password" : "Show password");
    });
    wrap.appendChild(eye);
  });
  var pw1 = document.querySelector("input[name=password1], input[name=new_password1]");
  var pw2 = document.querySelector("input[name=password2], input[name=new_password2]");
  if (pw1 && pw2) {
    var note = document.createElement("div");
    note.className = "form-text pw-match"; note.setAttribute("aria-live", "polite");
    pw2.closest(".pw-wrap").insertAdjacentElement("afterend", note);
    var check = function () {
      if (!pw2.value) { note.textContent = ""; return; }
      var ok = pw1.value === pw2.value;
      note.textContent = ok ? "✓ Passwords match" : "✗ Passwords don't match yet";
      note.style.color = ok ? "#007600" : "#b12704";
    };
    pw1.addEventListener("input", check); pw2.addEventListener("input", check);
  }

  // Sort menu on shop pages applies immediately.
  document.querySelectorAll("form[data-autosubmit] select").forEach(function (sel) {
    sel.addEventListener("change", function () { sel.form.submit(); });
  });

  // Checkout: refresh the order summary when the payment method or address changes (prepaid prices differ).
  document.querySelectorAll("input[name=payment_method], input[name=shipping_address]").forEach(function (r) {
    r.addEventListener("change", function () {
      var pm = document.querySelector("input[name=payment_method]:checked");
      var addr = document.querySelector("input[name=shipping_address]:checked");
      var q = new URLSearchParams();
      if (pm) { q.set("pm", pm.value); }
      if (addr) { q.set("addr", addr.value); }
      location.replace(location.pathname + "?" + q.toString() + "#pay-h");
    });
  });

  // Category menu: close when clicking elsewhere or pressing Escape.
  var mega = document.querySelector("details.mega");
  if (mega) {
    document.addEventListener("click", function (e) { if (mega.open && !mega.contains(e.target)) { mega.open = false; } });
    document.addEventListener("keydown", function (e) { if (e.key === "Escape" && mega.open) { mega.open = false; } });
  }

  // Address forms: fill city and state from the PIN code (never overwrites what the customer typed).
  document.querySelectorAll("input[name=pincode]").forEach(function (pin) {
    var form = pin.form, city = form && form.querySelector("input[name=city]"),
        state = form && form.querySelector("select[name=state_code]"), msg = form && form.querySelector("[data-pin-msg]");
    var last = "";
    pin.setAttribute("inputmode", "numeric");
    pin.addEventListener("input", function () {
      var v = pin.value.replace(/\D/g, "").slice(0, 6);
      if (v !== pin.value) { pin.value = v; }
      if (v.length !== 6 || v === last || !window.fetch) { return; }
      last = v;
      if (msg) { msg.textContent = "Looking up your area…"; }
      fetch("/checkout/pincode/" + v + "/", { headers: { "X-Requested-With": "XMLHttpRequest" } })
        .then(function (r) { return r.json(); })
        .then(function (d) {
          if (!d.found) { if (msg) { msg.textContent = "Please type your city and state."; } return; }
          if (city && !city.value) { city.value = d.city; }
          if (state && !state.value) { state.value = d.state_code; }
          if (msg) { msg.textContent = "✓ " + d.city + ", " + d.state; }
        })
        .catch(function () { if (msg) { msg.textContent = ""; } });
    });
  });

  // Product page: "Check delivery" by PIN code (the last PIN is remembered in this browser only).
  var dc = document.querySelector("[data-delivery-check]");
  if (dc) {
    var dcPin = dc.querySelector("input"), dcOut = dc.querySelector("[data-dc-result]");
    var check = function () {
      var pin = dcPin.value.replace(/\D/g, "").slice(0, 6);
      dcPin.value = pin;
      if (pin.length !== 6) { dcOut.textContent = "Enter a 6-digit PIN code."; return; }
      dcOut.textContent = "Checking…";
      fetch("/delivery-check/" + pin + "/").then(function (r) { return r.json(); }).then(function (d) {
        dcOut.innerHTML = "";
        var line = document.createElement("div");
        if (!d.ok) { line.className = "text-danger"; line.textContent = d.message || "Please try again."; dcOut.appendChild(line); return; }
        try { localStorage.setItem("lld_pin", pin); } catch (e) { /* private mode */ }
        line.className = "text-success fw-semibold";
        line.textContent = "✓ Delivery by " + d.delivery + (d.place ? " to " + d.place : "");
        dcOut.appendChild(line);
        if (d.cod) { var cod = document.createElement("div"); cod.textContent = "💵 Cash on Delivery available"; dcOut.appendChild(cod); }
      }).catch(function () { dcOut.textContent = "Couldn't check right now — please try again."; });
    };
    dc.querySelector("[data-dc-btn]").addEventListener("click", check);
    dcPin.addEventListener("keydown", function (e) { if (e.key === "Enter") { e.preventDefault(); check(); } });
    try { var saved = localStorage.getItem("lld_pin"); if (saved) { dcPin.value = saved; check(); } } catch (e) { /* ignore */ }
  }

  // Phones: sticky buy bar appears when the real buy buttons scroll out of view.
  var sticky = document.querySelector("[data-sticky-buy]"), buyBox = document.querySelector(".buybox");
  if (sticky && buyBox && "IntersectionObserver" in window) {
    new IntersectionObserver(function (entries) {
      sticky.hidden = entries[0].isIntersecting;
      document.body.classList.toggle("has-sticky-buy", !sticky.hidden);
    }).observe(buyBox);
    var press = function (sel) { var b = buyBox.querySelector(sel); if (b && !b.disabled) { b.click(); } };
    sticky.querySelector("[data-sticky-add]").addEventListener("click", function () { press("button[data-add-button]:not([name])"); });
    sticky.querySelector("[data-sticky-buynow]").addEventListener("click", function () { press("button[name=buy_now]"); });
  }

  // Phone bottom menu: "Search" jumps to the search box, "Categories" opens the category menu.
  document.querySelectorAll("[data-tab-search]").forEach(function (a) {
    a.addEventListener("click", function (e) {
      var box = document.getElementById("site-search");
      if (!box) { return; }
      e.preventDefault(); window.scrollTo({ top: 0, behavior: "smooth" }); box.focus();
    });
  });
  document.querySelectorAll("[data-tab-categories]").forEach(function (a) {
    a.addEventListener("click", function (e) {
      var menu = document.querySelector("details.mega");
      if (!menu) { return; }
      e.preventDefault(); e.stopPropagation(); window.scrollTo({ top: 0, behavior: "smooth" }); menu.open = true;
    });
  });

  // Billing address toggle at checkout.
  var same = document.getElementById("id_billing_same");
  var billing = document.getElementById("billing-choices");
  if (same && billing) {
    var sync = function () { billing.hidden = same.checked; };
    same.addEventListener("change", sync); sync();
  }
})();
