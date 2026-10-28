/* The Daily Ledger — small, dependency-free client script.
   Preferences, saved stories and search live in localStorage only. */
(function () {
  "use strict";
  var KEY = "ledger-prefs", SAVED = "ledger-saved";
  function load(k, d) { try { return JSON.parse(localStorage.getItem(k) || "null") || d; } catch (e) { return d; } }
  function store(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) {} }
  var prefs = load(KEY, {});
  var root = document.body.dataset.root || "";
  var lang = document.body.dataset.lang || "it";
  var html = document.documentElement;

  function apply() {
    if (prefs.theme) html.dataset.theme = prefs.theme; else delete html.dataset.theme;
    if (prefs.compact) html.dataset.compact = "1"; else delete html.dataset.compact;
    var follow = prefs.follow || [], hide = prefs.hide || [];
    document.querySelectorAll("[data-topic]").forEach(function (el) {
      el.classList.toggle("followed", follow.indexOf(el.dataset.topic) >= 0);
      el.classList.toggle("hidden-topic", hide.indexOf(el.dataset.topic) >= 0);
    });
    document.querySelectorAll('[data-toggle="theme"]').forEach(function (b) {
      var dark = html.dataset.theme === "dark" || (!html.dataset.theme && matchMedia("(prefers-color-scheme: dark)").matches);
      b.setAttribute("aria-pressed", dark ? "true" : "false");
    });
    document.querySelectorAll('[data-toggle="compact"]').forEach(function (b) { b.setAttribute("aria-pressed", prefs.compact ? "true" : "false"); });
  }
  document.addEventListener("click", function (ev) {
    var t = ev.target.closest("[data-toggle],[data-save],[data-setlang]");
    if (!t) return;
    if (t.dataset.toggle === "theme") {
      var dark = html.dataset.theme === "dark" || (!html.dataset.theme && matchMedia("(prefers-color-scheme: dark)").matches);
      prefs.theme = dark ? "light" : "dark"; store(KEY, prefs); apply();
    } else if (t.dataset.toggle === "compact") {
      prefs.compact = !prefs.compact; store(KEY, prefs); apply();
    } else if (t.dataset.setlang) {
      prefs.lang = t.dataset.setlang; store(KEY, prefs);
    } else if (t.dataset.save) {
      var saved = load(SAVED, {}), id = t.dataset.save;
      if (saved[id]) delete saved[id]; else saved[id] = { date: t.dataset.date, at: Date.now() };
      store(SAVED, saved); markSaved();
    }
  });
  function markSaved() {
    var saved = load(SAVED, {});
    document.querySelectorAll("[data-save]").forEach(function (b) { b.setAttribute("aria-pressed", saved[b.dataset.save] ? "true" : "false"); });
  }

  /* settings form */
  var form = document.getElementById("prefs");
  if (form) {
    form.lang.value = prefs.lang || lang;
    form.theme.value = prefs.theme || "";
    form.compact.checked = !!prefs.compact;
    form.querySelectorAll('[name="follow"]').forEach(function (c) { c.checked = (prefs.follow || []).indexOf(c.value) >= 0; });
    form.querySelectorAll('[name="hide"]').forEach(function (c) { c.checked = (prefs.hide || []).indexOf(c.value) >= 0; });
    form.addEventListener("change", function () {
      prefs.lang = form.lang.value; prefs.theme = form.theme.value || undefined; prefs.compact = form.compact.checked;
      prefs.follow = Array.prototype.map.call(form.querySelectorAll('[name="follow"]:checked'), function (c) { return c.value; });
      prefs.hide = Array.prototype.map.call(form.querySelectorAll('[name="hide"]:checked'), function (c) { return c.value; });
      store(KEY, prefs); apply();
      document.getElementById("prefs-saved").textContent = lang === "it" ? "Preferenze salvate." : "Preferences saved.";
    });
  }

  /* archive search */
  var af = document.getElementById("archive-filters");
  if (af) {
    var out = document.getElementById("archive-results"), count = document.getElementById("archive-count"), data = [];
    function norm(s) { return (s || "").toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, ""); }
    function run() {
      var q = norm(af.q.value).split(/\s+/).filter(Boolean), topic = af.topic.value, place = af.place.value,
          from = af.from.value, to = af.to.value, src = norm(af.source.value), onlySaved = af.saved.checked, saved = load(SAVED, {});
      var res = data.filter(function (r) {
        if (topic && r.topic !== topic) return false;
        if (place) { var p = place.split(":"); if (p[0] === "c" ? r.countries.indexOf(p[1]) < 0 : r.cities.indexOf(p[1]) < 0) return false; }
        if (from && r.date < from) return false;
        if (to && r.date > to) return false;
        if (src && !r.sources.some(function (s) { return norm(s).indexOf(src) >= 0; })) return false;
        if (onlySaved && !saved[r.id]) return false;
        var hay = norm([r.h.en, r.h.it, r.sub.en, r.sub.it, r.sources.join(" ")].join(" "));
        return q.every(function (w) { return hay.indexOf(w) >= 0; });
      }).slice(0, 300);
      out.innerHTML = "";
      res.forEach(function (r) {
        var li = document.createElement("li"), a = document.createElement("a"), d = document.createElement("div");
        a.textContent = r.h[lang] || r.h.en;
        a.href = root + r.date + "/" + lang + (r.c ? "/index.html#" + r.id : "/s/" + r.id + ".html");
        d.className = "ad";
        d.textContent = r.date + " — " + (r.sources.join(", ") || "") + (r.kind === "brief" ? (lang === "it" ? " — solo titolo" : " — headline only") : "");
        li.appendChild(a); li.appendChild(d); out.appendChild(li);
      });
      count.textContent = res.length + (lang === "it" ? " risultati" : " results");
    }
    fetch(root + "search-index.json").then(function (r) { return r.json(); }).then(function (j) { data = j.reverse ? j : []; run(); })
      .catch(function () { count.textContent = lang === "it" ? "Indice di ricerca non disponibile." : "Search index unavailable."; });
    af.addEventListener("input", run);
  }
  apply(); markSaved();
})();
