"use strict";
/* KI-Dirigent – Oberfläche. Kein Framework, keine externen Dateien. */

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];

const S = {
  gespraech: null,      // aktive Gesprächs-ID
  laeuft: null,         // AbortController der laufenden Aufgabe
  anhaenge: [],         // {art: 'ap'|'pc', pfad}
  status: null,
  pcPfad: null,
  ansicht: null,        // {art, pfad}
  jobTimer: null,
};

// ------------------------------------------------------------------ Helfer
function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function el(tag, attrs = {}, ...kinder) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") e.className = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (v !== undefined && v !== null && v !== false) e.setAttribute(k, v === true ? "" : v);
  }
  for (const k of kinder.flat()) if (k !== null && k !== undefined && k !== false) e.append(k instanceof Node ? k : document.createTextNode(String(k)));
  return e;
}
const zahl = n => (n || 0).toLocaleString("de-DE");
function groesse(b) {
  if (b < 1024) return b + " B";
  if (b < 1048576) return (b / 1024).toFixed(1).replace(".", ",") + " KB";
  return (b / 1048576).toFixed(1).replace(".", ",") + " MB";
}
function toast(text, ms = 2600) {
  const t = $("#toast");
  t.textContent = text; t.hidden = false;
  clearTimeout(toast.t); toast.t = setTimeout(() => (t.hidden = true), ms);
}
async function api(pfad, { methode = "GET", body } = {}) {
  const r = await fetch(pfad, {
    method: methode,
    headers: body !== undefined ? { "Content-Type": "application/json" } : {},
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(d.fehler || r.statusText);
  return d;
}

// ------------------------------------------------------------------ Markdown (sicher: erst escapen)
function inline(t) {
  return t
    .replace(/`([^`\n]+)`/g, (_, c) => `<code>${c}</code>`)
    .replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[^*\w])\*([^*\n]+)\*(?=[^*\w]|$)/g, "$1<em>$2</em>")
    .replace(/\[([^\]\n]+)\]\((https?:\/\/[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
}
function mdBlock(text) {
  const zeilen = esc(text).split("\n");
  let html = "", liste = null, absatz = [], tabelle = [];
  const absatzEnde = () => { if (absatz.length) { html += `<p>${inline(absatz.join("<br>"))}</p>`; absatz = []; } };
  const listeEnde = () => { if (liste) { html += `</${liste}>`; liste = null; } };
  const tabelleEnde = () => {
    if (!tabelle.length) return;
    const zellen = z => z.replace(/^\||\|$/g, "").split("|").map(x => inline(x.trim()));
    const kopf = zellen(tabelle[0]);
    const rumpf = tabelle.slice(2).map(zellen);
    html += "<table><thead><tr>" + kopf.map(k => `<th>${k}</th>`).join("") + "</tr></thead><tbody>" +
      rumpf.map(r => "<tr>" + r.map(c => `<td>${c}</td>`).join("") + "</tr>").join("") + "</tbody></table>";
    tabelle = [];
  };
  for (let i = 0; i < zeilen.length; i++) {
    const z = zeilen[i];
    if (/^\s*\|.*\|\s*$/.test(z) && (tabelle.length || /^\s*\|?[\s:-]+\|[\s|:-]*$/.test(zeilen[i + 1] || ""))) {
      absatzEnde(); listeEnde(); tabelle.push(z.trim()); continue;
    } else tabelleEnde();
    let m;
    if ((m = z.match(/^(#{1,6})\s+(.*)$/))) { absatzEnde(); listeEnde(); const n = Math.min(m[1].length + 1, 4); html += `<h${n}>${inline(m[2])}</h${n}>`; }
    else if (/^\s*([-*_])\s*\1\s*\1[\s\-*_]*$/.test(z)) { absatzEnde(); listeEnde(); html += "<hr>"; }
    else if ((m = z.match(/^\s*[-*+]\s+(.*)$/))) { absatzEnde(); if (liste !== "ul") { listeEnde(); html += "<ul>"; liste = "ul"; } html += `<li>${inline(m[1])}</li>`; }
    else if ((m = z.match(/^\s*\d+[.)]\s+(.*)$/))) { absatzEnde(); if (liste !== "ol") { listeEnde(); html += "<ol>"; liste = "ol"; } html += `<li>${inline(m[1])}</li>`; }
    else if ((m = z.match(/^&gt;\s?(.*)$/))) { absatzEnde(); listeEnde(); html += `<blockquote>${inline(m[1])}</blockquote>`; }
    else if (!z.trim()) { absatzEnde(); listeEnde(); }
    else { listeEnde(); absatz.push(z); }
  }
  tabelleEnde(); absatzEnde(); listeEnde();
  return html;
}
function markdown(text) {
  let html = "", pos = 0;
  const re = /^```([^\n`]*)\n([\s\S]*?)(?:\n```[ \t]*$|$(?![\s\S]))/gm;
  let m;
  while ((m = re.exec(text))) {
    html += mdBlock(text.slice(pos, m.index));
    const info = m[1].trim();
    const datei = info.startsWith("datei:") ? info.slice(6).trim() : "";
    const titel = datei ? "📄 " + datei : (info === "merken" ? "🧠 merken" : info || "code");
    html += `<div class="codeblock"><div class="codeblock-kopf"><span>${esc(titel)}</span><span class="platz"></span>` +
      (datei ? `<button data-oeffnen="${esc(datei)}">öffnen</button>` : "") +
      `<button data-kopieren>kopieren</button></div><pre><code>${esc(m[2])}</code></pre></div>`;
    pos = re.lastIndex;
    if (m[0].length === 0) re.lastIndex++;
  }
  html += mdBlock(text.slice(pos));
  return html;
}
document.addEventListener("click", e => {
  const k = e.target.closest("[data-kopieren]");
  if (k) {
    navigator.clipboard?.writeText(k.closest(".codeblock").querySelector("code").textContent).then(() => toast("Kopiert"));
  }
  const o = e.target.closest("[data-oeffnen]");
  if (o) oeffneDatei("ap", o.dataset.oeffnen);
});

// ------------------------------------------------------------------ Status & Spezialisten
const FAMILIEN = [["ollama", "Ollama"], ["anthropic", "Claude"], ["gemini", "Gemini"], ["openai", "ChatGPT"]];

async function ladeStatus() {
  try { S.status = await api("/api/status"); } catch (e) { toast("Server nicht erreichbar: " + e.message); return; }
  const st = S.status;
  // Statuskasten links
  const box = $("#status-box"); box.innerHTML = "";
  for (const [fam, name] of FAMILIEN) {
    const spez = st.spezialisten.filter(s => (s.familie || s.anbieter) === fam);
    const an = spez.filter(s => s.verfuegbar);
    let klasse = an.length ? "an" : "aus", info = an.length ? `${an.length} bereit` : "nicht bereit";
    if (fam === "ollama") {
      klasse = st.ollama.erreichbar ? (an.length ? "an" : "teil") : "aus";
      info = st.ollama.erreichbar ? `${st.ollama.modelle.length} Modelle` : "aus";
    }
    const wege = [...new Set(an.map(s => s.anbieter === "cli" ? "App" : s.anbieter === "ollama" ? "lokal" : "API"))].join(" + ");
    box.append(el("div", { class: "status-zeile" }, el("span", { class: "punkt " + klasse }), el("span", {}, name),
      el("span", { class: "platz" }), el("span", { style: "color:var(--leise);font-size:12px" }, wege || info)));
  }
  // Auswahl
  const sel = $("#spezialist"), alt = sel.value;
  sel.innerHTML = '<option value="auto">Automatisch – Ollama entscheidet</option>';
  for (const s of st.spezialisten.filter(s => s.verfuegbar)) sel.append(el("option", { value: s.id }, s.name));
  sel.value = [...sel.options].some(o => o.value === alt) ? alt : "auto";
  if (!ladeStatus.einmal) { $("#strategie").value = st.strategie; $("#zerlegen").checked = st.zerlegen; ladeStatus.einmal = true; }
  zeigeSpezialisten();
  zeigeKiOrdner();
}

function zeigeSpezialisten() {
  const st = S.status; if (!st) return;
  const o = st.ollama;
  $("#ollama-info").innerHTML = "";
  $("#ollama-info").append(el("div", { class: "karte" },
    el("div", { class: "karte-kopf" }, el("span", { class: "punkt " + (o.erreichbar ? "an" : "aus") }), "Ollama – Dirigent",
      el("span", { class: "platz" }), el("button", { class: "knopf klein", onclick: () => testeVerbindung("ollama") }, "testen")),
    el("div", { class: "klein" }, o.erreichbar
      ? `Plant mit ${o.router || "–"} · bereitet vor mit ${o.helfer || "–"} · Suche: ${o.embedding || "nur Text (ollama pull nomic-embed-text für Bedeutungssuche)"}`
      : `Nicht erreichbar unter ${o.url}. Ollama starten oder installieren: https://ollama.com`),
    o.autostart ? el("div", { class: "klein" }, "Autostart: " + o.autostart) : null,
    el("div", { class: "klein", id: "test-ollama" })));
  const pr = st.programme || { gefunden: {} };
  const gefunden = Object.entries(pr.gefunden || {});
  $("#ollama-info").append(el("div", { class: "karte" },
    el("div", { class: "karte-kopf" }, "Programme auf dem PC", el("span", { class: "platz" }),
      el("button", { class: "knopf klein", id: "programme-suchen", onclick: programmeSuchen }, pr.suche_laeuft ? "sucht …" : "auf allen Laufwerken suchen")),
    el("div", { class: "klein" }, gefunden.length
      ? gefunden.map(([n, p]) => `${n}: ${p}`).join(" · ")
      : "Noch nichts gesucht. Liegen Ollama, Gemini CLI oder Codex z. B. auf D:, findet die Suche sie."),
    el("div", { class: "klein", id: "programme-ergebnis" })));
  const liste = $("#spez-liste"); liste.innerHTML = "";
  const gruppen = [["cli", "Installierte Programme (dein Abo)"], ["ollama", "Lokal (kostenlos)"], ["anthropic", "Claude-API"], ["gemini", "Gemini-API"], ["openai", "ChatGPT-API"]];
  for (const [a, titel] of gruppen) {
    const spez = st.spezialisten.filter(s => s.anbieter === a);
    if (!spez.length) continue;
    liste.append(el("div", { class: "ordner-titel" }, titel, a !== "ollama" ? " " : "",
      a !== "ollama" ? el("button", { class: "knopf klein leise", onclick: () => testeVerbindung(a) }, "testen") : null));
    liste.append(el("div", { class: "klein", id: "test-" + a, style: "font-size:12px;color:var(--leise);padding:0 6px" }));
    for (const s of spez) {
      liste.append(el("div", { class: "karte" },
        el("div", { class: "karte-kopf" }, el("span", { class: "punkt " + (s.verfuegbar ? "an" : "aus") }), s.name,
          el("span", { class: "platz" }), el("span", { class: "pille" }, `Kosten ${s.kosten} · Niveau ${s.niveau}`)),
        el("div", { class: "klein" }, `${s.modell || ""} · ${s.kategorien.join(", ")}`),
        el("div", {}, s.staerken),
        el("div", { class: "klein" }, s.grund)));
    }
  }
}

async function programmeSuchen() {
  const k = $("#programme-suchen"), ziel = $("#programme-ergebnis");
  if (k) { k.disabled = true; k.textContent = "sucht … (bis 30 s)"; }
  try {
    const d = await api("/api/programme/suchen", { methode: "POST", body: {} });
    if (ziel) ziel.textContent = d.fehlen.length ? "Nicht gefunden: " + d.fehlen.join(", ") + " – Pfad in config.json bei programme.pfade eintragen." : "Alle benötigten Programme gefunden.";
  } catch (e) { if (ziel) ziel.textContent = "Fehler: " + e.message; }
  await ladeStatus();
}

async function testeVerbindung(a) {
  const ziel = $("#test-" + a); if (ziel) ziel.textContent = "teste …";
  try {
    const d = await api("/api/verbindung", { methode: "POST", body: { anbieter: a } });
    if (ziel) ziel.textContent = d.modelle.length ? "OK: " + d.modelle.slice(0, 40).join(", ") : "OK, aber keine Modelle/Programme gefunden";
  } catch (e) { if (ziel) ziel.textContent = "Fehler: " + e.message; }
}

// ------------------------------------------------------------------ Gespräche
async function ladeGespraeche() {
  const d = await api("/api/gespraeche");
  const nav = $("#gespraeche"); nav.innerHTML = "";
  for (const g of d.gespraeche) {
    nav.append(el("div", { class: "gespraech" + (g.id === S.gespraech ? " aktiv" : ""), onclick: () => oeffneGespraech(g.id) },
      el("span", {}, g.titel),
      el("button", { class: "loeschen", title: "Löschen", onclick: async ev => {
        ev.stopPropagation();
        if (!confirm(`„${g.titel}“ löschen?`)) return;
        await api(`/api/gespraeche/${g.id}`, { methode: "DELETE" });
        if (S.gespraech === g.id) neuesGespraech();
        ladeGespraeche();
      } }, "✕")));
  }
}

function neuesGespraech() {
  S.gespraech = null;
  $("#titel").textContent = "Neue Aufgabe";
  $("#verlauf").innerHTML = "";
  $("#verlauf").append(willkommenVorlage.cloneNode(true));
  bindeBeispiele();
  $$(".gespraech").forEach(g => g.classList.remove("aktiv"));
  $("#app").classList.remove("menue-offen");
  $("#text").focus();
}

async function oeffneGespraech(id) {
  const g = await api(`/api/gespraeche/${id}`);
  S.gespraech = id;
  $("#titel").textContent = g.titel;
  const v = $("#verlauf"); v.innerHTML = "";
  for (const n of g.nachrichten) {
    if (n.rolle === "user") v.append(nutzerNachricht(n.inhalt, n.meta.anhaenge || []));
    else {
      const a = antwortElement();
      a.text = n.inhalt; a.inhalt.innerHTML = markdown(n.inhalt);
      zeigeWegAusMeta(a, n.meta); zeigeFuss(a, n.meta);
      v.append(a.el);
    }
  }
  v.scrollTop = v.scrollHeight;
  $("#app").classList.remove("menue-offen");
  ladeGespraeche();
}

// ------------------------------------------------------------------ Nachrichten
function nutzerNachricht(text, anhaenge) {
  return el("div", { class: "nachricht nutzer" }, el("div", { class: "blase" }, text),
    anhaenge.length ? el("div", { class: "anh" }, "📎 " + anhaenge.map(p => p.split(/[\\/]/).pop()).join(", ")) : null);
}
function antwortElement() {
  const weg = el("div", { class: "weg" });
  const inhalt = el("div", { class: "inhalt" });
  const fuss = el("div", { class: "fuss" });
  return { el: el("div", { class: "nachricht antwort" }, weg, inhalt, fuss), weg, inhalt, fuss, text: "", schritte: {} };
}
function pilleFuer(t) {
  const lokal = t.anbieter === "ollama";
  const app = t.anbieter === "cli";
  return el("span", { class: "pille " + (lokal ? "lokal" : "extern"), title: t.modell || "" },
    t.name, el("span", { style: "color:var(--leise)" }, lokal ? "· lokal" : app ? "· App" : "· API"));
}
function zeigePlan(a, p) {
  a.weg.innerHTML = "";
  const kopf = el("div", { class: "weg-zeile" },
    el("span", { class: "pille lokal", title: "plant lokal" }, "Ollama", p.router ? el("span", { style: "color:var(--leise)" }, "· " + p.router) : null),
    el("span", { class: "pfeil" }, "→"));
  if (p.teile.length === 1) kopf.append(pilleFuer(p.teile[0]));
  else kopf.append(el("span", {}, `${p.teile.length} Teilaufgaben`));
  if (p.ablauf === "dirigent" && p.teile.some(t => t.anbieter !== "ollama"))
    kopf.append(el("span", { class: "pfeil" }, "→"), el("span", { class: "pille lokal", title: "Ollama fasst zusammen" }, "Ollama", el("span", { style: "color:var(--leise)" }, "· fasst zusammen")));
  if (p.teile.length === 1) kopf.append(el("span", {}, `${p.teile[0].kategorie}${p.teile[0].schwierigkeit ? " · Stufe " + p.teile[0].schwierigkeit : ""}`));
  a.weg.append(kopf);
  if (p.begruendung) a.weg.append(el("div", {}, p.begruendung));
  if (p.fehler) a.weg.append(el("div", { class: "weg-zeile" }, el("span", { class: "pille warn" }, "Hinweis"), p.fehler));
  if (p.pc_suche) a.weg.append(el("div", {}, "🔎 PC-Suche: " + p.pc_suche));
  if (p.teile.length > 1) {
    for (const t of p.teile) {
      const z = el("div", { class: "schritt" }, el("span", { class: "drehen" }), pilleFuer(t), el("span", {}, t.auftrag));
      a.schritte[t.nr] = z; a.weg.append(z);
    }
  }
  a.status = el("div", { class: "status-text" }); a.weg.append(a.status);
}
function aufklapper(titel, text, alsMarkdown) {
  const inhalt = el("div", { class: alsMarkdown ? "inhalt aufklapp-inhalt" : "aufklapp-inhalt" });
  if (alsMarkdown) inhalt.innerHTML = markdown(text); else inhalt.textContent = text;
  return el("details", { class: "aufklapp" }, el("summary", {}, titel), inhalt);
}

function zeigeWegAusMeta(a, meta) {
  if (!meta || !meta.spezialisten) return;
  const kopf = el("div", { class: "weg-zeile" }, el("span", { class: "pille lokal" }, "Ollama", meta.plan?.router ? el("span", { style: "color:var(--leise)" }, "· " + meta.plan.router) : null), el("span", { class: "pfeil" }, "→"));
  for (const s of meta.spezialisten) kopf.append(pilleFuer(s));
  a.weg.append(kopf);
  if (meta.ablauf === "dirigent") kopf.append(el("span", { class: "pfeil" }, "→"), el("span", { class: "pille lokal" }, "Ollama", el("span", { style: "color:var(--leise)" }, "· fasst zusammen")));
  if (meta.plan?.pc_suche) a.weg.append(el("div", {}, "🔎 PC-Suche: " + meta.plan.pc_suche));
  for (const s of meta.schritte || []) {
    a.weg.append(aufklapper(`📝 Ollama → ${s.name}: Auftrag`, s.prompt, false));
    a.weg.append(aufklapper(`💬 ${s.name} → Ollama: Antwort`, s.antwort, true));
  }
}
function zeigeFuss(a, meta) {
  a.fuss.innerHTML = "";
  if (!meta) return;
  const t = meta.tokens || {};
  a.fuss.append(el("span", { class: "pille lokal", title: "Tokens lokal (kostenlos)" }, "lokal " + zahl(t.lokal)));
  a.fuss.append(el("span", { class: "pille extern", title: "Tokens bei Claude/Gemini/ChatGPT" }, "extern " + zahl(t.extern)));
  if (t.cache_treffer) a.fuss.append(el("span", { class: "pille" }, "aus Cache"));
  if (meta.vorbereitung?.vorher_zeichen) a.fuss.append(el("span", { class: "pille", title: "Ollama hat die Anhänge vorher gekürzt" },
    `vorbereitet ${zahl(meta.vorbereitung.vorher_zeichen)} → ${zahl(meta.vorbereitung.nachher_zeichen)} Zeichen`));
  if (meta.dauer) a.fuss.append(el("span", {}, meta.dauer.toString().replace(".", ",") + " s"));
  for (const d of meta.dateien || []) a.fuss.append(el("span", { class: "pille datei", onclick: () => oeffneDatei("ap", d) }, "📄 " + d));
}

// ------------------------------------------------------------------ Aufgabe senden (Stream)
async function sende(text) {
  text = text.trim();
  if (!text || S.laeuft) return;
  if (!S.gespraech) {
    const d = await api("/api/gespraeche", { methode: "POST", body: { titel: text.split("\n")[0].slice(0, 60) } });
    S.gespraech = d.id;
    $("#titel").textContent = text.split("\n")[0].slice(0, 60);
    $("#verlauf").innerHTML = "";
  }
  const anh = S.anhaenge.slice();
  const v = $("#verlauf");
  v.append(nutzerNachricht(text, anh.map(x => x.pfad)));
  const a = antwortElement();
  a.status = el("div", { class: "status-text" }, "Ollama plant die Aufgabe …");
  a.weg.append(a.status);
  v.append(a.el); v.scrollTop = v.scrollHeight;
  $("#text").value = ""; groesseAnpassen();
  S.anhaenge = []; zeigeChips();

  const ctrl = new AbortController(); S.laeuft = ctrl; sendeKnopf();
  let gerendert = 0;
  const rendern = () => {
    if (gerendert) return;
    gerendert = requestAnimationFrame(() => {
      gerendert = 0;
      const unten = v.scrollHeight - v.scrollTop - v.clientHeight < 80;
      a.inhalt.innerHTML = markdown(a.text);
      if (unten) v.scrollTop = v.scrollHeight;
    });
  };
  const behandle = (typ, d) => {
    switch (typ) {
      case "status": if (a.status) a.status.textContent = d.text; break;
      case "hinweis": a.weg.append(el("div", { class: "weg-zeile" }, el("span", { class: "pille warn" }, "Hinweis"), d.text)); break;
      case "plan": zeigePlan(a, d); break;
      case "pc_suche":
        a.weg.append(el("div", {}, `🔎 Ollama fand ${d.treffer.length} Treffer: ` + d.treffer.slice(0, 5).map(t => t.pfad.split(/[\\/]/).pop()).join(", ")));
        break;
      case "vorbereitung":
        a.weg.append(el("div", {}, `🧹 Ollama hat die Daten vorbereitet: ${zahl(d.vorher_zeichen)} → ${zahl(d.nachher_zeichen)} Zeichen`));
        break;
      case "prompt":
        a.weg.append(aufklapper(`📝 Ollama → ${d.name}: Auftrag`, d.prompt, false));
        break;
      case "schritt":
        if (a.status) a.status.textContent = d.status === "laeuft" ? `${d.name} arbeitet …` : "";
        if (!a.schritte[d.nr] && d.status === "fertig" && d.text) a.weg.append(aufklapper(`💬 ${d.name} → Ollama: Antwort`, d.text, true));
        if (a.schritte[d.nr] && d.status === "fertig") {
          const z = a.schritte[d.nr];
          z.firstChild.replaceWith(el("span", {}, "✓"));
          if (d.text) {
            const det = el("details", {}, el("summary", {}, "Teilergebnis"), el("div", { class: "inhalt" }));
            det.querySelector(".inhalt").innerHTML = markdown(d.text);
            z.append(det);
          }
        }
        break;
      case "ersatz":
        a.weg.append(el("div", { class: "weg-zeile" }, el("span", { class: "pille warn" }, "Ersatz"),
          `${d.von} fiel aus${d.an ? " → " + d.an + " übernimmt" : ""}`, el("span", { title: d.fehler, style: "cursor:help" }, " ⓘ")));
        break;
      case "zuruecksetzen": a.text = ""; rendern(); break;
      case "delta": a.text += d.text; rendern(); break;
      case "dateien":
        if (d.fehler?.length) a.weg.append(el("div", { class: "weg-zeile" }, el("span", { class: "pille warn" }, "Nicht gespeichert"), d.fehler.join("; ")));
        ladeArbeitsplatz();
        break;
      case "fertig":
        a.text = d.text; a.inhalt.innerHTML = markdown(a.text);
        if (a.status) a.status.textContent = "";
        zeigeFuss(a, d.meta);
        break;
      case "fehler":
        if (a.status) a.status.textContent = "";
        a.inhalt.append(el("div", { class: "fehler-box" }, d.text));
        break;
    }
  };
  try {
    const r = await fetch("/api/aufgabe", {
      method: "POST", signal: ctrl.signal, headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        nachricht: text, gespraech_id: S.gespraech,
        arbeitsplatz_dateien: anh.filter(x => x.art === "ap").map(x => x.pfad),
        pc_pfade: anh.filter(x => x.art === "pc").map(x => x.pfad),
        optionen: {
          spezialist: $("#spezialist").value, strategie: $("#strategie").value,
          zerlegen: $("#zerlegen").checked, pc_suche: $("#pcsuche").checked, dateien_speichern: $("#speichern").checked,
          ablauf: $("#ueberollama").checked ? "dirigent" : "direkt",
        },
      }),
    });
    if (!r.ok) { const d = await r.json().catch(() => ({})); throw new Error(d.fehler || r.statusText); }
    const leser = r.body.getReader(), dec = new TextDecoder();
    let puffer = "";
    for (;;) {
      const { value, done } = await leser.read();
      if (done) break;
      puffer += dec.decode(value, { stream: true });
      let i;
      while ((i = puffer.indexOf("\n\n")) >= 0) {
        const block = puffer.slice(0, i); puffer = puffer.slice(i + 2);
        let typ = "message", daten = "";
        for (const z of block.split("\n")) {
          if (z.startsWith("event: ")) typ = z.slice(7);
          else if (z.startsWith("data: ")) daten += z.slice(6);
        }
        if (daten) behandle(typ, JSON.parse(daten));
      }
    }
  } catch (e) {
    if (e.name === "AbortError") { if (a.status) a.status.textContent = "Abgebrochen."; }
    else behandle("fehler", { text: e.message });
  } finally {
    S.laeuft = null; sendeKnopf();
    ladeGespraeche();
  }
}

function sendeKnopf() {
  const k = $("#senden");
  if (S.laeuft) { k.textContent = "■"; k.classList.add("stopp"); k.title = "Stoppen"; }
  else { k.textContent = "↑"; k.classList.remove("stopp"); k.title = "Senden"; }
}

// ------------------------------------------------------------------ Anhänge
function haengeAn(art, pfad) {
  if (!S.anhaenge.some(x => x.art === art && x.pfad === pfad)) S.anhaenge.push({ art, pfad });
  zeigeChips();
  toast("Angehängt: " + pfad.split(/[\\/]/).pop());
}
function zeigeChips() {
  const c = $("#chips"); c.innerHTML = "";
  S.anhaenge.forEach((x, i) => c.append(el("span", { class: "chip", title: x.pfad },
    x.art === "pc" ? "💻" : "📁", el("span", {}, x.pfad.split(/[\\/]/).pop() || x.pfad),
    el("button", { type: "button", onclick: () => { S.anhaenge.splice(i, 1); zeigeChips(); } }, "✕"))));
}

// ------------------------------------------------------------------ Arbeitsplatz
async function ladeArbeitsplatz() {
  let d;
  try { d = await api("/api/arbeitsplatz"); } catch { return; }
  $("#ap-wurzel").textContent = d.wurzel;
  const baum = $("#ap-baum"); baum.innerHTML = "";
  let ordner = null;
  for (const f of d.dateien) {
    const teile = f.pfad.split("/");
    const o = teile.slice(0, -1).join("/");
    if (o !== ordner) {
      ordner = o;
      baum.append(el("div", { class: "ordner-titel" }, o ? "📁 " + o : "📁 /",
        o ? el("button", { class: "aktion", title: "Ganzen Ordner anhängen", onclick: () => haengeAn("ap", o) }, "📎") : null));
    }
    baum.append(el("div", { class: "eintrag" },
      el("span", { class: "name", onclick: () => oeffneDatei("ap", f.pfad) }, teile.at(-1)),
      el("span", { class: "mehr" }, groesse(f.groesse)),
      el("button", { class: "aktion", title: "An Aufgabe anhängen", onclick: () => haengeAn("ap", f.pfad) }, "📎"),
      el("button", { class: "aktion", title: "Löschen (Sicherung in .versionen)", onclick: async () => {
        if (!confirm(`${f.pfad} löschen? (Eine Sicherung bleibt in .versionen)`)) return;
        await api("/api/arbeitsplatz/datei?pfad=" + encodeURIComponent(f.pfad), { methode: "DELETE" });
        ladeArbeitsplatz();
      } }, "🗑")));
  }
  if (!d.dateien.length) baum.append(el("div", { class: "hinweis" }, "Noch leer."));
}

async function oeffneDatei(art, pfad) {
  try {
    const d = await api((art === "pc" ? "/api/pc/datei?pfad=" : "/api/arbeitsplatz/datei?pfad=") + encodeURIComponent(pfad));
    S.ansicht = { art, pfad };
    $("#ansicht-pfad").textContent = (art === "pc" ? "💻 " : "📁 ") + pfad;
    $("#ansicht-text").value = d.inhalt;
    $("#ansicht-text").readOnly = art === "pc";
    $("#ansicht-speichern").hidden = art === "pc";
    $("#ansicht").hidden = false;
    $("#app").classList.remove("ohne-panel");
  } catch (e) { toast(e.message, 4000); }
}

// ------------------------------------------------------------------ KI-Ordner
function zeigeKiOrdner() {
  const st = S.status; if (!st) return;
  const liste = $("#ki-liste"); liste.innerHTML = "";
  const namen = { anthropic: "Claude", openai: "ChatGPT", gemini: "Gemini", ollama: "Ollama" };
  liste.append(el("div", { class: "karte" },
    el("div", { class: "karte-kopf" }, "🤝 gemeinsam"),
    el("div", { class: "klein" }, "Gilt für alle KIs."),
    el("div", { class: "links" },
      el("button", { class: "knopf klein", onclick: () => oeffneDatei("ap", "gemeinsam/ANWEISUNGEN.md") }, "ANWEISUNGEN.md"),
      el("button", { class: "knopf klein", onclick: () => oeffneDatei("ap", "gemeinsam/GEDAECHTNIS.md") }, "GEDAECHTNIS.md"))));
  for (const k of st.ki_ordner) {
    liste.append(el("div", { class: "karte" },
      el("div", { class: "karte-kopf" }, "📁 " + k.ordner, el("span", { class: "platz" }), el("span", { class: "pille" }, namen[k.anbieter] || k.anbieter)),
      el("div", { class: "links" },
        el("button", { class: "knopf klein", onclick: () => oeffneDatei("ap", k.ordner + "/ANWEISUNGEN.md") }, "ANWEISUNGEN.md"),
        el("button", { class: "knopf klein", onclick: () => oeffneDatei("ap", k.ordner + "/GEDAECHTNIS.md") }, "GEDAECHTNIS.md")),
      k.tool_ordner ? el("div", { class: "klein", style: "margin-top:6px" },
        k.tool_ordner_da ? "Programm-Ordner gefunden: " : "Programm-Ordner nicht gefunden: ", k.tool_ordner, " ",
        k.tool_ordner_da ? el("button", { class: "knopf klein leise", onclick: () => { zeigeTab("pc"); ladePcOrdner(k.tool_ordner); } }, "öffnen") : null) : null));
  }
}

// ------------------------------------------------------------------ PC
async function ladePcStart() {
  try {
    const d = await api("/api/pc/start");
    const box = $("#pc-start"); box.innerHTML = "";
    for (const s of d.startpunkte) box.append(el("button", { onclick: () => ladePcOrdner(s.pfad), title: s.pfad }, s.titel));
    if (!S.pcPfad && d.startpunkte.length) ladePcOrdner(d.startpunkte[0].pfad);
  } catch (e) { $("#pc-liste").textContent = e.message; }
}

async function ladePcOrdner(pfad) {
  let d;
  try { d = await api("/api/pc/ordner?pfad=" + encodeURIComponent(pfad)); } catch (e) { toast(e.message, 4000); return; }
  S.pcPfad = d.pfad;
  const p = $("#pc-pfad"); p.innerHTML = "";
  if (d.eltern) p.append(el("button", { onclick: () => ladePcOrdner(d.eltern) }, "⬆ hoch"), " ");
  p.append(d.pfad);
  const akt = $("#pc-aktionen"); akt.innerHTML = "";
  akt.append(
    el("button", { class: "knopf klein", title: "Ordner an Aufgabe anhängen", onclick: () => haengeAn("pc", d.pfad) }, "📎 Ordner anhängen"),
    el("button", { class: "knopf klein", title: "In den Arbeitsplatz kopieren", onclick: () => bereitstellen(d.pfad) }, "⤓ bereitstellen"),
    el("button", { class: "knopf klein", title: "Dateinamen in den Suchindex aufnehmen (schnell)", onclick: () => starteScan(d.pfad, false) }, "Scannen"),
    el("button", { class: "knopf klein", title: "Ollama liest Textdateien und beschreibt sie (langsamer, bessere Suche)", onclick: () => starteScan(d.pfad, true) }, "Mit Ollama analysieren"));
  zeigePcListe(d.eintraege, d.gekuerzt);
}

function zeigePcListe(eintraege, gekuerzt) {
  const liste = $("#pc-liste"); liste.innerHTML = "";
  for (const e of eintraege) {
    const ordner = e.typ === "ordner";
    liste.append(el("div", { class: "eintrag" },
      el("span", { class: "name", title: e.pfad, onclick: () => ordner ? ladePcOrdner(e.pfad) : oeffneDatei("pc", e.pfad) },
        (ordner ? "📁 " : "📄 ") + (e.name || e.pfad)),
      el("span", { class: "mehr" }, ordner ? "" : groesse(e.groesse || 0)),
      el("button", { class: "aktion", title: "An Aufgabe anhängen", onclick: () => haengeAn("pc", e.pfad) }, "📎"),
      el("button", { class: "aktion", title: "In den Arbeitsplatz kopieren", onclick: () => bereitstellen(e.pfad) }, "⤓")));
  }
  if (gekuerzt) liste.append(el("div", { class: "hinweis" }, "Liste gekürzt."));
  if (!eintraege.length) liste.append(el("div", { class: "hinweis" }, "Leer."));
}

async function bereitstellen(pfad) {
  try {
    const d = await api("/api/pc/bereitstellen", { methode: "POST", body: { pfad } });
    toast("Bereitgestellt im Arbeitsplatz: " + d.ziel, 3500);
    ladeArbeitsplatz();
  } catch (e) { toast(e.message, 4500); }
}

async function starteScan(pfad, analyse) {
  try {
    await api("/api/pc/scan", { methode: "POST", body: { pfad, analyse } });
    beobachteJob();
  } catch (e) { toast(e.message, 4500); }
}

async function beobachteJob() {
  clearTimeout(S.jobTimer);
  let j;
  try { j = await api("/api/pc/scan"); } catch { return; }
  const box = $("#pc-job"); box.innerHTML = "";
  if (j.art) {
    const name = j.art === "analyse" ? "Ollama analysiert" : "Scan";
    const text = j.laeuft
      ? `${name}: ${zahl(j.gezaehlt)}${j.gesamt ? " / " + zahl(j.gesamt) : ""} · ${(j.aktuell || "").slice(-60)}`
      : `${name} fertig: ${zahl(j.gezaehlt)} Einträge · Index: ${zahl(j.index_eintraege)}${j.fehler ? " · " + j.fehler : ""}`;
    box.append(el("div", {}, text,
      j.laeuft ? el("button", { class: "knopf klein leise", onclick: () => api("/api/pc/scan/abbrechen", { methode: "POST", body: {} }) }, "abbrechen") : null));
    if (j.laeuft && j.gesamt) { const b = el("div", { class: "balken" }, el("div")); b.firstChild.style.width = Math.round(100 * j.gezaehlt / j.gesamt) + "%"; box.append(b); }
  } else if (j.index_eintraege) box.append(el("div", {}, `Suchindex: ${zahl(j.index_eintraege)} Einträge`));
  else box.append(el("div", {}, "Noch kein Suchindex – einen Ordner scannen, damit Ollama auf dem PC suchen kann."));
  if (j.laeuft) S.jobTimer = setTimeout(beobachteJob, 1000);
}

async function pcSuche() {
  const q = $("#pc-suche").value.trim(); if (!q) return;
  try {
    const d = await api("/api/pc/suche?q=" + encodeURIComponent(q));
    $("#pc-pfad").textContent = `Suche: „${q}“ – ${d.treffer.length} Treffer`;
    $("#pc-aktionen").innerHTML = "";
    zeigePcListe(d.treffer.map(t => ({ ...t, name: t.pfad })), false);
  } catch (e) { toast(e.message, 4000); }
}

// ------------------------------------------------------------------ Verbrauch
async function ladeVerbrauch() {
  const d = await api("/api/verbrauch");
  const box = $("#verbrauch"); box.innerHTML = "";
  box.append(el("div", { class: "karte" },
    el("div", { class: "klein" }, "Anteil lokal (kostenlos) erledigter Tokens"),
    el("div", { class: "gross-zahl" }, d.lokal_anteil.toString().replace(".", ",") + " %"),
    el("div", { class: "klein" }, `${zahl(d.lokal_tokens)} von ${zahl(d.gesamt_tokens)} Tokens`)));
  const namen = { ollama: "Ollama (lokal)", cli: "Programme (Abo)", anthropic: "Claude-API", openai: "ChatGPT-API", gemini: "Gemini-API" };
  const t = el("table", { class: "tabelle" }, el("tr", {}, el("th", {}, "Quelle / Modell"), el("th", { class: "z" }, "Aufrufe"),
    el("th", { class: "z" }, "Ein"), el("th", { class: "z" }, "Aus"), el("th", { class: "z" }, "Heute")));
  for (const z of d.zeilen) {
    t.append(el("tr", {}, el("td", {}, `${namen[z.anbieter] || z.anbieter} · ${z.modell}${z.geschaetzt ? " ≈" : ""}`),
      el("td", { class: "z" }, zahl(z.aufrufe) + (z.cache_treffer ? ` (${z.cache_treffer} Cache)` : "")),
      el("td", { class: "z" }, zahl(z.ein)), el("td", { class: "z" }, zahl(z.aus)), el("td", { class: "z" }, zahl(z.heute))));
  }
  box.append(t);
  box.append(el("p", { class: "hinweis", style: "margin-top:10px" }, "≈ = geschätzt (Programm meldet keine genauen Tokens)."));
}

// ------------------------------------------------------------------ Tabs & Bedienung
function zeigeTab(name) {
  $$(".tabs button").forEach(b => b.classList.toggle("aktiv", b.dataset.tab === name));
  $$(".tab-inhalt").forEach(t => (t.hidden = t.id !== "tab-" + name));
  $("#ansicht").hidden = true;
  $("#app").classList.remove("ohne-panel");
  if (name === "arbeitsplatz") ladeArbeitsplatz();
  if (name === "pc") { ladePcStart(); beobachteJob(); }
  if (name === "spezialisten" || name === "ki") ladeStatus();
  if (name === "verbrauch") ladeVerbrauch();
}

function groesseAnpassen() {
  const t = $("#text");
  t.style.height = "auto";
  t.style.height = Math.min(t.scrollHeight, window.innerHeight * 0.4) + "px";
}

let willkommenVorlage;
function bindeBeispiele() {
  $$(".beispiele button").forEach(b => b.addEventListener("click", () => { $("#text").value = b.textContent; groesseAnpassen(); $("#text").focus(); }));
}

function init() {
  willkommenVorlage = $("#willkommen").cloneNode(true);
  bindeBeispiele();
  $("#eingabe").addEventListener("submit", e => {
    e.preventDefault();
    if (S.laeuft) { S.laeuft.abort(); return; }
    sende($("#text").value);
  });
  $("#text").addEventListener("keydown", e => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); $("#eingabe").requestSubmit(); }
  });
  $("#text").addEventListener("input", groesseAnpassen);
  $("#neu").addEventListener("click", neuesGespraech);
  $("#menue").addEventListener("click", () => $("#app").classList.toggle("menue-offen"));
  $("#panel-umschalten").addEventListener("click", () => $("#app").classList.toggle("ohne-panel"));
  $("#status-box").addEventListener("click", () => zeigeTab("spezialisten"));
  $$(".tabs button").forEach(b => b.addEventListener("click", () => zeigeTab(b.dataset.tab)));

  $("#ap-neu-laden").addEventListener("click", ladeArbeitsplatz);
  $("#hochladen").addEventListener("click", () => $("#datei-wahl").click());
  $("#datei-wahl").addEventListener("change", async e => {
    for (const f of e.target.files) {
      const b64 = await new Promise((ok, fehler) => {
        const r = new FileReader();
        r.onload = () => ok(String(r.result).split(",")[1] || "");
        r.onerror = fehler;
        r.readAsDataURL(f);
      });
      try { await api("/api/arbeitsplatz/datei", { methode: "PUT", body: { pfad: "uploads/" + f.name, base64: b64 } }); }
      catch (err) { toast(err.message, 4000); }
    }
    e.target.value = "";
    toast("Hochgeladen nach uploads/");
    ladeArbeitsplatz();
  });
  $("#neue-datei").addEventListener("click", async () => {
    const pfad = prompt("Pfad im Arbeitsplatz (z. B. notizen/ideen.md):");
    if (!pfad) return;
    try { await api("/api/arbeitsplatz/datei", { methode: "PUT", body: { pfad, inhalt: "" } }); ladeArbeitsplatz(); oeffneDatei("ap", pfad); }
    catch (err) { toast(err.message, 4000); }
  });

  $("#ansicht-zu").addEventListener("click", () => ($("#ansicht").hidden = true));
  $("#ansicht-anhaengen").addEventListener("click", () => S.ansicht && haengeAn(S.ansicht.art, S.ansicht.pfad));
  $("#ansicht-speichern").addEventListener("click", async () => {
    if (!S.ansicht || S.ansicht.art !== "ap") return;
    try {
      await api("/api/arbeitsplatz/datei", { methode: "PUT", body: { pfad: S.ansicht.pfad, inhalt: $("#ansicht-text").value } });
      toast("Gespeichert");
      ladeArbeitsplatz();
    } catch (err) { toast(err.message, 4000); }
  });

  $("#pc-suchen").addEventListener("click", pcSuche);
  $("#pc-suche").addEventListener("keydown", e => { if (e.key === "Enter") pcSuche(); });

  if (window.innerWidth < 1100) $("#app").classList.add("ohne-panel");
  ladeStatus();
  ladeGespraeche();
  ladeArbeitsplatz();
  setInterval(ladeStatus, 30000);
  $("#text").focus();
}

init();
