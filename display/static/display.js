'use strict';

// ═══════════════════════════════════════════════════════════════════
// THE ONE HTML-ESCAPE HELPER
// ═══════════════════════════════════════════════════════════════════
// Every value that reaches innerHTML from the network (character names, race,
// class, conditions, spell names, dice labels, UI-manifest strings) passes
// through esc() here. There used to be three helpers in this file (_escHtml,
// _esc, and a local copy inside _renderMarkdown) that disagreed about quotes,
// and two of them were used inside HTML attributes, where an unescaped quote
// is an injection. One helper, one set of characters, no per-callsite choices.
//
// Server-side sanitization for labels and character names strips shell
// metacharacters (` $ \) but does NOT strip < > &, so escaping here is the
// only thing standing between an LLM-chosen name and script in the DM's
// browser, which holds the LAN token in <meta name="dnd-token">.
//
// display/static/tactics.js uses this same function (it is a global, because
// this file is a classic script and tactics.js is loaded after it).
function esc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

// ═══════════════════════════════════════════════════════════════════
// SCENE MANAGEMENT
// ═══════════════════════════════════════════════════════════════════

const SCENE_DEFAULTS = {
  colors: ['#1a0800', '#2e1400'],
  accent: '#c8601a',
  particles: 'embers',
  label: 'The Inn',
  name: 'tavern',
};

let activeScene = { ...SCENE_DEFAULTS };
let bgActive = 'a'; // which bg layer is currently showing

function applyScene(scene) {
  if (!scene || scene.name === activeScene.name) return;
  activeScene = { ...SCENE_DEFAULTS, ...scene };

  // Update scene indicator with a fade
  const indicator = document.getElementById('scene-indicator');
  indicator.classList.add('changing');
  setTimeout(() => {
    indicator.textContent = '— ' + (activeScene.label || activeScene.name) + ' —';
    indicator.classList.remove('changing');
  }, 400);

  // Crossfade background layers
  const incoming = bgActive === 'a' ? 'b' : 'a';
  const inEl  = document.getElementById('bg-' + incoming);
  const outEl = document.getElementById('bg-' + bgActive);

  const [top, bot] = activeScene.colors;
  inEl.style.background =
    `radial-gradient(ellipse at 50% 30%, ${top} 0%, ${bot} 100%)`;
  inEl.style.opacity = '1';
  outEl.style.opacity = '0';
  bgActive = incoming;

  // Switch particle type
  particleSystem.switchType(activeScene.particles, activeScene.accent);
  // no audio hook on scene change — ambient removed
}

// Initialise background to default scene
;(function initBg() {
  const el = document.getElementById('bg-' + bgActive);
  const [top, bot] = SCENE_DEFAULTS.colors;
  el.style.background =
    `radial-gradient(ellipse at 50% 30%, ${top} 0%, ${bot} 100%)`;
})();


// ═══════════════════════════════════════════════════════════════════
// PARTICLE SYSTEM
// ═══════════════════════════════════════════════════════════════════

const canvas  = document.getElementById('particles');
const ctx     = canvas.getContext('2d');
let W = 0, H = 0;

function resizeCanvas() {
  const dpr = window.devicePixelRatio || 1;
  W = window.innerWidth;
  H = window.innerHeight;
  canvas.width  = W * dpr;
  canvas.height = H * dpr;
  canvas.style.width  = W + 'px';
  canvas.style.height = H + 'px';
  ctx.scale(dpr, dpr);
}
window.addEventListener('resize', resizeCanvas);
resizeCanvas();

// ── Particle classes ──────────────────────────────────────────────

function rand(a, b) { return a + Math.random() * (b - a); }
function randInt(a, b) { return Math.floor(rand(a, b + 1)); }
function hexToRgb(hex) {
  const r = parseInt(hex.slice(1,3),16);
  const g = parseInt(hex.slice(3,5),16);
  const b = parseInt(hex.slice(5,7),16);
  return [r,g,b];
}

class Particle {
  constructor(type, accent) {
    this.type   = type;
    this.accent = accent;
    this.reset();
  }
  reset() {
    const T = this.type;
    this.x    = rand(0, W);
    this.y    = rand(0, H);
    this.life = 0;
    this.maxLife = 1;
    this.size = 2;
    this.alpha = 0;
    this.vx = 0; this.vy = 0;
    this._phase = rand(0, Math.PI * 2);

    if (T === 'embers') {
      this.x = rand(0, W);
      this.y = rand(H * 0.6, H);
      this.vx = rand(-0.4, 0.4);
      this.vy = rand(-0.8, -0.3);
      this.size = rand(1, 3);
      this.maxLife = rand(120, 300);
      this._color = this.accent;
    } else if (T === 'fireflies') {
      this.x = rand(0, W);
      this.y = rand(H * 0.2, H * 0.9);
      this.vx = rand(-0.3, 0.3);
      this.vy = rand(-0.2, 0.2);
      this.size = rand(1.5, 3.5);
      this.maxLife = rand(200, 500);
      this._color = '#a0ff60';
    } else if (T === 'smoke') {
      this.x = rand(0, W);
      this.y = H + 10;
      this.vx = rand(-0.2, 0.2);
      this.vy = rand(-0.4, -0.15);
      this.size = rand(6, 18);
      this.maxLife = rand(180, 350);
      this._color = '#404040';
    } else if (T === 'dust') {
      this.x = rand(0, W);
      this.y = rand(0, H);
      this.vx = rand(-0.1, 0.1);
      this.vy = rand(-0.05, 0.08);
      this.size = rand(0.5, 1.8);
      this.maxLife = rand(300, 600);
      this._color = '#a09060';
    } else if (T === 'snow') {
      this.x = rand(-10, W + 10);
      this.y = rand(-10, 0);
      this.vx = rand(-0.3, 0.3);
      this.vy = rand(0.4, 1.2);
      this.size = rand(1, 3.5);
      this.maxLife = rand(200, 450);
      this._color = '#d8e8ff';
    } else if (T === 'stars') {
      this.x = rand(0, W);
      this.y = rand(0, H * 0.8);
      this.vx = 0; this.vy = 0;
      this.size = rand(0.5, 2);
      this.maxLife = rand(400, 900);
      this._color = '#c0d0ff';
    } else if (T === 'bubbles') {
      this.x = rand(0, W);
      this.y = H + 10;
      this.vx = rand(-0.2, 0.2);
      this.vy = rand(-0.5, -0.2);
      this.size = rand(2, 8);
      this.maxLife = rand(200, 400);
      this._color = '#60a0d0';
    } else if (T === 'sparks') {
      this.x = rand(0, W);
      this.y = rand(H * 0.4, H);
      this.vx = rand(-1.5, 1.5);
      this.vy = rand(-2, -0.5);
      this.size = rand(0.8, 2.5);
      this.maxLife = rand(40, 100);
      this._color = '#ffe060';
    } else if (T === 'sand') {
      this.x = rand(-20, 0);
      this.y = rand(0, H);
      this.vx = rand(1.2, 2.8);
      this.vy = rand(-0.2, 0.4);
      this.size = rand(0.8, 2.2);
      this.maxLife = rand(150, 350);
      this._color = '#c0a060';
    } else if (T === 'rain') {
      this.x = rand(-20, W);
      this.y = rand(-20, 0);
      this.vx = rand(0.5, 1.2);
      this.vy = rand(6, 14);
      this.size = rand(1, 2);
      this.maxLife = rand(60, 120);
      this._color = '#8090b0';
    } else if (T === 'drips') {
      this.x = rand(0, W);
      this.y = rand(-20, 0);
      this.vx = 0;
      this.vy = rand(1.5, 4);
      this.size = rand(1.5, 3.5);
      this.maxLife = rand(80, 180);
      this._color = '#6090b0';
    } else if (T === 'mist') {
      this.x = rand(-120, W + 120);
      this.y = rand(H * 0.05, H * 0.85);
      this.vx = rand(0.04, 0.18);
      this.vy = rand(-0.015, 0.015);
      this.size = rand(70, 180);
      this.maxLife = rand(700, 1400);
      this._color = '#7888a0';
    } else if (T === 'leaves') {
      this.x = rand(0, W);
      this.y = rand(-20, 0);
      this.vx = rand(-0.7, 0.7);
      this.vy = rand(0.5, 1.6);
      this.size = rand(4, 9);
      this.maxLife = rand(220, 520);
      this._rot = rand(0, Math.PI * 2);
      this._rotSpeed = rand(-0.045, 0.045);
      const lc = ['#8B4513','#D2691E','#c8a020','#CD853F','#a06828'];
      this._color = lc[randInt(0, lc.length - 1)];
    } else if (T === 'ripples') {
      this.x = rand(0, W);
      this.y = rand(H * 0.55, H);
      this.vx = 0; this.vy = 0;
      this.size = rand(6, 22);
      this.maxLife = rand(100, 220);
      this._color = '#4888c0';
    } else {
      // fallback = dust
      this.vx = 0; this.vy = 0;
      this.size = 1;
      this.maxLife = 300;
      this._color = '#808080';
    }
  }

  update() {
    this.life++;
    const T = this.type;
    const progress = this.life / this.maxLife;

    // Alpha envelope: fade in first 10%, full mid, fade out last 20%
    if (progress < 0.1)       this.alpha = progress / 0.1;
    else if (progress > 0.8)  this.alpha = (1 - progress) / 0.2;
    else                      this.alpha = 1;

    this.x += this.vx;
    this.y += this.vy;

    if (T === 'embers') {
      this.vx += rand(-0.05, 0.05);
      this.vx  = Math.max(-0.6, Math.min(0.6, this.vx));
      if (this.life % 20 === 0) this.alpha *= rand(0.5, 1.0);
    } else if (T === 'fireflies') {
      this._phase += 0.03;
      this.x += Math.sin(this._phase) * 0.5;
      this.y += Math.cos(this._phase * 0.7) * 0.3;
      this.alpha *= (0.85 + 0.15 * Math.sin(this._phase * 2));
    } else if (T === 'smoke') {
      this.size += 0.04;
      this.vx  += rand(-0.01, 0.01);
    } else if (T === 'stars') {
      this._phase += 0.02;
      this.alpha *= (0.85 + 0.15 * Math.sin(this._phase));
    } else if (T === 'sparks') {
      this.vy += 0.08; // gravity
    } else if (T === 'leaves') {
      this._rot += this._rotSpeed;
      this.vx += rand(-0.025, 0.025);
      this.vx = Math.max(-1.2, Math.min(1.2, this.vx));
    } else if (T === 'ripples') {
      this.size += 0.55;
    }

    return this.life < this.maxLife;
  }

  draw(ctx) {
    const T = this.type;
    const [r,g,b] = hexToRgb(this._color);
    ctx.globalAlpha = Math.max(0, Math.min(1, this.alpha));

    if (T === 'smoke') {
      const grad = ctx.createRadialGradient(
        this.x, this.y, 0, this.x, this.y, this.size);
      grad.addColorStop(0, `rgba(${r},${g},${b},0.3)`);
      grad.addColorStop(1, `rgba(${r},${g},${b},0)`);
      ctx.fillStyle = grad;
      ctx.beginPath();
      ctx.arc(this.x, this.y, this.size, 0, Math.PI * 2);
      ctx.fill();
    } else if (T === 'embers' || T === 'fireflies' || T === 'sparks') {
      // Glow effect
      const glow = ctx.createRadialGradient(
        this.x, this.y, 0, this.x, this.y, this.size * 3);
      glow.addColorStop(0, `rgba(${r},${g},${b},0.6)`);
      glow.addColorStop(1, `rgba(${r},${g},${b},0)`);
      ctx.fillStyle = glow;
      ctx.beginPath();
      ctx.arc(this.x, this.y, this.size * 3, 0, Math.PI * 2);
      ctx.fill();
      // Core dot
      ctx.fillStyle = `rgb(${Math.min(255,r+60)},${Math.min(255,g+40)},${Math.min(255,b+20)})`;
      ctx.beginPath();
      ctx.arc(this.x, this.y, this.size * 0.6, 0, Math.PI * 2);
      ctx.fill();
    } else if (T === 'bubbles') {
      ctx.strokeStyle = `rgba(${r},${g},${b},0.6)`;
      ctx.lineWidth = 0.8;
      ctx.beginPath();
      ctx.arc(this.x, this.y, this.size, 0, Math.PI * 2);
      ctx.stroke();
    } else if (T === 'rain') {
      ctx.strokeStyle = `rgba(${r},${g},${b},0.5)`;
      ctx.lineWidth = 0.8;
      ctx.beginPath();
      ctx.moveTo(this.x, this.y);
      ctx.lineTo(this.x + this.vx * 3, this.y + this.vy * 3);
      ctx.stroke();
    } else if (T === 'drips') {
      ctx.fillStyle = `rgba(${r},${g},${b},0.7)`;
      ctx.beginPath();
      ctx.ellipse(this.x, this.y, this.size * 0.4, this.size, 0, 0, Math.PI * 2);
      ctx.fill();
    } else if (T === 'mist') {
      const mg = ctx.createRadialGradient(this.x, this.y, 0, this.x, this.y, this.size);
      mg.addColorStop(0, `rgba(${r},${g},${b},0.055)`);
      mg.addColorStop(1, `rgba(${r},${g},${b},0)`);
      ctx.fillStyle = mg;
      ctx.beginPath();
      ctx.ellipse(this.x, this.y, this.size, this.size * 0.38, 0, 0, Math.PI * 2);
      ctx.fill();
    } else if (T === 'leaves') {
      ctx.save();
      ctx.translate(this.x, this.y);
      ctx.rotate(this._rot);
      ctx.fillStyle = `rgba(${r},${g},${b},0.88)`;
      ctx.beginPath();
      ctx.ellipse(0, 0, this.size * 0.42, this.size, 0, 0, Math.PI * 2);
      ctx.fill();
      ctx.restore();
    } else if (T === 'ripples') {
      ctx.strokeStyle = `rgba(${r},${g},${b},0.28)`;
      ctx.lineWidth = 0.9;
      ctx.beginPath();
      ctx.ellipse(this.x, this.y, this.size, this.size * 0.28, 0, 0, Math.PI * 2);
      ctx.stroke();
    } else {
      ctx.fillStyle = `rgba(${r},${g},${b},1)`;
      ctx.beginPath();
      ctx.arc(this.x, this.y, this.size, 0, Math.PI * 2);
      ctx.fill();
    }

    ctx.globalAlpha = 1;
  }
}

// ── Particle system controller ────────────────────────────────────

const PARTICLE_COUNT = {
  embers: 80, fireflies: 45, smoke: 20, dust: 90,
  snow: 110, stars: 160, bubbles: 45, sparks: 50,
  sand: 80, rain: 130, drips: 35,
  mist: 12, leaves: 55, ripples: 18,
};

const particleSystem = {
  particles: [],
  type: 'embers',
  accent: SCENE_DEFAULTS.accent,
  transitioning: false,

  init() {
    this.populate(this.type, this.accent);
  },

  populate(type, accent) {
    const count = PARTICLE_COUNT[type] || 60;
    this.particles = [];
    for (let i = 0; i < count; i++) {
      const p = new Particle(type, accent);
      p.life = randInt(0, p.maxLife);  // stagger so they don't all start together
      this.particles.push(p);
    }
  },

  switchType(newType, accent) {
    if (newType === this.type) return;
    this.type   = newType;
    this.accent = accent || '#ffffff';
    // Fade out current by letting them die, then repopulate
    // Mark old particles so they run out fast
    this.particles.forEach(p => { p.life = Math.max(p.life, p.maxLife - 30); });
    // Add new particles over ~2s
    let added = 0;
    const target = PARTICLE_COUNT[newType] || 60;
    const interval = setInterval(() => {
      if (added >= target) { clearInterval(interval); return; }
      const p = new Particle(newType, this.accent);
      this.particles.push(p);
      added++;
    }, 2000 / target);
  },

  update() {
    this.particles = this.particles.filter(p => p.update());
    // Replenish
    const target = PARTICLE_COUNT[this.type] || 60;
    while (this.particles.length < target) {
      const p = new Particle(this.type, this.accent);
      this.particles.push(p);
    }
  },

  draw() {
    ctx.clearRect(0, 0, W, H);
    this.particles.forEach(p => p.draw(ctx));
  },
};

particleSystem.init();

// ── Sky Renderer ──────────────────────────────────────────────────

const skyCanvas = document.getElementById('sky');
const skyCtx    = skyCanvas.getContext('2d');
let SW = 0, SH = 0;

function resizeSky() {
  const dpr = window.devicePixelRatio || 1;
  SW = window.innerWidth;
  SH = Math.round(window.innerHeight * 0.42);
  skyCanvas.width  = SW * dpr;
  skyCanvas.height = SH * dpr;
  skyCanvas.style.width  = SW + 'px';
  skyCanvas.style.height = SH + 'px';
  skyCtx.setTransform(dpr, 0, 0, dpr, 0, 0);
}
window.addEventListener('resize', resizeSky);
resizeSky();

const skyRenderer = (() => {
  let _time = 'morning', _weather = '';

  // Pre-computed star positions (twinkling dots, night only)
  const STARS = Array.from({length: 130}, () => ({
    x: Math.random(), y: Math.random(),
    r: 0.4 + Math.random() * 1.6,
    ph: Math.random() * Math.PI * 2,
    spd: 0.008 + Math.random() * 0.022,
  }));

  // Cloud objects — drift left, wrap to right
  const CLOUD_BLOBS = [[0,0,30],[-25,10,23],[23,8,25],[-9,-12,19],[13,-9,17],[36,14,17],[-36,14,15],[6,16,13]];
  const CLOUDS = Array.from({length: 5}, (_, i) => ({
    x: (i / 5) * (SW + 600) - 100,
    y: 0, sc: 1, spd: 0.1,
  }));
  function resetCloud(c, offscreen) {
    c.x   = offscreen ? SW + rand(100, 350) : rand(-200, SW + 200);
    c.y   = rand(SH * 0.06, SH * 0.68);
    c.sc  = rand(0.65, 1.75);
    c.spd = rand(0.07, 0.22);
  }
  CLOUDS.forEach(c => resetCloud(c, false));

  function drawCloud(c, opacity, color) {
    skyCtx.globalAlpha = opacity;
    skyCtx.fillStyle = color;
    CLOUD_BLOBS.forEach(([dx, dy, r]) => {
      skyCtx.beginPath();
      skyCtx.arc(c.x + dx * c.sc, c.y + dy * c.sc, r * c.sc, 0, Math.PI * 2);
      skyCtx.fill();
    });
    skyCtx.globalAlpha = 1;
  }

  function glow(x, y, r0, r1, c0, c1) {
    const g = skyCtx.createRadialGradient(x, y, r0, x, y, r1);
    g.addColorStop(0, c0); g.addColorStop(1, c1);
    skyCtx.fillStyle = g;
    skyCtx.beginPath();
    skyCtx.arc(x, y, r1, 0, Math.PI * 2);
    skyCtx.fill();
  }

  return {
    setState(wt) {
      if (!wt) return;
      _time    = (wt.time    || 'morning').toLowerCase();
      _weather = (wt.weather || '').toLowerCase();
    },

    draw() {
      skyCtx.clearRect(0, 0, SW, SH);

      const isNight  = /night|midnight|late/.test(_time);
      const isDusk   = /evening|dusk/.test(_time);
      const isDawn   = /dawn/.test(_time);
      const isRain   = /rain|storm/.test(_weather);
      const isCloudy = /cloud|overcast/.test(_weather) || isRain;
      const isFog    = /fog|mist/.test(_weather);

      // Stars (night / dusk)
      if (isNight || isDusk) {
        const mod = isNight ? 1 : 0.35;
        STARS.forEach(s => {
          s.ph += s.spd;
          const a = (0.25 + 0.55 * Math.sin(s.ph)) * mod * (isRain ? 0.15 : 1);
          if (a <= 0.01) return;
          skyCtx.globalAlpha = a;
          skyCtx.fillStyle = '#c8d4ff';
          skyCtx.beginPath();
          skyCtx.arc(s.x * SW, s.y * SH, s.r, 0, Math.PI * 2);
          skyCtx.fill();
        });
        skyCtx.globalAlpha = 1;
      }

      // Moon (night / dusk)
      if (isNight || isDusk) {
        const mx = SW * 0.76, my = SH * 0.24;
        const ma = isNight ? 0.82 : 0.32;
        glow(mx, my, 10, 72, `rgba(190,200,230,${(ma * 0.18).toFixed(2)})`, 'rgba(190,200,230,0)');
        skyCtx.globalAlpha = ma;
        skyCtx.fillStyle = '#d8e0f4';
        skyCtx.beginPath(); skyCtx.arc(mx, my, 15, 0, Math.PI * 2); skyCtx.fill();
        // Crescent: paint shadow circle using clip
        skyCtx.save();
        skyCtx.beginPath(); skyCtx.arc(mx, my, 15, 0, Math.PI * 2); skyCtx.clip();
        skyCtx.fillStyle = 'rgba(8,12,28,0.82)';
        skyCtx.beginPath(); skyCtx.arc(mx + 9, my - 4, 13, 0, Math.PI * 2); skyCtx.fill();
        skyCtx.restore();
        skyCtx.globalAlpha = 1;
      }

      // Sun (day / dusk / dawn)
      if (!isNight) {
        let sx, sy, inner, outer;
        if (isDawn) {
          [sx, sy] = [SW * 0.12, SH * 0.90];
          [inner, outer] = ['rgba(255,155,55,0.72)', 'rgba(255,100,20,0)'];
        } else if (/morning/.test(_time)) {
          [sx, sy] = [SW * 0.18, SH * 0.38];
          [inner, outer] = ['rgba(255,225,130,0.62)', 'rgba(255,200,80,0)'];
        } else if (/midday|noon/.test(_time)) {
          [sx, sy] = [SW * 0.50, SH * 0.06];
          [inner, outer] = ['rgba(255,248,190,0.44)', 'rgba(255,245,160,0)'];
        } else if (/afternoon/.test(_time)) {
          [sx, sy] = [SW * 0.80, SH * 0.32];
          [inner, outer] = ['rgba(255,215,100,0.55)', 'rgba(255,185,60,0)'];
        } else { // evening / dusk
          [sx, sy] = [SW * 0.88, SH * 0.84];
          [inner, outer] = ['rgba(245,110,28,0.75)', 'rgba(210,65,0,0)'];
        }
        glow(sx, sy, 4, 95, inner, outer);
        glow(sx, sy, 0, 26, inner.replace(/[\d.]+\)$/, '1)'), inner.replace(/[\d.]+\)$/, '0)'));
      }

      // Clouds
      const cloudAlpha = isRain ? 0.40 : isCloudy ? 0.22 : isFog ? 0.14 : 0.08;
      const cloudCount = isRain ? 5  : isCloudy ? 4 : 2;
      const cloudColor = isNight ? '#303248' : isDusk ? '#a05828' : isRain ? '#3a3c4c' : '#c4c8d8';
      const windMult   = isRain ? 2.2 : 1;

      CLOUDS.forEach((c, i) => {
        if (i >= cloudCount) return;
        c.x -= c.spd * windMult;
        if (c.x < -300 * c.sc) resetCloud(c, true);
        const ao = cloudAlpha * (0.7 + 0.06 * i);
        drawCloud(c, ao, cloudColor);
      });

      // Bottom gradient fade — sky dissolves into scene background
      skyCtx.globalCompositeOperation = 'destination-out';
      const fade = skyCtx.createLinearGradient(0, SH * 0.52, 0, SH);
      fade.addColorStop(0, 'rgba(0,0,0,0)');
      fade.addColorStop(1, 'rgba(0,0,0,1)');
      skyCtx.fillStyle = fade;
      skyCtx.fillRect(0, SH * 0.52, SW, SH * 0.48);
      skyCtx.globalCompositeOperation = 'source-over';
    },
  };
})();

// ── Animation loop ────────────────────────────────────────────────

// Honour prefers-reduced-motion (paint a still frame, refresh slowly) and stop
// the loop entirely while the tab is hidden; visibilitychange restarts it.
const _reducedMotionMQ = window.matchMedia ? window.matchMedia('(prefers-reduced-motion: reduce)') : null;
let _loopRaf = 0, _loopTimer = 0;
function loop() {
  _loopRaf = 0;
  if (document.hidden) return;
  const reduced = !!(_reducedMotionMQ && _reducedMotionMQ.matches);
  if (!reduced) particleSystem.update();
  particleSystem.draw();
  skyRenderer.draw();
  if (reduced) {
    clearTimeout(_loopTimer);
    _loopTimer = setTimeout(_kickLoop, 5000);
  } else {
    _loopRaf = requestAnimationFrame(loop);
  }
}
function _kickLoop() {
  clearTimeout(_loopTimer);
  if (!_loopRaf && !document.hidden) _loopRaf = requestAnimationFrame(loop);
}
document.addEventListener('visibilitychange', _kickLoop);
if (_reducedMotionMQ && _reducedMotionMQ.addEventListener) _reducedMotionMQ.addEventListener('change', _kickLoop);
_kickLoop();


// ═══════════════════════════════════════════════════════════════════
// TYPEWRITER / TEXT RENDERING
// ═══════════════════════════════════════════════════════════════════

const textScroll  = document.getElementById('text-scroll');
const textContent = document.getElementById('text-content');

let charQueue       = [];
let isTyping        = false;
let currentEl       = null;
let currentCursor   = null;
let _currentInlineEl = null;  // tracks open <em>/<strong> during typewriter
let lastChunkTime   = Date.now();
let idleTimer       = null;

// ── Markdown segment parser ───────────────────────────────────────────────────
// Converts a text string into typed queue items for the typewriter.
// Items are plain chars, '\n' strings, {open/close tag} objects, or {table} objects.

// Parse inline markdown (bold, italic) from a single line into items array.
function _mdParseInline(text, items) {
  let i = 0;
  while (i < text.length) {
    // Inline code (`text`) -- literal, no nested emphasis
    if (text[i] === '`') {
      const end = text.indexOf('`', i + 1);
      if (end === -1 || end === i + 1) { items.push(text[i]); i++; continue; }
      items.push({open: 'code'});
      for (let j = i + 1; j < end; j++) items.push(text[j]);
      items.push({close: 'code'});
      i = end + 1; continue;
    }
    // Bold (**text**) — must check before single *
    if (text[i] === '*' && text[i + 1] === '*') {
      const end = text.indexOf('**', i + 2);
      if (end === -1) { items.push(text[i]); i++; continue; }
      items.push({open: 'strong'});
      for (let j = i + 2; j < end; j++) items.push(text[j]);
      items.push({close: 'strong'});
      i = end + 2; continue;
    }
    // Italic (*text*)
    if (text[i] === '*') {
      const end = text.indexOf('*', i + 1);
      if (end === -1) { items.push(text[i]); i++; continue; }
      items.push({open: 'em'});
      for (let j = i + 1; j < end; j++) items.push(text[j]);
      items.push({close: 'em'});
      i = end + 1; continue;
    }
    items.push(text[i]); i++;
  }
}

// Split a markdown table row "| a | b | c |" into ["a","b","c"].
function _splitTableRow(line) {
  let s = line.trim();
  if (s.startsWith('|')) s = s.slice(1);
  if (s.endsWith('|'))   s = s.slice(0, -1);
  return s.split('|').map(c => c.trim());
}

// Detect a markdown table block starting at lines[idx].
// Returns {headers, rows, end} or null.
function _detectTable(lines, idx) {
  const header = lines[idx];
  const sep    = lines[idx + 1];
  if (!header || !sep) return null;
  if (!/^\s*\|.*\|\s*$/.test(header)) return null;
  if (!/^\s*\|?\s*:?-{2,}:?(\s*\|\s*:?-{2,}:?)+\s*\|?\s*$/.test(sep)) return null;
  const headerCells = _splitTableRow(header);
  const rows = [];
  let j = idx + 2;
  while (j < lines.length && /^\s*\|.*\|\s*$/.test(lines[j])) {
    rows.push(_splitTableRow(lines[j]));
    j++;
  }
  return { headers: headerCells, rows, end: j };
}

// Build a <table> DOM node from a parsed table segment.
function _buildTableNode(tbl) {
  const t = document.createElement('table');
  t.className = 'dm-table';
  if (tbl.headers && tbl.headers.length) {
    const thead = document.createElement('thead');
    const tr = document.createElement('tr');
    tbl.headers.forEach(h => {
      const th = document.createElement('th');
      const inline = [];
      _mdParseInline(h, inline);
      _appendInlineSegments(th, inline);
      tr.appendChild(th);
    });
    thead.appendChild(tr);
    t.appendChild(thead);
  }
  const tbody = document.createElement('tbody');
  tbl.rows.forEach(row => {
    const tr = document.createElement('tr');
    row.forEach(cell => {
      const td = document.createElement('td');
      const inline = [];
      _mdParseInline(cell, inline);
      _appendInlineSegments(td, inline);
      tr.appendChild(td);
    });
    tbody.appendChild(tr);
  });
  t.appendChild(tbody);
  return t;
}

// Append a flat list of inline segments to a DOM node.
function _appendInlineSegments(parent, segments) {
  let inline = null;
  for (const s of segments) {
    if (s && typeof s === 'object' && s.open) {
      const el = document.createElement(s.open);
      parent.appendChild(el);
      inline = el;
    } else if (s && typeof s === 'object' && s.close) {
      inline = null;
    } else {
      (inline || parent).appendChild(document.createTextNode(s));
    }
  }
}

// Block-level line shapes. Headings, bullets and numbered items each start
// their own block; everything else is prose.
const _MD_HEADING = /^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$/;
const _MD_BULLET  = /^\s{0,3}[-*+]\s+(.*)$/;
const _MD_ORDERED = /^\s{0,3}(\d{1,4})[.)]\s+(.*)$/;

// Main parser. Output items: chars, {open/close: tag} inline markers, {table},
// {block: tag, list, start} block starters, {soft: true} for a single newline
// (prose lines join into ONE paragraph) and '\n' for a real paragraph break.
// Nothing here touches the DOM; the renderers below only ever create elements
// and text nodes from these items, so raw LLM text is never parsed as HTML.
function _mdParse(text) {
  const items = [];
  const lines = text.split('\n');
  const lastIdx = lines.length - 1;
  let prev = null;                     // 'text' | 'blank' | 'block' | null
  for (let li = 0; li <= lastIdx; li++) {
    const raw = lines[li];
    const line = li > 0 ? raw.trim() : raw;
    const atEnd = li === lastIdx;      // the segment after the final newline
    if (!line.trim()) {
      if (!atEnd && prev !== 'blank') { items.push('\n'); prev = 'blank'; }
      continue;
    }
    const tbl = _detectTable(lines, li);
    if (tbl) {
      items.push({ table: { headers: tbl.headers, rows: tbl.rows } });
      li = tbl.end - 1;
      prev = 'block';
      continue;
    }
    let m;
    if (li > 0 || raw === line) {      // a mid-line streamed fragment is never a block start
      if ((m = _MD_HEADING.exec(line))) {
        items.push({ block: 'h' + Math.min(4, Math.max(2, m[1].length)) });
        _mdParseInline(m[2], items);
        prev = 'block';
        if (!atEnd) items.push('\n');
        continue;
      }
      if ((m = _MD_BULLET.exec(line))) {
        items.push({ block: 'li', list: 'ul' });
        _mdParseInline(m[1], items);
        prev = 'block';
        if (!atEnd) items.push('\n');
        continue;
      }
      if ((m = _MD_ORDERED.exec(line))) {
        items.push({ block: 'li', list: 'ol', start: parseInt(m[1], 10) });
        _mdParseInline(m[2], items);
        prev = 'block';
        if (!atEnd) items.push('\n');
        continue;
      }
    }
    _mdParseInline(line, items);
    prev = 'text';
    if (!atEnd) items.push({ soft: true });
  }
  return items;
}

// The element the next character should land in: the last paragraph, heading
// or list item of a block. Skips the badge <img>, the TTS bar and tables that
// may sit after it.
function _lastTextEl(block) {
  if (!block) return null;
  let e = block.lastElementChild;
  while (e) {
    if (e.tagName === 'UL' || e.tagName === 'OL') { e = e.lastElementChild; continue; }
    if (/^(P|H[1-6]|LI)$/.test(e.tagName)) return e;
    if (e.tagName === 'TABLE') return null;
    e = e.previousElementSibling;
  }
  return null;
}

// Start a block-level element inside `block`. An untouched empty paragraph is
// reused rather than left behind as a blank gap.
function _mdStartBlock(block, item) {
  const tag = item === '\n' ? 'p' : (item.block || 'p');
  const last = _lastTextEl(block);
  if (last && last.tagName === 'P' && !last.firstChild && last.parentNode === block) {
    if (tag === 'p') return last;
    block.removeChild(last);
  }
  let el;
  if (tag === 'li') {
    let list = block.lastElementChild;
    while (list && !/^(UL|OL|P|H[1-6]|TABLE)$/.test(list.tagName)) list = list.previousElementSibling;
    if (!list || list.tagName !== item.list.toUpperCase()) {
      list = document.createElement(item.list);
      list.className = 'dm-list';
      if (item.list === 'ol' && item.start > 1) list.setAttribute('start', String(item.start));
      block.appendChild(list);
    }
    el = document.createElement('li');
    list.appendChild(el);
  } else {
    el = document.createElement(tag);
    block.appendChild(el);
  }
  return el;
}

// Soft newline: a space between joined prose lines, never doubled.
function _mdSoftSpace(el) {
  if (!el) return;
  const t = el.lastChild;
  const txt = t ? (t.textContent || '') : '';
  if (!t || /\s$/.test(txt)) return;
  el.appendChild(document.createTextNode(' '));
}

// Render a whole text into a finished block, instantly (replay paths).
function _mdFillBlock(block, text) {
  let inline = null;
  for (const item of _mdParse(text)) {
    if (item === '\n' || (item && typeof item === 'object' && item.block)) {
      inline = null;
      _mdStartBlock(block, item);
    } else if (item && item.soft) {
      _mdSoftSpace(_lastTextEl(block));
    } else if (item && item.table) {
      inline = null;
      block.appendChild(_buildTableNode(item.table));
    } else if (item && item.open) {
      const el = document.createElement(item.open);
      (_lastTextEl(block) || _mdStartBlock(block, '\n')).appendChild(el);
      inline = el;
    } else if (item && item.close) {
      inline = null;
    } else {
      const host = inline || _lastTextEl(block) || _mdStartBlock(block, '\n');
      host.appendChild(document.createTextNode(item));
    }
  }
}

// Auto-scroll: only follow if the user hasn't scrolled up
let userScrolledUp = false;
const _newPill = document.getElementById('new-content-pill');
function _showNewPill() { if (_newPill) _newPill.classList.add('visible'); }
function _hideNewPill() { if (_newPill) _newPill.classList.remove('visible'); }
textScroll.addEventListener('scroll', () => {
  const distFromBottom = textScroll.scrollHeight - textScroll.scrollTop - textScroll.clientHeight;
  userScrolledUp = distFromBottom > 80;
  // Back near the bottom — the reader has caught up, so retire the pill.
  if (!userScrolledUp) _hideNewPill();
});
if (_newPill) {
  _newPill.addEventListener('click', () => {
    userScrolledUp = false;
    _hideNewPill();
    textScroll.scrollTop = textScroll.scrollHeight;
  });
}

let charDelay = 36;      // ms per character while typing (mutable — see speed toggle)
const IDLE_GAP   = 1800; // ms silence before starting a new block

function getOrCreateBlock() {
  if (!currentEl) {
    currentEl = document.createElement('div');
    currentEl.className = 'dm-block';
    const p = document.createElement('p');
    currentEl.appendChild(p);
    textContent.appendChild(currentEl);
    scrollToBottom();
  }
  return _lastTextEl(currentEl) || _mdStartBlock(currentEl, '\n');
}

function typeNextChar() {
  if (charQueue.length === 0) {
    isTyping = false;
    textScroll.setAttribute('aria-busy', 'false');   // announce the finished text, not every letter
    return;
  }

  const item = charQueue.shift();
  const p    = getOrCreateBlock();

  if (currentCursor) currentCursor.classList.remove('typing-cursor');

  if (item === '\n' || (item && typeof item === 'object' && item.block)) {
    // New paragraph, heading or list item -- reset inline state
    _currentInlineEl = null;
    _mdStartBlock(currentEl, item);
  } else if (item && typeof item === 'object' && item.soft) {
    _mdSoftSpace(p);
    _currentInlineEl = null;
  } else if (item && typeof item === 'object' && item.table) {
    _currentInlineEl = null;
    currentEl.appendChild(_buildTableNode(item.table));
  } else if (item && typeof item === 'object' && item.open) {
    // Open a styled inline element inside current paragraph
    const el = document.createElement(item.open);
    p.appendChild(el);
    _currentInlineEl = el;
  } else if (item && typeof item === 'object' && item.close) {
    // Close inline element
    _currentInlineEl = null;
  } else {
    // Plain character — append to inline element if open, else to paragraph
    const target = _currentInlineEl || p;
    target.appendChild(document.createTextNode(item));
  }

  currentCursor = _lastTextEl(currentEl);
  if (currentCursor) currentCursor.classList.add('typing-cursor');

  scrollToBottom();
  setTimeout(typeNextChar, charDelay);
}

function startTyping() {
  if (isTyping) return;
  if (charDelay === 0) { instantFlush(); return; }
  isTyping = true;
  textScroll.setAttribute('aria-busy', 'true');
  typeNextChar();
}

// ── Block type badge detection ───────────────────────────────────────────────
// ── Class icon mapping ──────────────────────────────────────────────
const _CLASS_ICONS = {
  barbarian: 'class_barbarian', bard:      'class_bard',
  cleric:    'class_cleric',    druid:     'class_druid',
  fighter:   'class_fighter',   monk:      'class_monk',
  paladin:   'class_paladin',   ranger:    'class_ranger',
  rogue:     'class_rogue',     sorcerer:  'class_sorcerer',
  warlock:   'class_warlock',   wizard:    'class_wizard',
  artificer: 'class_artificer',
};
function _classIconSrc(classStr) {
  if (!classStr) return null;
  const lower = classStr.toLowerCase();
  for (const [key, file] of Object.entries(_CLASS_ICONS)) {
    if (lower.includes(key)) return '/icons/' + file + '.png';
  }
  return null;
}

const _BLOCK_BADGES = [
  { icon: 'attack',       words: ['attack','strikes','slash','stab','sword','blade','combat','initiative','damage','hit','miss','roll','dex','str','con','saving throw','critical','fumble'] },
  { icon: 'chat',         words: ['says','replies','whispers','mutters','speaks','voice','asks','answers','"','dialogue','npc','turns to you'] },
  { icon: 'crystal_ball', words: ['arcane','ritual','spell','magic','incantation','rune','sigil','cast','magical','energy crackles','glows','aura'] },
  { icon: 'treasure',     words: ['gold','coin','loot','reward','chest','pouch','found','grants','gives you','item','platinum','gp','silver'] },
  { icon: 'location',     words: ['travel','road','path','arrive','enter','city','town','village','dungeon','forest','mountain','shore','coast','door','gate'] },
  { icon: 'heal',         words: ['heal','rest','recover','restore','health','potion','bandage','mend','wounds close','regain'] },
  { icon: 'scroll',       words: ['quest','mission','task','objective','assignment','letter','note','parchment','message','contract'] },
  { icon: 'faction',      words: ['faction','guild','order','council','alliance','sworn','allegiance','representative','envoy'] },
];

// Block KIND is language-agnostic — the display already knows an NPC block is
// an NPC block regardless of what language it is written in. Deriving what can
// be derived means these badges work in every locale, and only the semantic
// ones below have to fall back to reading words.
const _KIND_BADGES = {
  'npc-block':    'chat',
  'dice-block':   'attack',
  'tutor-block':  'scroll',
  'player-block': null,          // the player's own line needs no badge
};

// Semantic badges still need to read the prose. The word lists live in the
// active system's ui.json (`block_badges`) rather than in this file, so a
// system or a campaign in another language can supply its own without editing
// 7,000 lines of HTML. The English table below is the fallback, NOT the
// definition.
function _badgeTable() {
  const fromManifest = (window.GM_UI_MANIFEST || {}).block_badges;
  return Array.isArray(fromManifest) && fromManifest.length
    ? fromManifest
    : _BLOCK_BADGES;
}

function _addBlockBadge(el) {
  if (el.querySelector('.block-badge')) return;      // idempotent

  // 1. Kind first. Works in every language, no word list involved.
  let icon = null;
  for (const cls of Object.keys(_KIND_BADGES)) {
    if (el.classList.contains(cls)) {
      icon = _KIND_BADGES[cls];
      if (icon === null) return;                     // deliberately no badge
      break;
    }
  }

  // 2. Only DM narration falls through to reading words, and only then does
  //    the language of the table matter.
  //
  //    Read the PROSE, not el.textContent. A finalised block also contains the
  //    TTS bar, whose own labels are "Voices / Male / Charon / ..." — and
  //    "voice" is a keyword in the chat list, so every narration block matched
  //    chat regardless of what it said. The badge must describe the story, not
  //    the controls sitting next to it.
  if (!icon) {
    const paras = el.querySelectorAll('p');
    const text = (paras.length
      ? [...paras].map(n => n.textContent || '').join(' ')
      : (el.textContent || '')).toLowerCase();
    for (const entry of _badgeTable()) {
      const words = entry && entry.words;
      if (Array.isArray(words) && words.some(w => w && text.includes(String(w).toLowerCase()))) {
        icon = entry.icon;
        break;
      }
    }
  }
  if (!icon) return;                                 // no guess is better than a wrong one

  const img = document.createElement('img');
  img.src = '/icons/' + icon + '.png';
  img.className = 'block-badge';
  img.alt = '';                                      // decorative; the prose says it
  el.appendChild(img);
}

// Blocks are appended from sixteen different places, and only DM narration
// goes through flushNewBlock(). Adding the badge call at each site was how the
// first attempt at this missed dice and tutor blocks entirely — they are the
// two that never get a TTS bar, so they were not on the hook I had picked.
//
// One observer instead. Every block that appears in the feed passes through
// here regardless of which renderer built it, including any added later.
//
// DM blocks are the exception and are badged in flushNewBlock() rather than
// here: they are appended EMPTY and filled character by character, so a
// semantic match at append time would read an empty string.
// dm-block is included even though a LIVE one is appended empty. _addBlockBadge
// is idempotent and adds nothing when no keyword matches, so an empty block
// simply gets no badge here and is badged later by flushNewBlock() once its
// text has been typed. A REPLAYED dm-block arrives with its full text and is
// badged immediately — which is the path that matters on page load, and the
// one the first fix broke by removing the call from renderReplayBatch.
const _BADGE_ON_APPEND = new Set([
  'dm-block', 'npc-block', 'dice-block', 'tutor-block', 'player-block',
  'action-block', 'milestone-block', 'inspiration-block',
]);

function _watchBlocksForBadges() {
  const feed = document.getElementById('text-content');
  if (!feed || typeof MutationObserver === 'undefined') return;
  new MutationObserver(records => {
    for (const rec of records) {
      for (const node of rec.addedNodes) {
        if (node.nodeType !== 1) continue;
        const cls = (node.className || '').split(' ');
        if (cls.some(c => _BADGE_ON_APPEND.has(c))) _addBlockBadge(node);
      }
    }
  }).observe(feed, { childList: true });
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', _watchBlocksForBadges);
} else {
  _watchBlocksForBadges();
}

function flushNewBlock() {
  // Finalise the current block and start a fresh one next time
  if (currentCursor && currentCursor.parentNode) {
    currentCursor.parentNode.removeChild(currentCursor);
    currentCursor = null;
  }
  // Add a soft divider if there was content
  if (currentEl) {
    _addBlockBadge(currentEl);
    _addTtsBar(currentEl);
    const div = document.createElement('div');
    div.className = 'divider';
    div.textContent = '✦';
    textContent.appendChild(div);
  }
  currentEl = null;
}

// ── Instant flush: drain all pending chars synchronously ──────────────
// Called before inserting a non-DM block so the typewriter never gets
// interrupted mid-word. Renders all queued chars immediately, then
// stops the typewriter loop cleanly.
function instantFlush() {
  if (charQueue.length === 0) return;
  clearTimeout(idleTimer);
  const pending = charQueue.splice(0);  // atomically drain
  let flushInline = null;  // local inline tracker for this flush pass
  for (const item of pending) {
    if (item === '\n' || (item && typeof item === 'object' && item.block)) {
      flushInline = null;
      if (!currentEl) getOrCreateBlock();
      _mdStartBlock(currentEl, item);
    } else if (item && typeof item === 'object' && item.soft) {
      flushInline = null;
      if (currentEl) _mdSoftSpace(_lastTextEl(currentEl));
    } else if (item && typeof item === 'object' && item.table) {
      flushInline = null;
      if (!currentEl) getOrCreateBlock();
      currentEl.appendChild(_buildTableNode(item.table));
    } else if (item && typeof item === 'object' && item.open) {
      const p = getOrCreateBlock();
      const el = document.createElement(item.open);
      p.appendChild(el);
      flushInline = el;
    } else if (item && typeof item === 'object' && item.close) {
      flushInline = null;
    } else {
      const p = getOrCreateBlock();
      const target = flushInline || p;
      target.appendChild(document.createTextNode(item));
    }
  }
  isTyping = false;
  _currentInlineEl = null;
  if (currentCursor) currentCursor.classList.remove('typing-cursor');
  currentCursor = currentEl ? _lastTextEl(currentEl) : null;
}

// ── Shared: flush cursor and currentEl before inserting a non-DM block ──
function _flushForBlock() {
  instantFlush();  // finish any in-progress typewriter before inserting
  if (currentCursor) {
    currentCursor.classList.remove('typing-cursor');
    currentCursor = null;
  }
  currentEl = null;
  clearTimeout(idleTimer);
}

// ── Deferring a block until the reveal actually finishes ─────────────
//
// A non-DM block arriving mid-narration used to call instantFlush(): the rest
// of the paragraph snapped to its end and the block dropped in underneath.
// That guarantees the block never lands mid-word, but it resolves the
// collision by throwing the reveal away — and the thing that arrived is
// usually the consequence of the sentence being read.
//
// So hold the block instead. The part that is easy to get wrong is that the
// check must RE-ARM: narration often continues after the block arrives, so a
// single "wait N ms then flush" lands in the middle of the next paragraph. It
// has to keep asking whether the typewriter is idle and release only when it
// really is.
const PENDING_BLOCK_GAP = 320;    // ms of DRAINED typewriter before releasing
const PENDING_BLOCK_MAX = 8000;   // ms before releasing regardless

let _pendingBlocks = [];
let _pendingTimer  = null;
let _pendingSince  = 0;

function _typewriterActive() {
  return isTyping || charQueue.length > 0;
}

function _releasePending() {
  const items = _pendingBlocks;
  _pendingBlocks = [];
  _pendingSince = 0;
  for (const fn of items) {
    try { fn(); } catch (e) { console.error('deferred block failed', e); }
  }
}

function _schedulePendingFlush() {
  if (_pendingTimer) return;
  const tick = () => {
    _pendingTimer = null;
    // A stalled reveal must not swallow the queue. Without this valve a
    // typewriter that never drains means a dice result the table is waiting
    // on never appears at all, which is worse than the snap it replaced.
    const waited = _pendingSince ? (Date.now() - _pendingSince) : 0;
    if (_typewriterActive() && waited < PENDING_BLOCK_MAX) {
      _pendingTimer = setTimeout(tick, PENDING_BLOCK_GAP);   // re-arm
      return;
    }
    if (_typewriterActive()) instantFlush();   // valve fired: old behaviour
    _releasePending();
  };
  _pendingTimer = setTimeout(tick, PENDING_BLOCK_GAP);
}

function _deferBlock(fn, isReplay) {
  // A replay dumps history with no live reveal to protect, so deferring it
  // would only delay the whole transcript.
  if (isReplay || (!_typewriterActive() && _pendingBlocks.length === 0)) {
    fn();
    return;
  }
  if (!_pendingSince) _pendingSince = Date.now();
  _pendingBlocks.push(fn);
  _schedulePendingFlush();
}

// ── Shared: after inserting a non-DM block, re-establish cursor below it ──
// If typing is still in progress, immediately open the next DM block so the
// cursor appears below the inserted block rather than going invisible.
function _reanchorCursor() {
  if ((isTyping || charQueue.length > 0) && !currentEl) {
    const p = getOrCreateBlock();
    currentCursor = p;
    currentCursor.classList.add('typing-cursor');
  }
  scrollToBottom();
}

// ── Player action block ──────────────────────────────────────────────
function renderPlayerBlock(name, text, isReplay) {
  _deferBlock(() => _renderPlayerBlockNow(name, text, isReplay), isReplay);
}

function _renderPlayerBlockNow(name, text, isReplay) {
  _flushForBlock();

  const block = document.createElement('div');
  block.className = 'player-block';
  if (isReplay) block.style.opacity = '0.65';

  const nameEl = document.createElement('div');
  nameEl.className = 'player-name';
  nameEl.textContent = name;
  block.appendChild(nameEl);

  const cleaned = text
    .replace(/[\x00-\x08\x0b-\x1f\x7f]/g, '')
    .replace(/\r/g, '')
    .replace(/\t/g, '  ')
    .trim();

  cleaned.split('\n').forEach(line => {
    const trimmed = line.trim();
    if (!trimmed) return;
    const p = document.createElement('p');
    p.textContent = trimmed;
    block.appendChild(p);
  });

  textContent.appendChild(block);
  _reanchorCursor();
}

// ── Player action intent block ───────────────────────────────────────
function renderActionBlock(name, text, isReplay) {
  _deferBlock(() => _renderActionBlockNow(name, text, isReplay), isReplay);
}

function _renderActionBlockNow(name, text, isReplay) {
  _flushForBlock();

  const block = document.createElement('div');
  block.className = 'action-block';
  if (isReplay) block.style.opacity = '0.45';

  const tag = document.createElement('div');
  tag.className = 'action-tag';
  tag.textContent = name;
  block.appendChild(tag);

  const cleaned = text
    .replace(/[\x00-\x08\x0b-\x1f\x7f]/g, '')
    .replace(/\r/g, '')
    .trim();

  const p = document.createElement('p');
  p.textContent = cleaned;
  block.appendChild(p);

  textContent.appendChild(block);
  _reanchorCursor();
}

// ── NPC dialogue block ───────────────────────────────────────────────
function renderNPCBlock(name, text, isReplay) {
  _deferBlock(() => _renderNPCBlockNow(name, text, isReplay), isReplay);
}

function _renderNPCBlockNow(name, text, isReplay) {
  _flushForBlock();

  const block = document.createElement('div');
  block.className = 'npc-block';
  if (isReplay) block.style.opacity = '0.65';

  const nameEl = document.createElement('div');
  nameEl.className = 'npc-name';
  nameEl.textContent = name;
  block.appendChild(nameEl);

  const cleaned = text
    .replace(/[\x00-\x08\x0b-\x1f\x7f]/g, '')
    .replace(/\r/g, '')
    .replace(/\t/g, '  ')
    .trim();

  _mdFillBlock(block, cleaned);
  _addTtsBar(block);
  textContent.appendChild(block);
  _reanchorCursor();
}

// ── Dice block icon detection ─────────────────────────────────────────
function _diceRollIcon(text) {
  const t = text.toLowerCase();
  // Highest-stakes — check first
  if (/death save|death saving|critical hit|fumble/.test(t))             return 'dragon';
  // Spell
  if (/spell attack|concentration|cantrip|cast/.test(t))                 return 'spellbook';
  // Potion / medicine
  if (/potion|medicine|alchemist/.test(t))                               return 'potion';
  // Mystical sensing
  if (/insight|arcana|divination|perception/.test(t))                    return 'crystal_ball';
  // Combat
  if (/attack|vs ac|\bhit\b|\bmiss\b|slashing|piercing|bludgeoning|necrotic|radiant|fire|cold|lightning|damage/.test(t)) return 'attack';
  // Saving throws
  if (/saving throw|save dc|\bsave\b|con save|dex save|wis save|str save|int save|cha save/.test(t)) return 'shield';
  // Knowledge checks
  if (/investigation|history|nature|religion|survival/.test(t))          return 'scroll';
  // Social / physical skills
  if (/stealth|sleight|deception|persuasion|intimidation|performance|acrobatics|athletics/.test(t)) return 'dagger';
  // Healing
  if (/heal|restore|hit point|recover/.test(t))                          return 'heal';
  // Initiative
  if (/initiative/.test(t))                                              return 'timer';
  return null;
}

// ── Dice result block ─────────────────────────────────────────────────
function renderDiceBlock(text, isReplay) {
  _deferBlock(() => _renderDiceBlockNow(text, isReplay), isReplay);
}

function _renderDiceBlockNow(text, isReplay) {
  _flushForBlock();

  const block = document.createElement('div');
  block.className = 'dice-block';
  if (isReplay) block.style.opacity = '0.65';

  const cleaned = text.trim()
    .replace(/[\x00-\x08\x0b-\x1f\x7f]/g, '')
    .replace(/\r/g, '');

  // Icon on left based on roll type
  const iconName = _diceRollIcon(cleaned);
  if (iconName) {
    const ico = document.createElement('img');
    ico.src = '/icons/' + iconName + '.png';
    ico.className = 'dice-block-icon';
    ico.alt = '';
    block.appendChild(ico);
  }

  // Wrap results so they flex as a group
  const resultsWrap = document.createElement('div');
  let hasCrit = false, hasFumble = false;
  cleaned.split('\n').forEach(line => {
    const trimmed = line.trim();
    if (!trimmed) return;
    const el = document.createElement('div');
    el.className = 'dice-result';
    el.textContent = trimmed;
    resultsWrap.appendChild(el);
    if (/CRITICAL HIT/i.test(trimmed)) hasCrit = true;
    if (/FUMBLE/i.test(trimmed))        hasFumble = true;
  });
  block.appendChild(resultsWrap);

  if (!isReplay) {
    if (hasCrit)   triggerFlash('crit');
    else if (hasFumble) triggerFlash('fumble');
  }

  textContent.appendChild(block);
  _reanchorCursor();
}

// ── Tutor / learning hint block ──────────────────────────────────────
function renderTutorBlock(text, isReplay) {
  _deferBlock(() => _renderTutorBlockNow(text, isReplay), isReplay);
}

function _renderTutorBlockNow(text, isReplay) {
  _flushForBlock();

  // Detect warning prefix (lines starting with ⚠ or WARNING:)
  const isWarning = /^(⚠|WARNING[:\s])/i.test(text.trim());

  const block = document.createElement('div');
  block.className = 'tutor-block' + (isWarning ? ' tutor-warning' : '');
  if (isReplay) block.style.opacity = '0.65';

  // Clickable header row
  const header = document.createElement('div');
  header.className = 'tutor-header';
  header.innerHTML = `<span class="tutor-icon">${isWarning ? '⚠' : '◈'}</span>` +
                     `<span class="tutor-label">${isWarning ? 'Warning' : 'DM Hint'}</span>` +
                     `<span class="tutor-chevron">▼</span>`;
  block.appendChild(header);

  // Collapsible body
  const body = document.createElement('div');
  body.className = 'tutor-body';

  const cleaned = text.replace(/^(⚠|WARNING[:\s]+)/i, '').trim()
    .replace(/[\x00-\x08\x0b-\x1f\x7f]/g, '')
    .replace(/\r/g, '');

  cleaned.split('\n').forEach(line => {
    const trimmed = line.trim();
    if (!trimmed) return;
    const p = document.createElement('p');
    p.textContent = trimmed;
    body.appendChild(p);
  });
  block.appendChild(body);

  header.addEventListener('click', () => block.classList.toggle('expanded'));

  textContent.appendChild(block);
  _reanchorCursor();

  // Reset DM Help button — hint arrived, execution complete
  clearTimeout(_helpResetTimer);
  _dmHelpReset();
}

// ── Inspiration award block ────────────────────────────────────────────────
function renderInspirationBlock(name, reason) {
  _deferBlock(() => _renderInspirationBlockNow(name, reason), false);
}

function _renderInspirationBlockNow(name, reason) {
  _flushForBlock();
  const block = document.createElement('div');
  block.className = 'inspiration-block';
  const title = document.createElement('div');
  title.className = 'inspiration-title';
  // The two focus icons are fixed markup (only their margins differ), and the
  // award name between them is plain text. The name used to be interpolated
  // straight into this template, unescaped: it arrives as payload.inspiration_award
  // over SSE. Two fixed images plus one text node, no interpolation at all.
  const ICON = (m) => '<img src="/icons/focus.png" style="width:22px;height:22px;vertical-align:middle;'
    + `opacity:0.85;margin-${m}:10px;filter:drop-shadow(0 0 6px rgba(220,190,60,0.6))">`;
  title.innerHTML = ICON('right');
  title.appendChild(document.createTextNode(String(name == null ? '' : name)));
  title.insertAdjacentHTML('beforeend', ICON('left'));
  const sub = document.createElement('div');
  sub.className = 'inspiration-sub';
  sub.textContent = 'Inspiration';
  block.appendChild(title);
  block.appendChild(sub);
  if (reason) {
    const detail = document.createElement('div');
    detail.className = 'inspiration-detail';
    detail.textContent = reason;
    block.appendChild(detail);
  }
  textContent.appendChild(block);
  _reanchorCursor();
  scrollToBottom();
}

// ── Milestone award block (stack-based reward — Bardic Inspiration die / Hero Coin / etc.) ──
// Distinct from the binary D&D 5e Inspiration block above. Use the label arg to name what
// kind of reward was earned.
function renderMilestoneBlock(name, label, reason, isReplay) {
  _deferBlock(() => _renderMilestoneBlockNow(name, label, reason, isReplay), isReplay);
}

function _renderMilestoneBlockNow(name, label, reason, isReplay) {
  _flushForBlock();
  const block = document.createElement('div');
  block.className = 'milestone-block';
  if (isReplay) block.style.opacity = '0.7';
  const header = document.createElement('div');
  header.className = 'milestone-header';
  header.innerHTML = `<span class="milestone-icon">✦</span>` +
                     `<span class="milestone-name">${esc(name)}</span>` +
                     `<span class="milestone-label">${esc(label)}</span>`;
  block.appendChild(header);
  if (reason) {
    const detail = document.createElement('div');
    detail.className = 'milestone-detail';
    detail.textContent = reason;
    block.appendChild(detail);
  }
  textContent.appendChild(block);
  _reanchorCursor();
  scrollToBottom();
}

// ── XP award block ─────────────────────────────────────────────────────────
function renderXpBlock(xpData) {
  _flushForBlock();
  const block = document.createElement('div');
  block.className = 'xp-block';
  const line = document.createElement('div');
  line.className = 'xp-line';
  // names line: "Kat, Ben — +250 XP"
  const names = (xpData.names || []).join(', ');
  const amt   = xpData.xp != null ? `+${xpData.xp} XP` : '';
  line.textContent = [names, amt].filter(Boolean).join(' — ');
  block.appendChild(line);
  if (xpData.reason || xpData.total) {
    const detail = document.createElement('div');
    detail.className = 'xp-detail';
    const parts = [];
    if (xpData.reason) parts.push(xpData.reason);
    if (xpData.total)  parts.push(xpData.total);
    detail.textContent = parts.join('  ·  ');
    block.appendChild(detail);
  }
  textContent.appendChild(block);
  _reanchorCursor();
  scrollToBottom();
}

// ── Replay batch (reconnect / session resume) ─────────────────────────
function renderReplayBatch(items) {
  flushNewBlock();
  items.forEach(item => {
    if (!item) return;
    if (item.inspiration_award) {
      renderInspirationBlock(item.inspiration_award, item.reason || '');
      return;
    }
    if (item.milestone_award) {
      renderMilestoneBlock(item.milestone_award, item.label || 'Milestone', item.reason || '', true);
      return;
    }
    if (item.xp_award) {
      renderXpBlock(item.xp_award);
      return;
    }
    if (!item.text) return;
    if (item.action) {
      renderActionBlock(item.action, item.text, true);
    } else if (item.player) {
      renderPlayerBlock(item.player, item.text, true);
    } else if (item.npc) {
      renderNPCBlock(item.npc, item.text, true);
    } else if (item.dice) {
      renderDiceBlock(item.text, true);
    } else if (item.tutor) {
      renderTutorBlock(item.text, true);
    } else {
      const block = document.createElement('div');
      block.className = 'dm-block';
      block.style.opacity = '0.75';
      _mdFillBlock(block, item.text);
      _addTtsBar(block);
      textContent.appendChild(block);
    }
  });

  const div = document.createElement('div');
  div.className = 'divider';
  div.textContent = '✦';
  textContent.appendChild(div);

  scrollToBottom();
}

function clearDisplay(then) {
  // Wipe now, then fade back in and call the callback. Wiping after the fade
  // instead would also erase text that arrives during it: /gm new sends its
  // opening narration right after the clear.
  textContent.style.opacity = '0';
  // Abort any in-progress typewriter
  charQueue        = [];
  isTyping         = false;
  _currentInlineEl = null;
  if (currentCursor && currentCursor.parentNode) {
    currentCursor.parentNode.removeChild(currentCursor);
  }
  currentCursor = null;
  currentEl     = null;
  clearTimeout(idleTimer);

  textContent.innerHTML = '';
  textScroll.scrollTop  = 0;
  userScrolledUp = false;
  _hideProcessingIndicator();
  setTimeout(() => {
    textContent.style.opacity = '1';
    if (then) then();
  }, 370); // matches the CSS transition duration
}

function _showProcessingIndicator() {
  _hideProcessingIndicator(); // clear any existing one first
  const el = document.createElement('div');
  el.id = 'dm-processing-indicator';
  for (let i = 0; i < 3; i++) {
    const dot = document.createElement('span');
    dot.className = 'proc-dot';
    el.appendChild(dot);
  }
  textContent.appendChild(el);
  scrollToBottom();
}

function _hideProcessingIndicator() {
  const el = document.getElementById('dm-processing-indicator');
  if (el) el.remove();
}

function handleIncomingText(text) {
  _hideProcessingIndicator();
  // Debounce block separation: if there was a gap > IDLE_GAP, new block
  const now = Date.now();
  if (now - lastChunkTime > IDLE_GAP && charQueue.length === 0 && !isTyping) {
    flushNewBlock();
  }
  lastChunkTime = now;

  // Reset idle timer
  clearTimeout(idleTimer);
  idleTimer = setTimeout(() => {
    if (!isTyping && charQueue.length === 0) flushNewBlock();
  }, IDLE_GAP * 2);

  // Enqueue — preprocess markdown to typed segment items
  const cleaned = text
    .replace(/\x1b\[[\d;?]*[a-zA-Z]/g, '')       // whole ANSI sequences first
    .replace(/[\x00-\x08\x0b-\x1f\x7f]/g, '')  // strip control chars (keep \n \t)
    .replace(/\[\d[\d;]*[mKJH]/g, '')             // leftover CSI remnants; never a plain [word]
    .replace(/\r/g, '')
    .replace(/\t/g, '  ');
  for (const item of _mdParse(cleaned)) {
    charQueue.push(item);
  }

  startTyping();
}

function scrollToBottom() {
  if (!userScrolledUp) {
    textScroll.scrollTop = textScroll.scrollHeight;
    _hideNewPill();
  } else {
    // New narration landed below the fold — surface the "New ↓" pill so the
    // reader knows there's fresh text and can jump to it.
    _showNewPill();
  }
}

function renderReplay(text) {
  // Render historical text instantly (no typewriter) when browser reconnects.
  flushNewBlock();
  const cleaned = text
    .replace(/\x1b\[[\d;?]*[a-zA-Z]/g, '')
    .replace(/[\x00-\x08\x0b-\x1f\x7f]/g, '')
    .replace(/\[\d[\d;]*[mKJH]/g, '')
    .replace(/\r/g, '')
    .replace(/\t/g, '  ');
  if (!cleaned.trim()) return;

  const block = document.createElement('div');
  block.className = 'dm-block';
  block.style.opacity = '0.75';   // subtle visual distinction for historical text
  _mdFillBlock(block, cleaned);
  _addTtsBar(block);
  textContent.appendChild(block);

  // Add a divider after replay so live text starts fresh
  const div = document.createElement('div');
  div.className = 'divider';
  div.textContent = '✦';
  textContent.appendChild(div);

  scrollToBottom();
}


// ═══════════════════════════════════════════════════════════════════
// STATS SIDEBAR
// ═══════════════════════════════════════════════════════════════════

// Keyed by player name so repeated calls merge cleanly.
const _playerData = {};

// ── CRIT / FUMBLE flash ─────────────────────────────────────────────
const _flashOverlay = document.getElementById('flash-overlay');
function triggerFlash(type) {
  _flashOverlay.classList.remove('flash-crit', 'flash-fumble');
  // Force reflow so the animation restarts cleanly
  void _flashOverlay.offsetWidth;
  _flashOverlay.classList.add(type === 'crit' ? 'flash-crit' : 'flash-fumble');
}

// ── HP change tracking (for colour flash on sidebar numerals) ───────
const _prevHp = {}; // { playerName: currentHp }

function _hpColor(pct) {
  if (pct > 60) return '#4a9a4a';
  if (pct > 30) return '#c0901a';
  return '#a03030';
}

// ══════════════════════════════════════════════════════════════════════
// SYSTEM UI MANIFEST — per-system character sidebar + sheet rendering.
//
// The server injects the active system's systems/<system>/ui.json as
// window.GM_UI_MANIFEST. When it's null/invalid we fall back to
// DEFAULT_UI_MANIFEST below, which reproduces the original hardcoded D&D 5e
// layout exactly — so the display renders identically with or without a
// manifest file. A new game system ships its own ui.json to define its
// sidebar widgets, combat strip, and attribute grid. See systems/UI-MANIFEST.md.
//
// Widgets are self-hiding: each renders nothing when its bound field is absent,
// so a manifest declares the superset of what *can* show and the pushed stats
// decide what actually renders.
// ══════════════════════════════════════════════════════════════════════

const _DEFAULT_CONDITION_CLASS = {
  unconscious: 'danger', paralyzed: 'danger', petrified: 'danger', stunned: 'danger',
  incapacitated: 'warn', frightened: 'warn', poisoned: 'warn', charmed: 'warn', exhausted: 'warn',
  grappled: 'info', restrained: 'info', prone: 'info', blinded: 'info', deafened: 'info',
  invisible: 'buff',
};

const DEFAULT_UI_MANIFEST = {
  manifest_version: 1, system: 'dnd5e', label: 'D&D 5e',
  sidebar: [
    { type: 'bar', label: 'HP', bind: 'hp', icon: '/icons/enemy.png',
      row_class: 'sb-hp-row', num_class: 'sb-hp-nums', fill_role: 'hp-fill',
      cur: 'current', max: 'max', color: 'hp', temp: 'temp' },
    { type: 'bar', label: 'XP', bind: 'xp', icon: '/icons/ability.png',
      row_class: 'sb-xp-row', num_class: 'sb-xp-nums', fill_role: 'xp-fill',
      fill_class: 'sb-xp-bar', cur: 'current', max: 'next', require_cur: true },
    { type: 'stat_lines', lines: [
      { label: 'AC', bind: 'ac' }, { label: 'Init', bind: 'initiative' },
      { label: 'Spd', bind: 'speed' }, { label: 'HD', bind: 'hit_dice', format: 'hd' } ] },
    { type: 'tag_list', bind: 'conditions', srd_lookup: true, lookup_category: 'condition' },
    { type: 'tag_single', bind: 'concentration', prefix: '◈ ' },
    { type: 'effects', bind: 'effects' },
    { type: 'badge_set', bind: 'milestones' },
    { type: 'pip_levels', bind: 'spell_slots' },
    { type: 'feature_flags', flags: [ { label: '2nd Wind', bind: 'second_wind' } ] },
    { type: 'badge', bind: 'inspiration', label: '✦ Inspiration' },
  ],
  sheet: {
    combat_strip: [
      { label: 'HP', bind: 'hp', format: 'ratio' },
      { label: 'AC', bind: 'ac' },
      { label: 'Init', bind: 'initiative' },
      { label: 'Speed', bind: 'speed', suffix: ' ft' },
      { label: 'Hit Dice', bind: 'hit_dice', format: 'hd' },
    ],
    stat_grid: { label: 'Ability Scores', bind: 'ability_scores', show_modifier: true,
      stats: [ { key: 'str', label: 'STR' }, { key: 'dex', label: 'DEX' },
        { key: 'con', label: 'CON' }, { key: 'int', label: 'INT' },
        { key: 'wis', label: 'WIS' }, { key: 'cha', label: 'CHA' } ] },
  },
};

function _gmManifest() {
  const m = window.GM_UI_MANIFEST;
  return (m && typeof m === 'object' && Array.isArray(m.sidebar)) ? m : DEFAULT_UI_MANIFEST;
}

// Resolve a dot-path ('hp', 'hp.current') against a player object.
function _bind(p, path) {
  if (!path) return undefined;
  return String(path).split('.').reduce((o, k) => (o == null ? undefined : o[k]), p);
}

// ── Sidebar widget renderers (DOM/classes match the original hardcoded card) ──

function _wBar(card, p, w) {
  const obj = _bind(p, w.bind) || {};
  const cur = obj[w.cur || 'current'];
  const max = obj[w.max || 'max'];
  if (w.require_cur && cur == null) return;
  const row = document.createElement('div');
  row.className = w.row_class || 'sb-hp-row';
  const maxLabel = (w.max === 'next') ? (max ?? '?') : (max ?? '—');
  // w.icon and w.num_class come from the system UI manifest (systems/<system>/
  // ui.json), so they are semi-trusted: not the network, but a file a campaign
  // can ship. Both land inside an HTML attribute, so a quote in either one
  // would close the attribute and start a tag. esc() covers the attribute case.
  row.innerHTML =
    `${w.icon ? `<img src="${esc(w.icon)}" class="sb-row-icon" alt="">` : ''}<span class="sb-label">${esc(w.label || '')}</span>
    <span class="${esc(w.num_class || 'sb-hp-nums')}">${esc(cur ?? '—')} / ${esc(maxLabel)}</span>`;
  card.appendChild(row);
  const track = document.createElement('div');
  track.className = 'sb-bar-track';
  const fill = document.createElement('div');
  fill.className = 'sb-bar-fill' + (w.fill_class ? ' ' + w.fill_class : '');
  if (w.fill_role) fill.dataset.role = w.fill_role;
  const pct = (max && cur != null) ? Math.max(0, Math.min(100, (cur / max) * 100))
                                   : (w.color === 'hp' ? 100 : 0);
  fill.style.width = pct + '%';
  if (w.color === 'hp') fill.style.backgroundColor = _hpColor(pct);
  track.appendChild(fill);
  card.appendChild(track);
  if (w.temp && obj[w.temp]) {
    const tempEl = document.createElement('div');
    tempEl.className = 'sb-hp-temp-row';
    tempEl.dataset.role = 'hp-temp';
    tempEl.textContent = '+' + obj[w.temp] + ' temp';
    card.appendChild(tempEl);
  }
}

function _wStatLines(card, p, w) {
  const out = [];
  (w.lines || []).forEach(ln => {
    const v = _bind(p, ln.bind);
    if (ln.format === 'hd') {
      if (v) out.push(`${esc(ln.label)} <span class="val">${esc(v.remaining ?? '?')}/${esc(v.max ?? '?')} ${esc(v.die || '')}</span>`);
    } else if (v != null) {
      out.push(`${esc(ln.label)} <span class="val">${esc(v)}</span>`);
    }
  });
  if (!out.length) return;
  const qs = document.createElement('div');
  qs.className = 'sb-quick-stats';
  qs.innerHTML = out.join('<br>');
  card.appendChild(qs);
}

function _wTagList(card, p, w) {
  const items = _bind(p, w.bind) || [];
  const map = w.class_map || _DEFAULT_CONDITION_CLASS;
  const el = document.createElement('div');
  el.className = 'sb-conditions';
  el.dataset.role = w.role || 'conditions';   // always appended (placeholder lets merges clear it)
  items.forEach(c => {
    const pill = document.createElement('span');
    pill.className = `sb-condition-pill ${map[String(c).toLowerCase()] || 'info'}`;
    pill.textContent = c;
    // Tappable rule lookup — only when the manifest opts in via `srd_lookup`
    // (with `lookup_category`, e.g. "condition"). Systems without an SRD-style
    // dataset simply omit the flag and the pills stay display-only. A name may
    // carry a suffix like "Exhaustion (2)" — strip it to the base name.
    if (w.srd_lookup) {
      const base = String(c).replace(/\s*\(.*\)\s*$/, '').trim();
      pill.dataset.srdName = base;
      pill.dataset.srdCategory = w.lookup_category || '';
      pill.addEventListener('click', (e) => {
        e.stopPropagation();
        openSrdModal(base, w.lookup_category || '', null);
      });
    }
    el.appendChild(pill);
  });
  card.appendChild(el);
}

function _wTagSingle(card, p, w) {
  const v = _bind(p, w.bind);
  if (!v) return;
  const el = document.createElement('div');
  el.className = w.cls || 'sb-concentrate';
  el.dataset.role = w.role || 'concentration';
  el.textContent = (w.prefix || '') + v;
  card.appendChild(el);
}

function _wEffects(card, p, w) {
  const effects = _bind(p, w.bind) || [];
  const el = document.createElement('div');
  el.className = 'sb-effects';
  el.dataset.role = 'effects';
  effects.forEach(eff => el.appendChild(_makeEffectPill(eff, p.name)));
  card.appendChild(el);
}

function _wBadgeSet(card, p, w) {
  const ms = _bind(p, w.bind);
  if (!ms || !Object.keys(ms).length) return;
  const el = document.createElement('div');
  el.className = 'sb-milestones';
  el.dataset.role = w.role || 'milestones';
  Object.entries(ms)
    .filter(([_, count]) => count > 0)
    .sort(([a], [b]) => a.localeCompare(b))
    .forEach(([label, count]) => {
      const row = document.createElement('div'); row.className = 'sb-milestone-row';
      const lbl = document.createElement('span'); lbl.className = 'sb-milestone-label'; lbl.textContent = label;
      const pill = document.createElement('span'); pill.className = 'sb-milestone-count'; pill.textContent = count;
      row.appendChild(lbl); row.appendChild(pill); el.appendChild(row);
    });
  if (el.children.length) card.appendChild(el);
}

function _wPipLevels(card, p, w) {
  const slots = _bind(p, w.bind);
  if (!slots || !Object.keys(slots).length) return;
  const ROMAN = ['', 'I', 'II', 'III', 'IV', 'V', 'VI', 'VII', 'VIII', 'IX'];
  const el = document.createElement('div');
  el.className = 'sb-slots';
  el.dataset.role = w.role || 'spell_slots';
  Object.entries(slots).sort(([a], [b]) => +a - +b).forEach(([lvl, s]) => {
    const max = s.max || 0;
    if (!max) return;
    const avail = max - (s.used || 0);
    const row = document.createElement('div'); row.className = 'sb-slot-row';
    const lbl = document.createElement('span'); lbl.className = 'sb-slot-level';
    lbl.textContent = ROMAN[+lvl] || lvl; row.appendChild(lbl);
    for (let i = 0; i < max; i++) {
      const pip = document.createElement('span');
      pip.className = 'sb-slot-pip ' + (i < avail ? 'avail' : 'spent');
      row.appendChild(pip);
    }
    el.appendChild(row);
  });
  if (el.children.length) card.appendChild(el);
}

function _wFeatureFlags(card, p, w) {
  const feats = [];
  (w.flags || []).forEach(f => {
    const v = _bind(p, f.bind);
    if (v != null) feats.push(v ? `${f.label} ✓` : `${f.label} ✗`);
  });
  if (!feats.length) return;
  const el = document.createElement('div');
  el.className = 'sb-features';
  el.dataset.role = 'features';
  el.innerHTML = feats.map(f => {
    const dim = f.endsWith('✗');
    return `<span style="color:${dim ? 'rgba(200,185,150,0.25)' : 'rgba(140,190,140,0.6)'}">${esc(f)}</span>`;
  }).join('<br>');
  card.appendChild(el);
}

function _wBadge(card, p, w) {
  if (!_bind(p, w.bind)) return;
  const el = document.createElement('div');
  el.className = w.cls || 'sb-inspiration';
  el.dataset.role = w.role || 'inspiration';
  el.textContent = w.label || '';
  card.appendChild(el);
}

const _SIDEBAR_WIDGETS = {
  bar: _wBar, stat_lines: _wStatLines, tag_list: _wTagList, tag_single: _wTagSingle,
  effects: _wEffects, badge_set: _wBadgeSet, pip_levels: _wPipLevels,
  feature_flags: _wFeatureFlags, badge: _wBadge,
};

function _renderSidebarWidget(card, p, w) {
  const fn = w && _SIDEBAR_WIDGETS[w.type];
  if (fn) { try { fn(card, p, w); } catch (e) { console.warn('[GM] widget render failed:', w && w.type, e); } }
}

function _buildPlayerCard(p, solo) {
  const card = document.createElement('div');
  card.className = 'sb-player';
  card.dataset.playerName = p.name;

  // Class icon (top-right corner)
  const classIco = _classIconSrc(p.class);
  if (classIco) {
    const ico = document.createElement('img');
    ico.src = classIco; ico.className = 'sb-class-icon'; ico.alt = '';
    card.appendChild(ico);
  }

  // Name
  const nameEl = document.createElement('div');
  nameEl.className = 'sb-name';
  nameEl.textContent = p.name || '—';
  card.appendChild(nameEl);

  // Identity: Race · Class Level
  const identEl = document.createElement('div');
  identEl.className = 'sb-identity';
  const parts = [];
  // Line 1: Race · BaseClass Level   Line 2: Subclass (Background)
  // p.class may be "Rogue/Thief (Criminal)" — split on "/" if present
  let baseClass = p.class || '';
  let subLine = p.background || '';
  if (baseClass.includes('/')) {
    const slashIdx = baseClass.indexOf('/');
    subLine = baseClass.slice(slashIdx + 1).trim() + (p.background ? ' · ' + p.background : '');
    baseClass = baseClass.slice(0, slashIdx).trim();
  }
  const line1 = [];
  if (p.race)    line1.push(p.race);
  if (baseClass) line1.push(baseClass + (p.level ? ' ' + p.level : ''));
  identEl.innerHTML = esc(line1.join(' · ') || '—') +
    (subLine ? `<br><span style="opacity:0.7">${esc(subLine)}</span>` : '');
  card.appendChild(identEl);

  // Stat widgets — driven by the active system's UI manifest (systems/<system>/ui.json
  // -> window.GM_UI_MANIFEST), falling back to the built-in D&D 5e default.
  // See systems/UI-MANIFEST.md. Each widget self-hides when its bound field is absent.
  (_gmManifest().sidebar || []).forEach(w => _renderSidebarWidget(card, p, w));

  // Direct listener — belt-and-suspenders alongside the delegated sidebar listener
  card.addEventListener('click', function(e) {
    e.stopPropagation();
    openSheet(p.name);
  });

  return card;
}

// ── Character sheet modal ────────────────────────────────────────────────────

function _smDivider() {
  const d = document.createElement('div');
  d.className = 'sm-divider';
  return d;
}

let _openSheetName = null;

function openSheet(name) {
  _openSheetName = name;
  const p = _playerData[name];
  if (!p) {
    console.warn(
      `[DnD] openSheet("${name}"): no player data found.`,
      'Available:', Object.keys(_playerData),
      '— Run /gm load to push character data.'
    );
    return;
  }
  const content = document.getElementById('sheet-content');
  content.innerHTML = '';

  // Class icon in sheet panel top-right (clean up previous if any)
  const panel = document.getElementById('sheet-panel');
  const prevIco = document.getElementById('sm-class-ico');
  if (prevIco) prevIco.remove();
  const sheetClassIco = _classIconSrc(p.class);
  if (sheetClassIco) {
    const ico = document.createElement('img');
    ico.id = 'sm-class-ico'; ico.className = 'sm-header-icon';
    ico.src = sheetClassIco; ico.alt = '';
    panel.appendChild(ico);
  }

  // Header
  const nameEl = document.createElement('div');
  nameEl.className = 'sm-name';
  nameEl.textContent = p.name || '—';
  content.appendChild(nameEl);

  const identParts = [];
  if (p.race)       identParts.push(p.race);
  if (p.class)      identParts.push(p.class + (p.level ? ' ' + p.level : ''));
  if (p.background) identParts.push(p.background);
  const identEl = document.createElement('div');
  identEl.className = 'sm-identity';
  identEl.textContent = identParts.join('  ·  ') || '—';
  content.appendChild(identEl);

  // Combat stats strip — driven by the active system's manifest (sheet.combat_strip).
  const _sheetMan = _gmManifest();
  const _stripDef = (_sheetMan.sheet && _sheetMan.sheet.combat_strip)
    || DEFAULT_UI_MANIFEST.sheet.combat_strip;
  const strip = document.createElement('div');
  strip.className = 'sm-combat-strip';
  const _stripCell = (label, val) => {
    const cell = document.createElement('div');
    cell.className = 'sm-stat-cell';
    cell.innerHTML = `<span class="sm-stat-label">${esc(label)}</span><span class="sm-stat-val">${esc(val)}</span>`;
    strip.appendChild(cell);
  };
  _stripDef.forEach(entry => {
    const v = _bind(p, entry.bind);
    let val = '—';
    if (entry.format === 'ratio') {
      val = (v && v.current != null) ? `${v.current}/${v.max}` : '—';
    } else if (entry.format === 'hd') {
      val = (v && v.die) ? `${v.remaining ?? '?'}/${v.max ?? '?'} ${v.die}` : '—';
    } else if (v != null) {
      val = v + (entry.suffix || '');
    }
    _stripCell(entry.label, val);
    // Temp HP rides alongside the HP ratio cell, matching the original layout.
    if (entry.format === 'ratio' && entry.bind === 'hp' && v && v.temp) {
      _stripCell('Temp HP', '+' + v.temp);
    }
  });
  content.appendChild(strip);

  if (p.xp && p.xp.current != null) {
    const xpEl = document.createElement('div');
    xpEl.className = 'sm-xp';
    xpEl.textContent = `XP  ${p.xp.current} / ${p.xp.next ?? '?'}`;
    content.appendChild(xpEl);
  }

  // Attribute grid — driven by the manifest (sheet.stat_grid). Handles both
  // D&D-style {score, mod} entries (show_modifier) and pool-system raw ratings.
  const _gridDef = (_sheetMan.sheet && _sheetMan.sheet.stat_grid)
    || DEFAULT_UI_MANIFEST.sheet.stat_grid;
  const _abils = _bind(p, _gridDef.bind || 'ability_scores');
  if (_abils) {
    content.appendChild(_smDivider());
    const title = document.createElement('div');
    title.className = 'sm-section-title';
    title.textContent = _gridDef.label || 'Ability Scores';
    content.appendChild(title);
    const grid = document.createElement('div');
    grid.className = 'sm-ab-grid';
    (_gridDef.stats || []).forEach(st => {
      const raw = _abils[st.key];
      const showMod = (st.show_modifier !== undefined) ? st.show_modifier : _gridDef.show_modifier;
      let score, mod;
      if (raw && typeof raw === 'object') { score = raw.score; mod = raw.mod; }
      else { score = raw; }
      const cell = document.createElement('div');
      cell.className = 'sm-ab-cell';
      cell.innerHTML = `<div class="sm-ab-name">${esc(st.label || String(st.key).toUpperCase())}</div>
        <div class="sm-ab-score">${esc(score ?? '—')}</div>` +
        (showMod ? `<div class="sm-ab-mod">${esc(mod ?? '—')}</div>` : '');
      grid.appendChild(cell);
    });
    content.appendChild(grid);
  }

  const sheet = p.sheet || {};

  // Show friendly notice when sheet data hasn't been pushed yet
  if (!p.sheet) {
    const noData = document.createElement('div');
    noData.className = 'sm-no-data';
    noData.textContent = 'Full sheet not loaded — run /gm load to restore.';
    content.appendChild(noData);
  }

  // Attacks
  if (sheet.attacks && sheet.attacks.length) {
    content.appendChild(_smDivider());
    const title = document.createElement('div');
    title.className = 'sm-section-title';
    title.innerHTML = '<img src="/icons/dagger.png" class="sheet-section-icon" alt="">Attacks';
    content.appendChild(title);
    const table = document.createElement('table');
    table.className = 'sm-attacks-table';
    table.innerHTML = `<thead><tr>
      <th>Name</th><th>Bonus</th><th>Damage</th><th>Type</th><th>Notes</th>
    </tr></thead>`;
    const tbody = document.createElement('tbody');
    sheet.attacks.forEach(a => {
      const tr = document.createElement('tr');
      const nameCell = document.createElement('td');
      nameCell.className = 'atk-name';
      nameCell.textContent = a.name || '—';
      if (a.name) { nameCell.dataset.srdName = a.name; nameCell.dataset.srdCategory = 'item'; }
      tr.appendChild(nameCell);
      tr.insertAdjacentHTML('beforeend',
        `<td>${esc(a.bonus||'—')}</td><td>${esc(a.damage||'—')}</td>
         <td>${esc(a.type||'—')}</td><td class="atk-notes">${esc(a.notes||'')}</td>`);
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    content.appendChild(table);
  }

  // Spells
  if (sheet.spells) {
    const sp = sheet.spells;
    content.appendChild(_smDivider());
    const title = document.createElement('div');
    title.className = 'sm-section-title';
    title.innerHTML = '<img src="/icons/crystal_ball.png" class="sheet-section-icon" alt="">Spellcasting';
    content.appendChild(title);
    // Derive live slot string from spell_slots object if available; fall back to static string
    let _slotsStr = sp.slots || '';
    if (p.spell_slots && Object.keys(p.spell_slots).length) {
      const _ROM = ['','I','II','III','IV','V','VI','VII','VIII','IX'];
      _slotsStr = Object.entries(p.spell_slots)
        .sort(([a],[b]) => +a - +b)
        .filter(([,s]) => (s.max || 0) > 0)
        .map(([lvl,s]) => `${_ROM[+lvl]||lvl}: ${(s.max||0)-(s.used||0)}/${s.max||0}`)
        .join('  ');
    }
    if (_slotsStr || sp.save_dc || sp.attack_bonus) {
      const meta = document.createElement('div');
      meta.className = 'sm-spell-meta';
      const parts = [];
      if (_slotsStr)        parts.push('Slots: ' + _slotsStr);
      if (sp.save_dc)       parts.push('Save DC ' + sp.save_dc);
      if (sp.attack_bonus)  parts.push('Atk ' + sp.attack_bonus);
      meta.textContent = parts.join('  ·  ');
      content.appendChild(meta);
    }
    if (sp.cantrips && sp.cantrips.length) {
      const lbl = document.createElement('div');
      lbl.className = 'sm-sub-label cantrip-label';
      lbl.textContent = 'Cantrips';
      content.appendChild(lbl);
      const grid = document.createElement('div');
      grid.className = 'sm-spell-grid';
      sp.cantrips.forEach(s => {
        const tag = document.createElement('span');
        tag.className = 'sm-spell-tag cantrip';
        tag.textContent = s;
        tag.dataset.srdName = s; tag.dataset.srdCategory = 'spell';
        grid.appendChild(tag);
      });
      content.appendChild(grid);
    }
    if (sp.prepared && sp.prepared.length) {
      const lbl = document.createElement('div');
      lbl.className = 'sm-sub-label prepared-label';
      lbl.textContent = 'Prepared';
      content.appendChild(lbl);
      const grid = document.createElement('div');
      grid.className = 'sm-spell-grid';
      sp.prepared.forEach(s => {
        const tag = document.createElement('span');
        tag.className = 'sm-spell-tag';
        tag.textContent = s;
        tag.dataset.srdName = s; tag.dataset.srdCategory = 'spell';
        grid.appendChild(tag);
      });
      content.appendChild(grid);
    }
  }

  // Features & Passives
  if (sheet.features && sheet.features.length) {
    content.appendChild(_smDivider());
    const title = document.createElement('div');
    title.className = 'sm-section-title';
    title.innerHTML = '<img src="/icons/scroll.png" class="sheet-section-icon" alt="">Features & Passives';
    content.appendChild(title);
    sheet.features.forEach(f => {
      const el = document.createElement('div');
      el.className = 'sm-feature';
      const nameEl = document.createElement('div');
      nameEl.className = 'sm-feature-name';
      nameEl.textContent = f.name;
      nameEl.dataset.srdName = f.name; nameEl.dataset.srdCategory = 'feature';
      const textEl = document.createElement('div');
      textEl.className = 'sm-feature-text';
      textEl.textContent = f.text;
      el.appendChild(nameEl);
      el.appendChild(textEl);
      content.appendChild(el);
    });
  }

  // Inventory
  if (sheet.inventory && sheet.inventory.length) {
    content.appendChild(_smDivider());
    const title = document.createElement('div');
    title.className = 'sm-section-title';
    title.innerHTML = '<img src="/icons/pack.png" class="sheet-section-icon" alt="">Inventory';
    content.appendChild(title);
    const list = document.createElement('ul');
    list.className = 'sm-inventory-list';
    sheet.inventory.forEach(item => {
      const li = document.createElement('li');
      li.textContent = item;
      li.dataset.srdName = item; li.dataset.srdCategory = 'item';
      list.appendChild(li);
    });
    content.appendChild(list);
  }

  // Relationships
  if (sheet.relationships && sheet.relationships.length) {
    content.appendChild(_smDivider());
    const title = document.createElement('div');
    title.className = 'sm-section-title';
    title.textContent = 'Relationships';
    content.appendChild(title);
    sheet.relationships.forEach(r => {
      const el = document.createElement('div');
      el.className = 'sm-relationship';
      const nm = document.createElement('span');
      nm.className = 'sm-rel-name';
      nm.textContent = r.name || '—';
      const role = document.createElement('span');
      role.className = 'sm-rel-role';
      role.textContent = [r.role, r.standing].filter(Boolean).join(' · ');
      const note = document.createElement('span');
      note.className = 'sm-rel-note';
      note.textContent = r.note || '';
      el.appendChild(nm); el.appendChild(role); el.appendChild(note);
      content.appendChild(el);
    });
  }

  // No sheet data hint
  if (!sheet.attacks && !sheet.spells && !sheet.features && !sheet.inventory) {
    content.appendChild(_smDivider());
    const noData = document.createElement('div');
    noData.className = 'sm-no-data';
    noData.textContent = 'Full sheet not loaded — include "sheet" in the player object when pushing stats on /gm load';
    content.appendChild(noData);
  }

  document.getElementById('sheet-modal').classList.add('open');
  document.getElementById('sheet-panel').scrollTop = 0;
}

function closeSheet() {
  document.getElementById('sheet-modal').classList.remove('open');
}

document.getElementById('sheet-close').addEventListener('click', closeSheet);
document.getElementById('sheet-modal').addEventListener('click', e => {
  if (e.target === document.getElementById('sheet-modal')) closeSheet();
});

// ── SRD lookup modal ──────────────────────────────────────────────────────────
function openSrdModal(name, category, level) {
  const modal = document.getElementById('srd-modal');
  const body  = document.getElementById('srd-body');
  const badge = document.getElementById('srd-category-badge');
  badge.textContent = '';
  body.innerHTML = '<div id="srd-loading">Looking up…</div>';
  modal.classList.add('open');
  document.getElementById('srd-panel').scrollTop = 0;

  const params = new URLSearchParams({ name });
  if (category) params.set('category', category);
  if (level)    params.set('level', level);
  fetch('/srd-lookup?' + params)
    .then(r => r.json())
    .then(data => {
      if (!data.found) {
        // The label travels with the URL, because the destination is not always
        // the same reference and a link should say where it goes before it is
        // clicked. No verified destination for this category means no link.
        const refUrl   = data.reference_url || data.wikidot_url || '';
        const refLabel = data.reference_label || 'View on D&D 5e Wiki';
        const linkHtml = refUrl
          ? `<br><a href="${esc(refUrl)}" target="_blank" rel="noopener" style="color:#c9a84c;font-size:12px;opacity:0.85;text-decoration:none;">${esc(refLabel)} ↗</a>`
          : `<br><span style="font-size:12px;opacity:0.6">May be from a supplement (Xanathar's, Tasha's, etc.)</span>`;
        // Near-miss "did you mean?" chips — tap one to re-run the lookup on the
        // closest matching name. Recovers a mistyped spell/condition/monster
        // instead of dead-ending on the not-found notice.
        let didYouMean = '';
        if (Array.isArray(data.suggestions) && data.suggestions.length) {
          const chips = data.suggestions.map(s =>
            `<button class="srd-suggest-chip" data-srd-name="${esc(s.name)}" data-srd-category="${esc(s.category || '')}">${esc(s.name)}</button>`
          ).join('');
          didYouMean = `<div class="srd-did-you-mean"><span class="srd-dym-label">Did you mean</span>${chips}</div>`;
        }
        body.innerHTML = `<div id="srd-not-found">"${esc(name)}" is not in the local dataset.${linkHtml}</div>${didYouMean}`;
        body.querySelectorAll('.srd-suggest-chip').forEach(chip => {
          chip.addEventListener('click', () => {
            openSrdModal(chip.dataset.srdName, chip.dataset.srdCategory || '', level);
          });
        });
        return;
      }
      badge.textContent = data.category || '';
      // Render the pre-formatted text — first line is always "## Title [...]"
      const lines = (data.text || '').split('\n');
      let html = '';
      let inDesc = false;
      lines.forEach(line => {
        if (line.startsWith('## ')) {
          // Title line
          html += `<div class="srd-title">${esc(line.replace(/^## /, ''))}</div>`;
        } else if (line === '') {
          if (inDesc) html += '\n';
        } else if (!inDesc && line.match(/^[A-Z][a-z].*:/)) {
          // Key-value meta line
          html += `<div class="srd-meta">${esc(line)}</div>`;
        } else {
          if (!inDesc) { html += '<div class="srd-desc">'; inDesc = true; }
          html += esc(line) + '\n';
        }
      });
      if (inDesc) html += '</div>';
      body.innerHTML = html;
    })
    .catch(() => {
      body.innerHTML = '<div id="srd-not-found">Lookup unavailable — is the display running?</div>';
    });
}

function closeSrdModal() {
  document.getElementById('srd-modal').classList.remove('open');
}

document.getElementById('srd-close').addEventListener('click', closeSrdModal);
document.getElementById('srd-modal').addEventListener('click', e => {
  if (e.target === document.getElementById('srd-modal')) closeSrdModal();
});

// Delegate clicks from sheet-content to SRD modal
document.getElementById('sheet-content').addEventListener('click', e => {
  const el = e.target.closest('[data-srd-name]');
  if (!el) return;
  e.stopPropagation();
  const pd = _openSheetName && _playerData[_openSheetName];
  // Prefer explicit level field; fall back to trailing number in class string ("Rogue 3" → 3)
  let level = pd ? (pd.level || null) : null;
  if (!level && pd && pd.class) {
    const m = pd.class.match(/\b(\d+)\s*$/);
    if (m) level = parseInt(m[1]);
  }
  openSrdModal(el.dataset.srdName, el.dataset.srdCategory || '', level);
});

// Delegated listener — catches clicks on any .sb-player card even after DOM rebuilds
document.getElementById('sidebar').addEventListener('click', function(e) {
  const card = e.target.closest('.sb-player[data-player-name]');
  if (card) openSheet(card.dataset.playerName);
});
document.addEventListener('keydown', e => {
  if (e.key === 'Escape') closeSheet();
});

// ── Timed effect helpers ──────────────────────────────────────────────────────

function _fmtDuration(ms) {
  const s = Math.floor(ms / 1000);
  if (s <= 0) return '0:00';
  const m = Math.floor(s / 60);
  const sec = s % 60;
  if (m >= 60) {
    const h = Math.floor(m / 60);
    const min = m % 60;
    return min > 0 ? `${h}h ${min}m` : `${h}h`;
  }
  return `${m}:${String(sec).padStart(2, '0')}`;
}

function _makeEffectPill(eff, ownerName) {
  const pill = document.createElement('span');
  const isConc = eff.concentration;
  let warn = false;

  if (eff.duration_type === 'rounds') {
    const r = eff.duration_remaining || 0;
    pill.textContent = `⧗ ${eff.name} · ${r} rnd`;
    warn = r <= 2;
    pill.dataset.durationType = 'rounds';
  } else if (eff.duration_type === 'minutes' || eff.duration_type === 'hours') {
    const expireAt = (eff.started_at + eff.duration_seconds) * 1000;
    const remaining = Math.max(0, expireAt - Date.now());
    pill.textContent = `⧗ ${eff.name} ${_fmtDuration(remaining)}`;
    warn = remaining < eff.duration_seconds * 100;  // last 10%
    pill.dataset.durationType = 'time';
    pill.dataset.expireAt     = expireAt;
    pill.dataset.totalMs      = eff.duration_seconds * 1000;
    pill.dataset.owner        = ownerName;
    pill.dataset.spell        = eff.name;
  } else {
    pill.textContent = `⧗ ${eff.name} ∞`;
    pill.dataset.durationType = 'indefinite';
  }

  pill.className = 'sb-effect-pill' + (isConc ? ' conc' : '') + (warn ? ' warning' : '');
  return pill;
}

function renderEffectExpiredBlock(owner, name, wasConcentration) {
  _flushForBlock();
  const block = document.createElement('div');
  block.className = 'effect-expired-block';
  const inner = document.createElement('div');
  inner.className = 'effect-expired-result';
  const icon = wasConcentration ? '◈' : '⧗';
  inner.textContent = `${icon} ${name} (${owner}) — ${wasConcentration ? 'concentration ends' : 'expired'}`;
  block.appendChild(inner);
  textContent.appendChild(block);
  textScroll.scrollTop = textScroll.scrollHeight;
}

// Countdown ticker — updates time-based effect pills every second
setInterval(() => {
  document.querySelectorAll('.sb-effect-pill[data-duration-type="time"]').forEach(pill => {
    if (pill.dataset.fired) return;
    const expireAt = +pill.dataset.expireAt;
    const remaining = Math.max(0, expireAt - Date.now());
    const spell = pill.dataset.spell;
    pill.textContent = `⧗ ${spell} ${_fmtDuration(remaining)}`;

    const totalMs = +pill.dataset.totalMs || 1;
    const warn = remaining < totalMs * 0.1 || remaining < 10000;
    const isConc = pill.classList.contains('conc');
    pill.className = 'sb-effect-pill' + (isConc ? ' conc' : '') + (warn ? ' warning' : '');

    if (remaining <= 0) {
      pill.dataset.fired = '1';
      const owner = pill.dataset.owner;
      fetch('/effects/expire', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(_dndToken ? {'X-DND-Token': _dndToken} : {}),
        },
        body: JSON.stringify({owner, name: spell}),
      }).catch(() => {});
    }
  });
}, 1000);

// ── Stats update ─────────────────────────────────────────────────────────────

function updateStats(stats) {
  if (!stats || !stats.players) return;
  const sidebar = document.getElementById('sidebar');
  const turnSection = document.getElementById('sb-turn-section');
  const solo = stats.players.length === 1;

  // Merge incoming players into _playerData
  stats.players.forEach(p => {
    if (!p.name) return;
    const existing = _playerData[p.name] || {};
    // Deep merge hp, xp, ability_scores; replace scalars
    for (const [k, v] of Object.entries(p)) {
      if (v !== null && typeof v === 'object' && !Array.isArray(v) && typeof existing[k] === 'object') {
        existing[k] = Object.assign({}, existing[k], v);
      } else {
        existing[k] = v;
      }
    }
    _playerData[p.name] = existing;
  });

  // Rebuild all player cards (insert before turn section)
  const names = Object.keys(_playerData);
  names.forEach(name => {
    const p = _playerData[name];
    const prevHp = _prevHp[name];
    const newHp  = p.hp ? p.hp.current : undefined;

    const existing = sidebar.querySelector(`.sb-player[data-player-name="${CSS.escape(name)}"]`);
    const newCard = _buildPlayerCard(p, solo);
    if (existing) {
      sidebar.insertBefore(newCard, existing);
      existing.remove();
    } else {
      sidebar.insertBefore(newCard, turnSection);
    }

    // Flash HP numeral if HP changed
    if (newHp !== undefined && prevHp !== undefined && newHp !== prevHp) {
      const numsEl = newCard.querySelector('.sb-hp-nums');
      if (numsEl) {
        numsEl.classList.remove('hp-flash-dmg', 'hp-flash-heal');
        void numsEl.offsetWidth;
        numsEl.classList.add(newHp < prevHp ? 'hp-flash-dmg' : 'hp-flash-heal');
      }
    }
    if (newHp !== undefined) _prevHp[name] = newHp;
  });

  sidebar.classList.add('has-data');

  // Faction panel
  if ('factions' in stats) {
    const factPanel = document.getElementById('sb-factions');
    const factions  = stats.factions || [];
    factPanel.querySelectorAll('.sb-faction-item').forEach(el => el.remove());
    if (factions.length) {
      const SCLS = { allied:'allied', friendly:'friendly', neutral:'neutral',
                     unfriendly:'unfriendly', suspicious:'suspicious', hostile:'hostile' };
      factions.forEach(f => {
        const item = document.createElement('div');
        item.className = 'sb-faction-item';
        const nm = document.createElement('span');
        nm.className = 'sb-faction-name';
        nm.textContent = f.name || '—';
        const st = document.createElement('span');
        st.className = 'sb-faction-standing ' + (SCLS[(f.standing||'').toLowerCase()] || 'unknown');
        st.textContent = f.standing || '—';
        item.appendChild(nm);
        item.appendChild(st);
        factPanel.appendChild(item);
      });
      factPanel.style.display = '';
    } else {
      factPanel.style.display = 'none';
    }
  }

  // Quest panel
  if ('quests' in stats) {
    const questPanel = document.getElementById('sb-quests');
    const quests = stats.quests || [];
    questPanel.querySelectorAll('.sb-quest-item').forEach(el => el.remove());
    if (quests.length) {
      const QCLS = { active:'active', threat:'threat', resolved:'resolved', failed:'failed' };
      quests.forEach(q => {
        const item = document.createElement('div');
        item.className = 'sb-quest-item';
        const nm = document.createElement('span');
        nm.className = 'sb-quest-name';
        nm.textContent = q.name || '—';
        const st = document.createElement('span');
        st.className = 'sb-quest-status ' + (QCLS[(q.status||'').toLowerCase()] || 'unknown');
        st.textContent = q.status || '—';
        item.appendChild(nm);
        item.appendChild(st);
        questPanel.appendChild(item);
      });
      questPanel.style.display = '';
    } else {
      questPanel.style.display = 'none';
    }
  }

  // Turn order
  if ('turn_order' in stats) {
    if (!stats.turn_order) {
      turnSection.style.display = 'none';
    } else {
      const to = stats.turn_order;
      // Merge with existing if partial (only current or only round supplied)
      const existingTo = _currentTurnOrder || {};
      const merged = Object.assign({}, existingTo, to);
      _currentTurnOrder = merged;

      turnSection.style.display = '';
      document.getElementById('sb-round').textContent = merged.round ?? 1;
      const list = document.getElementById('sb-turn-list');
      list.innerHTML = '';
      (merged.order || []).forEach(name => {
        const el = document.createElement('div');
        el.className = 'sb-turn-item sb-turn-has-icon' + (name === merged.current ? ' sb-turn-current' : '');
        const isPC = name in _playerData;
        const iconSrc = isPC ? '/icons/helmet.png' : '/icons/enemy.png';
        el.innerHTML = `<img src="${iconSrc}" class="sb-turn-icon" alt="">${esc(name)}`;
        list.appendChild(el);
      });
    }
  }
}

let _currentTurnOrder = null;

// ═══════════════════════════════════════════════════════════════════
// AUDIO — SFX only. Python detects triggers from narration text and
// broadcasts {"sfx": name} via SSE. Browser fetches WAV from
// /audio/sfx/<name> and plays via Web Audio API. Toggle click is the
// user gesture that unlocks AudioContext.
// ═══════════════════════════════════════════════════════════════════


const _sfxTrack = document.getElementById('sfx-track');
let _sfxOn = false;

// ── DM Help button ──────────────────────────────────────────────────
const _dmHelpBtn = document.getElementById('dm-help-btn');
let _helpPending = false;
let _helpResetTimer = null;

function _dmHelpReset() {
  _helpPending = false;
  _dmHelpBtn.disabled = false;
  _dmHelpBtn.classList.remove('pending', 'locked');
  _dmHelpBtn.textContent = '◈ DM Help';
}

_dmHelpBtn.addEventListener('click', async () => {
  if (_helpPending) return;
  _helpPending = true;
  _dmHelpBtn.disabled = true;
  _dmHelpBtn.classList.add('pending');
  _dmHelpBtn.textContent = '◈ Thinking…';

  try {
    const res = await fetch('/help-request', {
      method: 'POST',
      headers: _dndToken ? { 'X-DND-Token': _dndToken } : {},
    });
    if (res.status === 409) {
      // Another request already running
      _dmHelpBtn.classList.remove('pending');
      _dmHelpBtn.classList.add('locked');
      _dmHelpBtn.textContent = '◈ Running…';
      // Auto-reset when the tutor block arrives (see renderTutorBlock)
      // Safety timeout in case it never arrives
      clearTimeout(_helpResetTimer);
      _helpResetTimer = setTimeout(_dmHelpReset, 20000);
    } else if (res.status === 202) {
      // Accepted — wait for the tutor SSE block to arrive
      // renderTutorBlock will call _dmHelpReset()
      clearTimeout(_helpResetTimer);
      _helpResetTimer = setTimeout(_dmHelpReset, 20000);
    } else {
      _dmHelpReset();
    }
  } catch (_) {
    _dmHelpReset();
  }
});

// Read token from meta tag injected by Flask (LAN mode)
const _dndTokenMeta = document.querySelector('meta[name="dnd-token"]');
const _dndToken = _dndTokenMeta ? _dndTokenMeta.getAttribute('content') : null;

// Persistent device ID — survives page refreshes, identifies this browser for DM approval
// crypto.randomUUID() requires a secure context (HTTPS/localhost); fall back to
// getRandomValues() which works over plain HTTP on LAN.
function _makeUUID() {
  if (typeof crypto.randomUUID === 'function') return crypto.randomUUID();
  return ([1e7]+-1e3+-4e3+-8e3+-1e11).replace(/[018]/g, c =>
    (c ^ crypto.getRandomValues(new Uint8Array(1))[0] & 15 >> c / 4).toString(16));
}
let _deviceId = localStorage.getItem('dnd_device_id');
if (!_deviceId) {
  _deviceId = _makeUUID();
  localStorage.setItem('dnd_device_id', _deviceId);
}

function _authHeaders() {
  return {
    'Content-Type': 'application/json',
    ...(_dndToken    ? { 'X-DND-Token':   _dndToken  } : {}),
    ...(_deviceId    ? { 'X-DND-Device':  _deviceId  } : {}),
  };
}

let _actx   = null;
let _sfxCache = {};

function _setTrack(el, on) {
  on ? el.classList.add('on') : el.classList.remove('on');
}

function _audioCtx() {
  if (!_actx) _actx = new (window.AudioContext || window.webkitAudioContext)();
  if (_actx.state === 'suspended') _actx.resume();
  return _actx;
}

async function _playSfx(name) {
  if (!_sfxOn) return;
  try {
    if (!_sfxCache[name]) {
      const r = await fetch(`/audio/sfx/${name}`);
      if (!r.ok) return;
      const ab  = await r.arrayBuffer();
      _sfxCache[name] = await _audioCtx().decodeAudioData(ab);
    }
    if (!_sfxOn) return;
    const src = _audioCtx().createBufferSource();
    src.buffer = _sfxCache[name];
    src.connect(_audioCtx().destination);
    src.start();
  } catch (_) {}
}

document.getElementById('sfx-row').addEventListener('click', () => {
  _sfxOn = !_sfxOn;
  _setTrack(_sfxTrack, _sfxOn);
  try {
    fetch('/audio-toggle', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ sfx: _sfxOn }),
    });
  } catch (_) {}
});

// ── Typing speed toggle ──────────────────────────────────────────────
const _speedSteps = [
  { label: 'Normal',  ms: 36 },
  { label: 'Fast',    ms: 18 },
  { label: 'Instant', ms: 0  },
];
let _speedIdx = 0;
const _speedLabel = document.getElementById('speed-label');
document.getElementById('speed-row').addEventListener('click', () => {
  _speedIdx = (_speedIdx + 1) % _speedSteps.length;
  const step = _speedSteps[_speedIdx];
  charDelay = step.ms;
  _speedLabel.textContent = step.label;
});

// ═══════════════════════════════════════════════════════════════════
// TTS — per-block narrator audio playback (Gemini Flash TTS)
// Only .dm-block and .npc-block get a TTS bar. Player/dice/tutor blocks
// are excluded by omission in their renderers.
//
// Per-browser AudioContext-based engine (NOT HTMLAudioElement) — iOS
// WebKit gesture handling is more reliable: ctx.resume() once inside a
// user gesture and the context stays running for the session.
//
// Auto-narrate is per-browser (localStorage). One device casting to a TV
// can toggle it on without affecting player phones.
// ═══════════════════════════════════════════════════════════════════

const _TTS_VOICES_MALE   = ['Charon', 'Enceladus', 'Fenrir', 'Umbriel'];
const _TTS_VOICES_FEMALE = ['Aoede', 'Gacrux', 'Kore', 'Vindemiatrix', 'Zephyr'];

const _ttsAvailableMeta = document.querySelector('meta[name="tts-available"]');
const _TTS_AVAILABLE = _ttsAvailableMeta && _ttsAvailableMeta.getAttribute('content') === '1';

const _narratorVoiceMeta = document.querySelector('meta[name="narrator-voice"]');
let _ttsVoice = (_narratorVoiceMeta && _narratorVoiceMeta.getAttribute('content'))
  || localStorage.getItem('gm_tts_voice')
  || 'Enceladus';

let _ttsAutoNarrate = localStorage.getItem('gm_tts_auto') === '1';

let _ttsCtx = null;
let _ttsActiveSource = null;
let _ttsActivePlayBtn = null;

function _getTtsCtx() {
  if (!_ttsCtx || _ttsCtx.state === 'closed') {
    _ttsCtx = new (window.AudioContext || window.webkitAudioContext)();
  }
  return _ttsCtx;
}

const _SVG_SPEAKER = '<svg xmlns="http://www.w3.org/2000/svg" width="15" height="15" viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="M3 5.5h3l4-3v11l-4-3H3v-5z" fill="currentColor"/><path d="M10.5 5.2c.9.8 1.5 1.8 1.5 2.8s-.6 2-1.5 2.8" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/><path d="M12.2 3.4c1.5 1.2 2.3 2.7 2.3 4.6s-.8 3.4-2.3 4.6" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/></svg>';
const _SVG_STOP    = '<svg xmlns="http://www.w3.org/2000/svg" width="13" height="13" viewBox="0 0 12 12" fill="none" aria-hidden="true"><rect x="2" y="2" width="8" height="8" rx="1.5" fill="currentColor"/></svg>';
const _SVG_SPIN    = '<svg xmlns="http://www.w3.org/2000/svg" width="13" height="13" viewBox="0 0 24 24" fill="none" class="tts-spin" aria-hidden="true"><circle cx="12" cy="12" r="10" stroke="currentColor" stroke-width="3" stroke-dasharray="31.4 31.4" stroke-linecap="round"/></svg>';
const _SVG_CHEVRON = '<svg xmlns="http://www.w3.org/2000/svg" width="9" height="9" viewBox="0 0 10 10" fill="none" aria-hidden="true"><path d="M2 3.5l3 3 3-3" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>';

function _ttsStopActive() {
  if (_ttsActiveSource) {
    try { _ttsActiveSource.stop(); } catch (_) {}
    _ttsActiveSource = null;
  }
  if (_ttsActivePlayBtn) {
    _ttsActivePlayBtn.innerHTML = _SVG_SPEAKER;
    _ttsActivePlayBtn.classList.remove('tts-playing', 'tts-loading');
    _ttsActivePlayBtn.setAttribute('aria-label', 'Listen to narration');
    _ttsActivePlayBtn = null;
  }
}

function _ttsExtractText(blockEl) {
  const clone = blockEl.cloneNode(true);
  const bar = clone.querySelector('.tts-bar');
  if (bar) bar.remove();
  return (clone.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 2000);
}

async function _ttsPlayBlock(btn, blockEl) {
  if (btn.classList.contains('tts-playing')) { _ttsStopActive(); return; }
  _ttsStopActive();
  const text = _ttsExtractText(blockEl);
  if (!text) return;

  const ctx = _getTtsCtx();
  const resumeP = ctx.state !== 'running' ? ctx.resume() : Promise.resolve();

  btn.innerHTML = _SVG_SPIN;
  btn.classList.add('tts-loading');
  btn.setAttribute('aria-label', 'Loading…');
  _ttsActivePlayBtn = btn;

  try {
    const resp = await fetch('/tts', {
      method: 'POST',
      headers: _authHeaders(),
      body: JSON.stringify({ text, voice: _ttsVoice }),
    });
    if (!resp.ok) throw new Error('TTS ' + resp.status);
    const pcm = await resp.arrayBuffer();
    if (!pcm.byteLength) throw new Error('TTS empty');

    await resumeP;
    if (ctx.state === 'suspended') await ctx.resume();

    const int16 = new Int16Array(pcm);
    const audioBuffer = ctx.createBuffer(1, int16.length, 24000);
    const ch = audioBuffer.getChannelData(0);
    for (let i = 0; i < int16.length; i++) ch[i] = int16[i] / 32768.0;

    const source = ctx.createBufferSource();
    source.buffer = audioBuffer;
    source.connect(ctx.destination);

    btn.innerHTML = _SVG_STOP;
    btn.classList.remove('tts-loading');
    btn.classList.add('tts-playing');
    btn.setAttribute('aria-label', 'Stop narration');
    _ttsActiveSource = source;

    source.onended = () => {
      if (_ttsActiveSource === source) {
        _ttsActiveSource = null;
        if (_ttsActivePlayBtn === btn) {
          btn.innerHTML = _SVG_SPEAKER;
          btn.classList.remove('tts-playing');
          btn.setAttribute('aria-label', 'Listen to narration');
          _ttsActivePlayBtn = null;
        }
      }
    };
    source.start(0);
  } catch (err) {
    if (_ttsActivePlayBtn === btn) {
      const label = (err.message || 'ERR').slice(0, 12);
      btn.textContent = label;
      btn.classList.remove('tts-loading', 'tts-playing');
      setTimeout(() => {
        if (!btn.classList.contains('tts-playing') && !btn.classList.contains('tts-loading')) {
          btn.innerHTML = _SVG_SPEAKER;
          btn.setAttribute('aria-label', 'Listen to narration');
        }
      }, 3000);
      _ttsActivePlayBtn = null;
    }
    _ttsActiveSource = null;
  }
}

function _ttsSaveVoice(voice) {
  _ttsVoice = voice;
  localStorage.setItem('gm_tts_voice', voice);
  // Persist per-campaign on the server so a fresh page load lands on the same
  // voice across browsers. Best-effort; UI never blocks on the response.
  fetch('/voice', {
    method: 'POST',
    headers: _authHeaders(),
    body: JSON.stringify({ voice }),
  }).catch(() => {});
  document.querySelectorAll('.tts-voice-opt').forEach(o => {
    o.classList.toggle('tts-voice-active', o.dataset.voice === voice);
  });
}

function _ttsBuildVoiceMenu() {
  const menu = document.createElement('div');
  menu.className = 'tts-voice-menu';
  menu.hidden = true;

  function _addGroup(label, voices) {
    const hdr = document.createElement('div');
    hdr.className = 'tts-voice-group';
    hdr.textContent = label;
    menu.appendChild(hdr);
    voices.forEach(v => {
      const opt = document.createElement('button');
      opt.className = 'tts-voice-opt';
      opt.dataset.voice = v;
      opt.textContent = v;
      if (v === _ttsVoice) opt.classList.add('tts-voice-active');
      opt.addEventListener('click', e => {
        e.stopPropagation();
        _ttsSaveVoice(v);
        menu.hidden = true;
      });
      menu.appendChild(opt);
    });
  }
  _addGroup('Male',   _TTS_VOICES_MALE);
  _addGroup('Female', _TTS_VOICES_FEMALE);
  return menu;
}

function _addTtsBar(blockEl) {
  if (!_TTS_AVAILABLE || !blockEl) return;
  if (blockEl.querySelector(':scope > .tts-bar')) return;  // already added

  const bar = document.createElement('div');
  bar.className = 'tts-bar';

  const playBtn = document.createElement('button');
  playBtn.className = 'tts-play-btn';
  playBtn.innerHTML = _SVG_SPEAKER;
  playBtn.setAttribute('aria-label', 'Listen to narration');
  playBtn.addEventListener('click', e => { e.stopPropagation(); _ttsPlayBlock(playBtn, blockEl); });
  bar.appendChild(playBtn);

  const voiceBtn = document.createElement('button');
  voiceBtn.className = 'tts-voice-btn';
  voiceBtn.innerHTML = '<span class="tts-voice-label">Voices</span>' + _SVG_CHEVRON;
  voiceBtn.setAttribute('aria-label', 'Select narrator voice');
  bar.appendChild(voiceBtn);

  const menu = _ttsBuildVoiceMenu();
  bar.appendChild(menu);

  voiceBtn.addEventListener('click', e => {
    e.stopPropagation();
    const wasHidden = menu.hidden;
    document.querySelectorAll('.tts-voice-menu').forEach(m => { m.hidden = true; });
    menu.hidden = !wasHidden;
  });

  blockEl.appendChild(bar);

  if (_ttsAutoNarrate) {
    // Defer one tick so the bar is in the DOM before triggering the click path
    setTimeout(() => _ttsPlayBlock(playBtn, blockEl), 0);
  }
}

document.addEventListener('click', () => {
  document.querySelectorAll('.tts-voice-menu').forEach(m => { m.hidden = true; });
}, { passive: true });

// Auto-narrate row — only shown if the server reports TTS available.
if (_TTS_AVAILABLE) {
  const narrateRow = document.getElementById('narrate-row');
  const narrateLabel = document.getElementById('narrate-label');
  if (narrateRow && narrateLabel) {
    narrateRow.hidden = false;
    const refresh = () => {
      narrateLabel.textContent = _ttsAutoNarrate ? 'On' : 'Off';
      narrateLabel.classList.toggle('on', _ttsAutoNarrate);
    };
    refresh();
    narrateRow.addEventListener('click', () => {
      _ttsAutoNarrate = !_ttsAutoNarrate;
      localStorage.setItem('gm_tts_auto', _ttsAutoNarrate ? '1' : '0');
      refresh();
      if (_ttsAutoNarrate) {
        // Prime the AudioContext from this click so auto-narrate works on the next
        // block without needing a per-block gesture (iOS Safari).
        const ctx = _getTtsCtx();
        if (ctx.state !== 'running') ctx.resume().catch(() => {});
      }
    });
  }
}

// ═══════════════════════════════════════════════════════════════════
// THEME PICKER (Dark / Light / Auto)
// Per-browser via localStorage["dnd-theme"]. The anti-FOUC inline script
// in <head> applies the stored value before CSS parses so there's no
// flash on load; this module handles runtime toggling.
// ═══════════════════════════════════════════════════════════════════

const _THEME_CYCLE = ['dark', 'light', 'auto'];
const _THEME_LABEL = { dark: 'Dark', light: 'Light', auto: 'Auto' };

function _readStoredTheme() {
  try {
    const v = localStorage.getItem('gm-theme');
    return (v === 'dark' || v === 'light' || v === 'auto') ? v : 'auto';
  } catch (_) {
    return 'auto';
  }
}

function _applyTheme(theme) {
  if (theme === 'auto') {
    document.documentElement.removeAttribute('data-theme');
  } else {
    document.documentElement.setAttribute('data-theme', theme);
  }
}

function _systemPrefersLight() {
  try {
    return window.matchMedia('(prefers-color-scheme: light)').matches;
  } catch (_) {
    return false;
  }
}

function _effectiveTheme(theme) {
  if (theme === 'auto') return _systemPrefersLight() ? 'light' : 'dark';
  return theme;
}

(function _initThemePicker() {
  const row = document.getElementById('theme-row');
  const label = document.getElementById('theme-label');
  if (!row || !label) return;

  let current = _readStoredTheme();
  _applyTheme(current);

  function refresh() {
    label.textContent = _THEME_LABEL[current] || 'Dark';
    label.classList.toggle('on', current !== 'dark');
    // Surface the effective theme for "auto" via the tooltip
    if (current === 'auto') {
      row.title = `Display theme — Auto (system: ${_effectiveTheme(current)})`;
    } else {
      row.title = `Display theme — ${_THEME_LABEL[current]}. Click to cycle.`;
    }
  }
  refresh();

  row.addEventListener('click', () => {
    const idx = _THEME_CYCLE.indexOf(current);
    current = _THEME_CYCLE[(idx + 1) % _THEME_CYCLE.length];
    try { localStorage.setItem('gm-theme', current); } catch (_) {}
    _applyTheme(current);
    refresh();
  });

  // Track system-pref changes so auto mode stays accurate without reload
  try {
    const mq = window.matchMedia('(prefers-color-scheme: light)');
    mq.addEventListener('change', () => {
      if (current === 'auto') refresh();
    });
  } catch (_) {}
})();

// ── Text-size control (font-size multiplier, not page zoom) ─────────────────
(function _initTextSize() {
  const dec = document.getElementById('ts-dec');
  const inc = document.getElementById('ts-inc');
  const val = document.getElementById('ts-val');
  if (!dec || !inc || !val) return;
  const MIN = 0.8, MAX = 2.0, STEP = 0.1;
  let scale = parseFloat(localStorage.getItem('gm-text-scale'));
  if (!(scale >= MIN && scale <= MAX)) scale = 1;
  const clamp = x => Math.min(MAX, Math.max(MIN, Math.round(x * 100) / 100));
  function apply() {
    document.documentElement.style.setProperty('--text-scale', String(scale));
    val.textContent = Math.round(scale * 100) + '%';
    try { localStorage.setItem('gm-text-scale', String(scale)); } catch (_) {}
  }
  apply();
  dec.addEventListener('click', e => { e.stopPropagation(); scale = clamp(scale - STEP); apply(); });
  inc.addEventListener('click', e => { e.stopPropagation(); scale = clamp(scale + STEP); apply(); });
  val.addEventListener('click', e => { e.stopPropagation(); scale = 1; apply(); });  // click % to reset
})();

// ── Narration-length (verbosity) control — sets the word target the GM aims
// for each turn. Persists locally for the slider and POSTs to the server so
// check_input.py can hand the directive to the GM at the start of the turn.
(function _initVerbosity() {
  const slider = document.getElementById('vb-slider');
  const val = document.getElementById('vb-val');
  if (!slider || !val) return;
  const stored = parseInt(localStorage.getItem('gm-narration-words') || '', 10);
  if (stored >= 250 && stored <= 2500) slider.value = String(stored);
  function label() { val.textContent = slider.value + 'w'; }
  label();
  function persist() {
    const n = parseInt(slider.value, 10);
    try { localStorage.setItem('gm-narration-words', String(n)); } catch (_) {}
    const headers = { 'Content-Type': 'application/json' };
    if (_dndToken) headers['X-DND-Token'] = _dndToken;
    fetch('/narration-pref', {
      method: 'POST', headers, body: JSON.stringify({ target_words: n }),
    }).catch(() => {});
  }
  slider.addEventListener('input', e => { e.stopPropagation(); label(); });
  slider.addEventListener('change', e => { e.stopPropagation(); persist(); });
  // Push the persisted value once on load so a fresh server learns the target.
  persist();
})();

// ═══════════════════════════════════════════════════════════════════
// PLAYER INPUT PANEL
// ═══════════════════════════════════════════════════════════════════

const _inputPanel      = document.getElementById('input-panel');
const _inputPanelHdr   = document.getElementById('input-panel-header');
const _inputBadge      = document.getElementById('input-badge');
const _inputArrow      = document.getElementById('input-toggle-arrow');
const _charTabs        = document.getElementById('char-tabs');
const _inputText       = document.getElementById('player-input-text');
const _sendBtn         = document.getElementById('send-btn');
const _skipTurnBtn     = document.getElementById('skip-turn-btn');
const _sentLogEl       = document.getElementById('sent-log');
const _waitingEl       = document.getElementById('waiting-indicator');

let _selectedChar     = 'Everybody';
let _sentData         = {};   // {char: {text}} — mirror of server sent log

// ── Toggle open/closed ────────────────────────────────────────────
_inputPanelHdr.addEventListener('click', () => {
  _inputPanel.classList.toggle('collapsed');
  _inputArrow.textContent = _inputPanel.classList.contains('collapsed') ? '▲' : '▼';
});

// ── Build character tabs from stats players array ─────────────────
function _buildCharTabs(players) {
  const names = ['Everybody', ...(players || []).map(p => p.name).filter(Boolean)];
  _charTabs.innerHTML = '';
  names.forEach(name => {
    const btn = document.createElement('button');
    btn.className = 'char-tab' + (name === _selectedChar ? ' active' : '');
    btn.dataset.char = name;
    btn.textContent = name;
    btn.addEventListener('click', e => {
      e.stopPropagation();
      _selectedChar = name;
      _charTabs.querySelectorAll('.char-tab').forEach(b =>
        b.classList.toggle('active', b.dataset.char === name)
      );
      _skipTurnBtn.style.display = name === 'Everybody' ? 'none' : '';
    });
    _charTabs.appendChild(btn);
  });
}

// ── Render sent log ────────────────────────────────────────────────
// Read-only record of what has been sent and is waiting on the DM. The only
// action is Recall, and only while the action is still queued.
function _renderSentLog(data) {
  _sentData = data || {};
  _sentLogEl.innerHTML = '';

  const entries = Object.entries(_sentData);
  entries.forEach(([char, info]) => {
    const entry = document.createElement('div');
    entry.className = 'sent-entry';

    const body = document.createElement('div');
    body.className = 'sent-entry-body';

    const charEl = document.createElement('div');
    charEl.className = 'sent-char';
    charEl.textContent = char;

    const textEl = document.createElement('div');
    textEl.className = 'sent-text';
    textEl.textContent = info.text;

    body.appendChild(charEl);
    body.appendChild(textEl);

    const acts = document.createElement('div');
    acts.className = 'sent-actions';

    const recallBtn = document.createElement('button');
    recallBtn.className = 'recall-btn';
    recallBtn.textContent = 'Recall';
    recallBtn.title = 'Pull this back before the DM reads it';
    recallBtn.addEventListener('click', () => _recallAction(char, recallBtn));

    acts.appendChild(recallBtn);

    entry.appendChild(body);
    entry.appendChild(acts);
    _sentLogEl.appendChild(entry);
  });

  // Badge + auto-open
  if (entries.length > 0) {
    _inputBadge.style.display = 'inline';
    _inputBadge.textContent = `${entries.length} sent`;
    if (_inputPanel.classList.contains('collapsed')) {
      _inputPanel.classList.remove('collapsed');
      _inputArrow.textContent = '▼';
    }
  } else {
    _inputBadge.style.display = 'none';
  }
}

// ── Autorun cycle countdown ───────────────────────────────────────
const _AUTORUN_CIRC = 56.55; // 2π × r=9
let _autorunRaf = null;
let _autorunCycleData = null;

function _startAutorunCountdown(interval, ts) {
  _autorunCycleData = { interval, ts };
  const fill = document.querySelector('.autorun-fill');
  const label = document.querySelector('.autorun-label');
  cancelAnimationFrame(_autorunRaf);

  // Auto-expand the panel so the countdown is visible
  if (_inputPanel && _inputPanel.classList.contains('collapsed')) {
    _inputPanel.classList.remove('collapsed');
    if (_inputArrow) _inputArrow.textContent = '▼';
  }

  function tick() {
    if (!_autorunCycleData) return;
    const elapsed = (Date.now() / 1000) - _autorunCycleData.ts;
    const remaining = Math.max(0, _autorunCycleData.interval - elapsed);
    const progress = Math.min(1, elapsed / _autorunCycleData.interval);
    if (fill) fill.setAttribute('stroke-dashoffset', (_AUTORUN_CIRC * progress).toFixed(2));
    if (label) {
      label.textContent = remaining > 1
        ? `Next Turn \u2014 ${Math.ceil(remaining)}s`
        : 'Next Turn \u2014 checking\u2026';
    }
    if (remaining > 0) {
      _autorunRaf = requestAnimationFrame(tick);
    } else {
      // Loop: restart the countdown after a brief pause so the display stays live
      setTimeout(() => {
        if (_autorunCycleData) {
          _autorunCycleData.ts = Date.now() / 1000;
          _autorunRaf = requestAnimationFrame(tick);
        }
      }, 800);
    }
  }
  tick();
}

function _stopAutorunCountdown() {
  cancelAnimationFrame(_autorunRaf);
  _autorunRaf = null;
  _autorunCycleData = null;
  const fill = document.querySelector('.autorun-fill');
  if (fill) { fill.setAttribute('stroke-dashoffset', '0'); fill.classList.remove('waiting'); }
  const label = document.querySelector('.autorun-label');
  if (label) label.textContent = 'Next Turn';
}

// ── Queue status panel (persists until wrapper injects) ──────────
function _renderQueueStatus(chars) {
  if (!chars || chars.length === 0) {
    _waitingEl.style.display = 'none';
    _waitingEl.innerHTML = '';
    return;
  }
  const checks = chars.map(c => `<span class="queue-check">✓ ${esc(c)}</span>`).join('');
  _waitingEl.innerHTML = `<span class="queue-label">⏳ Queued — fires on DM Enter</span>${checks}`;
  _waitingEl.style.display = 'block';
}

// Server refusals on /player-input/send and /skip come back as JSON
// {error, message} (409 no_roster, 403 not_in_party). Show the message in the
// panel; never fail silently. Pass null to clear.
function _autosizeInput() {
  const t = document.getElementById('player-input-text');
  if (!t) return;
  t.style.height = 'auto';
  t.style.height = Math.min(t.scrollHeight + 2, 140) + 'px';
}
document.addEventListener('input', e => {
  if (!e.target || e.target.id !== 'player-input-text') return;
  _autosizeInput();
  // Typing again retires a stale failure label and message.
  const btn = document.getElementById('send-btn');
  if (btn && /^Send failed/.test(btn.textContent)) btn.textContent = 'Send';
  _showInputError(null);
});
(function _initDiceToggle() {
  const pad = document.getElementById('dice-pad');
  const tg = document.getElementById('dp-toggle');
  if (!pad || !tg) return;
  tg.addEventListener('click', () => {
    const open = pad.classList.toggle('open');
    tg.setAttribute('aria-expanded', open ? 'true' : 'false');
  });
  const sum = document.getElementById('dp-toggle-sum');
  document.querySelectorAll('#dp-die-row .dp-die').forEach(b =>
    b.addEventListener('click', () => { if (sum) sum.textContent = b.textContent.trim(); }));
})();

function _showInputError(msg) {
  const el = document.getElementById('input-error');
  if (!el) return;
  el.textContent = msg || '';
  el.style.display = msg ? 'block' : 'none';
}
async function _refusalMessage(res) {
  try {
    const j = await res.clone().json();
    if (j && j.message) return String(j.message);
    if (j && j.error) return String(j.error);
  } catch (_) { /* not JSON */ }
  return '';
}

// ── Send an action ────────────────────────────────────────────────
// One tap straight into the DM-gated queue. No staging, no Ready step.
async function _sendAction() {
  const text = _inputText.value.trim();
  if (!text) {
    _showInputError('Type an action or line of dialogue first.');
    _inputText.focus();
    return;
  }
  _sendBtn.disabled = true;
  _sendBtn.textContent = '…';

  // Cache the submission in localStorage so the text survives a page reload
  // mid-flight (and so a failed send can be retried without retyping).
  localStorage.setItem('dnd_pending_input', JSON.stringify({ char: _selectedChar, text, ts: Date.now() }));

  let attempts = 0;
  let sent = false;
  let lastStatus = 0;
  let refusal = '';
  _showInputError(null);
  while (attempts < 3) {
    attempts++;
    try {
      const res = await fetch('/player-input/send', {
        method: 'POST',
        headers: _authHeaders(),
        body: JSON.stringify({ character: _selectedChar, text }),
      });
      lastStatus = res.status;
      if (res.status === 202) {
        // Device pending approval — persist state so it survives page reload
        localStorage.setItem('dnd_awaiting_approval', _deviceId);
        _sendBtn.disabled = false;
        _sendBtn.textContent = 'Awaiting approval…';
        return;  // leave text in box; auto-retry once approved via _onDeviceApproved
      }
      if (res.ok) {
        localStorage.removeItem('dnd_pending_input');
        localStorage.removeItem('dnd_awaiting_approval');
        _inputText.value = '';
        sent = true;
        break;
      }
      // Non-retriable error (403, 400, 409, etc.)
      refusal = await _refusalMessage(res);
      break;
    } catch (_) {
      if (attempts < 3) {
        _sendBtn.textContent = `Retrying (${attempts})…`;
        await new Promise(r => setTimeout(r, 2000));
      }
    }
  }
  _sendBtn.disabled = false;
  if (refusal) _showInputError(refusal);
  else if (!sent) _showInputError(lastStatus
    ? `Could not send (server replied ${lastStatus}). Your text is kept; tap Send to retry.`
    : 'Could not reach the GM display. Your text is kept; tap Send to retry.');
  if (sent) {
    _sendBtn.textContent = 'Send';
  } else {
    // Honest failure: the text + the dnd_pending_input cache are preserved, so a
    // tap re-sends without retyping. Never silently reset to 'Send' on failure —
    // that's what read as "submitted but not acknowledged, gone forever".
    _sendBtn.textContent = lastStatus
      ? `Send failed (${lastStatus}) — tap to retry`
      : 'Send failed — tap to retry';
  }
}

// ── Pull a sent action back, while it's still queued ──────────────
async function _recallAction(char, btn) {
  if (btn) { btn.disabled = true; btn.textContent = '…'; }
  try {
    const res = await fetch('/player-input/recall', {
      method: 'POST',
      headers: _authHeaders(),
      body: JSON.stringify({ character: char }),
    });
    if (res.status === 409) {
      // Not in the queue any more. The server cannot tell "consumed" from
      // "lost", so never claim "Delivered"; use its own wording.
      let msg = 'No longer queued';
      try { const t = (await res.text()).trim(); if (t) msg = t; } catch (_) {}
      if (btn) {
        btn.disabled = false;
        btn.textContent = 'No longer queued';
        btn.title = msg;
      }
      return;
    }
  } catch (_) {
    if (btn) { btn.disabled = false; btn.textContent = 'Recall'; }
  }
  // On success the SSE sent_log broadcast re-renders and drops the card.
}

// ── Skip a character's turn ───────────────────────────────────────
async function _skipTurn(char) {
  try {
    _showInputError(null);
    const res = await fetch('/player-input/skip', {
      method: 'POST',
      headers: _authHeaders(),
      body: JSON.stringify({ character: char }),
    });
    if (!res.ok && res.status !== 202) _showInputError(await _refusalMessage(res));
  } catch (_) { /* network error: nothing to show */ }
}

_sendBtn.addEventListener('click', _sendAction);
_skipTurnBtn.addEventListener('click', () => _skipTurn(_selectedChar));

// Enter sends from the textarea (Shift+Enter for a newline). Ctrl/Cmd+Enter
// also sends, for muscle memory from editors. Both go through the one send path.
_inputText.addEventListener('keydown', e => {
  if (e.key !== 'Enter') return;
  if (e.ctrlKey || e.metaKey || !e.shiftKey) {
    e.preventDefault();
    _sendAction();
  }
});

// The Party Input panel lives in the right-hand rail, outside the reading
// column, so #text-scroll no longer reserves bottom space for it.

// Initialise with no extra chars (populated when stats arrive)
_buildCharTabs([]);

// Restore a pending send on page load (survives a reload mid-flight, and gives
// a failed send something to retry without retyping).
(function _restorePendingCache() {
  try {
    const cached = JSON.parse(localStorage.getItem('dnd_pending_input') || 'null');
    if (!cached) return;
    if (Date.now() - cached.ts > 10 * 60 * 1000) {
      // Cache expired (> 10 min)
      localStorage.removeItem('dnd_pending_input');
      localStorage.removeItem('dnd_awaiting_approval');
      return;
    }
    if (cached.text && !_inputText.value.trim()) {
      _inputText.value = cached.text;
    }
    // If we were awaiting approval for this device, restore the button state;
    // the SSE device_request replay will confirm and keep it, or clear it if approved.
    if (localStorage.getItem('dnd_awaiting_approval') === _deviceId) {
      _sendBtn.disabled = false;
      _sendBtn.textContent = 'Awaiting approval…';
    }
  } catch (_) {
    localStorage.removeItem('dnd_pending_input');
    localStorage.removeItem('dnd_awaiting_approval');
  }
})();

// ═══════════════════════════════════════════════════════════════════
// DEVICE APPROVAL
// ═══════════════════════════════════════════════════════════════════

const _deviceApprovalsEl = document.getElementById('device-approvals');

function _showDeviceRequest(dev) {
  // Don't duplicate
  if (document.querySelector(`[data-device-id="${dev.id}"]`)) return;

  // If it's our own device replayed on SSE reconnect, restore the awaiting state
  if (dev.id === _deviceId) {
    _sendBtn.disabled = false;
    _sendBtn.textContent = 'Awaiting approval…';
    // Restore cached input text if available
    try {
      const cached = JSON.parse(localStorage.getItem('dnd_pending_input') || 'null');
      if (cached && cached.text && Date.now() - cached.ts < 10 * 60 * 1000) {
        if (!_inputText.value.trim()) _inputText.value = cached.text;
      }
    } catch (_) {}
  }

  const card = document.createElement('div');
  card.className = 'device-card';
  card.dataset.deviceId = dev.id;

  const label = document.createElement('span');
  label.className = 'device-card-label';
  label.textContent = 'New device requesting input access';

  const ip = document.createElement('span');
  ip.className = 'device-card-ip';
  ip.textContent = dev.ip;

  const approveBtn = document.createElement('button');
  approveBtn.className = 'device-approve-btn';
  approveBtn.textContent = 'Approve';
  approveBtn.addEventListener('click', () => _respondDevice(dev.id, true));

  const denyBtn = document.createElement('button');
  denyBtn.className = 'device-deny-btn';
  denyBtn.textContent = 'Deny';
  denyBtn.addEventListener('click', () => _respondDevice(dev.id, false));

  card.appendChild(label);
  card.appendChild(ip);
  card.appendChild(approveBtn);
  card.appendChild(denyBtn);
  _deviceApprovalsEl.appendChild(card);
}

async function _respondDevice(deviceId, approve) {
  try {
    await fetch(approve ? '/device/approve' : '/device/deny', {
      method: 'POST',
      headers: _authHeaders(),
      body: JSON.stringify({ id: deviceId }),
    });
  } catch (_) { /* ignore */ }
  document.querySelector(`[data-device-id="${deviceId}"]`)?.remove();
}

function _onDeviceApproved(deviceId) {
  document.querySelector(`[data-device-id="${deviceId}"]`)?.remove();
  // If it's our own device — reset the send button and retry
  if (deviceId === _deviceId) {
    localStorage.removeItem('dnd_awaiting_approval');
    _sendBtn.disabled = false;
    _sendBtn.textContent = 'Send';
    if (_inputText.value.trim()) {
      _sendAction();  // auto-retry with cached text
    }
  }
}

function _onDeviceDenied(deviceId) {
  document.querySelector(`[data-device-id="${deviceId}"]`)?.remove();
  if (deviceId === _deviceId) {
    localStorage.removeItem('dnd_awaiting_approval');
    localStorage.removeItem('dnd_pending_input');
    _sendBtn.disabled = true;
    _sendBtn.textContent = 'Access denied';
  }
}

// ═══════════════════════════════════════════════════════════════════
// WORLD CLOCK
// ═══════════════════════════════════════════════════════════════════

const _wcEl   = document.getElementById('world-clock');
const _wcIcon = document.getElementById('wc-icon');
const _wcDate = document.getElementById('wc-date');
const _wcSub  = document.getElementById('wc-sub');

const TIME_ICONS = {
  dawn:    '🌅',
  morning: '☀',
  midday:  '☀',
  noon:    '☀',
  afternoon:'☀',
  evening: '🌆',
  dusk:    '🌆',
  night:   '🌙',
  midnight:'🌙',
  late:    '🌙',
};

const WEATHER_ICONS = {
  rain:    '🌧',
  storm:   '⛈',
  fog:     '🌫',
  snow:    '❄',
  cloudy:  '☁',
  overcast:'☁',
  windy:   '💨',
  clear:   '',
  calm:    '',
};

function renderRevealedClocks(clocks) {
  const box = document.getElementById('sb-clocks');
  const list = document.getElementById('sb-clocks-list');
  if (!box || !list) return;
  list.textContent = '';
  (clocks || []).forEach(function (c) {
    const n = [4, 6, 8].indexOf(c.size) >= 0 ? c.size : 4;
    const NS = 'http://www.w3.org/2000/svg';
    const svg = document.createElementNS(NS, 'svg');
    svg.setAttribute('viewBox', '-17 -17 34 34');
    svg.setAttribute('class', 'sb-clock-dial');
    for (let i = 0; i < n; i++) {
      const a0 = (i / n) * 2 * Math.PI - Math.PI / 2;
      const a1 = ((i + 1) / n) * 2 * Math.PI - Math.PI / 2;
      const p = function (a) { return (15 * Math.cos(a)).toFixed(2) + ' ' + (15 * Math.sin(a)).toFixed(2); };
      const path = document.createElementNS(NS, 'path');
      path.setAttribute('d', 'M0 0 L' + p(a0) + ' A15 15 0 0 1 ' + p(a1) + ' Z');
      path.setAttribute('class', 'sb-clock-seg' + (i < c.filled ? ' filled' : ''));
      svg.appendChild(path);
    }
    const row = document.createElement('div');
    row.className = 'sb-clock-item';
    const nm = document.createElement('span');
    nm.className = 'sb-clock-name';
    nm.textContent = c.name;
    row.appendChild(svg);
    row.appendChild(nm);
    list.appendChild(row);
  });
  box.style.display = (clocks && clocks.length) ? '' : 'none';
}

function updateWorldClock(wt) {
  if (!wt) return;
  const timeKey    = (wt.time || '').toLowerCase();
  const weatherKey = Object.keys(WEATHER_ICONS).find(k => (wt.weather||'').toLowerCase().includes(k)) || '';

  const timeIcon    = TIME_ICONS[timeKey]    || '☀';
  const weatherIcon = WEATHER_ICONS[weatherKey] || '';

  _wcIcon.textContent = (weatherIcon || timeIcon);
  _wcDate.textContent = wt.date    || '';
  _wcSub.textContent  = [wt.season, wt.day_name].filter(Boolean).join(' · ');

  _wcEl.classList.add('has-data');
  skyRenderer.setState(wt);
}

// Ruleset badge — '5e 2014' (subdued) or '5e 2024' (amber/gold accent)
function updateRulesetBadge(ruleset) {
  const el = document.getElementById('ruleset-badge');
  if (!el) return;
  const rs = String(ruleset || '').trim();
  if (rs !== '2014' && rs !== '2024') {
    el.classList.remove('has-data', 'rs-2014', 'rs-2024');
    el.textContent = '';
    return;
  }
  el.textContent = '5e ' + rs;
  el.classList.add('has-data');
  el.classList.toggle('rs-2024', rs === '2024');
  el.classList.toggle('rs-2014', rs === '2014');
}

// ═══════════════════════════════════════════════════════════════════
// SIDEBAR TOGGLE
// ═══════════════════════════════════════════════════════════════════

const _sidebar      = document.getElementById('sidebar');
const _sidebarBtn   = document.getElementById('sidebar-toggle');
const _textScroll   = document.getElementById('text-scroll');
let _sidebarVisible = true;

function _setSidebarVisible(visible) {
  _sidebarVisible = visible;
  if (visible) {
    _sidebar.classList.remove('hidden');
    _textScroll.classList.remove('sidebar-hidden');
    _sidebarBtn.textContent = '◀ Hide';
  } else {
    _sidebar.classList.add('hidden');
    _textScroll.classList.add('sidebar-hidden');
    _sidebarBtn.textContent = '▶ Stats';
  }
}

_sidebarBtn.addEventListener('click', () => _setSidebarVisible(!_sidebarVisible));

// Settings-column (right) hide toggle — mirrors the left sidebar toggle.
const _audioControls = document.getElementById('audio-controls');
const _controlsBtn   = document.getElementById('controls-toggle');
let _controlsVisible = true;
function _setControlsVisible(visible, persist = true) {
  _controlsVisible = visible;
  // aria-expanded is what says the column is showing, so it belongs on the
  // control that toggles it and moves with it.
  if (_controlsBtn) _controlsBtn.parentElement.setAttribute('aria-expanded', String(visible));
  if (visible) {
    _audioControls.classList.remove('collapsed');
    _textScroll.classList.remove('controls-hidden');
    if (_controlsBtn) _controlsBtn.textContent = 'Hide ▶';
  } else {
    _audioControls.classList.add('collapsed');
    _textScroll.classList.add('controls-hidden');
    if (_controlsBtn) _controlsBtn.textContent = '◀ Settings';
  }
  if (persist) { try { localStorage.setItem('otgm-controls-visible', visible ? '1' : '0'); } catch (e) {} }
}
if (_controlsBtn) {
  // On the row, not the label: the row is the button, so a click anywhere in it
  // and a keyboard activation of the button itself both land here. A listener on
  // the inner span meant Enter and Space did nothing at all.
  _controlsBtn.closest('.audio-row').addEventListener('click', () => _setControlsVisible(!_controlsVisible));
  try {
    const pref = localStorage.getItem('otgm-controls-visible');
    if (pref === '0') _setControlsVisible(false);
    // No stored choice on a narrow window: start collapsed so the rail does not sit on the prose.
    else if (pref === null && window.matchMedia && window.matchMedia('(max-width: 1100px)').matches) _setControlsVisible(false, false);
  } catch (e) {}
}

// Toggle button becomes visible once sidebar has data (called from SSE handler)

// ═══════════════════════════════════════════════════════════════════
// SSE CONNECTION
// ═══════════════════════════════════════════════════════════════════

let evtSource = null;
let reconnectDelay = 1000;
let _lastSeq = 0;          // highest server seq rendered; sent as ?since= on reconnect
let _sseEpoch = null;      // server run id; a change means the server restarted
let _reconnectAttempts = 0;

// Connection status pill (connected / reconnecting), announced politely.
function _setConnStatus(state, attempt) {
  const el = document.getElementById('conn-status');
  if (!el) return;
  el.dataset.state = state;
  el.textContent = state === 'connected' ? 'Connected'
    : 'Reconnecting' + (attempt > 1 ? ' (attempt ' + attempt + ')' : '') + '\u2026';
}

function _sseHello(h) {
  _sseEpoch = h.epoch;
  // Resumed: the missed narration follows, each with its own seq. Otherwise the
  // full text replay follows, so start counting from the server's current seq.
  if (!h.resumed) _lastSeq = h.seq;
}

// Phones append ?char=<name> (or ?character=<name>) so the server can route
// dice-requests to the phone bound to that PC. The TV view passes neither and
// is treated as the shared display.
const _sp = new URLSearchParams(location.search);
const _streamChar = (_sp.get('char') || _sp.get('character') || '').trim();

function connect() {
  const _qs = [];
  if (_streamChar) _qs.push('character=' + encodeURIComponent(_streamChar));
  if (_sseEpoch !== null) _qs.push('since=' + _lastSeq, 'epoch=' + encodeURIComponent(_sseEpoch));
  evtSource = new EventSource('/stream' + (_qs.length ? '?' + _qs.join('&') : ''));

  evtSource.onopen = () => {
    reconnectDelay = 1000;
    _reconnectAttempts = 0;
    _setConnStatus('connected');
  };

  evtSource.onmessage = (e) => {
    let payload;
    try { payload = JSON.parse(e.data); } catch (err) { console.warn('SSE: bad JSON', err); return; }

    // Sequencing: the server stamps every broadcast with a seq. Drop anything at
    // or below what we already rendered (a replay racing a live payload).
    if (payload.hello) {
      _sseHello(payload.hello);
      return;
    }
    if (payload.seq !== undefined) {
      if (payload.seq <= _lastSeq) return;
      _lastSeq = payload.seq;
    }

    // One failing branch must not abort the rest of the payload.
    const _try = (name, fn) => {
      try { fn(); } catch (err) { console.error('SSE handler failed:', name, err); }
    };
    _try('payload.dice_request && typeof window._o', () => {
      if (payload.dice_request && typeof window._onDiceRequest === 'function') {
        window._onDiceRequest(payload.dice_request);
      }
    });
    _try('payload.dice_request_cancelled && typeof', () => {
      if (payload.dice_request_cancelled && typeof window._onDiceRequestCancelled === 'function') {
        window._onDiceRequestCancelled(payload.dice_request_cancelled);
      }
    });
    _try('payload.dice_pending !== undefined', () => {
      if (payload.dice_pending !== undefined) {
        _updateDicePendingBadge(payload.dice_pending);
      }
    });
    _try('payload.stats', () => {
  
      if (payload.stats) {
        updateStats(payload.stats);
        if (payload.stats.players && payload.stats.players.length > 0) {
          _sidebarBtn.classList.add('visible');
          _buildCharTabs(payload.stats.players);
          _modeSwitcherCachePlayers(payload.stats.players);
        }
        if (payload.stats.world_time) {
          updateWorldClock(payload.stats.world_time);
        }
        if (payload.stats.ruleset) {
          updateRulesetBadge(payload.stats.ruleset);
        }
      }
    });
    _try('payload.clocks !== undefined', () => {
      if (payload.clocks !== undefined) {
        renderRevealedClocks(payload.clocks);
      }
    });
    _try('payload.sent_log !== undefined', () => {
      if (payload.sent_log !== undefined) {
        _renderSentLog(payload.sent_log);
      }
    });
    _try('payload.queue_status !== undefined', () => {
      if (payload.queue_status !== undefined) {
        _renderQueueStatus(payload.queue_status);
      }
    });
    _try('payload.dm_processing', () => {
      if (payload.dm_processing) {
        _showProcessingIndicator();
      }
    });
    _try('payload.autorun_threshold !== undefined', () => {
      if (payload.autorun_threshold !== undefined) {
        // Accepted for compatibility; sends no longer wait on a threshold.
      }
    });
    _try('payload.autorun_cycle', () => {
      if (payload.autorun_cycle) {
        const el = document.getElementById('autorun-indicator');
        if (el) el.style.display = 'flex';
        _startAutorunCountdown(payload.autorun_cycle.interval, payload.autorun_cycle.ts);
      }
    });
    _try('payload.autorun_waiting !== undefined', () => {
      if (payload.autorun_waiting !== undefined) {
        const el = document.getElementById('autorun-indicator');
        const fill = document.querySelector('.autorun-fill');
        if (payload.autorun_waiting) {
          if (el) el.style.display = 'flex';
          if (fill) fill.classList.add('waiting');
        } else {
          if (el) el.style.display = 'none';
          _stopAutorunCountdown();
        }
      }
    });
    _try('payload.effect_expired', () => {
      if (payload.effect_expired) {
        const ev = payload.effect_expired;
        renderEffectExpiredBlock(ev.owner, ev.name, ev.was_concentration);
      }
    });
    _try('payload.device_request', () => {
      if (payload.device_request) {
        _showDeviceRequest(payload.device_request);
      }
    });
    // Grid combat: the tactics engine's snapshot (display/static/tactics.js).
    _try('payload.combat !== undefined && window.T', () => {
      if (payload.combat !== undefined && window.Tactics) {
        window.Tactics.update(payload.combat);
      }
    });
    _try('payload.device_approved', () => {
      if (payload.device_approved) {
        _onDeviceApproved(payload.device_approved);
      }
    });
    _try('payload.device_denied', () => {
      if (payload.device_denied) {
        _onDeviceDenied(payload.device_denied);
      }
    });
    _try('payload.clear', () => {
      if (payload.clear) {
        // Wipe sidebar player data and DOM
        for (const key of Object.keys(_playerData)) delete _playerData[key];
        document.querySelectorAll('.sb-player').forEach(el => el.remove());
        const sbEl = document.getElementById('sidebar');
        if (sbEl) sbEl.classList.remove('has-data');
        const turnSectionEl = document.getElementById('sb-turn-section');
        if (turnSectionEl) turnSectionEl.style.display = 'none';
        _sidebarBtn.classList.remove('visible');
        _setSidebarVisible(true);   // reset to visible for next campaign
        clearDisplay();
      }
    });
    _try('payload.replay_batch', () => {
      if (payload.replay_batch) {
        renderReplayBatch(payload.replay_batch);
      }
    });
    _try('payload.replay', () => {
      if (payload.replay) {
        renderReplay(payload.replay);  // legacy fallback
      }
    });
    _try('payload.scene', () => {
      if (payload.scene) {
        applyScene(payload.scene);
      }
    });
    _try('payload.sfx', () => {
      if (payload.sfx) {
        _playSfx(payload.sfx);
      }
    });
    _try('payload.inspiration_award', () => {
      if (payload.inspiration_award) {
        renderInspirationBlock(payload.inspiration_award, payload.reason || '');
      }
    });
    _try('payload.milestone_award', () => {
      if (payload.milestone_award) {
        renderMilestoneBlock(payload.milestone_award, payload.label || 'Milestone', payload.reason || '');
      }
    });
    // milestone_spend: server processes the counter decrement; no feed block
    _try('payload.xp_award', () => {
      if (payload.xp_award) {
        renderXpBlock(payload.xp_award);
      }
    });
    _try('payload.text && !payload.inspiration_awa', () => {
      if (payload.text && !payload.inspiration_award && !payload.milestone_award && !payload.milestone_spend && !payload.xp_award) {
        if (payload.action) {
          renderActionBlock(payload.action, payload.text);
        } else if (payload.player) {
          renderPlayerBlock(payload.player, payload.text);
        } else if (payload.npc) {
          renderNPCBlock(payload.npc, payload.text);
        } else if (payload.dice) {
          renderDiceBlock(payload.text);
        } else if (payload.tutor) {
          renderTutorBlock(payload.text);
        } else {
          handleIncomingText(payload.text);
        }
      }
    });
  };

  evtSource.onerror = () => {
    evtSource.close();
    _reconnectAttempts += 1;
    _setConnStatus('reconnecting', _reconnectAttempts);
    setTimeout(() => {
      reconnectDelay = Math.min(reconnectDelay * 1.5, 3000);
      connect();
    }, reconnectDelay);
  };
}

connect();

// ── Input-only mode for mobile players ───────────────────────────────────
// Triggered by either:
//   ?view=input              — explicit (long form, still supported)
//   ?char=<Name>             — shorthand: char= alone implies input-only,
//                              so players only need to type the short URL
//                              (e.g. http://<host>:5001/?char=Mira).
// Both forms set the character binding the same way (the binding is read
// from ?character= or ?char= inside _initDicePad / _loadCharacterSheet).
{
  const _qp = new URLSearchParams(location.search);
  const _inputMode = _qp.get('view') === 'input' || _qp.has('char') || _qp.has('character');
  if (_inputMode) {
    document.body.classList.add('input-only');
    const ip = document.getElementById('input-panel');
    if (ip) ip.classList.remove('collapsed');
    _initInputTabs();
  }
  // N2: the dice pad lives inside the Party Input panel, which also exists on the
  // main view — so it must be initialised in BOTH. Showing the pad via CSS without
  // calling _initDicePad() left a visible but dead Roll button for anyone not on
  // ?view=input, which is exactly the player N2 exists to serve: the badge now
  // floats the pad out on the main view, and it has to actually roll.
  // _initDicePad is idempotent (it guards on reel/rollBtn being present) and reads
  // its character binding from ?char= / ?character= or localStorage, so calling it
  // here changes nothing for input-only users.
  _initDicePad();
  // _initDiceBadgeClick is idempotent and guards on a dataset flag.
  _initDiceBadgeClick();
  _initModeSwitcher(_inputMode);
}

// ── Mode switcher ─────────────────────────────────────────────────────────
// Two entry points to save players from typing the input URL by hand:
//   - Full DM view → "Phone Mode" button drops a character picker pulled
//     from the latest SSE `stats` payload; click a name to jump to
//     ?view=input&char=<Name>.
//   - Input view → "Full Display" button bottom-left strips query params
//     and navigates back to the DM view (useful for reading narration off
//     the phone without losing the binding next reload).
// Pure client-side. No server changes needed; the dropdown reads the
// players array that already arrives via the existing /stream SSE.
let _modePlayersCache = [];

function _modeSwitcherCachePlayers(players) {
  const names = (players || [])
    .map(p => (p && (p.name || p.player_name || '')).toString().trim())
    .filter(n => n);
  // Dedupe while preserving order
  const seen = new Set();
  _modePlayersCache = names.filter(n => {
    const k = n.toLowerCase();
    if (seen.has(k)) return false;
    seen.add(k);
    return true;
  });
}

function _initModeSwitcher(inputMode) {
  if (inputMode) {
    // Input-view: drop a "Full Display" button that strips query params
    const btn = document.createElement('button');
    btn.id = 'full-mode-btn';
    btn.type = 'button';
    btn.title = 'Switch to full DM display (read narration off this device)';
    btn.textContent = '👁 Full Display';
    btn.addEventListener('click', e => {
      e.stopPropagation();
      // Strip ?view, ?char, ?character — keep any other harmless params
      const url = new URL(window.location.href);
      ['view', 'char', 'character'].forEach(k => url.searchParams.delete(k));
      window.location.href = url.pathname + (url.searchParams.toString() ? '?' + url.searchParams.toString() : '');
    });
    document.body.appendChild(btn);
    return;
  }

  // Full-view: "Phone Mode" button + lazy character-picker dropdown
  const btn = document.createElement('button');
  btn.id = 'phone-mode-btn';
  btn.type = 'button';
  btn.title = 'Switch this device to phone input mode for a character';
  btn.textContent = '📱 Phone Mode';

  const menu = document.createElement('div');
  menu.id = 'phone-mode-menu';
  menu.setAttribute('role', 'menu');

  function _rebuildMenu() {
    menu.innerHTML = '';
    const hdr = document.createElement('div');
    hdr.className = 'pm-header';
    hdr.textContent = 'Bind this device to…';
    menu.appendChild(hdr);

    if (_modePlayersCache.length === 0) {
      const empty = document.createElement('div');
      empty.className = 'pm-empty';
      empty.textContent = 'No characters loaded yet';
      menu.appendChild(empty);
      return;
    }

    _modePlayersCache.forEach(name => {
      const opt = document.createElement('button');
      opt.className = 'pm-opt';
      opt.type = 'button';
      // textContent escapes by itself. Escaping first and then assigning would
      // double-escape, so a name containing "&" showed up literally as "&amp;".
      opt.textContent = name;
      opt.addEventListener('click', e => {
        e.stopPropagation();
        const url = new URL(window.location.href);
        url.searchParams.set('view', 'input');
        url.searchParams.set('char', name);
        window.location.href = url.toString();
      });
      menu.appendChild(opt);
    });
  }

  btn.addEventListener('click', e => {
    e.stopPropagation();
    if (menu.classList.contains('open')) {
      menu.classList.remove('open');
    } else {
      _rebuildMenu();
      menu.classList.add('open');
    }
  });

  // Close the menu on any outside click
  document.addEventListener('click', () => {
    menu.classList.remove('open');
  }, { passive: true });

  document.body.appendChild(btn);
  document.body.appendChild(menu);
}

// ── Move / Roll tab switcher (input-only mode only) ──────────────────────
function _initInputTabs() {
  const body = document.getElementById('input-body');
  if (!body) return;
  document.querySelectorAll('.dp-tab').forEach(btn => {
    btn.addEventListener('click', () => _setActiveTab(btn.dataset.tab));
  });
}

function _setActiveTab(tab) {
  if (tab !== 'move' && tab !== 'roll' && tab !== 'character') return;
  const body = document.getElementById('input-body');
  if (!body) return;
  body.dataset.activeTab = tab;
  document.querySelectorAll('.dp-tab').forEach(b => {
    b.classList.toggle('active', b.dataset.tab === tab);
  });
  // Clear the "DM request waiting" dot once the player navigates to Roll.
  if (tab === 'roll') {
    const rollTab = document.querySelector('.dp-tab[data-tab="roll"]');
    if (rollTab) rollTab.classList.remove('has-request');
  }
  // Lazy-load the character sheet the first time Sheet is opened, and refresh
  // each subsequent open so HP / slots / conditions stay current with the
  // campaign file (which the DM updates between turns).
  if (tab === 'character') _loadCharacterSheet();
}

// ── Character sheet pane (Sheet tab) ─────────────────────────────────────
async function _loadCharacterSheet() {
  const status = document.getElementById('cp-status');
  const bodyEl = document.getElementById('cp-body');
  if (!status || !bodyEl) return;
  // Resolve the character: prefer ?char= / ?character=, then localStorage / typed name.
  const _qpCh = new URLSearchParams(location.search);
  const ch = (_qpCh.get('char') || _qpCh.get('character')
              || localStorage.getItem('gm_player_name')
              || '').trim();
  if (!ch) {
    status.textContent = 'No character bound — open with ?char=<Name>';
    bodyEl.innerHTML = '';
    return;
  }
  status.textContent = `Loading ${ch}…`;
  try {
    const res = await fetch(`/character/${encodeURIComponent(ch)}`, {
      headers: _authHeaders(),
    });
    if (!res.ok) {
      status.textContent = `Could not load sheet (${res.status})`;
      bodyEl.innerHTML = '';
      return;
    }
    const md = await res.text();
    bodyEl.innerHTML = _renderMarkdown(md);
    status.textContent = '';
  } catch (e) {
    status.textContent = `Network error — ${e.message || e}`;
    bodyEl.innerHTML = '';
  }
}

// Minimal markdown → HTML for character sheets. Supports the subset used in
// the campaign template: # ## ### headers, **bold**, *italic*, - bullet lists,
// 1. ordered lists, single newlines kept as <br> inside a paragraph,
// and GitHub-style pipe tables. Not a full CommonMark implementation; deliberately
// small so we don't drag in a dependency for one read-only viewer.
function _renderMarkdown(md) {
  // The shared esc() from the top of this file, used deliberately: the sheet is
  // markdown from the campaign folder, and this renderer emits no attributes of
  // its own, so escaping first is enough. (A local copy used to shadow the
  // shared helper here and escape fewer characters.)
  const inline = (s) => esc(s)
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/(^|[\s(])\*([^*\n]+)\*/g, '$1<em>$2</em>')
    .replace(/(^|[\s(])_([^_\n]+)_/g, '$1<em>$2</em>');

  const lines = md.replace(/\r\n/g, '\n').split('\n');
  const out = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    // Headers
    let m = /^(#{1,3})\s+(.*)$/.exec(line);
    if (m) { const lvl = m[1].length; out.push(`<h${lvl}>${inline(m[2])}</h${lvl}>`); i++; continue; }
    // Horizontal rule
    if (/^---+\s*$/.test(line)) { out.push('<hr>'); i++; continue; }
    // Table — header row followed by separator row of dashes/pipes.
    if (line.includes('|') && i + 1 < lines.length && /^\s*\|?\s*:?-{2,}.*\|/.test(lines[i+1])) {
      const cells = (s) => s.replace(/^\s*\|/, '').replace(/\|\s*$/, '').split('|').map(c => c.trim());
      const head = cells(line);
      const rows = [];
      i += 2;
      while (i < lines.length && lines[i].includes('|')) { rows.push(cells(lines[i])); i++; }
      let html = '<table><thead><tr>' + head.map(h => `<th>${inline(h)}</th>`).join('') + '</tr></thead><tbody>';
      for (const r of rows) html += '<tr>' + r.map(c => `<td>${inline(c)}</td>`).join('') + '</tr>';
      html += '</tbody></table>';
      out.push(html);
      continue;
    }
    // Bullet list
    if (/^\s*-\s+/.test(line)) {
      const items = [];
      while (i < lines.length && /^\s*-\s+/.test(lines[i])) {
        items.push(inline(lines[i].replace(/^\s*-\s+/, '')));
        i++;
      }
      out.push('<ul>' + items.map(t => `<li>${t}</li>`).join('') + '</ul>');
      continue;
    }
    // Ordered list (1. 2. 3.)
    if (/^\s*\d+[.)]\s+/.test(line)) {
      const items = [];
      while (i < lines.length && /^\s*\d+[.)]\s+/.test(lines[i])) {
        items.push(inline(lines[i].replace(/^\s*\d+[.)]\s+/, '')));
        i++;
      }
      out.push('<ol>' + items.map(t => `<li>${t}</li>`).join('') + '</ol>');
      continue;
    }
    // Blank line → paragraph break
    if (line.trim() === '') { i++; continue; }
    // Otherwise paragraph (collect consecutive non-blank, non-special lines)
    const para = [line];
    i++;
    while (i < lines.length && lines[i].trim() !== '' && !/^#{1,3}\s/.test(lines[i])
           && !/^\s*-\s+/.test(lines[i]) && !/^\s*\d+[.)]\s+/.test(lines[i])
           && !lines[i].includes('|')
           && !/^---+\s*$/.test(lines[i])) {
      para.push(lines[i]); i++;
    }
    // A single newline inside a paragraph is a line break, not a space.
    out.push('<p>' + para.map(inline).join('<br>') + '</p>');
  }
  return out.join('\n');
}

// ── DM-side "Waiting on Piper, Mira…" badge for unresolved dice-requests ──
// Driven by the server's dice_pending SSE event (snapshot of all active
// requests). Hidden when the snapshot is empty.
function _updateDicePendingBadge(snapshot) {
  const badge = document.getElementById('dice-pending-badge');
  if (!badge) return;
  const entries = Array.isArray(snapshot) ? snapshot.filter(e => e && (e.pending || []).length) : [];
  if (entries.length === 0) {
    badge.classList.remove('visible');
    badge.innerHTML = '';
    return;
  }
  // Most recent request first (best-effort: snapshot order is insertion order on the server).
  // Both `e.pending` member names and `e.label` go through esc(): server-side
  // sanitization strips ` $ \ but not < > &, so any approved-phone caller could
  // otherwise inject script into the DM browser via those fields.
  const lines = entries.map(e => {
    const who   = (e.pending || []).map(esc).join(', ');
    const label = e.label ? `<span class="dpb-label">${esc(e.label)}</span>` : '';
    return `Waiting on: ${who}${label}`;
  }).join('<hr style="border:none;border-top:1px solid rgba(180,140,60,0.25);margin:6px 0">');
  badge.innerHTML = lines;
  badge.classList.add('visible');
  // N2: make it obvious the badge is the way to the pad on the main view. The
  // hint is omitted in input-only, where the pad is already a tab.
  if (!document.body.classList.contains('input-only')) {
    badge.innerHTML += '<span class="dpb-label">Tap to roll</span>';
  }
}

/* N2: the dice pad lives inside the Party Input panel, which is collapsed on the
   main (TV) view — so a player watching the big screen had no way to roll at all.
   Clicking the "Waiting on" badge floats the pad out as a small panel; Escape or
   a second click closes it. */
function _initDiceBadgeClick() {
  const badge = document.getElementById('dice-pending-badge');
  if (!badge || badge.dataset.padBound) return;
  badge.dataset.padBound = '1';
  badge.addEventListener('click', () => {
    document.body.classList.toggle('dice-pad-open');
  });
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape') document.body.classList.remove('dice-pad-open');
  });
  // A finished roll means nothing is waiting: drop the pad too, or it covers the
  // narration with a roll nobody is asked for any more.
  const obs = new MutationObserver(() => {
    if (!badge.classList.contains('visible')) {
      document.body.classList.remove('dice-pad-open');
    }
  });
  obs.observe(badge, { attributes: true, attributeFilter: ['class'] });
}

// ── Phone dice pad: server-side roll + slot-machine reveal ───────────────
function _initDicePad() {
  const reel    = document.getElementById('dp-reel');
  const rollBtn = document.getElementById('dp-roll');
  const modVal  = document.getElementById('dp-mod-val');
  const nameEl  = document.getElementById('dp-name');
  const labelEl = document.getElementById('dp-label');
  const boundEl = document.getElementById('dp-bound');
  if (!reel || !rollBtn) return;

  // Character binding: ?char=Piper (or ?character=Piper) locks this phone to
  // that player. Without the param the phone falls back to a free-text name
  // + localStorage. Short form (?char=) is preferred for hand-typed URLs.
  const _qp = new URLSearchParams(location.search);
  const _bound = (_qp.get('char') || _qp.get('character') || '').trim().slice(0, 24);
  if (_bound) {
    nameEl.value = _bound;
    nameEl.classList.add('locked');
    nameEl.setAttribute('readonly', '');
    if (boundEl) {
      boundEl.style.display = 'block';
      boundEl.textContent = _bound;
    }
  } else {
    // B8: the main (DM/TV) view has no player identity. Falling back to the
    // last phone's remembered name labelled the DM's pad with a stale player,
    // so only input-only (phone) views restore the remembered name; the main
    // view starts blank and the placeholder says the name is a choice.
    const _phoneView = document.body.classList.contains('input-only');
    if (_phoneView) {
      nameEl.value = localStorage.getItem('gm_player_name') || '';
      nameEl.addEventListener('input', () => {
        localStorage.setItem('gm_player_name', nameEl.value.trim().slice(0, 24));
      });
    } else {
      nameEl.value = '';
      nameEl.setAttribute('placeholder', 'Roller name (choose)');
    }
  }

  let spec = '1d20';
  let adv  = 'normal';
  let mod  = 0;
  // Currently-active DM request, if any. Echoed back on the roll POST so the
  // server can match the response to the pending request and release any
  // blocking --wait call on the DM side.
  let _activeRequestId = '';
  reel.textContent = 'd20';

  function _syncAdvAvailability() {
    const isD20 = (spec === '1d20');
    document.querySelectorAll('.dp-adv').forEach(b => {
      if (isD20) b.removeAttribute('disabled');
      else       b.setAttribute('disabled', '');
    });
    if (!isD20 && adv !== 'normal') {
      adv = 'normal';
      document.querySelectorAll('.dp-adv').forEach(b => b.classList.remove('active'));
      const n = document.querySelector('.dp-adv[data-adv="normal"]');
      if (n) n.classList.add('active');
    }
    const hint = document.getElementById('dp-hint');
    if (hint) hint.style.opacity = isD20 ? '1' : '0.4';
  }

  document.querySelectorAll('#dp-die-row .dp-die').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('#dp-die-row .dp-die').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      spec = btn.dataset.spec;
      if (!reel.classList.contains('spinning')) reel.textContent = spec.replace('1d', 'd');
      _syncAdvAvailability();
    });
  });
  _syncAdvAvailability();
  document.querySelectorAll('.dp-adv').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.dp-adv').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      adv = btn.dataset.adv;
    });
  });
  document.querySelectorAll('.dp-mod').forEach(btn => {
    btn.addEventListener('click', () => {
      mod = Math.max(-30, Math.min(30, mod + Number(btn.dataset.delta)));
      modVal.textContent = (mod >= 0 ? '+' : '') + mod;
    });
  });

  // Slot-machine ticker: cycle random faces, ease the interval, then lock.
  function spin(maxFace) {
    return new Promise(resolve => {
      reel.classList.add('spinning');
      reel.classList.remove('locked');
      let delay = 40;             // ms between ticks
      const start = performance.now();
      const minMs = 900;          // minimum spin duration
      function tick() {
        reel.textContent = String(1 + Math.floor(Math.random() * maxFace));
        const elapsed = performance.now() - start;
        delay = Math.min(260, 40 + elapsed * 0.25);   // ease out
        if (elapsed > minMs && delay > 220) { resolve(); return; }
        setTimeout(tick, delay);
      }
      tick();
    });
  }

  function lockTo(value) {
    reel.classList.remove('spinning');
    reel.textContent = String(value);
    // restart animation
    reel.classList.remove('locked');
    void reel.offsetWidth;
    reel.classList.add('locked');
  }

  rollBtn.addEventListener('click', async () => {
    const character = (nameEl.value || '').trim() || 'Player';
    const label     = (labelEl.value || '').trim();
    rollBtn.disabled = true;
    document.getElementById('dp-result-line').textContent = '';

    const m = /^(\d+)d(\d+)$/.exec(spec);
    const faces = m ? Number(m[2]) : 20;

    // Kick off spin animation immediately so the phone feels responsive,
    // then await the authoritative server roll.
    const spinPromise = spin(faces);
    let result = null;
    try {
      const res = await fetch('/player-input/dice', {
        method: 'POST',
        headers: _authHeaders(),
        body: JSON.stringify({
          character, spec, modifier: mod, advantage: adv, label,
          request_id: _activeRequestId || undefined,
        }),
      });
      if (res.ok) result = await res.json();
    } catch (_) { /* network error — fall through */ }

    await spinPromise;
    if (!result) {
      reel.classList.remove('spinning');
      reel.textContent = '!';
      document.getElementById('dp-result-line').textContent = 'roll failed';
      rollBtn.disabled = false;
      rollBtn.classList.remove('pulse');
      if (_locked) _setLocked(false);
      return;
    }

    // Lock to the kept die value (single die) or the subtotal (multi-die).
    const displayFace = (result.kept && result.kept.length === 1)
      ? result.kept[0]
      : result.subtotal;
    lockTo(displayFace);

    const line = document.getElementById('dp-result-line');
    line.innerHTML = `${esc(result.spec)}${result.modifier ? (result.modifier > 0 ? '+' : '') + esc(result.modifier) : ''}` +
                     ` &nbsp;→&nbsp; <span class="total">${esc(result.total)}</span>` +
                     (result.both ? ` &nbsp;<span style="opacity:.7">(${result.both.map(esc).join(' / ')} ${esc(result.advantage)})</span>` : '');
    if (navigator.vibrate) navigator.vibrate(30);
    rollBtn.classList.remove('pulse');
    if (boundEl) {
      // Clear the inline request line once the roll has been resolved.
      const req = boundEl.querySelector('.req');
      if (req) req.remove();
    }

    // If this was a prescribed (DM-requested) roll, keep the pad fully locked
    // afterwards so the player can't fire another unsolicited roll. The lock
    // is released only by the next dice_request arriving for this character
    // (which unlocks + re-pre-fills inside _applyDiceRequest).
    if (_activeRequestId) {
      _setLocked(true, spec, adv);          // keep die / adv selections frozen
      rollBtn.disabled = true;              // and disable Roll itself
      rollBtn.classList.add('rolled');
      if (boundEl) {
        let done = boundEl.querySelector('.req');
        if (!done) { done = document.createElement('span'); done.className = 'req'; boundEl.appendChild(done); }
        done.textContent = '✓ Rolled — waiting for DM';
      }
      _activeRequestId = '';
    } else {
      // Free-roll path: unlock so the player can roll again freely.
      rollBtn.disabled = false;
      if (_locked) _setLocked(false);
    }
  });

  // Lock the pad to a single prescribed roll. Only the matching die / adv
  // button and the Roll button remain interactive. Cleared after the roll
  // resolves (success or failure) so the player can free-roll again.
  let _locked = false;
  function _setLocked(active, keepSpec, keepAdv) {
    _locked = !!active;
    document.querySelectorAll('.dp-die').forEach(b => {
      if (active && b.dataset.spec !== keepSpec) b.setAttribute('disabled', '');
      else                                       b.removeAttribute('disabled');
    });
    document.querySelectorAll('.dp-adv').forEach(b => {
      // Adv buttons are also gated by _syncAdvAvailability for non-d20 specs;
      // re-syncing afterwards restores that behaviour.
      if (active && b.dataset.adv !== keepAdv) b.setAttribute('disabled', '');
      else                                     b.removeAttribute('disabled');
    });
    document.querySelectorAll('.dp-mod').forEach(b => {
      if (active) b.setAttribute('disabled', '');
      else        b.removeAttribute('disabled');
    });
    if (active) labelEl.setAttribute('readonly', '');
    else        labelEl.removeAttribute('readonly');
    if (!active) _syncAdvAvailability();
  }

  // ── DM → phone: react to a /dice-request broadcast ───────────────────────
  function _applyDiceRequest(req) {
    if (!req) return;
    const me      = (nameEl.value || '').trim().toLowerCase();
    const targets = Array.isArray(req.characters) && req.characters.length
      ? req.characters.map(c => String(c).toLowerCase())
      : [String(req.character || 'any').toLowerCase()];
    const isAny   = targets.includes('any');
    if (!isAny && !targets.includes(me)) return;                  // not for this phone

    _activeRequestId = String(req.request_id || '');

    // Spec → click the matching die button. If unknown, fall back to d20.
    const reqSpec = (req.spec || '1d20').toLowerCase();
    const dieBtn  = document.querySelector(`.dp-die[data-spec="${reqSpec}"]`)
                 || document.querySelector('.dp-die[data-spec="1d20"]');
    if (dieBtn) dieBtn.click();

    // Adv/dis (only meaningful on 1d20; _syncAdvAvailability gates the rest).
    const advBtn = document.querySelector(`.dp-adv[data-adv="${req.advantage || 'normal'}"]`);
    if (advBtn && !advBtn.hasAttribute('disabled')) advBtn.click();

    // Modifier — jump straight to the requested value rather than +/- ticking.
    mod = Math.max(-30, Math.min(30, Number(req.modifier) || 0));
    modVal.textContent = (mod >= 0 ? '+' : '') + mod;

    // Label / DC line.
    const baseLabel = (req.label || '').trim();
    labelEl.value = baseLabel;
    // The last roll's result belongs to the last request, not this one.
    const resultLine = document.getElementById('dp-result-line');
    if (resultLine) resultLine.textContent = '';
    if (boundEl) {
      const dcStr = (req.dc != null) ? ` · DC ${req.dc}` : '';
      const desc  = baseLabel || (reqSpec === '1d20' ? 'Roll d20' : `Roll ${reqSpec}`);
      let r = boundEl.querySelector('.req');
      if (!r) { r = document.createElement('span'); r.className = 'req'; boundEl.appendChild(r); }
      r.textContent = `↻ ${desc}${dcStr}`;
    }

    rollBtn.disabled = false;                 // re-enable after a previous "rolled" lock
    rollBtn.classList.remove('rolled');
    rollBtn.classList.add('pulse');
    if (navigator.vibrate) navigator.vibrate([20, 60, 20]);

    // Lock everything except the prescribed die + adv button + Roll.
    _setLocked(true, reqSpec, (req.advantage || 'normal'));

    // Pull the player to the Roll tab so they don't miss the request. If they
    // were already on Roll, this is a no-op; if they were on Move, the tab
    // switches and the dot on the Roll button is cleared by _setActiveTab.
    const onMove = document.getElementById('input-body')?.dataset.activeTab === 'move';
    if (onMove) {
      const rollTab = document.querySelector('.dp-tab[data-tab="roll"]');
      if (rollTab) rollTab.classList.add('has-request');
      _setActiveTab('roll');
    }
  }
  window._onDiceRequest = _applyDiceRequest;

  // DM cancelled the active prescribed roll (e.g. via DELETE /dice-request/<id>).
  // Server broadcasts {dice_request_cancelled: <request_id>}. If it matches
  // this phone's active request, fully unlock the pad so the player isn't
  // stuck staring at a locked Roll button while the badge has already cleared.
  // Cancellations for other phones (or for requests we never received) are
  // safely ignored.
  function _onDiceRequestCancelled(rid) {
    if (!rid || rid !== _activeRequestId) return;
    _activeRequestId = '';
    if (_locked) _setLocked(false);
    rollBtn.disabled = false;
    rollBtn.classList.remove('pulse', 'rolled');
    if (boundEl) {
      const r = boundEl.querySelector('.req');
      if (r) r.remove();
    }
    // Brief inline confirmation so the player sees the unlock happened.
    const line = document.getElementById('dp-result-line');
    if (line) {
      line.textContent = '— request cancelled by DM —';
      setTimeout(() => { if (line.textContent === '— request cancelled by DM —') line.textContent = ''; }, 4000);
    }
  }
  window._onDiceRequestCancelled = _onDiceRequestCancelled;
}
