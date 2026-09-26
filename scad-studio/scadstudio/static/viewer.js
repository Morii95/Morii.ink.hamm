/* ==========================================================================
   SCAD STUDIO — viewer.js
   Abhängigkeitsfreier STL-Viewer auf WebGL1-Basis. Läuft komplett offline,
   ohne Build-Schritt, ohne Module und ohne externe Bibliotheken.

   Verwendung:
     const viewer = new STLViewer(container, {
       background: '#101014', modelColor: '#c9a86a', gridColor: '#2a2a33',
       showGrid: true, bed: [220, 220, 250], showDimensions: true,
     });
     const info = await viewer.loadUrl('/render/model.stl');
     viewer.setView('top');

   Optionen (alle optional):
     background      CSS-Farbe oder 'transparent'
     modelColor      Farbe des Modells
     gridColor       Farbe des Rasters (Druckbett)
     bedColor        Akzentfarbe für Bett-Umriss und Schnittebene
     showGrid        Raster anzeigen (Standard true)
     showAxes        Achsenkreuz unten links (Standard true)
     showDimensions  Bounding-Box mit Maßen X/Y/Z (Standard true)
     bed             Druckbett [x, y, z] in mm oder null (Standard [220, 220, 250])

   Methoden:
     loadUrl(url, {keepView})   Promise → getInfo(); null, wenn von neuerem Laden
                                überholt; Fehler mit deutscher Meldung.
                                keepView: Kamera behalten (z. B. beim Neu-Rendern)
     loadArrayBuffer(buf, opts) wie oben, synchron (wirft bei Fehlern)
     clear()                    Modell entfernen
     resetView()                Standardansicht (iso), Modell eingerahmt
     setView(name, {animate})   'iso' | 'top' | 'front' | 'side' ('right'),
                                außerdem 'back' | 'left' | 'bottom'
     setWireframe(bool)         Dreieckskanten einblenden
     setXray(bool)              halbtransparente Röntgenansicht
     setClipZ(z | null)         alles oberhalb von z (mm) ausblenden, Schnitt schraffiert
     setModelColor(css), setBackground(css), setShowGrid(bool), setShowDimensions(bool)
     setBed([x, y, z] | null)   Druckbett; rot, wenn das Modell nicht passt
     fitsBed()                  true/false, null ohne Modell oder Bett
     screenshot()               PNG-Daten-URL der aktuellen Ansicht (ohne HTML-Labels)
     getInfo()                  {triangles, size, min, max, volume (mm³), format} oder null
     destroy()                  alles freigeben und entfernen
     STLViewer.parse(buffer)    reiner Parser ohne Anzeige

   Bedienung: Linke Maustaste drehen · rechte/mittlere Taste oder Shift ziehen
   verschieben · Mausrad zoomen (zum Cursor) · Doppelklick Ansicht zurücksetzen ·
   Touch: ein Finger drehen, zwei Finger zoomen/verschieben, Doppeltippen zurücksetzen.

   Koordinaten wie in OpenSCAD: Z zeigt nach oben, Einheiten in Millimetern.
   Das Druckbett (Raster) liegt in der XY-Ebene auf Höhe der Modell-Unterkante,
   das Modell steht also immer auf dem Raster.
   ========================================================================== */

(function (global) {
  'use strict';

  // ------------------------------------------------------------------------
  // Konstanten
  // ------------------------------------------------------------------------

  const DEG = Math.PI / 180;
  const FOV_Y = 35 * DEG;          // vertikaler Öffnungswinkel der Kamera
  const MAX_DPR = 2;               // Pixeldichte deckeln (Leistung auf HiDPI)
  const SHADOW_SIZE = 256;         // Auflösung der Kontaktschatten-Textur
  const ANIM_MS = 420;             // Dauer der Kamerafahrten
  const LOADING_DELAY_MS = 120;    // Ladehinweis erst verzögert zeigen (kein Flackern)
  const ORBIT_SPEED = 0.008;       // Bogenmaß pro Pixel beim Drehen

  const AXIS_COLORS = ['#e5484d', '#46a758', '#3e7bfa']; // X, Y, Z
  const AXIS_NAMES = ['X', 'Y', 'Z'];
  const BED_OVER_COLOR = '#ff4d4f';

  // Blickrichtungen: theta = Azimut um Z (0 = von +X), phi = Elevation
  const VIEWS = {
    iso:    { theta: -58 * DEG, phi: 27 * DEG },
    top:    { theta: -90 * DEG, phi: 90 * DEG },
    bottom: { theta: -90 * DEG, phi: -90 * DEG },
    front:  { theta: -90 * DEG, phi: 0 },
    back:   { theta: 90 * DEG, phi: 0 },
    side:   { theta: 0, phi: 0 },
    right:  { theta: 0, phi: 0 },
    left:   { theta: 180 * DEG, phi: 0 },
  };

  const DEFAULTS = {
    background: '#101014',
    modelColor: '#c9a86a',
    gridColor: '#2a2a33',
    bedColor: '#5b8def',
    showGrid: true,
    showAxes: true,          // kleines Achsenkreuz unten links
    showDimensions: true,    // Bounding-Box mit Maßangaben
    bed: [220, 220, 250],    // Druckbett in mm (Anycubic Kobra 2 Neo), null = aus
  };

  // ------------------------------------------------------------------------
  // STL-Parser
  // ------------------------------------------------------------------------

  function emptyModelError() {
    return new Error('Das Modell ist leer – die STL-Datei enthält keine Dreiecke.');
  }

  function toBytes(input) {
    if (input instanceof ArrayBuffer) return new Uint8Array(input);
    if (ArrayBuffer.isView(input)) {
      return new Uint8Array(input.buffer, input.byteOffset, input.byteLength);
    }
    throw new TypeError('STL-Daten müssen als ArrayBuffer übergeben werden.');
  }

  function isSpace(b) {
    return b === 32 || b === 9 || b === 10 || b === 13 || b === 11 || b === 12;
  }

  // Beginnt die Datei (nach Leerraum / UTF-8-BOM) mit "solid"?
  function startsWithSolid(bytes) {
    let i = 0;
    if (bytes[0] === 0xef && bytes[1] === 0xbb && bytes[2] === 0xbf) i = 3;
    while (i < bytes.length && isSpace(bytes[i])) i++;
    return bytes[i] === 115 && bytes[i + 1] === 111 && bytes[i + 2] === 108 &&
      bytes[i + 3] === 105 && bytes[i + 4] === 100; // "solid"
  }

  // Text enthält keine Steuerzeichen; Binärdaten (Floats, Dreieckszahl) fast immer.
  function looksLikeText(bytes) {
    const end = Math.min(bytes.length, 1024);
    for (let i = 0; i < end; i++) {
      const b = bytes[i];
      if (b < 9 || (b > 13 && b < 32) || b === 127) return false;
    }
    return true;
  }

  // Robuste Erkennung: Auch Binärdateien beginnen manchmal mit "solid",
  // deshalb zählt vor allem die Größenformel 84 + 50 · n.
  function detectFormat(bytes) {
    const len = bytes.length;
    const text = looksLikeText(bytes);
    let count = -1;
    let expected = -1;
    if (len >= 84) {
      count = new DataView(bytes.buffer, bytes.byteOffset, len).getUint32(80, true);
      expected = 84 + count * 50;
      if (expected === len && !text) return 'binary';
    }
    if (text && startsWithSolid(bytes)) return 'ascii';
    if (count > 0 && expected <= len && !text) return 'binary'; // mit angehängten Bytes
    if (expected === len) return 'binary';
    if (text) return 'ascii'; // ASCII ohne korrekten Kopf – der Parser entscheidet
    return null;
  }

  function readBinary(bytes) {
    const dv = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
    const declared = dv.getUint32(80, true);
    const n = Math.min(declared, Math.floor((bytes.length - 84) / 50));
    const out = new Float32Array(n * 9);
    let o = 84 + 12; // gespeicherte Normale überspringen (oft ohnehin 0)
    let k = 0;
    for (let i = 0; i < n; i++, o += 50) {
      out[k++] = dv.getFloat32(o, true);
      out[k++] = dv.getFloat32(o + 4, true);
      out[k++] = dv.getFloat32(o + 8, true);
      out[k++] = dv.getFloat32(o + 12, true);
      out[k++] = dv.getFloat32(o + 16, true);
      out[k++] = dv.getFloat32(o + 20, true);
      out[k++] = dv.getFloat32(o + 24, true);
      out[k++] = dv.getFloat32(o + 28, true);
      out[k++] = dv.getFloat32(o + 32, true);
    }
    return out;
  }

  const POW10 = [];
  for (let i = 0; i <= 22; i++) POW10.push(Math.pow(10, i));

  // Endposition der zuletzt gelesenen Zahl (vermeidet Objekt-Allokationen)
  let numberEnd = 0;

  // Liest eine Dezimalzahl (auch wissenschaftliche Notation) direkt aus Bytes.
  function readNumber(b, p, len) {
    while (p < len && isSpace(b[p])) p++;
    let sign = 1;
    if (b[p] === 45) { sign = -1; p++; } else if (b[p] === 43) { p++; }
    let mant = 0;
    let digits = 0;
    let exp = 0;
    let c;
    while (p < len && (c = b[p] - 48) >= 0 && c <= 9) { mant = mant * 10 + c; p++; digits++; }
    if (b[p] === 46) {
      p++;
      while (p < len && (c = b[p] - 48) >= 0 && c <= 9) { mant = mant * 10 + c; exp--; p++; digits++; }
    }
    if (digits === 0) { numberEnd = p; return NaN; }
    if (b[p] === 101 || b[p] === 69) { // e / E
      let q = p + 1;
      let es = 1;
      let ev = 0;
      let ed = 0;
      if (b[q] === 45) { es = -1; q++; } else if (b[q] === 43) { q++; }
      while (q < len && (c = b[q] - 48) >= 0 && c <= 9) { ev = ev * 10 + c; q++; ed++; }
      if (ed > 0) { exp += es * ev; p = q; }
    }
    numberEnd = p;
    if (exp < 0) mant = -exp <= 22 ? mant / POW10[-exp] : mant / Math.pow(10, -exp);
    else if (exp > 0) mant = exp <= 22 ? mant * POW10[exp] : mant * Math.pow(10, exp);
    return sign * mant;
  }

  // ASCII-Parser direkt auf den Bytes: kein riesiger String, auch 70-MB-Dateien
  // gehen schnell. Sucht "vertex x y z" und ignoriert alles andere (CRLF egal).
  function readAscii(bytes) {
    const len = bytes.length;
    let out = new Float32Array(Math.max(900, Math.ceil(len / 160) * 9));
    let n = 0;
    let i = 0;
    while (i < len) {
      const k = bytes.indexOf(118, i); // 'v'
      if (k < 0 || k + 6 > len) break;
      i = k + 1;
      if (bytes[k + 1] !== 101 || bytes[k + 2] !== 114 || bytes[k + 3] !== 116 ||
          bytes[k + 4] !== 101 || bytes[k + 5] !== 120) continue; // "ertex"
      if ((k > 0 && !isSpace(bytes[k - 1])) || !isSpace(bytes[k + 6])) continue;
      const x = readNumber(bytes, k + 6, len);
      const y = readNumber(bytes, numberEnd, len);
      const z = readNumber(bytes, numberEnd, len);
      if (x !== x || y !== y || z !== z) continue; // NaN → keine gültige Zeile
      if (n + 3 > out.length) {
        const grown = new Float32Array(out.length * 2);
        grown.set(out);
        out = grown;
      }
      out[n++] = x;
      out[n++] = y;
      out[n++] = z;
      i = numberEnd;
    }
    return out.slice(0, n - (n % 9));
  }

  // Bounding-Box, Volumen und Aufräumen (Dreiecke mit NaN/Infinity entfernen).
  function analyzeMesh(pos) {
    const count = Math.floor(pos.length / 9);
    let minX = Infinity, minY = Infinity, minZ = Infinity;
    let maxX = -Infinity, maxY = -Infinity, maxZ = -Infinity;
    let volume = 0;
    let w = 0;
    // Bezugspunkt nahe am Modell verringert Auslöschung bei der Volumenberechnung
    let ox = 0, oy = 0, oz = 0;
    for (let i = 0; i < count; i++) {
      const o = i * 9;
      const s = pos[o] + pos[o + 1] + pos[o + 2];
      if (s - s === 0) { ox = pos[o]; oy = pos[o + 1]; oz = pos[o + 2]; break; }
    }
    for (let i = 0; i < count; i++) {
      const o = i * 9;
      const ax = pos[o], ay = pos[o + 1], az = pos[o + 2];
      const bx = pos[o + 3], by = pos[o + 4], bz = pos[o + 5];
      const cx = pos[o + 6], cy = pos[o + 7], cz = pos[o + 8];
      const sum = ax + ay + az + bx + by + bz + cx + cy + cz;
      if (sum - sum !== 0) continue; // NaN oder Infinity
      if (w !== i) {
        const t = w * 9;
        pos[t] = ax; pos[t + 1] = ay; pos[t + 2] = az;
        pos[t + 3] = bx; pos[t + 4] = by; pos[t + 5] = bz;
        pos[t + 6] = cx; pos[t + 7] = cy; pos[t + 8] = cz;
      }
      w++;
      if (ax < minX) minX = ax; if (ax > maxX) maxX = ax;
      if (bx < minX) minX = bx; if (bx > maxX) maxX = bx;
      if (cx < minX) minX = cx; if (cx > maxX) maxX = cx;
      if (ay < minY) minY = ay; if (ay > maxY) maxY = ay;
      if (by < minY) minY = by; if (by > maxY) maxY = by;
      if (cy < minY) minY = cy; if (cy > maxY) maxY = cy;
      if (az < minZ) minZ = az; if (az > maxZ) maxZ = az;
      if (bz < minZ) minZ = bz; if (bz > maxZ) maxZ = bz;
      if (cz < minZ) minZ = cz; if (cz > maxZ) maxZ = cz;
      // Vorzeichenbehaftetes Tetraedervolumen (Divergenzsatz)
      const px = ax - ox, py = ay - oy, pz = az - oz;
      const qx = bx - ox, qy = by - oy, qz = bz - oz;
      const rx = cx - ox, ry = cy - oy, rz = cz - oz;
      volume += px * (qy * rz - qz * ry) - py * (qx * rz - qz * rx) + pz * (qx * ry - qy * rx);
    }
    return {
      positions: w * 9 === pos.length ? pos : pos.slice(0, w * 9),
      triangles: w,
      min: [minX, minY, minZ],
      max: [maxX, maxY, maxZ],
      volume: volume / 6,
    };
  }

  // Flächennormalen aus der Eckpunkt-Reihenfolge (gespeicherte STL-Normalen
  // sind häufig 0 und werden bewusst ignoriert).
  function computeNormals(pos) {
    const nrm = new Float32Array(pos.length);
    for (let o = 0; o < pos.length; o += 9) {
      const ux = pos[o + 3] - pos[o], uy = pos[o + 4] - pos[o + 1], uz = pos[o + 5] - pos[o + 2];
      const vx = pos[o + 6] - pos[o], vy = pos[o + 7] - pos[o + 1], vz = pos[o + 8] - pos[o + 2];
      let nx = uy * vz - uz * vy;
      let ny = uz * vx - ux * vz;
      let nz = ux * vy - uy * vx;
      const l = Math.sqrt(nx * nx + ny * ny + nz * nz);
      if (l > 0) { nx /= l; ny /= l; nz /= l; } else { nx = 0; ny = 0; nz = 1; } // entartet
      nrm[o] = nrm[o + 3] = nrm[o + 6] = nx;
      nrm[o + 1] = nrm[o + 4] = nrm[o + 7] = ny;
      nrm[o + 2] = nrm[o + 5] = nrm[o + 8] = nz;
    }
    return nrm;
  }

  function parseSTL(input) {
    const bytes = toBytes(input);
    if (bytes.length === 0) throw emptyModelError();
    const format = detectFormat(bytes);
    if (!format) throw new Error('Die Datei ist keine gültige STL-Datei.');
    const raw = format === 'binary' ? readBinary(bytes) : readAscii(bytes);
    const mesh = analyzeMesh(raw);
    mesh.format = format;
    if (mesh.triangles === 0) {
      if (format === 'ascii' && !startsWithSolid(bytes)) {
        throw new Error('Die Datei ist keine gültige STL-Datei.');
      }
      throw emptyModelError();
    }
    return mesh;
  }

  // ------------------------------------------------------------------------
  // Hilfsfunktionen: Farben, Vektoren, Matrizen
  // ------------------------------------------------------------------------

  function clamp(v, lo, hi) { return v < lo ? lo : v > hi ? hi : v; }
  function dot3(a, b) { return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]; }
  function round(v, digits) { const f = Math.pow(10, digits); return Math.round(v * f) / f; }

  let colorCtx = null;

  // Beliebige CSS-Farbe → [r, g, b, a] (0..1, sRGB). Ungültig → fallback.
  function parseColor(css, fallback) {
    if (!colorCtx) {
      const c = document.createElement('canvas');
      c.width = c.height = 1;
      colorCtx = c.getContext('2d', { willReadFrequently: true });
    }
    const str = css == null ? '' : String(css).trim();
    const ctx = colorCtx;
    ctx.fillStyle = '#010203'; // Merker für ungültige Werte
    ctx.fillStyle = str;
    if (!str || (ctx.fillStyle === '#010203' && str.toLowerCase() !== '#010203')) {
      return fallback ? parseColor(fallback) : [0, 0, 0, 1];
    }
    ctx.clearRect(0, 0, 1, 1);
    ctx.fillRect(0, 0, 1, 1);
    const d = ctx.getImageData(0, 0, 1, 1).data;
    return [d[0] / 255, d[1] / 255, d[2] / 255, d[3] / 255];
  }

  function toLinear(c) { return [Math.pow(c[0], 2.2), Math.pow(c[1], 2.2), Math.pow(c[2], 2.2)]; }
  function luminance(c) { return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]; }
  function mix3(a, b, t) { return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t]; }

  // Kamerabasis aus Azimut theta (um Z) und Elevation phi.
  // r = rechts, u = oben, b = von der Szene zur Kamera. Bei phi = ±90° entartet nichts.
  function cameraBasis(theta, phi) {
    const ct = Math.cos(theta), st = Math.sin(theta);
    const cp = Math.cos(phi), sp = Math.sin(phi);
    return {
      r: [-st, ct, 0],
      u: [-sp * ct, -sp * st, cp],
      b: [cp * ct, cp * st, sp],
    };
  }

  function viewMatrix(out, basis, eye) {
    const r = basis.r, u = basis.u, b = basis.b;
    out[0] = r[0]; out[1] = u[0]; out[2] = b[0]; out[3] = 0;
    out[4] = r[1]; out[5] = u[1]; out[6] = b[1]; out[7] = 0;
    out[8] = r[2]; out[9] = u[2]; out[10] = b[2]; out[11] = 0;
    out[12] = -dot3(r, eye); out[13] = -dot3(u, eye); out[14] = -dot3(b, eye); out[15] = 1;
  }

  function perspectiveMatrix(out, fovy, aspect, near, far) {
    const f = 1 / Math.tan(fovy / 2);
    const nf = 1 / (near - far);
    out.fill(0);
    out[0] = f / aspect;
    out[5] = f;
    out[10] = (far + near) * nf;
    out[11] = -1;
    out[14] = 2 * far * near * nf;
  }

  function wrapAngle(a) {
    a = (a + Math.PI) % (2 * Math.PI);
    if (a < 0) a += 2 * Math.PI;
    return a - Math.PI;
  }

  function normalizeBed(bed) {
    if (!Array.isArray(bed) || bed.length < 2) return null;
    const x = Number(bed[0]), y = Number(bed[1]), z = Number(bed[2]) || 0;
    if (!(x > 0 && y > 0 && isFinite(x) && isFinite(y))) return null;
    return [x, y, z > 0 && isFinite(z) ? z : 0];
  }

  function easeInOut(t) { return t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2; }

  function formatMM(v) { return (v < 1 ? v.toFixed(2) : v.toFixed(1)) + ' mm'; }

  // ------------------------------------------------------------------------
  // Shader
  // ------------------------------------------------------------------------

  const PRECISION =
    '#ifdef GL_FRAGMENT_PRECISION_HIGH\nprecision highp float;\n#else\nprecision mediump float;\n#endif\n';

  const MODEL_VS = `
    attribute vec3 aPosition;
    attribute vec3 aNormal;
    attribute vec3 aBary;
    uniform mat4 uView;
    uniform mat4 uProj;
    varying vec3 vNormalView;
    varying vec3 vNormalWorld;
    varying vec3 vViewPos;
    varying vec3 vBary;
    varying float vWorldZ;
    void main() {
      vec4 viewPos = uView * vec4(aPosition, 1.0);
      vViewPos = viewPos.xyz;
      vNormalView = (uView * vec4(aNormal, 0.0)).xyz;
      vNormalWorld = aNormal;
      vBary = aBary;
      vWorldZ = aPosition.z;
      gl_Position = uProj * viewPos;
    }`;

  // Beleuchtung: Kopflicht (an der Kamera), Fülllicht, Hemisphären-Umgebungslicht,
  // Randlicht und leichter Glanz. Rückseiten rötlich → falsche Normalen sichtbar.
  const MODEL_FS = `
    uniform vec3 uColor;
    uniform vec3 uBackColor;
    uniform vec3 uCapColor;
    uniform vec3 uWireColor;
    uniform float uWire;
    uniform float uWirePx;
    uniform float uXray;
    uniform vec2 uClip;       // x: aktiv, y: Schnitthöhe (Welt-Z)
    uniform float uHatch;     // Schraffurdichte der Schnittfläche
    uniform vec3 uKeyPos;     // Kopflicht (Kameraraum), leicht oben links hinter der Kamera
    varying vec3 vNormalView;
    varying vec3 vNormalWorld;
    varying vec3 vViewPos;
    varying vec3 vBary;
    varying float vWorldZ;

    vec3 toSrgb(vec3 c) { return pow(clamp(c, 0.0, 1.0), vec3(1.0 / 2.2)); }

    void main() {
    #ifdef HAS_DERIV
      vec3 baryWidth = fwidth(vBary);
    #endif
      if (uClip.x > 0.5 && vWorldZ > uClip.y) discard;

      vec3 n = normalize(vNormalView);
      vec3 nw = normalize(vNormalWorld);
      vec3 base = uColor;
      if (!gl_FrontFacing) {
        n = -n;
        nw = -nw;
        if (uClip.x > 0.5) {
          // Schnittansicht: Innenseiten als flache, schraffierte Kappe darstellen
          float hatch = step(0.5, fract((gl_FragCoord.x + gl_FragCoord.y) * uHatch));
          vec3 cap = uCapColor * (0.82 + 0.18 * hatch);
          float capAlpha = uXray > 0.5 ? 0.55 : 1.0;
          gl_FragColor = vec4(toSrgb(cap) * capAlpha, capAlpha);
          return;
        }
        base = uXray > 0.5 ? uColor * 0.5 : uBackColor;
      }

      vec3 v = normalize(-vViewPos);
      vec3 lKey = normalize(uKeyPos - vViewPos); // Punktlicht → sanfte Verläufe auf Flächen
      vec3 lFill = normalize(vec3(0.7, -0.35, 0.45));
      float key = max(dot(n, lKey), 0.0);
      float fill = max(dot(n, lFill), 0.0);
      float hemi = nw.z * 0.5 + 0.5;
      vec3 ambient = mix(vec3(0.06, 0.06, 0.07), vec3(0.26, 0.27, 0.30), hemi);
      float ndv = max(dot(n, v), 0.0);
      float rim = pow(1.0 - ndv, 4.0);
      vec3 h = normalize(lKey + v);
      float spec = pow(max(dot(n, h), 0.0), 56.0) * 0.20;
      // Schwacher Umgebungsglanz: Reflexion eines Himmel/Boden-Verlaufs, zum Rand hin stärker
      vec3 rv = reflect(-v, n);
      float envT = clamp(rv.y * 0.5 + 0.5, 0.0, 1.0);
      vec3 env = mix(vec3(0.02, 0.02, 0.025), vec3(0.55, 0.58, 0.64), envT * envT);
      float fres = 0.04 + 0.5 * pow(1.0 - ndv, 5.0);
      vec3 col = base * (ambient + key * 0.82 + fill * 0.22) + vec3(spec) + env * fres
        + rim * 0.06 * vec3(0.8, 0.86, 1.0);

      float alpha = 1.0;
      if (uXray > 0.5) alpha = clamp(0.12 + 0.6 * pow(1.0 - ndv, 2.0), 0.0, 1.0);

      if (uWire > 0.5) {
      #ifdef HAS_DERIV
        vec3 a3 = smoothstep(vec3(0.0), baryWidth * uWirePx, vBary);
        float edge = 1.0 - min(min(a3.x, a3.y), a3.z);
      #else
        float edge = 1.0 - smoothstep(0.0, 0.03, min(min(vBary.x, vBary.y), vBary.z));
      #endif
        col = mix(col, uWireColor, edge * 0.85);
        alpha = max(alpha, edge * 0.8);
      }
      gl_FragColor = vec4(toSrgb(col) * alpha, alpha);
    }`;

  const GRID_VS = `
    attribute vec2 aPos;
    uniform vec4 uQuad;       // Mitte xy, halbe Kantenlänge, Z-Höhe
    uniform mat4 uView;
    uniform mat4 uProj;
    varying vec2 vWorld;
    void main() {
      vWorld = uQuad.xy + aPos * uQuad.z;
      gl_Position = uProj * uView * vec4(vWorld, uQuad.w, 1.0);
    }`;

  // Prozedurales Raster: feine Linien, jede 5. und 10. etwas heller, weich
  // ausgeblendet; dazu Druckbett-Umriss und weicher Kontaktschatten.
  const GRID_FS = `
    uniform vec3 uFillColor;
    uniform vec3 uLineColor;
    uniform vec3 uMidColor;
    uniform vec3 uMajorColor;
    uniform float uMinor;        // Linienabstand in mm
    uniform float uPx;           // Gerätepixel pro CSS-Pixel
    uniform float uPxWorld;      // Ersatz für fwidth ohne Ableitungen
    uniform float uGridOn;
    uniform float uOpacity;
    uniform vec4 uFade;          // Mitte xy, Innen-/Außenradius (modellbezogen)
    uniform vec4 uBed;           // Mitte xy, halbe Breite/Tiefe
    uniform float uBedOn;
    uniform float uBedMargin;
    uniform vec3 uBedColor;
    uniform vec4 uShadowRect;    // Mitte xy, halbe Kantenlänge, Stärke
    uniform sampler2D uShadow;
    varying vec2 vWorld;

    vec4 over(vec4 dst, vec3 c, float a) { return vec4(c * a, a) + dst * (1.0 - a); }

    // Linien je Richtung; zu dicht liegende Linien (flacher Blickwinkel, weit weg)
    // werden pro Achse ausgeblendet, damit kein Moiré entsteht.
    float lineAlpha(vec2 coord, vec2 fw, float widthPx) {
      vec2 d = abs(fract(coord - 0.5) - 0.5) / max(fw, vec2(1e-6));
      vec2 a = clamp(widthPx * 0.5 + 0.5 - d, 0.0, 1.0);
      vec2 lod = 1.0 - smoothstep(vec2(0.06), vec2(0.15), fw / uPx);
      return max(a.x * lod.x, a.y * lod.y);
    }

    void main() {
      vec2 cMinor = vWorld / uMinor;
    #ifdef HAS_DERIV
      vec2 fwMinor = fwidth(cMinor);
    #else
      vec2 fwMinor = vec2(uPxWorld / uMinor);
    #endif
      vec2 fwWorld = fwMinor * uMinor;

      // Sichtbarer Bereich: Kreis um das Modell und/oder Druckbett
      float fade = 1.0 - smoothstep(uFade.z, uFade.w, length(vWorld - uFade.xy));
      float inBed = 0.0;
      float bedSdf = 1e9;
      if (uBedOn > 0.5) {
        vec2 q = abs(vWorld - uBed.xy) - uBed.zw;
        bedSdf = length(max(q, 0.0)) + min(max(q.x, q.y), 0.0);
        fade = max(fade, 1.0 - smoothstep(0.0, uBedMargin, bedSdf));
        inBed = 1.0 - smoothstep(-0.5, 0.5, bedSdf / max(max(fwWorld.x, fwWorld.y), 1e-6));
      }

      vec4 acc = vec4(0.0);
      if (uGridOn > 0.5) {
        float plate = uBedOn > 0.5 ? max(inBed, fade * 0.35) : fade;
        acc = over(acc, uFillColor, 0.55 * plate);

        vec2 sUv = (vWorld - uShadowRect.xy) / (2.0 * uShadowRect.z) + 0.5;
        if (sUv.x > 0.0 && sUv.x < 1.0 && sUv.y > 0.0 && sUv.y < 1.0) {
          float s = texture2D(uShadow, sUv).r * uShadowRect.w;
          acc = over(acc, vec3(0.0), s);
        }

        float aMinor = lineAlpha(cMinor, fwMinor, uPx);
        float aMid = lineAlpha(cMinor / 5.0, fwMinor / 5.0, uPx);
        float aMajor = lineAlpha(cMinor / 10.0, fwMinor / 10.0, 1.4 * uPx);
        acc = over(acc, uLineColor, aMinor * fade * 0.7);
        acc = over(acc, uMidColor, aMid * fade);
        acc = over(acc, uMajorColor, aMajor * fade);

        // Ursprungsachsen auf dem Bett: X rot, Y grün
        vec2 dAxis = abs(vWorld) / max(fwWorld, vec2(1e-6));
        acc = over(acc, vec3(0.78, 0.25, 0.26), clamp(uPx + 0.5 - dAxis.y, 0.0, 1.0) * 0.75 * fade);
        acc = over(acc, vec3(0.25, 0.62, 0.30), clamp(uPx + 0.5 - dAxis.x, 0.0, 1.0) * 0.75 * fade);
      }

      if (uBedOn > 0.5) {
        float px = abs(bedSdf) / max(max(fwWorld.x, fwWorld.y), 1e-6);
        acc = over(acc, uBedColor, clamp(0.9 * uPx + 0.5 - px, 0.0, 1.0) * 0.85);
      }
      gl_FragColor = acc * uOpacity;
    }`;

  const LINE_VS = `
    attribute vec3 aPosition;
    attribute vec4 aColor;
    uniform mat4 uView;
    uniform mat4 uProj;
    varying vec4 vColor;
    void main() {
      vec4 v = uView * vec4(aPosition, 1.0);
      v.xyz *= 0.997; // minimal zur Kamera ziehen, damit Kanten auf Flächen sichtbar bleiben
      vColor = aColor;
      gl_Position = uProj * v;
    }`;

  const LINE_FS = `
    varying vec4 vColor;
    void main() { gl_FragColor = vColor; }`;

  // Kontaktschatten: Modell von unten orthografisch rendern, Intensität nach Höhe
  const SHADOW_VS = `
    attribute vec3 aPosition;
    uniform vec4 uRect;       // Mitte xy, halbe Kantenlänge
    uniform vec3 uZ;          // minZ, 1 / Z-Spanne, 1 / Schattenhöhe
    varying float vH;
    void main() {
      float z = aPosition.z - uZ.x;
      vH = z * uZ.z;
      gl_Position = vec4((aPosition.xy - uRect.xy) / uRect.z, clamp(z * uZ.y, 0.0, 1.0) * 2.0 - 1.0, 1.0);
    }`;

  const SHADOW_FS = `
    varying float vH;
    void main() {
      float s = 1.0 - clamp(vH, 0.0, 1.0);
      gl_FragColor = vec4(s * s, 0.0, 0.0, 1.0);
    }`;

  const BLUR_VS = `
    attribute vec2 aPos;
    varying vec2 vUv;
    void main() { vUv = aPos * 0.5 + 0.5; gl_Position = vec4(aPos, 0.0, 1.0); }`;

  // Separierbarer Gauß (9 Abgriffe über lineare Filterung)
  const BLUR_FS = `
    uniform sampler2D uTex;
    uniform vec2 uDir;
    varying vec2 vUv;
    void main() {
      vec4 c = texture2D(uTex, vUv) * 0.2270270;
      c += texture2D(uTex, vUv + uDir * 1.3846154) * 0.3162162;
      c += texture2D(uTex, vUv - uDir * 1.3846154) * 0.3162162;
      c += texture2D(uTex, vUv + uDir * 3.2307692) * 0.0702703;
      c += texture2D(uTex, vUv - uDir * 3.2307692) * 0.0702703;
      gl_FragColor = c;
    }`;

  function compileShader(gl, type, src) {
    const sh = gl.createShader(type);
    gl.shaderSource(sh, src);
    gl.compileShader(sh);
    if (!gl.getShaderParameter(sh, gl.COMPILE_STATUS) && !gl.isContextLost()) {
      const log = gl.getShaderInfoLog(sh);
      gl.deleteShader(sh);
      throw new Error('Shader-Fehler: ' + log);
    }
    return sh;
  }

  function createProgram(gl, vsSrc, fsSrc, attribs, deriv) {
    let header = '';
    if (deriv) header = '#extension GL_OES_standard_derivatives : enable\n#define HAS_DERIV 1\n';
    const vs = compileShader(gl, gl.VERTEX_SHADER, 'precision highp float;\n' + vsSrc);
    const fs = compileShader(gl, gl.FRAGMENT_SHADER, header + PRECISION + fsSrc);
    const program = gl.createProgram();
    gl.attachShader(program, vs);
    gl.attachShader(program, fs);
    attribs.forEach((name, i) => gl.bindAttribLocation(program, i, name));
    gl.linkProgram(program);
    gl.deleteShader(vs);
    gl.deleteShader(fs);
    if (!gl.getProgramParameter(program, gl.LINK_STATUS) && !gl.isContextLost()) {
      throw new Error('Shader-Programm fehlerhaft: ' + gl.getProgramInfoLog(program));
    }
    const u = {};
    const count = gl.getProgramParameter(program, gl.ACTIVE_UNIFORMS) || 0;
    for (let i = 0; i < count; i++) {
      const info = gl.getActiveUniform(program, i);
      u[info.name] = gl.getUniformLocation(program, info.name);
    }
    // Nicht verwendete (wegoptimierte) Uniforms still ignorieren
    const uniforms = new Proxy(u, { get: (t, k) => (k in t ? t[k] : null) });
    return { program, u: uniforms, attribCount: attribs.length };
  }

  // ------------------------------------------------------------------------
  // Stylesheet (einmalig eingefügt, Klassen mit Präfix "stlv-")
  // ------------------------------------------------------------------------

  const STYLE_ID = 'stlviewer-styles';
  const CSS = `
.stlv-canvas{position:absolute;left:0;top:0;width:100%;height:100%;display:block;touch-action:none;
  outline:none;cursor:grab;user-select:none;-webkit-user-select:none;-webkit-tap-highlight-color:transparent}
.stlv-canvas.stlv-orbiting{cursor:grabbing}
.stlv-canvas.stlv-panning{cursor:move}
.stlv-gizmo{position:absolute;left:6px;bottom:6px;width:84px;height:84px;pointer-events:none}
.stlv-labels{position:absolute;left:0;top:0;right:0;bottom:0;overflow:hidden;pointer-events:none}
.stlv-dim{position:absolute;left:0;top:0;white-space:nowrap;pointer-events:none;
  font:600 11px/1 ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
  font-variant-numeric:tabular-nums;letter-spacing:.01em;color:#eceef3;
  background:rgba(14,14,18,.82);border:1px solid rgba(255,255,255,.10);border-radius:5px;
  padding:4px 7px 4px 6px;box-shadow:0 2px 8px rgba(0,0,0,.35);will-change:transform}
.stlv-dim b{font-weight:700;margin-right:5px}
.stlv-overlay{position:absolute;left:0;top:0;right:0;bottom:0;display:flex;align-items:center;
  justify-content:center;pointer-events:none;padding:16px;box-sizing:border-box}
.stlv-overlay[hidden],.stlv-dim[hidden]{display:none}
.stlv-msg{display:flex;align-items:center;gap:10px;max-width:420px;text-align:center;
  font:500 13px/1.45 ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;color:#e8e9ee;
  background:rgba(14,14,18,.78);border:1px solid rgba(255,255,255,.08);border-radius:10px;padding:9px 14px;
  box-shadow:0 6px 24px rgba(0,0,0,.35)}
.stlv-spin{flex:none;width:14px;height:14px;border-radius:50%;border:2px solid rgba(255,255,255,.22);
  border-top-color:rgba(255,255,255,.9);animation:stlv-spin .8s linear infinite}
@keyframes stlv-spin{to{transform:rotate(360deg)}}
@media (prefers-reduced-motion:reduce){.stlv-spin{animation-duration:2.4s}}`;

  function injectStyles() {
    if (document.getElementById(STYLE_ID)) return;
    const style = document.createElement('style');
    style.id = STYLE_ID;
    style.textContent = CSS;
    (document.head || document.documentElement).appendChild(style);
  }

  function createContext(canvas) {
    const attrs = {
      antialias: true, alpha: true, depth: true, stencil: false,
      premultipliedAlpha: true, preserveDrawingBuffer: false,
    };
    for (const name of ['webgl', 'experimental-webgl']) {
      try {
        const gl = canvas.getContext(name, attrs);
        if (gl) return gl;
      } catch { /* nächsten Kontexttyp versuchen */ }
    }
    return null;
  }

  // ------------------------------------------------------------------------
  // STLViewer
  // ------------------------------------------------------------------------

  class STLViewer {
    constructor(container, options) {
      if (!container || typeof container.appendChild !== 'function') {
        throw new Error('STLViewer: Container-Element fehlt.');
      }
      this.container = container;
      this.options = Object.assign({}, DEFAULTS, options || {});
      injectStyles();

      // Container als Bezugsrahmen für Canvas und Overlays
      this._restorePosition = null;
      const pos = getComputedStyle(container).position;
      if (!pos || pos === 'static') {
        this._restorePosition = container.style.position;
        container.style.position = 'relative';
      }

      this.canvas = document.createElement('canvas');
      this.canvas.className = 'stlv-canvas';
      this.canvas.setAttribute('role', 'img');
      this.canvas.setAttribute('aria-label', '3D-Vorschau des Modells');
      container.appendChild(this.canvas);

      this._labelLayer = document.createElement('div');
      this._labelLayer.className = 'stlv-labels';
      this._dimLabels = AXIS_NAMES.map((name, i) => {
        const el = document.createElement('div');
        el.className = 'stlv-dim stlv-dim-' + name.toLowerCase();
        el.hidden = true;
        const b = document.createElement('b');
        b.textContent = name;
        b.style.color = AXIS_COLORS[i];
        el.appendChild(b);
        el.appendChild(document.createTextNode(''));
        this._labelLayer.appendChild(el);
        return { el, text: el.lastChild, w: 0, h: 0, value: '' };
      });
      container.appendChild(this._labelLayer);

      this._gizmo = document.createElement('canvas');
      this._gizmo.className = 'stlv-gizmo';
      this._gizmo.setAttribute('aria-hidden', 'true');
      container.appendChild(this._gizmo);

      this._overlay = document.createElement('div');
      this._overlay.className = 'stlv-overlay';
      this._overlay.hidden = true;
      this._overlay.setAttribute('role', 'status');
      container.appendChild(this._overlay);

      // Zustand
      this._mesh = null;          // { positions, triangles, min, max, volume, format }
      this._gpu = null;           // GPU-Puffer des Modells
      this._cam = { theta: VIEWS.iso.theta, phi: VIEWS.iso.phi, distance: 100, target: [0, 0, 0] };
      this._anim = null;
      this._view = new Float32Array(16);
      this._proj = new Float32Array(16);
      this._eye = [0, 0, 0];
      this._basis = cameraBasis(this._cam.theta, this._cam.phi);
      this._near = 0.1;
      this._aspect = 1;
      this._cssW = 0;
      this._cssH = 0;
      this._dpr = 1;
      this._raf = 0;
      this._wire = false;
      this._xray = false;
      this._clipZ = null;
      this._showGrid = !!this.options.showGrid;
      this._showAxes = !!this.options.showAxes;
      this._showDims = !!this.options.showDimensions;
      this._bed = null;
      this._dimLayout = null;
      this._pointers = new Map();
      this._drag = null;
      this._pinch = null;
      this._lastTap = null;
      this._loadToken = 0;
      this._abort = null;
      this._loadingTimer = 0;
      this._fatal = null;
      this._destroyed = false;
      this._lost = false;
      this._pendingFrame = null;

      this._setColors();
      this._bed = normalizeBed(this.options.bed);

      this.gl = createContext(this.canvas);
      if (this.gl) {
        try {
          this._initGL();
        } catch (err) {
          console.error(err);
          this.gl = null;
        }
      }
      if (!this.gl) {
        this._fatal = 'WebGL ist in diesem Browser nicht verfügbar – die 3D-Vorschau kann nicht angezeigt werden.';
        this._showMessage(this._fatal, false);
      }

      this._bindEvents();
      this._syncSize();
      this._updateScene();
      this._cam = this._frameState(VIEWS.iso);
      this._requestRender();
    }

    // ---------------------------------------------------------------- Laden

    /** Lädt eine STL-Datei per URL. Liefert getInfo() oder null, falls überholt. */
    async loadUrl(url, opts) {
      const token = this._beginLoad();
      const ctrl = typeof AbortController === 'function' ? new AbortController() : null;
      this._abort = ctrl;
      this._setLoading(true);
      try {
        let buffer;
        try {
          const res = await fetch(url, { cache: 'no-store', signal: ctrl ? ctrl.signal : undefined });
          if (token !== this._loadToken) return null;
          if (!res.ok) {
            throw new Error('Das 3D-Modell konnte nicht geladen werden (HTTP ' + res.status + ').');
          }
          buffer = await res.arrayBuffer();
        } catch (err) {
          if (token !== this._loadToken || this._destroyed) return null;
          if (err && /^Das 3D-Modell/.test(err.message)) throw err;
          throw new Error('Das 3D-Modell konnte nicht geladen werden (Netzwerkfehler).');
        }
        if (token !== this._loadToken || this._destroyed) return null;
        // Dem Browser kurz Luft lassen, damit der Ladehinweis gezeichnet wird
        await new Promise((resolve) => setTimeout(resolve, 0));
        if (token !== this._loadToken || this._destroyed) return null;
        return this._loadBuffer(buffer, opts);
      } finally {
        if (token === this._loadToken) {
          this._setLoading(false);
          this._abort = null;
        }
      }
    }

    /** Lädt STL-Daten aus einem ArrayBuffer (synchron). Wirft bei Fehlern. */
    loadArrayBuffer(buffer, opts) {
      this._beginLoad();
      this._setLoading(false);
      return this._loadBuffer(buffer, opts);
    }

    /** Entfernt das Modell. */
    clear() {
      this._beginLoad();
      this._setLoading(false);
      this._disposeMesh();
      this._mesh = null;
      this._updateScene();
      this._bakeShadow();
      this._anim = null;
      this._cam = this._frameState(VIEWS.iso);
      this._requestRender();
    }

    _beginLoad() {
      this._loadToken++;
      if (this._abort) {
        try { this._abort.abort(); } catch { /* egal */ }
        this._abort = null;
      }
      return this._loadToken;
    }

    _loadBuffer(buffer, opts) {
      if (this._destroyed) return null;
      const mesh = parseSTL(buffer);
      const keepView = !!(opts && opts.keepView && this._mesh);
      this._disposeMesh();
      this._mesh = mesh;
      this._updateScene();
      if (this.gl && !this._lost) {
        this._uploadMesh();
        this._bakeShadow();
      }
      this._measureLabels();
      if (keepView) {
        const c = this._cam;
        c.distance = clamp(c.distance, this._scene.minDistance, this._scene.maxDistance);
      } else {
        this._anim = null;
        this._cam = this._frameState(VIEWS.iso);
      }
      this._requestRender();
      return this.getInfo();
    }

    // ----------------------------------------------------------- Öffentlich

    getInfo() {
      const m = this._mesh;
      if (!m) return null;
      return {
        triangles: m.triangles,
        size: [0, 1, 2].map((i) => round(m.max[i] - m.min[i], 4)),
        min: m.min.map((v) => round(v, 4)),
        max: m.max.map((v) => round(v, 4)),
        volume: round(Math.abs(m.volume), 2), // mm³ (nur bei geschlossenen Netzen sinnvoll)
        format: m.format,
      };
    }

    /** Passt das Modell auf das Druckbett? null, wenn kein Modell oder Bett gesetzt. */
    fitsBed() {
      if (!this._mesh || !this._bed) return null;
      return !this._bedExceeded();
    }

    resetView() { this.setView('iso'); }

    setView(name, opts) {
      const v = VIEWS[name] || VIEWS.iso;
      const animate = !opts || opts.animate !== false;
      this._animateTo(this._frameState(v), animate);
    }

    setWireframe(on) {
      this._wire = !!on;
      if (this._wire) this._ensureBary();
      this._requestRender();
    }

    setXray(on) {
      this._xray = !!on;
      this._requestRender();
    }

    /** Horizontale Schnittebene: alles oberhalb von z (mm) ausblenden; null = aus. */
    setClipZ(z) {
      this._clipZ = z == null || z === false || !isFinite(z) ? null : Number(z);
      this._requestRender();
    }

    setModelColor(css) {
      this.options.modelColor = css;
      this._setColors();
      this._requestRender();
    }

    setBackground(css) {
      this.options.background = css;
      this._setColors();
      this._requestRender();
    }

    setShowGrid(on) {
      this._showGrid = !!on;
      this._requestRender();
    }

    setShowDimensions(on) {
      this._showDims = !!on;
      this._requestRender();
    }

    /** Druckbett [x, y, z] in mm (z optional) oder null zum Ausblenden. */
    setBed(bed) {
      this._bed = normalizeBed(bed);
      this._updateScene();
      if (!this._mesh) this._cam = this._frameState(VIEWS.iso);
      this._requestRender();
    }

    /** PNG-Daten-URL der aktuellen Ansicht (ohne HTML-Beschriftungen). */
    screenshot() {
      if (!this.gl || this._lost) return null;
      if (this._anim) {
        this._cam = this._anim.to;
        this._anim = null;
      }
      this._render();
      return this.canvas.toDataURL('image/png');
    }

    destroy() {
      if (this._destroyed) return;
      this._destroyed = true;
      this._beginLoad();
      clearTimeout(this._loadingTimer);
      if (this._raf) cancelAnimationFrame(this._raf);
      this._raf = 0;
      this._unbindEvents();
      const gl = this.gl;
      if (gl) {
        this._disposeMesh();
        this._disposeGL();
        const lose = gl.getExtension('WEBGL_lose_context');
        if (lose) lose.loseContext();
      }
      this.gl = null;
      this._mesh = null;
      for (const el of [this.canvas, this._labelLayer, this._gizmo, this._overlay]) {
        if (el.parentNode) el.parentNode.removeChild(el);
      }
      if (this._restorePosition !== null) this.container.style.position = this._restorePosition;
    }

    // ---------------------------------------------------------------- Farben

    _setColors() {
      const o = this.options;
      const model = parseColor(o.modelColor, DEFAULTS.modelColor);
      this._bg = parseColor(o.background, DEFAULTS.background);
      const grid = parseColor(o.gridColor, DEFAULTS.gridColor);
      this._modelLin = toLinear(model);
      // Rückseiten: deutlich rötlich und dunkler
      this._backLin = mix3(this._modelLin, [0.42, 0.035, 0.03], 0.82);
      this._capLin = mix3(this._modelLin, [1, 1, 1], 0.12).map((c) => c * 0.75);
      this._wireLin = luminance(this._modelLin) > 0.12 ? this._modelLin.map((c) => c * 0.12) : [0.75, 0.77, 0.82];
      // Kontrastrichtung abhängig vom Hintergrund (dunkles vs. helles Thema)
      const ref = this._bg[3] > 0 ? this._bg : grid;
      const dark = luminance(ref) < 0.5;
      const toward = dark ? [1, 1, 1] : [0, 0, 0];
      this._gridFill = grid.slice(0, 3);
      this._gridLine = mix3(grid, toward, 0.07);
      this._gridMid = mix3(grid, toward, 0.14);
      this._gridMajor = mix3(grid, toward, 0.24);
      this._shadowStrength = dark ? 0.85 : 0.35;
      this._bedColor = parseColor(o.bedColor, DEFAULTS.bedColor).slice(0, 3);
      this._bedOverColor = parseColor(BED_OVER_COLOR).slice(0, 3);
      this._dimLine = dark ? [0.75, 0.78, 0.86] : [0.25, 0.27, 0.32];
      this._axisRgb = AXIS_COLORS.map((c) => parseColor(c).slice(0, 3));
    }

    // ----------------------------------------------------------------- Szene

    // Leitet Raster, Druckbett, Schattenbereich und Kameragrenzen aus dem Modell ab.
    _updateScene() {
      let min, max;
      const bed = this._bed;
      if (this._mesh) {
        min = this._mesh.min;
        max = this._mesh.max;
      } else if (bed) {
        // Leerer Zustand: der komplette Bauraum des Druckers wird eingerahmt
        min = [-bed[0] / 2, -bed[1] / 2, 0];
        max = [bed[0] / 2, bed[1] / 2, bed[2] || Math.max(bed[0], bed[1]) * 0.08];
      } else {
        min = [-20, -20, 0];
        max = [20, 20, 20];
      }
      const size = [max[0] - min[0], max[1] - min[1], max[2] - min[2]];
      const center = [(min[0] + max[0]) / 2, (min[1] + max[1]) / 2, (min[2] + max[2]) / 2];
      const S = Math.max(size[0], size[1], size[2] * 0.75, 1e-3);
      const foot = Math.max(size[0], size[1]) / 2;
      // Ohne Modell, aber mit Bett: Raster nur auf dem Bett (kein Kreis ums Modell)
      const modelFade = this._mesh || !bed;
      const fadeIn = modelFade ? Math.hypot(size[0], size[1]) / 2 + S * 0.25 : 0;
      const fadeOut = modelFade ? fadeIn + S * 0.9 : 1e-3;
      // Rasterweite: 10er-Potenz, sodass ca. 5–50 Linien über das Modell laufen
      const basis = this._mesh ? S : Math.max(S, bed ? Math.max(bed[0], bed[1]) * 0.5 : 0);
      const minor = Math.pow(10, Math.floor(Math.log10(basis / 5)));

      let half = fadeOut;
      let bedRect = null;
      if (bed) {
        const margin = Math.max(bed[0], bed[1]) * 0.06;
        bedRect = { cx: center[0], cy: center[1], hx: bed[0] / 2, hy: bed[1] / 2, h: bed[2], margin };
        half = Math.max(half, Math.max(bedRect.hx, bedRect.hy) + margin * 1.5);
      }

      // Umkugel aller sichtbaren Teile (für near/far)
      let rad = Math.hypot(size[0], size[1], size[2]) / 2;
      rad = Math.max(rad, Math.hypot(half, half, size[2] / 2));
      if (bedRect && bedRect.h > 0) rad = Math.max(rad, Math.hypot(bedRect.hx, bedRect.hy, bedRect.h));

      const radius = Math.max(Math.hypot(size[0], size[1], size[2]) / 2, 1e-3);
      this._scene = {
        min, max, size, center, S,
        z0: min[2],
        grid: { cx: center[0], cy: center[1], half, minor, fadeIn, fadeOut },
        bed: bedRect,
        shadow: { cx: center[0], cy: center[1], half: foot + S * 0.35, height: S * 0.5 },
        sphere: { center: [center[0], center[1], min[2] + size[2] / 2], radius: rad },
        minDistance: radius * 0.05,
        maxDistance: Math.max(radius * 25, bedRect ? Math.hypot(bedRect.hx, bedRect.hy, bedRect.h) * 6 : 0),
      };
    }

    _bedExceeded() {
      const m = this._mesh, bed = this._bed;
      if (!m || !bed) return false;
      const eps = 1e-6;
      return (m.max[0] - m.min[0]) > bed[0] + eps || (m.max[1] - m.min[1]) > bed[1] + eps ||
        (bed[2] > 0 && (m.max[2] - m.min[2]) > bed[2] + eps);
    }

    // Kamera so ausrichten, dass die Bounding-Box das Bild füllt.
    _frameState(view) {
      const sc = this._scene;
      const basis = cameraBasis(view.theta, view.phi);
      const valid = this._cssW > 0 && this._cssH > 0;
      // Container noch unsichtbar (0×0)? Dann beim ersten Resize erneut einrahmen.
      this._pendingFrame = valid ? null : view;
      const aspect = valid ? this._cssW / this._cssH : 1;
      const margin = 1.38;
      const tanV = Math.tan(FOV_Y / 2) / margin;
      const tanH = tanV * aspect;
      let dist = 0;
      for (let i = 0; i < 8; i++) {
        const c = [
          (i & 1 ? sc.max[0] : sc.min[0]) - sc.center[0],
          (i & 2 ? sc.max[1] : sc.min[1]) - sc.center[1],
          (i & 4 ? sc.max[2] : sc.min[2]) - sc.center[2],
        ];
        const x = dot3(c, basis.r), y = dot3(c, basis.u), z = dot3(c, basis.b);
        dist = Math.max(dist, z + Math.abs(x) / tanH, z + Math.abs(y) / tanV);
      }
      dist = clamp(dist, sc.minDistance, sc.maxDistance);
      return { theta: view.theta, phi: view.phi, distance: dist, target: sc.center.slice() };
    }

    _animateTo(state, animate) {
      const reduce = global.matchMedia && global.matchMedia('(prefers-reduced-motion: reduce)').matches;
      if (!animate || reduce) {
        this._anim = null;
        this._cam = state;
      } else {
        const from = this._cam;
        const to = Object.assign({}, state, { theta: from.theta + wrapAngle(state.theta - from.theta) });
        this._anim = { from, to, start: performance.now() };
      }
      this._requestRender();
    }

    _stepAnimation(now) {
      const a = this._anim;
      const t = clamp((now - a.start) / ANIM_MS, 0, 1);
      const e = easeInOut(t);
      const f = a.from, to = a.to;
      this._cam = {
        theta: f.theta + (to.theta - f.theta) * e,
        phi: f.phi + (to.phi - f.phi) * e,
        // Abstand logarithmisch interpolieren wirkt gleichmäßiger
        distance: Math.exp(Math.log(f.distance) + (Math.log(to.distance) - Math.log(f.distance)) * e),
        target: [0, 1, 2].map((i) => f.target[i] + (to.target[i] - f.target[i]) * e),
      };
      if (t >= 1) {
        this._cam = to;
        this._anim = null;
      }
    }

    _stopAnimation() { this._anim = null; }

    // -------------------------------------------------------------- WebGL

    _initGL() {
      const gl = this.gl;
      this._deriv = !!gl.getExtension('OES_standard_derivatives');
      this._progModel = createProgram(gl, MODEL_VS, MODEL_FS, ['aPosition', 'aNormal', 'aBary'], this._deriv);
      this._progGrid = createProgram(gl, GRID_VS, GRID_FS, ['aPos'], this._deriv);
      this._progLine = createProgram(gl, LINE_VS, LINE_FS, ['aPosition', 'aColor'], false);
      this._progShadow = createProgram(gl, SHADOW_VS, SHADOW_FS, ['aPosition'], false);
      this._progBlur = createProgram(gl, BLUR_VS, BLUR_FS, ['aPos'], false);
      this._enabledAttribs = 0;

      this._quad = gl.createBuffer();
      gl.bindBuffer(gl.ARRAY_BUFFER, this._quad);
      gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), gl.STATIC_DRAW);
      this._lineBuf = gl.createBuffer();
      this._baryBuf = null;
      this._baryCapacity = 0;

      // Kontaktschatten: zwei Texturen für das Weichzeichnen im Pingpong
      const makeTarget = (withDepth) => {
        const tex = gl.createTexture();
        gl.bindTexture(gl.TEXTURE_2D, tex);
        gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, SHADOW_SIZE, SHADOW_SIZE, 0, gl.RGBA, gl.UNSIGNED_BYTE, null);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
        const fb = gl.createFramebuffer();
        gl.bindFramebuffer(gl.FRAMEBUFFER, fb);
        gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, tex, 0);
        let depth = null;
        if (withDepth) {
          depth = gl.createRenderbuffer();
          gl.bindRenderbuffer(gl.RENDERBUFFER, depth);
          gl.renderbufferStorage(gl.RENDERBUFFER, gl.DEPTH_COMPONENT16, SHADOW_SIZE, SHADOW_SIZE);
          gl.framebufferRenderbuffer(gl.FRAMEBUFFER, gl.DEPTH_ATTACHMENT, gl.RENDERBUFFER, depth);
        }
        const ok = gl.checkFramebufferStatus(gl.FRAMEBUFFER) === gl.FRAMEBUFFER_COMPLETE;
        return { tex, fb, depth, ok };
      };
      const a = makeTarget(true);
      const b = makeTarget(false);
      gl.bindFramebuffer(gl.FRAMEBUFFER, null);
      gl.bindTexture(gl.TEXTURE_2D, null);
      this._shadow = { a, b, ok: a.ok && b.ok, active: false };
    }

    _disposeGL() {
      const gl = this.gl;
      if (!gl) return;
      for (const p of [this._progModel, this._progGrid, this._progLine, this._progShadow, this._progBlur]) {
        if (p) gl.deleteProgram(p.program);
      }
      gl.deleteBuffer(this._quad);
      gl.deleteBuffer(this._lineBuf);
      if (this._baryBuf) gl.deleteBuffer(this._baryBuf);
      this._baryBuf = null;
      this._baryCapacity = 0;
      if (this._shadow) {
        for (const t of [this._shadow.a, this._shadow.b]) {
          gl.deleteTexture(t.tex);
          gl.deleteFramebuffer(t.fb);
          if (t.depth) gl.deleteRenderbuffer(t.depth);
        }
      }
      this._shadow = null;
    }

    _uploadMesh() {
      const gl = this.gl;
      const m = this._mesh;
      const normals = computeNormals(m.positions);
      const pos = gl.createBuffer();
      gl.bindBuffer(gl.ARRAY_BUFFER, pos);
      gl.bufferData(gl.ARRAY_BUFFER, m.positions, gl.STATIC_DRAW);
      const nrm = gl.createBuffer();
      gl.bindBuffer(gl.ARRAY_BUFFER, nrm);
      gl.bufferData(gl.ARRAY_BUFFER, normals, gl.STATIC_DRAW);
      this._gpu = { pos, nrm, count: m.triangles * 3 };
      if (this._wire) this._ensureBary();
    }

    _disposeMesh() {
      if (this._gpu && this.gl) {
        this.gl.deleteBuffer(this._gpu.pos);
        this.gl.deleteBuffer(this._gpu.nrm);
      }
      this._gpu = null;
    }

    // Baryzentrische Koordinaten fürs Drahtgitter (für alle Modelle gleich, wiederverwendet)
    _ensureBary() {
      const gl = this.gl;
      if (!gl || !this._gpu || this._baryCapacity >= this._gpu.count) return;
      const verts = Math.ceil(this._gpu.count / 3) * 3;
      const data = new Uint8Array(verts * 4);
      for (let i = 0; i < verts * 4; i += 12) {
        data[i] = 255;
        data[i + 5] = 255;
        data[i + 10] = 255;
      }
      if (!this._baryBuf) this._baryBuf = gl.createBuffer();
      gl.bindBuffer(gl.ARRAY_BUFFER, this._baryBuf);
      gl.bufferData(gl.ARRAY_BUFFER, data, gl.STATIC_DRAW);
      this._baryCapacity = verts;
    }

    _useAttribs(count) {
      const gl = this.gl;
      while (this._enabledAttribs < count) gl.enableVertexAttribArray(this._enabledAttribs++);
      while (this._enabledAttribs > count) gl.disableVertexAttribArray(--this._enabledAttribs);
    }

    // Kontaktschatten neu berechnen (nur beim Laden, das Modell bewegt sich nicht)
    _bakeShadow() {
      const gl = this.gl;
      const sh = this._shadow;
      if (!gl || this._lost || !sh || !sh.ok) return;
      const sc = this._scene.shadow;
      gl.bindFramebuffer(gl.FRAMEBUFFER, sh.a.fb);
      gl.viewport(0, 0, SHADOW_SIZE, SHADOW_SIZE);
      gl.clearColor(0, 0, 0, 0);
      gl.depthMask(true);
      gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
      sh.active = false;
      if (this._gpu) {
        const m = this._mesh;
        const span = Math.max(m.max[2] - m.min[2], 1e-6);
        gl.enable(gl.DEPTH_TEST);
        gl.depthFunc(gl.LESS);
        gl.disable(gl.BLEND);
        gl.disable(gl.CULL_FACE);
        let p = this._progShadow;
        gl.useProgram(p.program);
        gl.uniform4f(p.u.uRect, sc.cx, sc.cy, sc.half, 0);
        gl.uniform3f(p.u.uZ, m.min[2], 1 / span, 1 / Math.max(sc.height, 1e-6));
        this._useAttribs(1);
        gl.bindBuffer(gl.ARRAY_BUFFER, this._gpu.pos);
        gl.vertexAttribPointer(0, 3, gl.FLOAT, false, 0, 0);
        gl.drawArrays(gl.TRIANGLES, 0, this._gpu.count);

        // Weichzeichnen: zweimal horizontal + vertikal
        gl.disable(gl.DEPTH_TEST);
        p = this._progBlur;
        gl.useProgram(p.program);
        gl.bindBuffer(gl.ARRAY_BUFFER, this._quad);
        gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 0, 0);
        gl.activeTexture(gl.TEXTURE0);
        gl.uniform1i(p.u.uTex, 0);
        const step = 1.6 / SHADOW_SIZE;
        for (let i = 0; i < 2; i++) {
          gl.bindFramebuffer(gl.FRAMEBUFFER, sh.b.fb);
          gl.bindTexture(gl.TEXTURE_2D, sh.a.tex);
          gl.uniform2f(p.u.uDir, step, 0);
          gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
          gl.bindFramebuffer(gl.FRAMEBUFFER, sh.a.fb);
          gl.bindTexture(gl.TEXTURE_2D, sh.b.tex);
          gl.uniform2f(p.u.uDir, 0, step);
          gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
        }
        sh.active = true;
      }
      gl.bindFramebuffer(gl.FRAMEBUFFER, null);
      gl.bindTexture(gl.TEXTURE_2D, null);
    }

    // ------------------------------------------------------------ Zeichnen

    _requestRender() {
      if (this._raf || this._destroyed) return;
      this._raf = requestAnimationFrame(() => {
        this._raf = 0;
        this._render();
      });
    }

    _syncSize() {
      const w = this.canvas.clientWidth;
      const h = this.canvas.clientHeight;
      const dpr = Math.min(global.devicePixelRatio || 1, MAX_DPR);
      const pw = Math.max(1, Math.round(w * dpr));
      const ph = Math.max(1, Math.round(h * dpr));
      const changed = w !== this._cssW || h !== this._cssH || dpr !== this._dpr;
      this._cssW = w;
      this._cssH = h;
      this._dpr = dpr;
      if (this.canvas.width !== pw || this.canvas.height !== ph) {
        this.canvas.width = pw;
        this.canvas.height = ph;
      }
      const gs = Math.round(84 * dpr);
      if (this._gizmo.width !== gs) {
        this._gizmo.width = gs;
        this._gizmo.height = gs;
      }
      if (changed && this._pendingFrame && w > 0 && h > 0) {
        this._anim = null;
        this._cam = this._frameState(this._pendingFrame);
      }
      return changed;
    }

    _render() {
      if (this._destroyed) return;
      this._syncSize();
      if (this._cssW < 1 || this._cssH < 1) return;
      if (this._anim) this._stepAnimation(performance.now());
      this._updateCamera();
      this._layoutDimensions();
      const gl = this.gl;
      if (gl && !this._lost) this._drawScene();
      this._drawGizmo();
      if (this._anim) this._requestRender();
    }

    _updateCamera() {
      const cam = this._cam;
      const basis = cameraBasis(cam.theta, cam.phi);
      const t = cam.target;
      const eye = [
        t[0] + basis.b[0] * cam.distance,
        t[1] + basis.b[1] * cam.distance,
        t[2] + basis.b[2] * cam.distance,
      ];
      viewMatrix(this._view, basis, eye);
      const sphere = this._scene.sphere;
      const dc = Math.hypot(eye[0] - sphere.center[0], eye[1] - sphere.center[1], eye[2] - sphere.center[2]);
      const far = dc + sphere.radius * 1.05 + 1e-3;
      const near = Math.max(dc - sphere.radius * 1.05, far / 4000, 1e-4);
      this._aspect = this._cssW / this._cssH;
      perspectiveMatrix(this._proj, FOV_Y, this._aspect, near, far);
      this._basis = basis;
      this._eye = eye;
      this._near = near;
    }

    _drawScene() {
      const gl = this.gl;
      gl.bindFramebuffer(gl.FRAMEBUFFER, null);
      gl.viewport(0, 0, this.canvas.width, this.canvas.height);
      const bg = this._bg;
      gl.clearColor(bg[0] * bg[3], bg[1] * bg[3], bg[2] * bg[3], bg[3]);
      gl.depthMask(true);
      gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
      gl.enable(gl.DEPTH_TEST);
      gl.depthFunc(gl.LEQUAL);
      gl.disable(gl.CULL_FACE);

      const hasModel = !!this._gpu;
      if (hasModel && !this._xray) this._drawModel(false);
      if (this._showGrid || this._bed) this._drawGrid();
      if (hasModel && this._xray) this._drawModel(true);
      this._drawLines();
    }

    _drawModel(xray) {
      const gl = this.gl;
      const p = this._progModel;
      const u = p.u;
      gl.useProgram(p.program);
      gl.uniformMatrix4fv(u.uView, false, this._view);
      gl.uniformMatrix4fv(u.uProj, false, this._proj);
      gl.uniform3fv(u.uColor, this._modelLin);
      gl.uniform3fv(u.uBackColor, this._backLin);
      gl.uniform3fv(u.uCapColor, this._capLin);
      gl.uniform3fv(u.uWireColor, this._wireLin);
      gl.uniform1f(u.uWire, this._wire ? 1 : 0);
      gl.uniform1f(u.uWirePx, 0.75 * this._dpr);
      gl.uniform1f(u.uXray, xray ? 1 : 0);
      gl.uniform2f(u.uClip, this._clipZ === null ? 0 : 1, this._clipZ === null ? 0 : this._clipZ);
      gl.uniform1f(u.uHatch, 1 / (7 * this._dpr));
      const k = this._cam.distance * 1.1;
      gl.uniform3f(u.uKeyPos, -0.3 * k, 0.5 * k, 0.81 * k);

      const wire = this._wire && this._baryBuf && this._baryCapacity >= this._gpu.count;
      this._useAttribs(wire ? 3 : 2);
      gl.bindBuffer(gl.ARRAY_BUFFER, this._gpu.pos);
      gl.vertexAttribPointer(0, 3, gl.FLOAT, false, 0, 0);
      gl.bindBuffer(gl.ARRAY_BUFFER, this._gpu.nrm);
      gl.vertexAttribPointer(1, 3, gl.FLOAT, false, 0, 0);
      if (wire) {
        gl.bindBuffer(gl.ARRAY_BUFFER, this._baryBuf);
        gl.vertexAttribPointer(2, 3, gl.UNSIGNED_BYTE, true, 4, 0);
      } else {
        gl.vertexAttrib3f(2, 1, 1, 1);
      }
      if (xray) {
        gl.enable(gl.BLEND);
        gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
        gl.depthMask(false);
      } else {
        gl.disable(gl.BLEND);
        gl.depthMask(true);
      }
      gl.drawArrays(gl.TRIANGLES, 0, this._gpu.count);
      gl.depthMask(true);
      gl.disable(gl.BLEND);
    }

    _drawGrid() {
      const gl = this.gl;
      const sc = this._scene;
      const g = sc.grid;
      const p = this._progGrid;
      const u = p.u;
      gl.useProgram(p.program);
      gl.uniformMatrix4fv(u.uView, false, this._view);
      gl.uniformMatrix4fv(u.uProj, false, this._proj);
      gl.uniform4f(u.uQuad, g.cx, g.cy, g.half, sc.z0);
      gl.uniform3fv(u.uFillColor, this._gridFill);
      gl.uniform3fv(u.uLineColor, this._gridLine);
      gl.uniform3fv(u.uMidColor, this._gridMid);
      gl.uniform3fv(u.uMajorColor, this._gridMajor);
      gl.uniform1f(u.uMinor, g.minor);
      gl.uniform1f(u.uPx, this._dpr);
      gl.uniform1f(u.uPxWorld, 2 * this._cam.distance * Math.tan(FOV_Y / 2) / Math.max(this.canvas.height, 1));
      gl.uniform1f(u.uGridOn, this._showGrid ? 1 : 0);
      // Von unten betrachtet das Raster abschwächen
      gl.uniform1f(u.uOpacity, this._eye[2] < sc.z0 ? 0.35 : 1);
      gl.uniform4f(u.uFade, g.cx, g.cy, g.fadeIn, g.fadeOut);
      const bed = sc.bed;
      gl.uniform1f(u.uBedOn, bed ? 1 : 0);
      if (bed) {
        gl.uniform4f(u.uBed, bed.cx, bed.cy, bed.hx, bed.hy);
        gl.uniform1f(u.uBedMargin, bed.margin);
        gl.uniform3fv(u.uBedColor, this._bedExceeded() ? this._bedOverColor : this._bedColor);
      }
      const sh = this._shadow;
      const s = sc.shadow;
      gl.uniform4f(u.uShadowRect, s.cx, s.cy, s.half, sh && sh.active ? this._shadowStrength : 0);
      gl.activeTexture(gl.TEXTURE0);
      gl.bindTexture(gl.TEXTURE_2D, sh ? sh.a.tex : null);
      gl.uniform1i(u.uShadow, 0);

      this._useAttribs(1);
      gl.bindBuffer(gl.ARRAY_BUFFER, this._quad);
      gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 0, 0);
      gl.enable(gl.BLEND);
      gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
      gl.depthMask(false);
      gl.enable(gl.POLYGON_OFFSET_FILL);
      gl.polygonOffset(1, 2); // Raster minimal hinter die Modellunterseite schieben
      gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
      gl.disable(gl.POLYGON_OFFSET_FILL);
      gl.depthMask(true);
      gl.disable(gl.BLEND);
      gl.bindTexture(gl.TEXTURE_2D, null);
    }

    // Linien: Bounding-Box mit Maßkanten, Bauraum des Druckers, Schnitthöhe
    _drawLines() {
      const gl = this.gl;
      const verts = [];
      const seg = (a, b, rgb, alpha) => {
        verts.push(a[0], a[1], a[2], rgb[0] * alpha, rgb[1] * alpha, rgb[2] * alpha, alpha,
          b[0], b[1], b[2], rgb[0] * alpha, rgb[1] * alpha, rgb[2] * alpha, alpha);
      };
      const box = (min, max, rgb, alpha, skipBottom, skip) => {
        for (let a = 0; a < 3; a++) {
          const b = (a + 1) % 3, c = (a + 2) % 3;
          for (let i = 0; i < 2; i++) {
            for (let j = 0; j < 2; j++) {
              const p = [0, 0, 0], q = [0, 0, 0];
              p[a] = min[a]; q[a] = max[a];
              p[b] = q[b] = i ? max[b] : min[b];
              p[c] = q[c] = j ? max[c] : min[c];
              if (skipBottom && a !== 2 && p[2] === min[2]) continue;
              if (skip && skip(p, q)) continue;
              seg(p, q, rgb, alpha);
            }
          }
        }
      };

      const m = this._mesh;
      const dims = this._dimLayout;
      if (m && this._showDims) {
        const same = (p, q, e) => e && p[0] === e.p0[0] && p[1] === e.p0[1] && p[2] === e.p0[2] &&
          q[0] === e.p1[0] && q[1] === e.p1[1] && q[2] === e.p1[2];
        box(m.min, m.max, this._dimLine, 0.2, false,
          (p, q) => dims && dims.some((e) => same(p, q, e)));
        if (dims) {
          for (const e of dims) {
            if (e) seg(e.p0, e.p1, this._axisRgb[e.axis], 0.95);
          }
        }
      }
      const bed = this._scene.bed;
      if (bed && bed.h > 0) {
        const z0 = this._scene.z0;
        const col = this._bedExceeded() ? this._bedOverColor : this._bedColor;
        box([bed.cx - bed.hx, bed.cy - bed.hy, z0], [bed.cx + bed.hx, bed.cy + bed.hy, z0 + bed.h], col, 0.2, true);
      }
      if (m && this._clipZ !== null && this._clipZ > m.min[2] && this._clipZ < m.max[2]) {
        const pad = Math.max(m.max[0] - m.min[0], m.max[1] - m.min[1]) * 0.04;
        const z = this._clipZ;
        const x0 = m.min[0] - pad, x1 = m.max[0] + pad, y0 = m.min[1] - pad, y1 = m.max[1] + pad;
        const col = this._bedColor;
        seg([x0, y0, z], [x1, y0, z], col, 0.9);
        seg([x1, y0, z], [x1, y1, z], col, 0.9);
        seg([x1, y1, z], [x0, y1, z], col, 0.9);
        seg([x0, y1, z], [x0, y0, z], col, 0.9);
      }
      if (!verts.length) return;

      const p = this._progLine;
      gl.useProgram(p.program);
      gl.uniformMatrix4fv(p.u.uView, false, this._view);
      gl.uniformMatrix4fv(p.u.uProj, false, this._proj);
      this._useAttribs(2);
      gl.bindBuffer(gl.ARRAY_BUFFER, this._lineBuf);
      gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(verts), gl.DYNAMIC_DRAW);
      gl.vertexAttribPointer(0, 3, gl.FLOAT, false, 28, 0);
      gl.vertexAttribPointer(1, 4, gl.FLOAT, false, 28, 12);
      gl.enable(gl.BLEND);
      gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
      gl.depthMask(false);
      gl.drawArrays(gl.LINES, 0, verts.length / 7);
      gl.depthMask(true);
      gl.disable(gl.BLEND);
    }

    // ------------------------------------------------- Maße (HTML-Beschriftung)

    _project(p) {
      const m = this._view;
      const vx = m[0] * p[0] + m[4] * p[1] + m[8] * p[2] + m[12];
      const vy = m[1] * p[0] + m[5] * p[1] + m[9] * p[2] + m[13];
      const vz = m[2] * p[0] + m[6] * p[1] + m[10] * p[2] + m[14];
      if (-vz < this._near) return null;
      const f = 1 / Math.tan(FOV_Y / 2);
      return {
        x: (0.5 + 0.5 * (f / this._aspect) * vx / -vz) * this._cssW,
        y: (0.5 - 0.5 * f * vy / -vz) * this._cssH,
      };
    }

    _measureLabels() {
      const m = this._mesh;
      if (!m) return;
      this._dimLabels.forEach((l, i) => {
        const value = formatMM(m.max[i] - m.min[i]);
        if (value !== l.value) {
          l.value = value;
          l.text.nodeValue = value;
          l.w = 0; // neu messen
        }
      });
    }

    // Wählt je Achse eine Silhouettenkante der Bounding-Box und platziert die
    // Beschriftung außerhalb der projizierten Box, damit sie das Modell nicht verdeckt.
    _pickDimEdge(axis) {
      const m = this._mesh;
      const eye = this._eye;
      // Kante zeigt (fast) auf den Betrachter → Maß wäre nicht ablesbar
      if (Math.abs(this._basis.b[axis]) > 0.92) return null;
      const b = axis === 0 ? 1 : 0;
      const c = axis === 2 ? 1 : 2;
      let best = null;
      for (let i = 0; i < 2; i++) {
        for (let j = 0; j < 2; j++) {
          const p0 = [0, 0, 0], p1 = [0, 0, 0];
          p0[axis] = m.min[axis];
          p1[axis] = m.max[axis];
          p0[b] = p1[b] = i ? m.max[b] : m.min[b];
          p0[c] = p1[c] = j ? m.max[c] : m.min[c];
          const s0 = this._project(p0), s1 = this._project(p1);
          if (!s0 || !s1) continue;
          const frontB = i ? eye[b] > m.max[b] : eye[b] < m.min[b];
          const frontC = j ? eye[c] > m.max[c] : eye[c] < m.min[c];
          const mid = { x: (s0.x + s1.x) / 2, y: (s0.y + s1.y) / 2 };
          let score = frontB !== frontC ? 0 : 1000; // Silhouettenkante bevorzugen
          if (axis < 2) score += (j === 0 ? 0 : 100) - mid.y * 0.01; // unten, weiter vorn
          else score += mid.x * 0.01; // Höhe: linke Kante
          if (!best || score < best.score) best = { axis, p0, p1, s0, s1, mid, score };
        }
      }
      if (!best) return null;
      best.len = Math.hypot(best.s1.x - best.s0.x, best.s1.y - best.s0.y);
      return best;
    }

    _layoutDimensions() {
      const show = this._showDims && this._mesh && this.gl && !this._lost;
      if (!show) {
        this._dimLayout = null;
        for (const l of this._dimLabels) l.el.hidden = true;
        return;
      }
      const m = this._mesh;
      const center = this._project([
        (m.min[0] + m.max[0]) / 2, (m.min[1] + m.max[1]) / 2, (m.min[2] + m.max[2]) / 2,
      ]);
      const edges = [0, 1, 2].map((a) => this._pickDimEdge(a));
      this._dimLayout = edges;
      const rects = [];
      edges.forEach((e, a) => {
        const l = this._dimLabels[a];
        if (!e || !center) { l.el.hidden = true; return; }
        if (l.el.hidden) l.el.hidden = false;
        if (!l.w) { l.w = l.el.offsetWidth; l.h = l.el.offsetHeight; }
        const w = l.w, h = l.h;
        // Normale der Kante auf dem Bildschirm, nach außen gerichtet (sehr kurze
        // Kanten, z. B. 1 mm hohe Platten: Höhe links, sonst unten beschriften)
        let nx = a === 2 ? -1 : 0;
        let ny = a === 2 ? 0 : 1;
        if (e.len >= 2) {
          nx = -(e.s1.y - e.s0.y) / e.len;
          ny = (e.s1.x - e.s0.x) / e.len;
          if ((e.mid.x - center.x) * nx + (e.mid.y - center.y) * ny < 0) { nx = -nx; ny = -ny; }
        }
        const reach = Math.abs(nx) * w / 2 + Math.abs(ny) * h / 2 + 10;
        const r = { x: e.mid.x + nx * reach - w / 2, y: e.mid.y + ny * reach - h / 2, w, h };
        // Überlappung mit bereits platzierten Beschriftungen auflösen
        for (let k = 0; k < 4; k++) {
          const hit = rects.find((o) => r.x < o.x + o.w + 4 && o.x < r.x + w + 4 && r.y < o.y + o.h + 4 && o.y < r.y + h + 4);
          if (!hit) break;
          r.x += nx * (h + 6);
          r.y += ny * (h + 6);
        }
        rects.push(r);
        const x = Math.round(clamp(r.x, 4, Math.max(4, this._cssW - w - 4)));
        const y = Math.round(clamp(r.y, 4, Math.max(4, this._cssH - h - 4)));
        l.el.style.transform = 'translate(' + x + 'px,' + y + 'px)';
      });
    }

    // Achsenkreuz unten links (2D-Canvas, nicht Teil des Screenshots)
    _drawGizmo() {
      const cv = this._gizmo;
      cv.hidden = !this._showAxes || !this.gl;
      if (cv.hidden) return;
      const ctx = cv.getContext('2d');
      const s = cv.width;
      const d = this._dpr;
      ctx.clearRect(0, 0, s, s);
      const c = s / 2;
      const len = s * 0.3;
      const basis = this._basis;
      const axes = [0, 1, 2].map((i) => {
        const e = [0, 0, 0];
        e[i] = 1;
        return { i, x: dot3(e, basis.r), y: -dot3(e, basis.u), z: dot3(e, basis.b) };
      });
      axes.sort((a, b) => a.z - b.z); // hintere Achsen zuerst
      ctx.lineCap = 'round';
      ctx.font = '700 ' + Math.round(9.5 * d) + 'px ui-sans-serif, system-ui, sans-serif';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      for (const a of axes) {
        const x = c + a.x * len, y = c + a.y * len;
        const fade = a.z < -0.2 ? 0.55 : 1;
        ctx.globalAlpha = fade;
        ctx.strokeStyle = AXIS_COLORS[a.i];
        ctx.lineWidth = 2 * d;
        ctx.beginPath();
        ctx.moveTo(c, c);
        ctx.lineTo(x, y);
        ctx.stroke();
        ctx.fillStyle = AXIS_COLORS[a.i];
        ctx.beginPath();
        ctx.arc(x, y, 7.5 * d, 0, Math.PI * 2);
        ctx.fill();
        ctx.fillStyle = '#fff';
        ctx.fillText(AXIS_NAMES[a.i], x, y + 0.5 * d);
      }
      ctx.globalAlpha = 1;
    }

    // ----------------------------------------------------------- Meldungen

    _showMessage(text, spinner) {
      const o = this._overlay;
      o.textContent = '';
      const box = document.createElement('div');
      box.className = 'stlv-msg';
      if (spinner) {
        const sp = document.createElement('span');
        sp.className = 'stlv-spin';
        box.appendChild(sp);
      }
      box.appendChild(document.createTextNode(text));
      o.appendChild(box);
      o.hidden = false;
    }

    _hideMessage() {
      if (this._fatal) this._showMessage(this._fatal, false);
      else this._overlay.hidden = true;
    }

    _setLoading(on) {
      clearTimeout(this._loadingTimer);
      this._loadingTimer = 0;
      if (on) {
        this._loadingTimer = setTimeout(() => {
          if (!this._fatal) this._showMessage('Lade 3D-Modell …', true);
        }, LOADING_DELAY_MS);
      } else {
        this._hideMessage();
      }
    }

    // ----------------------------------------------------------- Steuerung

    _orbit(dx, dy) {
      const c = this._cam;
      c.theta = wrapAngle(c.theta - dx * ORBIT_SPEED);
      c.phi = clamp(c.phi + dy * ORBIT_SPEED, -90 * DEG, 90 * DEG);
      this._requestRender();
    }

    _pan(dx, dy) {
      const c = this._cam;
      const basis = cameraBasis(c.theta, c.phi);
      const perPx = 2 * c.distance * Math.tan(FOV_Y / 2) / Math.max(this._cssH, 1);
      for (let i = 0; i < 3; i++) {
        c.target[i] += (-basis.r[i] * dx + basis.u[i] * dy) * perPx;
      }
      this._requestRender();
    }

    // Zoom zum Mauszeiger: Punkt unter dem Cursor bleibt stehen.
    _zoomAt(factor, clientX, clientY) {
      const c = this._cam;
      const sc = this._scene;
      const next = clamp(c.distance * factor, sc.minDistance, sc.maxDistance);
      const k = next / c.distance;
      if (clientX != null && this._cssW > 0) {
        const rect = this.canvas.getBoundingClientRect();
        const nx = ((clientX - rect.left) / rect.width) * 2 - 1;
        const ny = 1 - ((clientY - rect.top) / rect.height) * 2;
        const tanV = Math.tan(FOV_Y / 2);
        const basis = cameraBasis(c.theta, c.phi);
        const offR = nx * tanV * (rect.width / rect.height) * c.distance;
        const offU = ny * tanV * c.distance;
        for (let i = 0; i < 3; i++) {
          c.target[i] += (basis.r[i] * offR + basis.u[i] * offU) * (1 - k);
        }
      }
      c.distance = next;
      this._requestRender();
    }

    _bindEvents() {
      const cv = this.canvas;
      this._handlers = {
        pointerdown: (e) => this._onPointerDown(e),
        pointermove: (e) => this._onPointerMove(e),
        pointerup: (e) => this._onPointerUp(e),
        pointercancel: (e) => this._onPointerUp(e),
        wheel: (e) => this._onWheel(e),
        dblclick: (e) => { e.preventDefault(); this.resetView(); },
        contextmenu: (e) => e.preventDefault(),
        mousedown: (e) => { if (e.button === 1) e.preventDefault(); }, // kein Auto-Scroll
        webglcontextlost: (e) => {
          e.preventDefault();
          this._lost = true;
          this._gpu = null;
        },
        webglcontextrestored: () => this._restoreContext(),
      };
      for (const [type, fn] of Object.entries(this._handlers)) {
        cv.addEventListener(type, fn, type === 'wheel' ? { passive: false } : false);
      }
      if (typeof ResizeObserver === 'function') {
        this._ro = new ResizeObserver(() => {
          if (this._syncSize()) this._render(); // sofort zeichnen, sonst flackert es
        });
        this._ro.observe(this.container);
      }
      this._onWindowResize = () => { if (this._syncSize()) this._requestRender(); };
      global.addEventListener('resize', this._onWindowResize);
    }

    _unbindEvents() {
      const cv = this.canvas;
      if (this._handlers) {
        for (const [type, fn] of Object.entries(this._handlers)) cv.removeEventListener(type, fn);
      }
      if (this._ro) this._ro.disconnect();
      global.removeEventListener('resize', this._onWindowResize);
    }

    _restoreContext() {
      if (this._destroyed) return;
      try {
        this._lost = false;
        this._initGL();
        if (this._mesh) {
          this._uploadMesh();
          this._bakeShadow();
        }
        this._requestRender();
      } catch (err) {
        console.error(err);
      }
    }

    _onPointerDown(e) {
      if (e.pointerType === 'mouse' && e.button > 2) return;
      e.preventDefault();
      try { this.canvas.setPointerCapture(e.pointerId); } catch { /* egal */ }
      this._stopAnimation();
      this._pendingFrame = null;
      this._pointers.set(e.pointerId, { x: e.clientX, y: e.clientY, sx: e.clientX, sy: e.clientY });
      if (this._pointers.size === 1) {
        const pan = e.pointerType === 'mouse' &&
          (e.button === 1 || e.button === 2 || e.shiftKey || e.ctrlKey || e.metaKey);
        this._drag = { mode: pan ? 'pan' : 'orbit', moved: 0, type: e.pointerType };
        this.canvas.classList.add(pan ? 'stlv-panning' : 'stlv-orbiting');
      } else if (this._pointers.size === 2) {
        this._drag = { mode: 'pinch', moved: 99, type: e.pointerType };
        this._pinch = null;
      }
    }

    _onPointerMove(e) {
      const p = this._pointers.get(e.pointerId);
      if (!p || !this._drag) return;
      const dx = e.clientX - p.x;
      const dy = e.clientY - p.y;
      p.x = e.clientX;
      p.y = e.clientY;
      const d = this._drag;
      d.moved += Math.abs(dx) + Math.abs(dy);
      if (d.mode === 'pinch') {
        if (this._pointers.size < 2) return;
        const [a, b] = Array.from(this._pointers.values());
        const dist = Math.hypot(a.x - b.x, a.y - b.y);
        const cx = (a.x + b.x) / 2, cy = (a.y + b.y) / 2;
        if (this._pinch) {
          if (dist > 0 && this._pinch.dist > 0) this._zoomAt(this._pinch.dist / dist, cx, cy);
          this._pan(cx - this._pinch.cx, cy - this._pinch.cy);
        }
        this._pinch = { dist, cx, cy };
      } else if (d.mode === 'pan' || (e.shiftKey && d.type === 'mouse')) {
        this._pan(dx, dy);
      } else {
        this._orbit(dx, dy);
      }
    }

    _onPointerUp(e) {
      if (!this._pointers.has(e.pointerId)) return;
      this._pointers.delete(e.pointerId);
      try { this.canvas.releasePointerCapture(e.pointerId); } catch { /* egal */ }
      const d = this._drag;
      if (this._pointers.size === 1) {
        // Vom Zwei-Finger-Gestus zurück zum Drehen, ohne Sprung
        this._drag = { mode: 'orbit', moved: 99, type: d ? d.type : 'touch' };
        this._pinch = null;
        return;
      }
      if (this._pointers.size === 0) {
        this.canvas.classList.remove('stlv-orbiting', 'stlv-panning');
        // Doppeltippen auf Touch-Geräten → Ansicht zurücksetzen
        if (d && d.type === 'touch' && d.moved < 10 && e.type === 'pointerup') {
          const now = performance.now();
          const t = this._lastTap;
          if (t && now - t.time < 320 && Math.hypot(e.clientX - t.x, e.clientY - t.y) < 30) {
            this._lastTap = null;
            this.resetView();
          } else {
            this._lastTap = { time: now, x: e.clientX, y: e.clientY };
          }
        }
        this._drag = null;
        this._pinch = null;
      }
    }

    _onWheel(e) {
      e.preventDefault();
      this._stopAnimation();
      this._pendingFrame = null;
      let dy = e.deltaY;
      if (e.deltaMode === 1) dy *= 16;
      else if (e.deltaMode === 2) dy *= 400;
      // Pinch-Geste auf Trackpads kommt als Wheel mit ctrlKey und kleinen Werten
      const factor = Math.exp(clamp(dy, -300, 300) * (e.ctrlKey ? 0.01 : 0.0015));
      this._zoomAt(factor, e.clientX, e.clientY);
    }
  }

  // Parser auch einzeln nutzbar, z. B. für Tests oder Metadaten ohne Anzeige
  STLViewer.parse = function (buffer) {
    const m = parseSTL(buffer);
    return {
      triangles: m.triangles,
      min: m.min.slice(),
      max: m.max.slice(),
      size: [0, 1, 2].map((i) => m.max[i] - m.min[i]),
      volume: Math.abs(m.volume),
      format: m.format,
      positions: m.positions,
    };
  };

  global.STLViewer = STLViewer;
})(window);
