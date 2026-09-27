/* ==========================================================================
   SCAD Studio — app.js
   Oberfläche: KI-Konstrukteur, Bild → 3D, Gemini-Bilder, Code-Editor,
   3D-Ansicht, Prüfbericht und Projekte. Spricht mit der lokalen JSON-API.
   ========================================================================== */

(function () {
  'use strict';

  // ---------------------------------------------------------------------
  // Hilfen
  // ---------------------------------------------------------------------
  var $ = function (sel, root) { return (root || document).querySelector(sel); };
  var $$ = function (sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); };

  function el(tag, attrs, children) {
    var node = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (key) {
      var value = attrs[key];
      if (value === null || value === undefined || value === false) return;
      if (key === 'text') node.textContent = value;
      else if (key === 'html') node.innerHTML = value;
      else if (key === 'class') node.className = value;
      else if (key.slice(0, 2) === 'on') node.addEventListener(key.slice(2), value);
      else node.setAttribute(key, value === true ? '' : value);
    });
    (children || []).forEach(function (child) {
      if (child === null || child === undefined) return;
      node.appendChild(typeof child === 'string' ? document.createTextNode(child) : child);
    });
    return node;
  }

  function api(path, body) {
    var opts = { method: body === undefined ? 'GET' : 'POST', headers: {} };
    if (body !== undefined) {
      opts.headers['Content-Type'] = 'application/json';
      opts.headers['X-Studio'] = '1';
      opts.body = JSON.stringify(body);
    }
    return fetch(path, opts).then(function (res) {
      return res.json().catch(function () { return {}; }).then(function (data) {
        if (!res.ok || data.error) {
          var err = new Error(data.error || ('Fehler ' + res.status));
          err.status = res.status;
          throw err;
        }
        return data;
      });
    });
  }

  var toastTimer = null;
  function toast(text, isError) {
    var t = $('#toast');
    t.textContent = text;
    t.className = 'toast show' + (isError ? ' error' : '');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { t.className = 'toast'; }, isError ? 6000 : 3000);
  }

  function fmt(n, digits) {
    if (n === null || n === undefined || isNaN(n)) return '–';
    return Number(n).toLocaleString('de-DE', { minimumFractionDigits: digits || 0, maximumFractionDigits: digits || 0 });
  }

  function sizeText(size) {
    if (!size) return '–';
    return size.map(function (v) { return fmt(v, 1); }).join(' × ') + ' mm';
  }

  function readFileAsDataURL(file) {
    return new Promise(function (resolve, reject) {
      var reader = new FileReader();
      reader.onload = function () { resolve(reader.result); };
      reader.onerror = function () { reject(new Error('Datei konnte nicht gelesen werden.')); };
      reader.readAsDataURL(file);
    });
  }

  function debounce(fn, ms) {
    var timer = null;
    return function () {
      var args = arguments;
      clearTimeout(timer);
      timer = setTimeout(function () { fn.apply(null, args); }, ms);
    };
  }

  // ---------------------------------------------------------------------
  // Zustand
  // ---------------------------------------------------------------------
  var state = {
    status: null,
    project: null,
    job: null,
    viewer: null,
    viewing: null,            // {kind: 'model'|'part', id}
    aiImages: [],
    refineImages: [],
    genImages: [],
    imgSource: null,          // dataURL des Bildes für Bild → 3D
    imgMode: 'relief',
    imgValues: {}
  };

  // ---------------------------------------------------------------------
  // Bild → 3D: Modi und Parameter
  // ---------------------------------------------------------------------
  var ICONS = {
    relief: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M3 17l4-5 3 3 4-6 7 8"/><path d="M3 20h18"/></svg>',
    lithophane: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><rect x="4" y="4" width="16" height="16" rx="1"/><circle cx="12" cy="11" r="3"/><path d="M6 18l4-4 3 2 5-5"/></svg>',
    extrude: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M12 3l2.5 5 5.5.8-4 3.9.9 5.5L12 15.6 7.1 18.2 8 12.7 4 8.8 9.5 8z"/></svg>',
    plate: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><rect x="3" y="6" width="18" height="12" rx="2"/><path d="M8 14l2-4 2 3 2-2 2 3"/></svg>',
    keychain: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><circle cx="12" cy="5" r="2.5"/><path d="M7 9h10l-2 11H9z"/></svg>',
    stamp: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M9 3h6v6l3 3H6l3-3z"/><rect x="4" y="12" width="16" height="4"/><path d="M4 20h16"/></svg>',
    cookie_cutter: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M12 4c3 0 6 2 6 5 0 4-6 10-6 10S6 13 6 9c0-3 3-5 6-5z"/><path d="M12 7c1.6 0 3 1 3 2.5"/></svg>',
    stencil: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><rect x="3" y="4" width="18" height="16" rx="1"/><path d="M12 8l1.5 3 3 .4-2.2 2.1.6 3L12 15l-2.9 1.5.6-3-2.2-2.1 3-.4z"/></svg>'
  };

  var P = {
    // Relief / Lithophanie
    width: { label: 'Breite (mm)', type: 'range', min: 20, max: 220, step: 1, def: 100 },
    depth: { label: 'Relieftiefe (mm)', type: 'range', min: 0.4, max: 20, step: 0.2, def: 3 },
    base: { label: 'Grundplatte (mm)', type: 'range', min: 0.4, max: 10, step: 0.2, def: 1.2 },
    frame: { label: 'Rahmen (mm)', type: 'range', min: 0, max: 15, step: 0.5, def: 0 },
    invert: { label: 'Invertieren', type: 'check', def: false },
    resolution_r: { key: 'resolution', label: 'Auflösung (Punkte)', type: 'range', min: 50, max: 400, step: 10, def: 200, adv: true },
    blur: { label: 'Weichzeichnen', type: 'range', min: 0, max: 5, step: 0.5, def: 0, adv: true },
    gamma: { label: 'Gamma', type: 'range', min: 0.3, max: 3, step: 0.1, def: 1, adv: true },
    auto_contrast: { label: 'Kontrast automatisch', type: 'check', def: true, adv: true },
    // Silhouette
    size: { label: 'Größe (längste Seite, mm)', type: 'range', min: 10, max: 220, step: 1, def: 80 },
    height: { label: 'Höhe (mm)', type: 'range', min: 0.4, max: 30, step: 0.2, def: 3 },
    threshold_auto: { label: 'Schwellwert automatisch', type: 'check', def: true },
    threshold: { label: 'Schwellwert', type: 'range', min: 1, max: 254, step: 1, def: 128 },
    thicken: { label: 'Linien verdicken (mm)', type: 'range', min: -1, max: 3, step: 0.1, def: 0 },
    resolution_s: { key: 'resolution', label: 'Auflösung (Pixel)', type: 'range', min: 100, max: 900, step: 20, def: 400, adv: true },
    simplify: { label: 'Vereinfachen (px)', type: 'range', min: 0, max: 3, step: 0.1, def: 0.6, adv: true },
    smooth: { label: 'Glätten (Stufen)', type: 'range', min: 0, max: 3, step: 1, def: 1, adv: true },
    cleanup: { label: 'Flecken entfernen', type: 'range', min: 0, max: 5, step: 1, def: 1, adv: true },
    min_area: { label: 'Mindestfläche (px²)', type: 'range', min: 0, max: 200, step: 1, def: 4, adv: true },
    use_alpha: { label: 'Transparenz nutzen', type: 'select', options: [['auto', 'automatisch'], ['yes', 'ja'], ['no', 'nein']], def: 'auto', adv: true }
  };

  var SIL_COMMON = ['size', 'height', 'invert', 'threshold_auto', 'threshold', 'thicken', 'resolution_s', 'simplify', 'smooth', 'cleanup', 'min_area', 'use_alpha'];

  var MODES = {
    relief: { label: 'Relief', help: 'Helligkeit wird zu Höhe: helle Bereiche stehen hoch. Ideal für Tiefenkarten aus Gemini, Wandbilder und Plaketten.',
      params: ['width', 'depth', 'base', 'frame', 'invert', 'resolution_r', 'blur', 'gamma', 'auto_contrast'] },
    lithophane: { label: 'Lithophanie', help: 'Dünne Platte, dunkle Stellen dicker – gegen Licht gehalten erscheint das Foto. Hochkant drucken, 100 % Infill.',
      params: ['width', 'depth', 'base', 'frame', 'invert', 'resolution_r', 'blur', 'gamma', 'auto_contrast'],
      defaults: { width: 100, depth: 2.4, base: 0.8, frame: 3, invert: true, resolution: 250 } },
    extrude: { label: 'Figur', help: 'Die dunkle Form wird flach extrudiert – z. B. Logo, Tattoo-Motiv, Wanddeko.', params: SIL_COMMON, extra: [] },
    plate: { label: 'Schild', help: 'Grundplatte mit erhabenem (oder eingraviertem) Motiv.', params: SIL_COMMON, extra: 'plate' },
    keychain: { label: 'Anhänger', help: 'Motiv mit Rückplatte, die lose Teile verbindet, und Öse für den Schlüsselring.', params: SIL_COMMON, extra: 'keychain', defaults: { size: 50 } },
    stamp: { label: 'Stempel', help: 'Spiegelverkehrtes Motiv auf einem Block – der Abdruck ist richtig herum.', params: SIL_COMMON, extra: 'stamp', defaults: { size: 40 } },
    cookie_cutter: { label: 'Ausstechform', help: 'Dünne Schneidwand entlang der Außenkontur mit Griffkante.', params: SIL_COMMON, extra: 'cookie_cutter', skip: ['height'], defaults: { size: 70 } },
    stencil: { label: 'Schablone', help: 'Platte, aus der das Motiv ausgeschnitten ist.', params: SIL_COMMON, extra: 'stencil', skip: ['height'] }
  };

  // Modusspezifische Zusatzparameter (Namen wie in generators.silhouette_scad)
  var MODE_EXTRA = {
    plate: [
      { key: 'plate_thickness', label: 'Plattenstärke (mm)', type: 'range', min: 0.8, max: 10, step: 0.2, def: 2 },
      { key: 'plate_margin', label: 'Rand (mm)', type: 'range', min: 0, max: 30, step: 0.5, def: 5 },
      { key: 'corner_radius', label: 'Eckenradius (mm)', type: 'range', min: 0, max: 20, step: 0.5, def: 3 },
      { key: 'engrave', label: 'Gravieren statt erhaben', type: 'check', def: false }
    ],
    keychain: [
      { key: 'backing', label: 'Rückplatte Rand (mm)', type: 'range', min: 0, max: 8, step: 0.5, def: 2 },
      { key: 'raise_height', label: 'Motiv erhaben (mm)', type: 'range', min: 0, max: 4, step: 0.2, def: 1 },
      { key: 'backing_solid', label: 'Innenflächen füllen', type: 'check', def: true },
      { key: 'ring_outer', label: 'Öse außen Ø (mm)', type: 'range', min: 6, max: 20, step: 0.5, def: 10 },
      { key: 'ring_inner', label: 'Öse innen Ø (mm)', type: 'range', min: 3, max: 14, step: 0.5, def: 5 }
    ],
    stamp: [
      { key: 'stamp_thickness', label: 'Blockstärke (mm)', type: 'range', min: 2, max: 20, step: 0.5, def: 6 },
      { key: 'stamp_margin', label: 'Blockrand (mm)', type: 'range', min: 0, max: 20, step: 0.5, def: 3 },
      { key: 'corner_radius', label: 'Eckenradius (mm)', type: 'range', min: 0, max: 20, step: 0.5, def: 3 },
      { key: 'handle', label: 'Griff (eigenes Teil zum Aufkleben)', type: 'check', def: false },
      { key: 'handle_height', label: 'Griffhöhe (mm)', type: 'range', min: 10, max: 60, step: 1, def: 25 },
      { key: 'handle_diameter', label: 'Griff Ø (mm)', type: 'range', min: 10, max: 40, step: 1, def: 20 }
    ],
    cookie_cutter: [
      { key: 'cutter_height', label: 'Schneidhöhe (mm)', type: 'range', min: 5, max: 40, step: 1, def: 15 },
      { key: 'cutter_wall', label: 'Schneidwand (mm)', type: 'range', min: 0.6, max: 3, step: 0.1, def: 0.8 },
      { key: 'flange_width', label: 'Griffkante Breite (mm)', type: 'range', min: 0, max: 10, step: 0.5, def: 4 },
      { key: 'flange_height', label: 'Griffkante Höhe (mm)', type: 'range', min: 0.6, max: 5, step: 0.2, def: 1.6 }
    ],
    stencil: [
      { key: 'stencil_thickness', label: 'Stärke (mm)', type: 'range', min: 0.6, max: 5, step: 0.2, def: 1.2 },
      { key: 'stencil_margin', label: 'Rand (mm)', type: 'range', min: 2, max: 40, step: 1, def: 8 },
      { key: 'corner_radius', label: 'Eckenradius (mm)', type: 'range', min: 0, max: 20, step: 0.5, def: 3 }
    ]
  };

  function paramSpec(name) {
    var spec = P[name];
    return { key: spec.key || name, name: name, label: spec.label, type: spec.type, min: spec.min, max: spec.max,
      step: spec.step, def: spec.def, adv: spec.adv, options: spec.options };
  }

  function modeSpecs(mode) {
    var m = MODES[mode];
    var specs = m.params.filter(function (name) { return (m.skip || []).indexOf(name) < 0; }).map(paramSpec);
    if (m.extra && MODE_EXTRA[m.extra]) specs = specs.concat(MODE_EXTRA[m.extra]);
    var defs = m.defaults || {};
    return specs.map(function (s) {
      var copy = Object.assign({}, s);
      if (defs[copy.key] !== undefined) copy.def = defs[copy.key];
      return copy;
    });
  }

  // ---------------------------------------------------------------------
  // Start
  // ---------------------------------------------------------------------
  function init() {
    setupTabs();
    setupViewer();
    setupAI();
    setupImageTab();
    setupImageGen();
    setupCode();
    setupReport();
    setupDialogs();
    setupLog();
    setupPaste();
    loadStatus().then(function () {
      var last = null;
      try { last = localStorage.getItem('scadstudio.project'); } catch (e) { /* egal */ }
      if (last) openProject(last, true);
    });
  }

  function loadStatus() {
    return api('/api/status').then(function (status) {
      state.status = status;
      renderChips();
      renderProviderHint();
      fillSettings();
      fillPresets();
      updateBed();
      return status;
    }).catch(function (err) { toast('Server nicht erreichbar: ' + err.message, true); });
  }

  function renderChips() {
    var s = state.status;
    var box = $('#status-chips');
    box.innerHTML = '';
    var o = s.openscad;
    var oChip = el('span', { class: 'chip ' + (o.found ? 'ok' : 'err'),
      title: o.found ? o.path : o.error,
      text: o.found ? ('OpenSCAD ' + o.version + (o.manifold ? ' · Manifold' : '')) : 'OpenSCAD fehlt' });
    if (!o.found) oChip.addEventListener('click', function () { openSettings(); });
    box.appendChild(oChip);
    var prov = s.providers;
    var active = prov.active;
    box.appendChild(el('span', { class: 'chip ' + (prov[active] ? 'ok' : 'warn'),
      title: prov[active] ? 'KI eingerichtet' : 'KI noch nicht eingerichtet – Einstellungen öffnen',
      text: 'KI: ' + (s.provider_labels[active] || active) + (prov[active] ? '' : ' (einrichten)') }));
    if (s.projects_error) {
      box.appendChild(el('span', { class: 'chip err', title: s.projects_error, text: 'Projektordner fehlt',
        onclick: function () { openSettings(); } }));
    }
    var pr = s.printer;
    box.appendChild(el('span', { class: 'chip', title: 'Düse ' + pr.nozzle + ' mm · Spaltmaß ' + pr.tol + ' mm · ' + pr.material,
      text: pr.name + ' · ' + pr.bed.join('×') + ' mm' }));
  }

  function renderProviderHint() {
    var s = state.status;
    var active = s.providers.active;
    var hint = $('#ai-provider-hint');
    if (s.providers[active]) {
      hint.textContent = 'KI: ' + (s.provider_labels[active] || active) + ' · ' + s.settings.repair_attempts +
        ' Auto-Reparaturen · ' + s.settings.visual_rounds + ' Sichtprüfung(en) · Drucker ' + s.printer.name;
    } else {
      hint.innerHTML = '';
      hint.appendChild(document.createTextNode('Noch keine KI eingerichtet. '));
      hint.appendChild(el('a', { href: '#', text: 'Jetzt in den Einstellungen einrichten', onclick: function (e) { e.preventDefault(); openSettings(); } }));
    }
  }

  // ---------------------------------------------------------------------
  // Tabs
  // ---------------------------------------------------------------------
  function setupTabs() {
    $$('.tabs button').forEach(function (btn) {
      btn.addEventListener('click', function () { showTab(btn.getAttribute('data-tab')); });
    });
  }

  function showTab(name) {
    $$('.tabs button').forEach(function (b) { b.classList.toggle('active', b.getAttribute('data-tab') === name); });
    $$('.tab-body').forEach(function (b) { b.classList.toggle('active', b.getAttribute('data-tab-body') === name); });
    if (name === 'code') updateGutter();
  }

  function activeTab() {
    var b = $('.tabs button.active');
    return b ? b.getAttribute('data-tab') : 'ai';
  }

  // ---------------------------------------------------------------------
  // 3D-Ansicht
  // ---------------------------------------------------------------------
  function setupViewer() {
    if (!window.STLViewer) {
      $('#viewer').textContent = '3D-Ansicht nicht verfügbar.';
      return;
    }
    try {
      state.viewer = new window.STLViewer($('#viewer'), {
        background: '#101014', modelColor: '#d8b56d', gridColor: '#2a2a33', showGrid: true, bed: [220, 220, 250]
      });
    } catch (err) {
      $('#viewer').textContent = '3D-Ansicht konnte nicht gestartet werden: ' + err.message;
      return;
    }
    $$('#view-buttons button').forEach(function (b) {
      b.addEventListener('click', function () { callViewer('setView', b.getAttribute('data-view')); });
    });
    $('#opt-dims').addEventListener('change', function (e) { callViewer('setShowDimensions', e.target.checked); });
    $('#opt-wire').addEventListener('change', function (e) { callViewer('setWireframe', e.target.checked); });
    $('#opt-bed').addEventListener('change', updateBed);
    $('#opt-clip').addEventListener('change', function (e) {
      $('#opt-clip-z').disabled = !e.target.checked;
      applyClip();
    });
    $('#opt-clip-z').addEventListener('input', applyClip);
    $('#opt-xray').addEventListener('change', function (e) { callViewer('setXray', e.target.checked); });
    $('#btn-shot').addEventListener('click', function () {
      var shot = callViewer('screenshot');
      if (!shot) { toast('Bild konnte nicht erstellt werden.', true); return; }
      var a = el('a', { href: shot, download: ((state.project && state.project.id) || 'modell') + '.png' });
      document.body.appendChild(a); a.click(); a.remove();
    });
  }

  // Druckbett nur dort zeigen, wo es aussagekräftig ist: bei einteiligen Modellen
  // und bei einzelnen Druckteilen – ein Zusammenbau wird ja nie am Stück gedruckt.
  function updateBed() {
    var multiPart = !!(state.project && state.project.parts && state.project.parts.some(function (p) { return !p.unused; }));
    var assemblyView = !state.viewing || state.viewing.kind === 'model';
    var show = $('#opt-bed').checked && state.status && !(multiPart && assemblyView);
    callViewer('setBed', show ? state.status.printer.bed : null);
  }

  function callViewer(method) {
    var v = state.viewer;
    if (v && typeof v[method] === 'function') {
      try { return v[method].apply(v, Array.prototype.slice.call(arguments, 1)); } catch (e) { console.warn(e); }
    }
    return null;
  }

  function applyClip() {
    if (!state.viewer) return;
    if (!$('#opt-clip').checked) { callViewer('setClipZ', null); return; }
    var info = callViewer('getInfo');
    if (!info) return;
    var pct = Number($('#opt-clip-z').value) / 100;
    callViewer('setClipZ', info.min[2] + (info.max[2] - info.min[2]) * pct);
  }

  function showInViewer(url, label, what) {
    if (!state.viewer) return Promise.resolve();
    $('#empty-stage').hidden = true;
    $('#viewer-source').textContent = label || '';
    state.viewing = what || null;
    // Gleiches Modell neu gerendert → Kameraposition behalten
    var keepView = state.lastViewUrl === url;
    state.lastViewUrl = url;
    updateBed();
    return state.viewer.loadUrl(url + (url.indexOf('?') < 0 ? '?' : '&') + 't=' + Date.now(), { keepView: keepView }).then(function () {
      applyClip();
      highlightPart();
    }).catch(function (err) { toast(err.message, true); });
  }

  // ---------------------------------------------------------------------
  // Projekte
  // ---------------------------------------------------------------------
  function openProject(id, quiet) {
    if (state.job && !quiet) {
      toast('Bitte warten, bis der laufende Vorgang fertig ist (oder abbrechen).');
      return Promise.resolve();
    }
    return api('/api/projects/' + encodeURIComponent(id)).then(function (project) {
      setProject(project, true);
    }).catch(function (err) {
      if (!quiet) toast(err.message, true);
      try { localStorage.removeItem('scadstudio.project'); } catch (e) { /* egal */ }
    });
  }

  function setProject(project, reloadViewer) {
    state.project = project;
    try { localStorage.setItem('scadstudio.project', project.id); } catch (e) { /* egal */ }
    $('#project-title').value = project.title || '';
    setCode(project.code || '');
    renderVersions();
    renderChat();
    renderReport();
    var hasCode = !!(project.code && project.code.trim());
    $('#ai-new').hidden = hasCode && project.kind === 'ai';
    $('#ai-refine').hidden = !hasCode;
    if (project.kind === 'image' && project.source_image) {
      setImageSource('/files/' + project.id + '/' + project.source_image, true);
      if (project.image_mode && MODES[project.image_mode]) {
        state.imgMode = project.image_mode;
        state.imgValues = Object.assign({}, project.image_params || {});
        renderModes();
        renderParams();
      }
    }
    if (reloadViewer) {
      if (!state.viewing || state.viewing.kind !== 'model') state.lastViewUrl = null;
      if (project.has_stl) showInViewer('/files/' + project.id + '/model.stl', 'Gesamtmodell', { kind: 'model' });
      else { callViewer('clear'); $('#viewer-source').textContent = ''; $('#empty-stage').hidden = false; }
    }
  }

  function newProject() {
    if (busy()) return;
    state.project = null;
    try { localStorage.removeItem('scadstudio.project'); } catch (e) { /* egal */ }
    $('#project-title').value = '';
    setCode('');
    renderVersions();
    renderChat();
    renderReport();
    $('#ai-new').hidden = false;
    $('#ai-refine').hidden = true;
    $('#ai-instruction').value = '';
    state.aiImages = [];
    renderThumbs('#ai-thumbs', state.aiImages);
    callViewer('clear');
    $('#viewer-source').textContent = '';
    $('#empty-stage').hidden = false;
  }

  function renameProject() {
    var title = $('#project-title').value.trim();
    if (!state.project || !title || title === state.project.title) return;
    api('/api/projects/' + state.project.id + '/rename', { title: title }).then(function (p) {
      state.project.title = p.title;
    }).catch(function (err) { toast(err.message, true); });
  }

  // ---------------------------------------------------------------------
  // Aufträge (Jobs)
  // ---------------------------------------------------------------------
  function busy() {
    if (state.job) { toast('Es läuft bereits ein Vorgang – bitte warten oder abbrechen.'); return true; }
    return false;
  }

  function runJob(startPromise, onDone) {
    setBusy(true);
    openLog(true);
    return startPromise.then(function (res) {
      state.job = { id: res.job, since: 0 };
      if (res.project_id && (!state.project || state.project.id !== res.project_id)) {
        state.project = { id: res.project_id, title: $('#project-title').value, code: '', chat: [], versions: [] };
        try { localStorage.setItem('scadstudio.project', res.project_id); } catch (e) { /* egal */ }
      }
      return pollJob(onDone);
    }).catch(function (err) {
      setBusy(false);
      state.job = null;
      toast(err.message, true);
      logLine({ level: 'error', text: err.message, t: '' });
    });
  }

  function pollJob(onDone) {
    return new Promise(function (resolve) {
      function tick() {
        if (!state.job) { resolve(null); return; }
        api('/api/jobs/' + state.job.id + '?since=' + state.job.since).then(function (job) {
          job.log.forEach(logLine);
          state.job.since = job.log_count;
          if (job.log.length) $('#job-status').textContent = job.log[job.log.length - 1].text;
          var bar = $('#progress');
          if (job.progress > 0 && job.progress < 1) {
            bar.classList.remove('indeterminate');
            $('#progress-bar').style.width = Math.round(job.progress * 100) + '%';
          }
          if (job.status === 'running') { setTimeout(tick, 700); return; }
          state.job = null;
          setBusy(false);
          $('#job-status').textContent = job.status === 'done' ? 'Fertig (' + fmt(job.elapsed, 0) + ' s)' : (job.error || job.status);
          if (job.status === 'error') toast(job.error, true);
          var done = function () { if (onDone) onDone(job); resolve(job); };
          if (job.result && job.result.project) {
            setProject(job.result.project, true);
            done();
          } else if (state.project && state.project.id && job.kind !== 'imagegen') {
            openProject(state.project.id).then(done);
          } else {
            done();
          }
        }).catch(function (err) {
          if (err.status === 404) {
            // Auftrag unbekannt (z. B. Server neu gestartet) – nicht endlos weiterfragen
            state.job = null;
            setBusy(false);
            $('#job-status').textContent = 'Vorgang nicht mehr vorhanden (Server neu gestartet?).';
            resolve(null);
            return;
          }
          setTimeout(tick, 1500);
        });
      }
      tick();
    });
  }

  function setBusy(on) {
    $('#progress').hidden = !on;
    $('#btn-cancel').hidden = !on;
    if (on) {
      $('#progress').classList.add('indeterminate');
      $('#progress-bar').style.width = '30%';
    }
    ['#btn-generate', '#btn-refine', '#btn-convert', '#btn-render', '#btn-imagegen'].forEach(function (id) {
      $(id).disabled = on;
    });
  }

  // ---------------------------------------------------------------------
  // Protokoll
  // ---------------------------------------------------------------------
  function setupLog() {
    $('#btn-log-toggle').addEventListener('click', function () { openLog($('#log').hidden); });
    $('#btn-cancel').addEventListener('click', function () {
      if (!state.job) return;
      api('/api/jobs/' + state.job.id + '/cancel', {}).then(function () { toast('Wird abgebrochen …'); });
    });
  }

  function openLog(open) {
    $('#log').hidden = !open;
    $('#btn-log-toggle').setAttribute('aria-expanded', open ? 'true' : 'false');
  }

  function logLine(entry) {
    var log = $('#log');
    var line = el('div', { class: 'l-' + (entry.level || 'info') }, [
      el('span', { class: 't', text: entry.t !== '' && entry.t !== undefined ? (entry.t + ' s') : '' }),
      entry.text
    ]);
    if (entry.detail) line.title = entry.detail;
    log.appendChild(line);
    while (log.childNodes.length > 800) log.removeChild(log.firstChild);
    log.scrollTop = log.scrollHeight;
  }

  // ---------------------------------------------------------------------
  // Drop-Zonen und Einfügen
  // ---------------------------------------------------------------------
  function setupDrop(zoneSel, inputSel, onFiles) {
    var zone = $(zoneSel);
    var input = $(inputSel);
    zone.addEventListener('click', function (e) { if (e.target.tagName !== 'BUTTON') input.click(); });
    zone.addEventListener('keydown', function (e) { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); input.click(); } });
    input.addEventListener('change', function () { onFiles(Array.prototype.slice.call(input.files)); input.value = ''; });
    zone.addEventListener('dragover', function (e) { e.preventDefault(); zone.classList.add('over'); });
    zone.addEventListener('dragleave', function () { zone.classList.remove('over'); });
    zone.addEventListener('drop', function (e) {
      e.preventDefault();
      zone.classList.remove('over');
      onFiles(Array.prototype.slice.call(e.dataTransfer.files));
    });
  }

  function imagesFrom(files) {
    var images = files.filter(function (f) { return /^image\//.test(f.type); });
    if (files.length && !images.length) toast('Bitte eine Bilddatei (PNG, JPG, WebP) wählen.', true);
    return Promise.all(images.map(readFileAsDataURL));
  }

  function renderThumbs(sel, list) {
    var box = $(sel);
    box.innerHTML = '';
    list.forEach(function (src, i) {
      box.appendChild(el('div', { class: 'thumb' }, [
        el('img', { src: src, alt: 'Bild ' + (i + 1) }),
        el('button', { type: 'button', title: 'Entfernen', text: '×', onclick: function () { list.splice(i, 1); renderThumbs(sel, list); } })
      ]));
    });
  }

  function setupPaste() {
    document.addEventListener('paste', function (e) {
      var items = (e.clipboardData && e.clipboardData.items) || [];
      var files = [];
      for (var i = 0; i < items.length; i++) {
        if (items[i].kind === 'file' && /^image\//.test(items[i].type)) files.push(items[i].getAsFile());
      }
      if (!files.length) return;
      e.preventDefault();
      imagesFrom(files).then(function (urls) {
        var tab = activeTab();
        if (tab === 'image') setImageSource(urls[0]);
        else if (tab === 'imagegen') { state.genImages = urls.slice(0, 1); renderThumbs('#gen-thumbs', state.genImages); }
        else if (!$('#ai-refine').hidden) { state.refineImages = state.refineImages.concat(urls); renderThumbs('#refine-thumbs', state.refineImages); }
        else { showTab('ai'); state.aiImages = state.aiImages.concat(urls); renderThumbs('#ai-thumbs', state.aiImages); }
        toast('Bild eingefügt.');
      });
    });
  }

  // ---------------------------------------------------------------------
  // KI-Konstrukteur
  // ---------------------------------------------------------------------
  var SUGGESTIONS = [
    'Wandstärken überall mindestens 2 mm',
    'Ohne Stützmaterial druckbar machen (Fasen statt Überhänge)',
    'In Teile aufteilen, die aufs Druckbett passen',
    'Verbindungen mit Gewinde statt Stecker',
    'Kabelkanal Ø 8 mm durchgehend vorsehen',
    'Proportionen näher am Referenzbild'
  ];

  function setupAI() {
    setupDrop('#ai-drop', '#ai-files', function (files) {
      imagesFrom(files).then(function (urls) { state.aiImages = state.aiImages.concat(urls).slice(0, 6); renderThumbs('#ai-thumbs', state.aiImages); });
    });
    setupDrop('#refine-drop', '#refine-files', function (files) {
      imagesFrom(files).then(function (urls) { state.refineImages = state.refineImages.concat(urls).slice(0, 4); renderThumbs('#refine-thumbs', state.refineImages); });
    });
    var sug = $('#refine-suggestions');
    SUGGESTIONS.forEach(function (text) {
      sug.appendChild(el('button', { type: 'button', text: text, onclick: function () {
        var ta = $('#refine-text');
        ta.value = ta.value ? ta.value.replace(/\s*$/, '') + '\n' + text : text;
        ta.focus();
      } }));
    });
    $('#btn-generate').addEventListener('click', generate);
    $('#btn-refine').addEventListener('click', refine);
    $('#btn-ai-new').addEventListener('click', newProject);
    $('#ai-instruction').addEventListener('keydown', function (e) { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) generate(); });
    $('#refine-text').addEventListener('keydown', function (e) { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) refine(); });
    $('#project-title').addEventListener('change', renameProject);
    $('#btn-new').addEventListener('click', newProject);
  }

  function generate() {
    if (busy()) return;
    var instruction = $('#ai-instruction').value.trim();
    if (!instruction && !state.aiImages.length) { toast('Bitte beschreibe das Objekt oder füge ein Bild hinzu.', true); return; }
    // Titel nur übernehmen, wenn gerade kein anderes Projekt geöffnet ist
    var title = state.project ? '' : $('#project-title').value.trim();
    var body = { instruction: instruction, images: state.aiImages, title: title };
    state.project = null;
    runJob(api('/api/ai/generate', body), function (job) {
      if (job.status === 'done') {
        state.aiImages = [];
        renderThumbs('#ai-thumbs', state.aiImages);
        $('#ai-instruction').value = '';
      }
    });
  }

  function refine() {
    if (busy() || !state.project) return;
    var change = $('#refine-text').value.trim();
    if (!change) { toast('Bitte beschreibe die gewünschte Änderung.', true); return; }
    runJob(api('/api/ai/refine', { project_id: state.project.id, change: change, images: state.refineImages }), function (job) {
      if (job.status === 'done') {
        $('#refine-text').value = '';
        state.refineImages = [];
        renderThumbs('#refine-thumbs', state.refineImages);
      }
    });
  }

  function renderChat() {
    var box = $('#chat');
    box.innerHTML = '';
    var p = state.project;
    if (!p || !p.chat) return;
    p.chat.forEach(function (m) {
      if (m.role === 'system') return;
      var children = [m.text];
      if (m.images && m.images.length) {
        children.push(el('div', { class: 'msg-images' }, m.images.map(function (name) {
          return el('img', { src: '/files/' + p.id + '/' + name, alt: '' });
        })));
      }
      var meta = el('span', { class: 'meta' }, [m.time || '']);
      if (m.version) {
        meta.appendChild(document.createTextNode(' · '));
        meta.appendChild(el('a', { text: 'Version ' + m.version, title: 'Diese Version wiederherstellen', onclick: function () { restoreVersion(m.version); } }));
      }
      children.push(meta);
      box.appendChild(el('div', { class: 'msg ' + m.role }, children));
    });
    box.scrollTop = box.scrollHeight;
  }

  // ---------------------------------------------------------------------
  // Bild → 3D
  // ---------------------------------------------------------------------
  function setupImageTab() {
    setupDrop('#img-drop', '#img-file', function (files) {
      imagesFrom(files).then(function (urls) { if (urls[0]) setImageSource(urls[0]); });
    });
    renderModes();
    renderParams();
    $('#btn-convert').addEventListener('click', convertImage);
  }

  function setImageSource(src, fromProject) {
    var img = $('#img-source');
    if (/^\/files\//.test(src)) {
      // Bild aus einem Projekt: für Vorschau und Umwandlung als dataURL laden
      fetch(src).then(function (r) { return r.blob(); }).then(function (blob) {
        return readFileAsDataURL(blob);
      }).then(function (url) { setImageSource(url, fromProject); });
      return;
    }
    state.imgSource = src;
    state.imgFromProject = !!fromProject;
    img.src = src;
    img.hidden = false;
    $('#img-drop-text').hidden = true;
    requestPreview();
  }

  function renderModes() {
    var box = $('#img-modes');
    box.innerHTML = '';
    Object.keys(MODES).forEach(function (key) {
      var btn = el('button', { type: 'button', class: 'mode' + (key === state.imgMode ? ' active' : ''), title: MODES[key].help,
        html: ICONS[key] + '<span>' + MODES[key].label + '</span>',
        onclick: function () {
          var wasRelief = isRelief(state.imgMode);
          state.imgMode = key;
          if (wasRelief !== isRelief(key)) state.imgValues = {};
          renderModes();
          renderParams(true);
          requestPreview();
        } });
      box.appendChild(btn);
    });
    $('#img-mode-help').textContent = MODES[state.imgMode].help;
  }

  function isRelief(mode) { return mode === 'relief' || mode === 'lithophane'; }

  function renderParams(applyDefaults) {
    var box = $('#img-params');
    box.innerHTML = '';
    var specs = modeSpecs(state.imgMode);
    if (applyDefaults) {
      var defs = MODES[state.imgMode].defaults || {};
      Object.keys(defs).forEach(function (k) { state.imgValues[k] = defs[k]; });
    }
    var basic = el('div', { class: 'form full' });
    basic.style.gridColumn = '1 / -1';
    var adv = el('div', { class: 'form' });
    specs.forEach(function (spec) {
      if (state.imgValues[spec.key] === undefined) state.imgValues[spec.key] = spec.def;
      var field = paramField(spec);
      (spec.adv ? adv : basic).appendChild(field);
    });
    box.appendChild(basic);
    if (adv.childNodes.length) {
      box.appendChild(el('details', {}, [el('summary', { text: 'Erweitert' }), adv]));
    }
    syncThreshold();
  }

  function paramField(spec) {
    var value = state.imgValues[spec.key];
    var onChange = function (v) {
      state.imgValues[spec.key] = v;
      syncThreshold();
      requestPreview();
    };
    if (spec.type === 'check') {
      var cb = el('input', { type: 'checkbox', onchange: function () { onChange(cb.checked); } });
      cb.checked = !!value;
      return el('label', { class: 'check full', 'data-key': spec.key }, [cb, spec.label]);
    }
    if (spec.type === 'select') {
      var sel = el('select', { onchange: function () { onChange(sel.value); } }, spec.options.map(function (o) {
        return el('option', { value: o[0], text: o[1] });
      }));
      sel.value = value;
      return el('label', { class: 'label', 'data-key': spec.key }, [spec.label, sel]);
    }
    var out = el('output', { text: String(value) });
    var range = el('input', { type: 'range', min: spec.min, max: spec.max, step: spec.step, value: value,
      oninput: function () { out.textContent = range.value; },
      onchange: function () { onChange(Number(range.value)); } });
    return el('label', { class: 'label', 'data-key': spec.key }, [spec.label, el('div', { class: 'range-row' }, [range, out])]);
  }

  function syncThreshold() {
    var row = $('#img-params [data-key="threshold"]');
    if (row) row.hidden = !!state.imgValues.threshold_auto;
  }

  function imageParams() {
    var params = {};
    modeSpecs(state.imgMode).forEach(function (spec) { params[spec.key] = state.imgValues[spec.key]; });
    if (!isRelief(state.imgMode)) {
      if (params.threshold_auto) params.threshold = null;
      delete params.threshold_auto;
    }
    return params;
  }

  var requestPreview = debounce(function () {
    if (!state.imgSource) return;
    api('/api/image/preview', { image: state.imgSource, mode: state.imgMode, params: imageParams() }).then(function (res) {
      $('#img-preview-box').hidden = false;
      $('#img-preview').src = res.preview;
      var info = res.width + ' × ' + res.height + ' Punkte';
      if (res.contours !== undefined) info += ' · ' + res.contours + ' Konturen · ' + fmt(res.points) + ' Eckpunkte';
      $('#img-preview-info').textContent = info;
    }).catch(function (err) { $('#img-preview-info').textContent = err.message; });
  }, 350);

  function convertImage() {
    if (busy()) return;
    if (!state.imgSource) { toast('Bitte zuerst ein Bild wählen.', true); return; }
    var reuse = state.project && state.project.kind === 'image' && state.imgFromProject;
    var title = (reuse || !state.project ? $('#project-title').value.trim() : '') || (MODES[state.imgMode].label + ' aus Bild');
    var body = { image: state.imgSource, mode: state.imgMode, params: imageParams(), title: title };
    // Gleiches Bild im selben Bild-Projekt → Projekt weiterverwenden
    if (state.project && state.project.kind === 'image' && state.imgFromProject) {
      body.project_id = state.project.id;
      delete body.image;
    } else {
      state.project = null;
    }
    runJob(api('/api/image/convert', body));
  }

  // ---------------------------------------------------------------------
  // Bilder mit Gemini erzeugen
  // ---------------------------------------------------------------------
  function setupImageGen() {
    setupDrop('#gen-drop', '#gen-file', function (files) {
      imagesFrom(files).then(function (urls) { state.genImages = urls.slice(0, 1); renderThumbs('#gen-thumbs', state.genImages); });
    });
    $('#btn-imagegen').addEventListener('click', function () {
      if (busy()) return;
      var body = { prompt: $('#gen-prompt').value, preset: $('#gen-preset').value, images: state.genImages,
        aspect: $('#gen-aspect').value };
      runJob(api('/api/image/generate', body), function (job) {
        if (job.status !== 'done' || !job.result) return;
        $('#gen-result').hidden = false;
        $('#gen-image').src = job.result.image;
        $('#gen-download').href = job.result.image;
      });
    });
    $('#gen-to-image').addEventListener('click', function () {
      setImageSource($('#gen-image').src);
      var preset = $('#gen-preset').value;
      if (preset === 'tiefenkarte' || preset === 'bild_zu_tiefenkarte') state.imgMode = 'relief';
      else if (preset === 'silhouette') state.imgMode = 'keychain';
      else if (preset === 'lineart') state.imgMode = 'plate';
      state.imgValues = {};
      renderModes();
      renderParams(true);
      state.project = null;
      showTab('image');
    });
    $('#gen-to-ai').addEventListener('click', function () {
      newProject();
      state.aiImages = [$('#gen-image').src];
      renderThumbs('#ai-thumbs', state.aiImages);
      showTab('ai');
      $('#ai-instruction').focus();
    });
  }

  function fillPresets() {
    var sel = $('#gen-preset');
    if (sel.options.length) return;
    var presets = state.status.image_presets;
    Object.keys(presets).forEach(function (key) {
      sel.appendChild(el('option', { value: key, text: presets[key].label }));
    });
  }

  // ---------------------------------------------------------------------
  // Code-Editor
  // ---------------------------------------------------------------------
  function setupCode() {
    var ta = $('#code');
    ta.addEventListener('input', updateGutter);
    ta.addEventListener('scroll', function () { $('#code-gutter').scrollTop = ta.scrollTop; });
    ta.addEventListener('keydown', function (e) {
      if (e.key === 'Tab') {
        e.preventDefault();
        var s = ta.selectionStart;
        ta.setRangeText('  ', s, ta.selectionEnd, 'end');
        updateGutter();
      } else if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
        e.preventDefault();
        renderCode();
      }
    });
    $('#btn-render').addEventListener('click', renderCode);
    $('#code-versions').addEventListener('change', function (e) {
      var v = e.target.value;
      e.target.value = '';
      if (v) restoreVersion(Number(v));
    });
  }

  function setCode(code) {
    $('#code').value = code;
    updateGutter();
  }

  function updateGutter() {
    var lines = $('#code').value.split('\n').length;
    var text = '';
    for (var i = 1; i <= lines; i++) text += i + '\n';
    $('#code-gutter').textContent = text;
    $('#code-gutter').scrollTop = $('#code').scrollTop;
  }

  function renderCode() {
    if (busy()) return;
    var code = $('#code').value;
    if (!code.trim()) { toast('Der Code ist leer.', true); return; }
    var body = { code: code, title: $('#project-title').value.trim() || 'Eigener Code' };
    if (state.project && state.project.id) body.project_id = state.project.id;
    runJob(api('/api/render', body));
  }

  function renderVersions() {
    var sel = $('#code-versions');
    sel.innerHTML = '';
    sel.appendChild(el('option', { value: '', text: 'Versionen …' }));
    var versions = (state.project && state.project.versions) || [];
    versions.slice().reverse().forEach(function (v) {
      sel.appendChild(el('option', { value: v.n, text: 'v' + v.n + ' · ' + (v.note || '') + ' · ' + (v.time || '').slice(11, 16) }));
    });
  }

  function restoreVersion(n) {
    if (!state.project || busy()) return;
    api('/api/projects/' + state.project.id + '/restore', { version: n }).then(function (project) {
      setProject(project, false);
      toast('Version ' + n + ' wiederhergestellt – zum Anzeigen „Rendern & prüfen“.');
      showTab('code');
    }).catch(function (err) { toast(err.message, true); });
  }

  // ---------------------------------------------------------------------
  // Prüfbericht
  // ---------------------------------------------------------------------
  function setupReport() {
    $('#btn-open-openscad').addEventListener('click', function () {
      if (!state.project) return;
      api('/api/projects/' + state.project.id + '/open', {}).then(function () {
        toast('OpenSCAD wird geöffnet …');
      }).catch(function (err) { toast(err.message, true); });
    });
    $('#btn-open-folder').addEventListener('click', function () {
      if (!state.project) return;
      api('/api/projects/' + state.project.id + '/folder', {}).catch(function (err) { toast(err.message, true); });
    });
  }

  function issueList(issues) {
    return el('ul', { class: 'issues' }, issues.map(function (i) {
      return el('li', { class: i.level || 'info', text: i.text });
    }));
  }

  function checkFlags(check) {
    var flags = [];
    if (!check) return flags;
    flags.push(el('span', { class: 'flag ' + (check.watertight ? 'ok' : 'err'), text: check.watertight ? 'Netz geschlossen' : 'Netz offen' }));
    if (check.bed) {
      flags.push(el('span', { class: 'flag ' + (check.bed.fits ? 'ok' : (check.bed.fits_rotated ? 'warn' : 'err')),
        text: check.bed.fits ? 'passt aufs Bett' : (check.bed.fits_rotated ? 'passt gedreht' : 'zu groß fürs Bett') }));
    }
    if (check.walls && check.walls.min !== null && check.walls.min !== undefined) {
      var min = check.walls.min;
      var pr = state.status ? state.status.printer : { min_wall: 0.8 };
      flags.push(el('span', { class: 'flag ' + (min >= pr.min_wall ? 'ok' : 'warn'), text: 'Wand ≥ ' + fmt(min, 2) + ' mm' }));
    }
    if (check.shells > 1) flags.push(el('span', { class: 'flag', text: check.shells + ' Körper' }));
    return flags;
  }

  function renderReport() {
    var p = state.project;
    var summary = $('#report-summary');
    var modelBox = $('#report-model');
    var partsBox = $('#report-parts');
    var issuesBox = $('#report-issues');
    modelBox.innerHTML = '';
    partsBox.innerHTML = '';
    issuesBox.innerHTML = '';
    $('#downloads').hidden = !p || !p.id;
    if (!p || !p.render) {
      summary.className = 'report-summary muted';
      summary.textContent = 'Noch kein Modell gerendert.';
      if (p && p.id) setDownloads(p);
      return;
    }
    setDownloads(p);
    var render = p.render;
    var problems = p.problems || [];
    if (!render.ok) {
      summary.className = 'report-summary err';
      summary.textContent = '✗ OpenSCAD-Fehler – das Modell konnte nicht gerendert werden.';
      issuesBox.appendChild(el('h4', { text: 'Fehler' }));
      issuesBox.appendChild(issueList((render.errors.length ? render.errors : problems).map(function (t) { return { level: 'error', text: t }; })));
      return;
    }
    var hasErr = problems.length > 0;
    summary.className = 'report-summary ' + (hasErr ? 'warn' : 'ok');
    summary.textContent = hasErr ? '⚠ ' + problems.length + ' Hinweis(e) – bitte prüfen.' : '✓ Druckbereit: Netz geschlossen, Teile passen aufs Druckbett.';

    var c = p.check;
    var multiPart = (p.parts || []).some(function (part) { return !part.unused; });
    if (c) {
      modelBox.appendChild(el('h4', { text: multiPart ? 'Zusammenbau (nur Ansicht)' : 'Gesamtmodell' }));
      var kv = el('dl', { class: 'kv' });
      [['Maße', sizeText(c.size)], ['Volumen', fmt(c.volume_cm3, 1) + ' cm³'],
       ['Dreiecke', fmt(c.triangles)], ['Körper', c.shells], ['Renderzeit', fmt(render.seconds, 1) + ' s']].forEach(function (row) {
        kv.appendChild(el('dt', { text: row[0] }));
        kv.appendChild(el('dd', { text: String(row[1]) }));
      });
      modelBox.appendChild(kv);
      if (multiPart) {
        // Im Zusammenbau berühren sich Teile gewollt – maßgeblich sind die Druckteile
        modelBox.appendChild(el('p', { class: 'hint', text: 'Gedruckt werden die einzelnen Druckteile unten – sie werden jeweils einzeln geprüft.' }));
      } else {
        modelBox.appendChild(el('div', { class: 'part-flags' }, checkFlags(c)));
      }
      var card = el('div', { class: 'part' + (state.viewing && state.viewing.kind === 'model' ? ' active' : ''), 'data-part': '__model',
        onclick: function () { showInViewer('/files/' + p.id + '/model.stl', 'Gesamtmodell', { kind: 'model' }); } },
        [el('div', { class: 'part-head' }, [el('span', { class: 'part-name', text: 'Zusammenbau ansehen' })])]);
      modelBox.appendChild(card);
    }

    var parts = p.parts || [];
    if (parts.length) {
      partsBox.appendChild(el('h4', { text: 'Druckteile (' + parts.length + ')' }));
      var unused = parts.filter(function (part) { return part.unused; });
      parts = parts.filter(function (part) { return !part.unused; });
      partsBox.querySelector('h4').textContent = 'Druckteile (' + parts.length + ')';
      parts.forEach(function (part) {
        var pc = part.check;
        var children = [
          el('div', { class: 'part-head' }, [
            el('span', { class: 'part-name', text: part.label }),
            el('span', { class: 'part-size', text: pc ? sizeText(pc.size) : (part.ok ? '' : 'Fehler') })
          ]),
          el('div', { class: 'part-flags' }, pc ? checkFlags(pc) : [el('span', { class: 'flag err', text: 'Export fehlgeschlagen' })])
        ];
        if (pc && pc.issues && pc.issues.length) {
          children.push(issueList(pc.issues.filter(function (i) { return i.level !== 'info'; })));
        }
        if (part.ok) {
          children.push(el('a', { class: 'btn small', href: '/files/' + p.id + '/' + part.stl + '?download=1', download: part.id + '.stl',
            text: 'STL laden', onclick: function (e) { e.stopPropagation(); } }));
        }
        partsBox.appendChild(el('div', { class: 'part', 'data-part': part.id, onclick: function () {
          if (part.ok) showInViewer('/files/' + p.id + '/' + part.stl, 'Teil: ' + part.label, { kind: 'part', id: part.id });
        } }, children));
      });
      if (unused.length) {
        partsBox.appendChild(el('p', { class: 'hint', text: 'Nicht verwendet in dieser Variante: ' +
          unused.map(function (u) { return u.label; }).join(', ') }));
      }
    }

    renderFit(p);

    var issues = [];
    if (c && c.issues && !multiPart) issues = issues.concat(c.issues);
    (render.warnings || []).slice(0, 10).forEach(function (w) { issues.push({ level: 'warning', text: w }); });
    if (issues.length) {
      issuesBox.appendChild(el('h4', { text: multiPart ? 'OpenSCAD-Hinweise' : 'Hinweise' }));
      issuesBox.appendChild(issueList(issues));
    }
    highlightPart();
  }

  function renderFit(p) {
    var box = $('#report-fit');
    box.innerHTML = '';
    if (!p.parts || !p.parts.length) return;
    box.appendChild(el('h4', { text: 'Passungen & Kollisionen' }));
    var fit = p.collisions;
    var usable = /module\s+placed\s*\(/.test(p.code || '');
    if (!usable) {
      box.appendChild(el('p', { class: 'hint', text: 'Für die Passungsprüfung braucht das Modell ein Modul placed(id) (KI-Modelle haben es automatisch).' }));
      return;
    }
    if (fit) {
      var bad = fit.collisions || [];
      var open = (fit.incomplete || []).concat((fit.floating || []).map(function (l) { return l + ': berührt kein anderes Teil (schwebt?)'; }));
      var cls = bad.length ? 'err' : (open.length ? 'warn' : 'ok');
      var txt = bad.length ? ('✗ ' + bad.length + ' Kollision(en) im Zusammenbau')
        : open.length ? ('⚠ Unvollständig: ' + open.length + ' Teil(e)/Paar(e) nicht prüfbar')
        : ('✓ ' + fit.pairs.length + ' Teilepaare geprüft – nichts steckt ineinander');
      box.appendChild(el('div', { class: 'report-summary ' + cls, text: txt }));
      if (open.length) box.appendChild(issueList(open.map(function (t) { return { level: 'warning', text: t }; })));
      var regionText = function (r) {
        if (!r) return '';
        return ' bei ' + ['x', 'y', 'z'].map(function (a, i) { return a + ' ' + fmt(r.min[i], 1) + '…' + fmt(r.max[i], 1); }).join(', ') + ' mm';
      };
      var items = fit.pairs.map(function (pair) {
        var level = pair.ok === false ? 'error' : (pair.ok ? 'info' : 'warning');
        var text = pair.label + ': ' + (pair.ok === false ? ('Überschneidung ' + fmt(pair.volume_mm3, 1) + ' mm³' + regionText(pair.region))
          : (pair.ok ? 'frei' : (pair.error || 'nicht prüfbar')));
        return { level: level, text: text };
      });
      box.appendChild(issueList(items));
    }
    box.appendChild(el('button', { class: 'btn small', style: 'margin-top:6px', text: fit ? 'Erneut prüfen' : 'Passungen prüfen',
      title: 'Setzt alle Teile zusammen und prüft paarweise auf Überschneidungen (Gewinde, Stecker, Schrauben).',
      onclick: function () {
        if (busy()) return;
        runJob(api('/api/projects/' + p.id + '/collisions', {}));
      } }));
  }

  function highlightPart() {
    var v = state.viewing;
    $$('#report-parts .part, #report-model .part').forEach(function (node) {
      var id = node.getAttribute('data-part');
      node.classList.toggle('active', !!v && ((v.kind === 'model' && id === '__model') || (v.kind === 'part' && id === v.id)));
    });
  }

  function setDownloads(p) {
    $('#dl-stl').href = '/files/' + p.id + '/model.stl?download=1';
    $('#dl-stl').setAttribute('download', p.id + '.stl');
    $('#dl-scad').href = '/files/' + p.id + '/model.scad?download=1';
    $('#dl-scad').setAttribute('download', p.id + '.scad');
    $('#dl-zip').href = '/zip/' + p.id;
    $('#dl-stl').hidden = !p.has_stl;
  }

  // ---------------------------------------------------------------------
  // Dialoge: Projekte & Einstellungen
  // ---------------------------------------------------------------------
  function setupDialogs() {
    $$('dialog [data-close]').forEach(function (b) {
      b.addEventListener('click', function () { b.closest('dialog').close(); });
    });
    $('#btn-projects').addEventListener('click', openProjects);
    $('#btn-settings').addEventListener('click', openSettings);
    $('#settings-form').addEventListener('submit', function (e) { e.preventDefault(); saveSettings(); });
    $('#printer-select').addEventListener('change', updateCustomBed);
    $('#btn-gemini-models').addEventListener('click', function () {
      var key = $('#settings-form [name="gemini_api_key"]').value.trim();
      api('/api/models/gemini', { api_key: key }).then(function (res) {
        fillDatalist('#dl-gemini', res.models);
        fillDatalist('#dl-gemini-image', res.image_models);
        toast(res.models.length + ' Text- und ' + res.image_models.length + ' Bildmodelle gefunden – im Feld auswählen.');
      }).catch(function (err) { toast(err.message, true); });
    });
  }

  function openProjects() {
    var grid = $('#projects-grid');
    grid.innerHTML = '<p class="hint">Lade …</p>';
    $('#dlg-projects').showModal();
    api('/api/projects').then(function (res) {
      grid.innerHTML = '';
      if (!res.projects.length) grid.appendChild(el('p', { class: 'hint', text: 'Noch keine Projekte.' }));
      res.projects.forEach(function (p) {
        var thumb = el('div', { class: 'pc-thumb' });
        if (p.thumb) thumb.style.backgroundImage = 'url("/files/' + p.id + '/' + p.thumb + '")';
        var del = el('button', { class: 'btn ghost small', title: 'Löschen', text: '🗑', onclick: function (e) {
          e.stopPropagation();
          if (!confirm('Projekt „' + p.title + '“ wirklich löschen?')) return;
          api('/api/projects/' + p.id + '/delete', {}).then(function () {
            if (state.project && state.project.id === p.id) newProject();
            openProjects();
          }).catch(function (err) { toast(err.message, true); });
        } });
        grid.appendChild(el('div', { class: 'project-card', onclick: function () {
          $('#dlg-projects').close();
          openProject(p.id);
        } }, [thumb, el('div', { class: 'pc-body' }, [
          el('div', {}, [el('div', { class: 'pc-title', text: p.title }), el('div', { class: 'pc-date', text: (p.updated || p.created || '').slice(0, 16) })]),
          del
        ])]));
      });
    }).catch(function (err) { grid.textContent = err.message; });
    var ex = $('#examples-grid');
    ex.innerHTML = '';
    api('/api/examples').then(function (res) {
      res.examples.forEach(function (e) {
        ex.appendChild(el('div', { class: 'project-card example', title: e.description, onclick: function () {
          if (busy()) return;
          $('#dlg-projects').close();
          state.project = null;
          showTab('code');
          runJob(api('/api/examples/' + e.id + '/open', {}));
        } }, [el('div', { class: 'pc-thumb', text: '◆' }), el('div', { class: 'pc-body' }, [
          el('div', {}, [el('div', { class: 'pc-title', text: e.title }), el('div', { class: 'pc-date', text: e.description })])
        ])]));
      });
    }).catch(function () { /* keine Beispiele */ });
  }

  function openSettings() {
    fillSettings();
    $('#dlg-settings').showModal();
  }

  function fillSettings() {
    var s = state.status;
    if (!s) return;
    var form = $('#settings-form');
    var st = s.settings;
    // Anbieter
    var radios = $('#provider-radios');
    radios.innerHTML = '';
    Object.keys(s.provider_labels).forEach(function (key) {
      var input = el('input', { type: 'radio', name: 'provider', value: key, onchange: showProviderBox });
      input.checked = st.provider === key;
      var ready = s.providers[key] ? ' ✓' : '';
      radios.appendChild(el('label', {}, [input, s.provider_labels[key] + ready]));
    });
    showProviderBox();
    // Druckerliste
    var sel = $('#printer-select');
    if (!sel.options.length) {
      s.printers.forEach(function (p) {
        sel.appendChild(el('option', { value: p.id, text: p.name + (p.id === 'custom' ? '' : ' (' + p.bed.join('×') + ')') }));
      });
    }
    // Vorschläge für Modellnamen
    fillDatalist('#dl-gemini', s.models.gemini);
    fillDatalist('#dl-gemini-image', s.models.gemini_image);
    fillDatalist('#dl-anthropic', s.models.anthropic);
    fillDatalist('#dl-openai', s.models.openai);
    // Werte
    $$('input, select', form).forEach(function (input) {
      var name = input.name;
      if (!name || name === 'provider') return;
      if (/_api_key$/.test(name)) {
        input.value = '';
        input.placeholder = st[name + '_hint'] ? ('gespeichert ' + st[name + '_hint'] + ' – leer lassen = behalten') : 'nicht gesetzt';
        return;
      }
      if (input.type === 'checkbox') input.checked = !!st[name];
      else if (st[name] !== undefined && st[name] !== null) input.value = st[name];
    });
    updateCustomBed();
    var o = s.openscad;
    $('#openscad-info').textContent = o.found ? ('Gefunden: ' + o.path + ' (Version ' + o.version + (o.manifold ? ', Manifold verfügbar' : ', ohne Manifold – für schnellere Renderings eine aktuelle Entwicklerversion installieren') + ')') : (o.error || 'Nicht gefunden.');
  }

  function fillDatalist(sel, values) {
    var dl = $(sel);
    dl.innerHTML = '';
    (values || []).forEach(function (v) { dl.appendChild(el('option', { value: v })); });
  }

  function showProviderBox() {
    var checked = $('#provider-radios input:checked');
    var key = checked ? checked.value : 'gemini';
    $$('.provider-box').forEach(function (box) { box.classList.toggle('active', box.getAttribute('data-provider') === key); });
  }

  function updateCustomBed() {
    $('#custom-bed').hidden = $('#printer-select').value !== 'custom';
  }

  function saveSettings() {
    var form = $('#settings-form');
    var data = {};
    $$('input, select', form).forEach(function (input) {
      var name = input.name;
      if (!name) return;
      if (input.type === 'radio') { if (input.checked) data[name] = input.value; return; }
      if (input.type === 'checkbox') { data[name] = input.checked; return; }
      data[name] = input.value;
    });
    api('/api/settings', data).then(function (status) {
      state.status = status;
      renderChips();
      renderProviderHint();
      updateBed();
      $('#dlg-settings').close();
      toast('Einstellungen gespeichert.');
    }).catch(function (err) { toast(err.message, true); });
  }

  document.addEventListener('DOMContentLoaded', init);
})();
