// DexMentor landing page: one continuous shot through a drafting room in space.
// Scroll moves the camera: logo -> the library assembling -> one sheet being read -> facts pinned with balloons.
import * as THREE from 'three';

const TEAL = 0x2c8c98, DEEP = 0x071a1d, AMBER = 0xf9af46;
const canvas = document.getElementById('stage');
const small = innerWidth < 800;
const renderer = new THREE.WebGLRenderer({ canvas, antialias: !small, alpha: false, powerPreference: 'high-performance' });
renderer.setPixelRatio(Math.min(devicePixelRatio, small ? 1.5 : 2));
renderer.setClearColor(DEEP);
const scene = new THREE.Scene();
scene.fog = new THREE.FogExp2(DEEP, 0.028);
const camera = new THREE.PerspectiveCamera(48, 1, 0.1, 200);

// ---- drafting grid floor and ceiling of dust ----
const grid = new THREE.GridHelper(240, 120, TEAL, 0x12454c);
grid.position.y = -6; grid.material.transparent = true; grid.material.opacity = 0.55; scene.add(grid);
const dustGeo = new THREE.BufferGeometry();
const dust = new Float32Array(900 * 3);
for (let i = 0; i < dust.length; i++) dust[i] = (Math.random() - 0.5) * (i % 3 === 1 ? 40 : 120);
dustGeo.setAttribute('position', new THREE.BufferAttribute(dust, 3));
const dustPts = new THREE.Points(dustGeo, new THREE.PointsMaterial({ color: 0x4fc3cf, size: 0.09, transparent: true, opacity: 0.6 }));
scene.add(dustPts);

// ---- a drawn document page, used for every sheet ----
function pageTexture(detail) {
  const c = document.createElement('canvas'); c.width = detail ? 1024 : 256; c.height = detail ? 1448 : 362;
  const g = c.getContext('2d'), s = c.width / 256;
  g.fillStyle = '#fbfdfd'; g.fillRect(0, 0, c.width, c.height);
  g.fillStyle = '#143f45'; g.fillRect(20 * s, 22 * s, 120 * s, 9 * s);
  g.fillStyle = '#9fb4b8';
  for (let y = 46; y < 150; y += 11) g.fillRect(20 * s, y * s, (150 + Math.sin(y * 3.1) * 55) * s, 4 * s);
  g.strokeStyle = '#143f45'; g.lineWidth = 1.2 * s;
  for (let r = 0; r < 5; r++) for (let col = 0; col < 3; col++) {
    if (detail && r === 3) { g.fillStyle = '#ffd98a'; g.fillRect((20 + col * 72) * s, (170 + r * 22) * s, 72 * s, 22 * s); }
    g.strokeRect((20 + col * 72) * s, (170 + r * 22) * s, 72 * s, 22 * s);
    g.fillStyle = r ? '#5b7f85' : '#143f45'; g.fillRect((28 + col * 72) * s, (178 + r * 22) * s, (r ? 30 + ((r * 7 + col * 13) % 22) : 44) * s, 5 * s);
  }
  g.fillStyle = '#9fb4b8';
  for (let y = 300; y < 330; y += 11) g.fillRect(20 * s, y * s, (130 + Math.cos(y) * 50) * s, 4 * s);
  g.strokeStyle = '#143f45'; g.strokeRect(150 * s, 328 * s, 90 * s, 24 * s); // title block
  const t = new THREE.CanvasTexture(c); t.colorSpace = THREE.SRGBColorSpace; t.anisotropy = 8; return t;
}

// ---- the library: many sheets with three arrangements ----
const N = small ? 90 : 220, COLS = small ? 15 : 22;
const sheets = new THREE.InstancedMesh(new THREE.PlaneGeometry(1.4, 1.98), new THREE.MeshBasicMaterial({ map: pageTexture(false), side: THREE.DoubleSide }), N);
scene.add(sheets);
const poses = [];
const rnd = (a) => (Math.random() - 0.5) * a;
for (let i = 0; i < N; i++) {
  const col = i % COLS, row = Math.floor(i / COLS), ang = (col / (COLS - 1) - 0.5) * 2.5, R = 17;
  poses.push({
    cloud: { p: new THREE.Vector3(rnd(70), rnd(26) + 4, -18 - Math.random() * 60), q: new THREE.Quaternion().setFromEuler(new THREE.Euler(rnd(2), rnd(3), rnd(2))) },
    wall: { p: new THREE.Vector3(Math.sin(ang) * R, row * 2.3 - 5, -Math.cos(ang) * R - 12), q: new THREE.Quaternion().setFromEuler(new THREE.Euler(0, -ang, 0)) },
    back: { p: new THREE.Vector3(Math.sin(ang) * (R + 14), row * 2.6 - 6, -Math.cos(ang) * (R + 14) - 20), q: new THREE.Quaternion().setFromEuler(new THREE.Euler(0, -ang, 0)) },
    drift: Math.random() * 6.28,
  });
}

// ---- the one sheet being read ----
const hero = new THREE.Group(); scene.add(hero);
const page = new THREE.Mesh(new THREE.PlaneGeometry(5.6, 7.92), new THREE.MeshBasicMaterial({ map: pageTexture(true) }));
hero.add(page);
const beam = new THREE.Mesh(new THREE.PlaneGeometry(6.2, 0.07), new THREE.MeshBasicMaterial({ color: AMBER, transparent: true, opacity: 0.95 }));
const glow = new THREE.Mesh(new THREE.PlaneGeometry(6.2, 1.5), new THREE.MeshBasicMaterial({ color: AMBER, transparent: true, opacity: 0.16, blending: THREE.AdditiveBlending, depthWrite: false }));
beam.position.z = glow.position.z = 0.02; hero.add(beam, glow);
hero.position.set(0, 1, -12);

// ---- citation balloons with leader lines ----
function numberTexture(n) {
  const c = document.createElement('canvas'); c.width = c.height = 128; const g = c.getContext('2d');
  g.fillStyle = '#f9af46'; g.beginPath(); g.arc(64, 64, 58, 0, 7); g.fill();
  g.strokeStyle = '#143f45'; g.lineWidth = 9; g.stroke();
  g.fillStyle = '#143f45'; g.font = '700 70px system-ui, sans-serif'; g.textAlign = 'center'; g.textBaseline = 'middle'; g.fillText(n, 64, 70);
  const t = new THREE.CanvasTexture(c); t.colorSpace = THREE.SRGBColorSpace; return t;
}
const balloons = [1, 2, 3].map((n, i) => {
  const s = new THREE.Sprite(new THREE.SpriteMaterial({ map: numberTexture(n), transparent: true, depthTest: false }));
  const from = new THREE.Vector3(-1.2 + i * 1.5, 1 - 0.55, -11.9), to = new THREE.Vector3(1.6 + i * 0.5, 3.3 - i * 1.5, -8.2 + i * 0.5);
  const line = new THREE.Line(new THREE.BufferGeometry().setFromPoints([from, from.clone()]), new THREE.LineBasicMaterial({ color: AMBER, transparent: true }));
  scene.add(s, line); return { s, line, from, to };
});

// ---- the brand diamond ----
const shape = new THREE.Shape(); const D = 1.5, r = 0.42;
shape.moveTo(-D + r, -D); shape.lineTo(D - r, -D); shape.quadraticCurveTo(D, -D, D, -D + r); shape.lineTo(D, D - r); shape.quadraticCurveTo(D, D, D - r, D);
shape.lineTo(-D + r, D); shape.quadraticCurveTo(-D, D, -D, D - r); shape.lineTo(-D, -D + r); shape.quadraticCurveTo(-D, -D, -D + r, -D);
const diamond = new THREE.Mesh(new THREE.ExtrudeGeometry(shape, { depth: 0.5, bevelEnabled: true, bevelSize: 0.08, bevelThickness: 0.08, bevelSegments: 3 }),
  new THREE.MeshStandardMaterial({ color: TEAL, roughness: 0.35, metalness: 0.15, emissive: 0x0d3338 }));
diamond.rotation.z = Math.PI / 4; diamond.position.set(0, 4.6, 4);
const miniPage = new THREE.Mesh(new THREE.PlaneGeometry(1.25, 1.75), new THREE.MeshBasicMaterial({ map: pageTexture(false) }));
miniPage.position.set(-0.15, 4.75, 4.62);
const dot = new THREE.Sprite(new THREE.SpriteMaterial({ map: numberTexture('D'), depthTest: false })); dot.scale.setScalar(1.25); dot.position.set(0.85, 3.85, 4.9);
const logo = new THREE.Group(); logo.add(diamond, miniPage, dot); scene.add(logo);
scene.add(new THREE.AmbientLight(0xffffff, 1.1)); const key = new THREE.DirectionalLight(0xffffff, 2.2); key.position.set(4, 8, 10); scene.add(key);

// ---- camera path, driven by scroll ----
const path = [ // [progress, camera position, look-at]; one keyframe per page section
  [0.00, [0, 4.2, 13], [0, 2.6, 0]],
  [0.17, [0, 3, 16], [0, 1, -14]],
  [0.34, [3.4, 1.4, -2.6], [1.9, 1, -12]],
  [0.50, [-3.6, 1.5, -1.2], [-2.1, 1, -11]],
  [0.67, [9, 4.5, 11], [0, 1, -15]],
  [0.84, [0, 11, 23], [0, 2, -10]],
  [1.00, [0, 4.2, 13], [0, 2.6, 0]],
].map(([t, p, l]) => ({ t, p: new THREE.Vector3(...p), l: new THREE.Vector3(...l) }));
const ease = (x) => x * x * (3 - 2 * x), clamp = (x) => Math.min(1, Math.max(0, x)), span = (p, a, b) => ease(clamp((p - a) / (b - a)));
let target = 0, prog = 0;
const read = () => { const h = document.documentElement.scrollHeight - innerHeight; target = h > 0 ? scrollY / h : 0; };
addEventListener('scroll', read, { passive: true });
function resize() { renderer.setSize(innerWidth, innerHeight, false); camera.aspect = innerWidth / innerHeight; camera.fov = innerWidth < innerHeight ? 68 : 48; camera.updateProjectionMatrix(); }
addEventListener('resize', resize); resize(); read();

const m = new THREE.Matrix4(), q = new THREE.Quaternion(), v = new THREE.Vector3(), one = new THREE.Vector3(1, 1, 1), look = new THREE.Vector3();
const still = matchMedia('(prefers-reduced-motion: reduce)').matches;
function frame(ms) {
  const t = ms / 1000; prog += (target - prog) * (still ? 1 : 0.075);
  let i = 0; while (i < path.length - 2 && prog > path[i + 1].t) i++;
  const a = path[i], b = path[i + 1], k = ease(clamp((prog - a.t) / (b.t - a.t)));
  camera.position.lerpVectors(a.p, b.p, k); camera.position.y += still ? 0 : Math.sin(t * 0.5) * 0.12; look.lerpVectors(a.l, b.l, k); camera.lookAt(look);

  const gather = span(prog, 0.04, 0.2), recede = span(prog, 0.24, 0.36) * (1 - span(prog, 0.58, 0.7));
  for (let n = 0; n < N; n++) {
    const s = poses[n], wob = still ? 0 : Math.sin(t * 0.4 + s.drift);
    v.lerpVectors(s.cloud.p, s.wall.p, gather).lerp(s.back.p, recede); v.y += wob * 0.25 * (1 - gather);
    q.slerpQuaternions(s.cloud.q, s.wall.q, gather);
    sheets.setMatrixAt(n, m.compose(v, q, one));
  }
  sheets.instanceMatrix.needsUpdate = true;

  const show = span(prog, 0.22, 0.34) * (1 - span(prog, 0.58, 0.68));
  hero.scale.setScalar(0.001 + show); hero.rotation.y = (1 - show) * 0.9;
  const sweep = still ? 0.2 : (Math.sin(t * 1.3) * 0.5 + 0.5); beam.position.y = glow.position.y = 3.6 - sweep * 7.2;
  beam.material.opacity = 0.95 * (1 - span(prog, 0.4, 0.46)); glow.material.opacity = 0.16 * (1 - span(prog, 0.4, 0.46));

  balloons.forEach((bl, n) => {
    const fly = span(prog, 0.4 + n * 0.02, 0.5 + n * 0.02) * (1 - span(prog, 0.58, 0.66));
    v.lerpVectors(bl.from, bl.to, fly); bl.s.position.copy(v); bl.s.scale.setScalar(0.001 + fly * (0.95 + (still ? 0 : Math.sin(t * 2 + n) * 0.04)));
    bl.line.geometry.setFromPoints([bl.from, v]); bl.line.material.opacity = fly * 0.9; bl.s.material.opacity = clamp(fly * 3);
  });

  const fade = Math.max(1 - span(prog, 0.04, 0.14), span(prog, 0.9, 1));
  logo.visible = fade > 0.01; logo.scale.setScalar(0.45 + 0.3 * fade); logo.rotation.y = still ? 0 : Math.sin(t * 0.6) * 0.35; logo.position.y = 1.9 + (still ? 0 : Math.sin(t * 0.9) * 0.12) + (1 - fade) * 6;
  dustPts.rotation.y = t * 0.01; grid.position.z = still ? 0 : (t * 0.6) % 2;
  renderer.render(scene, camera);
  if (!document.hidden) requestAnimationFrame(frame);
}
document.addEventListener('visibilitychange', () => { if (!document.hidden) requestAnimationFrame(frame); });
requestAnimationFrame(frame);

// ---- text reveals ----
const io = new IntersectionObserver((es) => es.forEach((e) => e.target.classList.toggle('in', e.isIntersecting)), { threshold: 0.25 });
document.querySelectorAll('.reveal').forEach((el) => io.observe(el));
document.documentElement.classList.add('js');
