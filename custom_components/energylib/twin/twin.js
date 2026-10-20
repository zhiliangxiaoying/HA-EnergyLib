/* EnergyLIB digital twin: self-built scene + live EnergyLIB entities from the HA frontend.
 * Runs in the EnergyLIB dashboard's iframe card and reads the parent page's hass object;
 * it stores no credentials and makes no REST calls. */
(() => {
  'use strict';
  const VERSION = window.ENERGYLIB_TWIN_VERSION || 'dev';
  // The all-in-one cabinet sits just behind the EV charger in the driveway.
  // Caster bottoms sit on the access paver (top Y=-0.411).
  const ENERGY_HUB = {x: 12.025, y: -.425, z: -3.15};
  // The pole foundation bottom sits on the site surface (Y=-0.48).
  const GRID_POLE = {x: 17.5, y: -.405, z: -4.7, height: 6};
  const ENERGY_PORT = {x: ENERGY_HUB.x - .18, y: 1.55, z: ENERGY_HUB.z - 1};
  const HOME_LOAD = {x: -2.5, y: 1.93, z: -4.45};
  // World box the support board behind the cabinet must fill (bottom on the paver).
  const BACKBOARD = {center: [11.665, 1.649, -3.15], height: 4.12};
  // Charging bay: rear faces the charger, charge port lines up with the cable plug.
  const CAR = {x: 14.4, y: 0, z: 3.32, length: 4.49};
  const COLORS = {solar: '#f7b731', grid: '#5299ff', battery: '#b28aff', home: '#38d39f'};
  // Standard EnergyLIB sign convention: grid positive = import, battery positive = discharge.
  const KEYS = ['solar_power', 'grid_power', 'battery_power', 'load_power', 'battery_total_soc', 'battery_soc', 'running_state'];
  const DEVICE_KEY = 'energylib.twin.device';
  const VIEWS = {
    overview: [25, 23, 29, 3, 0, -.5, 8],
    living: [-2.5, 6, 8, -2.5, 1, -2, 2.5],
    energy: [26, 11.5, 17, 14.1, 2, -2.2, 2.5],
    ev: [22, 12, 15, 12, 1.6, 0, 2.5],
  };

  const $ = id => document.getElementById(id);
  const srgb = hex => new THREE.Color(hex).convertSRGBToLinear();
  const fmt = v => v === null || v === undefined ? '—' : Math.abs(v) >= 1000 ? (Math.abs(v) / 1000).toFixed(2) + ' kW' : Math.abs(v).toFixed(0) + ' W';

  let scene, camera, renderer, controls, composer = null, aaPass = null, fine = false, evening = false;
  let hass = null, devices = [], device = null, registryFallback = null, registryPending = false;
  const flowRoutes = [], pins = {}, loaded = new Set();
  let viewReady = false, loadFailed = false, cameraTween = null;

  // ---------- Loading overlay ----------
  function finishLoading() {
    if (viewReady || loadFailed || loaded.size < 5) return;
    viewReady = true;
    requestAnimationFrame(() => {
      controls.update(); renderFrame();
      document.body.classList.remove('twin-booting'); $('twin-loading').hidden = true;
    });
  }
  function failLoading(message) {
    loadFailed = true; $('twin-loading-message').textContent = message; $('twin-loading-retry').hidden = false;
  }

  // ---------- Scene ----------
  function initScene() {
    scene = new THREE.Scene();
    scene.background = new THREE.Color(0xa9bac4);
    camera = new THREE.PerspectiveCamera(40, innerWidth / innerHeight, .1, 160);
    camera.position.set(25, 23, 29);
    renderer = new THREE.WebGLRenderer({antialias: true, alpha: true});
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    renderer.outputEncoding = THREE.sRGBEncoding;
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = .92;
    $('canvas-container').appendChild(renderer.domElement);

    controls = new THREE.OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = .05;
    controls.maxPolarAngle = Math.PI / 2.15;
    controls.minDistance = 8;
    controls.maxDistance = 65;
    controls.target.set(3, 0, -.5);

    scene.add(new THREE.AmbientLight(0xffffff, .16));
    const key = new THREE.DirectionalLight(0xffefd6, 1.25);
    key.name = 'Key_Light';
    key.position.set(-8, 20, 10); key.target.position.set(2, 0, -2);
    key.castShadow = true; key.shadow.mapSize.set(2048, 2048);
    Object.assign(key.shadow.camera, {left: -23, right: 23, top: 23, bottom: -23, near: .1, far: 85});
    key.shadow.camera.updateProjectionMatrix();
    key.shadow.bias = -.0003; key.shadow.normalBias = .025; key.shadow.radius = 3;
    scene.add(key, key.target);
    scene.add(new THREE.HemisphereLight(0xc7dcf4, 0x685849, .32));
    const rim = new THREE.DirectionalLight(0xb2cfff, .45); rim.position.set(14, 10, -18); scene.add(rim);
    const energyLight = new THREE.PointLight(0xffeee0, .35, 13); energyLight.position.set(-7.3, 5.3, -5); scene.add(energyLight);

    // A small local studio environment supplies paint and glass reflections without network requests.
    const studio = new THREE.Scene(); studio.background = new THREE.Color(.22, .26, .30);
    const panel = (x, y, z, w, h, color, power) => {
      const m = new THREE.Mesh(new THREE.PlaneGeometry(w, h), new THREE.MeshBasicMaterial({color: new THREE.Color(color).multiplyScalar(power), side: THREE.DoubleSide}));
      m.position.set(x, y, z); m.lookAt(0, 0, 0); studio.add(m);
    };
    panel(-10, 9, 5, 10, 14, 0xffe7c5, 2.5); panel(8, 9, -8, 8, 16, 0xc3dfff, 2); panel(0, 15, 0, 15, 10, 0xffffff, 1.5);
    const pmrem = new THREE.PMREMGenerator(renderer);
    scene.environment = pmrem.fromScene(studio, .04, .1, 100).texture;
    pmrem.dispose(); studio.traverse(o => { o.geometry?.dispose(); o.material?.dispose(); });
    const stage = new THREE.Mesh(new THREE.PlaneGeometry(2000, 2000), new THREE.MeshStandardMaterial({color: 0x52616a, roughness: 1, metalness: 0, envMapIntensity: .12}));
    stage.rotation.x = -Math.PI / 2; stage.position.y = -.85; stage.receiveShadow = true; scene.add(stage);

    try {
      composer = new THREE.EffectComposer(renderer);
      const ao = new THREE.SSAOPass(scene, camera, innerWidth, innerHeight);
      ao.kernelRadius = .8; ao.minDistance = .001; ao.maxDistance = .06;
      // Avoid depth quantization bands in contact shading at the overview distance.
      ao.normalRenderTarget.depthTexture.type = THREE.UnsignedIntType;
      // Energy indicators are graphics, so they must not cast ambient shading onto walls.
      const hideForAO = ao.overrideVisibility.bind(ao);
      ao.overrideVisibility = () => { hideForAO(); for (const r of flowRoutes) for (const o of [r.mesh, r.glow, r.pulse, ...r.arrows]) o.visible = false; };
      composer.addPass(ao); composer.addPass(new THREE.ShaderPass(THREE.GammaCorrectionShader));
      // Offscreen SSAO loses native canvas MSAA. Apply SMAA last, in display colour space.
      aaPass = new THREE.SMAAPass(1, 1); composer.addPass(aaPass);
    } catch (e) { console.warn('Ambient shading unavailable', e.message); composer = null; }
    resize(); addEventListener('resize', resize);
  }

  function renderFrame() { if (composer && fine) composer.render(); else renderer.render(scene, camera); }

  function resize() {
    const w = innerWidth, h = innerHeight;
    camera.aspect = w / h; camera.updateProjectionMatrix();
    // Centre the model in the open area beside the energy panel.
    camera.setViewOffset(w, h, w > 760 ? -Math.min(160, w * .125) : 0, 0, w, h);
    const scale = fine ? Math.min(2, Math.max(1.5, devicePixelRatio)) : Math.min(devicePixelRatio, 1);
    renderer.setPixelRatio(Math.min(scale, renderer.capabilities.maxTextureSize / Math.max(w, h)));
    renderer.setSize(w, h);
    // Size every offscreen pass to the same physical buffer, including SMAA texels.
    const buffer = renderer.getDrawingBufferSize(new THREE.Vector2());
    if (composer) { composer.setPixelRatio(1); composer.setSize(buffer.x, buffer.y); }
  }

  // ---------- Models ----------
  function shade(root, envIntensity) {
    root.traverse(o => {
      if (!o.isMesh) return;
      o.castShadow = true; o.receiveShadow = true;
      for (const m of [].concat(o.material)) {
        if (m.isMeshStandardMaterial) m.envMapIntensity = envIntensity;
        if (m.transparent) { m.depthWrite = false; o.castShadow = false; }
      }
    });
  }

  // Move the support board from the entry wall to behind the cabinet. Box-based, so it
  // does not depend on the node transform the model compressor chose.
  function placeBackboard(board) {
    board.parent.remove(board);
    board.rotation.y = Math.PI / 2; board.updateMatrixWorld(true);
    const box = new THREE.Box3().setFromObject(board);
    board.scale.y *= BACKBOARD.height / box.getSize(new THREE.Vector3()).y; board.updateMatrixWorld(true);
    box.setFromObject(board);
    board.position.add(new THREE.Vector3(...BACKBOARD.center).sub(box.getCenter(new THREE.Vector3())));
    scene.add(board);
  }

  function loadModels() {
    const loader = new THREE.GLTFLoader();
    loader.setMeshoptDecoder(window.MeshoptDecoder);
    const load = (name, failure, done) => loader.load('models/' + name + '?v=' + encodeURIComponent(VERSION), g => {
      done(g.scene); scene.add(g.scene); loaded.add(name); finishLoading();
    }, undefined, err => { console.error(name, err); failLoading(failure); });
    load('home-interior.glb', '家庭视图加载失败，请重新加载', root => {
      root.name = 'Home_Interior'; shade(root, .28);
      const board = root.getObjectByName('Entry_Energy_Backboard');
      if (board) placeBackboard(board);
    });
    load('home-energy-system.glb', '能源设备视图加载失败，请重新加载', root => {
      root.name = 'Energy_System'; root.position.set(ENERGY_HUB.x, ENERGY_HUB.y, ENERGY_HUB.z);
      root.rotation.y = Math.PI / 2; root.scale.setScalar(1.8); shade(root, 1);
    });
    load('solar-ev-system.glb', '光伏与充电桩视图加载失败，请重新加载', root => { root.name = 'Solar_Charger'; shade(root, .8); });
    load('garden-site.glb', '庭院视图加载失败，请重新加载', root => { root.name = 'Garden_Site'; shade(root, .25); });
    // Sports car (CC BY 4.0, see NOTICE): scaled to its real length, nose +z, parked in the bay.
    load('sports-car.glb', '汽车视图加载失败，请重新加载', root => {
      root.name = 'Sports_Car';
      const size = new THREE.Box3().setFromObject(root).getSize(new THREE.Vector3());
      root.scale.setScalar(CAR.length / Math.max(size.x, size.z));
      const fit = new THREE.Box3().setFromObject(root), centre = fit.getCenter(new THREE.Vector3());
      root.position.set(CAR.x - centre.x, CAR.y - fit.min.y, CAR.z - centre.z);
      shade(root, .9);
    });
  }

  // ---------- Grid pole and power-flow routes ----------
  function buildGridPole() {
    const pole = new THREE.Group(); pole.name = 'Utility_Grid_Pole'; pole.position.set(GRID_POLE.x, GRID_POLE.y, GRID_POLE.z);
    const concrete = new THREE.MeshStandardMaterial({color: 0xb0b5b5, roughness: .92});
    const steel = new THREE.MeshStandardMaterial({color: 0x46515b, roughness: .58, metalness: .55});
    const ceramic = new THREE.MeshStandardMaterial({color: 0xb6d2ce, roughness: .3});
    const add = (geometry, material, x, y, z) => { const mesh = new THREE.Mesh(geometry, material); mesh.position.set(x, y, z); mesh.castShadow = true; mesh.receiveShadow = true; pole.add(mesh); return mesh; };
    add(new THREE.CylinderGeometry(.13, .23, GRID_POLE.height, 16), concrete, 0, GRID_POLE.height / 2, 0);
    add(new THREE.CylinderGeometry(.36, .4, .25, 16), concrete, 0, .05, 0);
    add(new THREE.BoxGeometry(.24, .18, 2.3), steel, 0, 5.45, 0);
    for (const z of [-.85, 0, .85]) {
      add(new THREE.CylinderGeometry(.055, .055, .4, 10), steel, 0, 5.7, z);
      for (const y of [5.65, 5.76, 5.87]) add(new THREE.CylinderGeometry(.14, .14, .07, 12), ceramic, 0, y, z);
    }
    add(new THREE.BoxGeometry(.34, .5, .25), steel, .22, 1.7, 0);
    const band = new THREE.MeshStandardMaterial({color: 0xe9bd52, roughness: .8});
    for (const y of [.7, 1, 1.3]) add(new THREE.CylinderGeometry(.22, .23, .1, 16), band, 0, y, 0);
    scene.add(pole);
    // Permanent overhead conductors stay visible when grid power is zero.
    const wire = new THREE.MeshStandardMaterial({color: 0x303941, roughness: .8});
    for (const z of [-.85, 0, .85]) {
      const curve = new THREE.CatmullRomCurve3([new THREE.Vector3(22, GRID_POLE.y + 5.91, GRID_POLE.z + z), new THREE.Vector3(20, GRID_POLE.y + 5.5, GRID_POLE.z + z), new THREE.Vector3(GRID_POLE.x, GRID_POLE.y + 5.91, GRID_POLE.z + z)]);
      scene.add(new THREE.Mesh(new THREE.TubeGeometry(curve, 32, .024, 6, false), wire));
    }
    const badge = document.createElement('canvas'); badge.width = 512; badge.height = 160;
    const ctx = badge.getContext('2d');
    ctx.fillStyle = '#13232b'; ctx.fillRect(0, 0, 512, 160); ctx.strokeStyle = '#70d9d1'; ctx.strokeRect(2, 2, 508, 156);
    ctx.fillStyle = '#cee5df'; ctx.font = '42px sans-serif'; ctx.textAlign = 'center'; ctx.fillText('电网进线', 256, 96);
    const label = new THREE.Sprite(new THREE.SpriteMaterial({map: new THREE.CanvasTexture(badge), transparent: true}));
    label.position.set(GRID_POLE.x, GRID_POLE.y + 6.7, GRID_POLE.z); label.scale.set(2.1, .65, 1); scene.add(label);
  }

  function buildFlows() {
    const v = (x, y, z) => new THREE.Vector3(x, y, z);
    const hub = v(ENERGY_PORT.x, ENERGY_PORT.y, ENERGY_PORT.z);
    // Each route runs in its positive direction: solar/grid/battery into the inverter, inverter to the home.
    const defs = [
      ['solar', [v(7.1, 2.7, -11.0), v(7.3, 2.3, -7.5), v(10.1, 2.4, ENERGY_HUB.z - 1.75), v(11.4, 2.1, ENERGY_HUB.z - 1.3), hub], 0xf7b731],
      ['grid', [v(GRID_POLE.x, GRID_POLE.y + 5.91, GRID_POLE.z), v(15.4, 4.4, ENERGY_HUB.z - 1.4), v(13.1, 3.9, ENERGY_HUB.z - 1.15), v(ENERGY_HUB.x + .18, 2.85, ENERGY_HUB.z - 1.1), hub], 0x5299ff],
      // A short DC link alongside the gap between battery and inverter.
      ['battery', [v(ENERGY_HUB.x - .04, 1.48, ENERGY_HUB.z + .57), v(ENERGY_HUB.x + .12, 1.53, ENERGY_HUB.z + 1.1), v(ENERGY_HUB.x + .12, 1.78, ENERGY_HUB.z + 1.1), v(ENERGY_HUB.x - .04, 1.86, ENERGY_HUB.z + .57)], 0xb28aff],
      ['home', [hub, v(8.7, .5, -1.5), v(3.4, .18, -2.3), v(-2.5, .16, -3.1), v(-2.5, .45, -4.3), v(HOME_LOAD.x, HOME_LOAD.y, HOME_LOAD.z)], 0x38d39f],
    ];
    for (const [id, points, color] of defs) {
      const curve = new THREE.CatmullRomCurve3(points), sample = curve.getPoint.bind(curve);
      curve.getPoint = (t, target) => { const p = sample(t, target); p.y = Math.max(.14, p.y); return p; };
      const length = curve.getLength(), linear = srgb(color), thin = id === 'battery';
      if (id === 'grid') scene.add(new THREE.Mesh(new THREE.TubeGeometry(curve, 80, .03, 8, false), new THREE.MeshStandardMaterial({color: 0x303941, roughness: .8})));
      const mesh = new THREE.Mesh(new THREE.TubeGeometry(curve, 80, thin ? .015 : .035, 8, false), new THREE.MeshBasicMaterial({color: linear, transparent: true, opacity: .04, depthWrite: false, toneMapped: false}));
      const glow = new THREE.Mesh(new THREE.TubeGeometry(curve, 80, thin ? .045 : .11, 8, false), new THREE.MeshBasicMaterial({color: linear, transparent: true, opacity: 0, depthWrite: false, toneMapped: false, blending: THREE.AdditiveBlending}));
      // Separated packets with a white highlight read well against a pale floor.
      const stripe = document.createElement('canvas'); stripe.width = 256; stripe.height = 16;
      const ctx = stripe.getContext('2d'), rgb = [color >> 16 & 255, color >> 8 & 255, color & 255].join(',');
      const gradient = ctx.createLinearGradient(0, 0, 160, 0);
      gradient.addColorStop(0, 'rgba(' + rgb + ',0)'); gradient.addColorStop(.25, 'rgba(' + rgb + ',.7)'); gradient.addColorStop(.55, COLORS[id]); gradient.addColorStop(.8, '#fff'); gradient.addColorStop(1, '#fff');
      ctx.fillStyle = gradient; ctx.fillRect(0, 0, 160, 16);
      const texture = new THREE.CanvasTexture(stripe); texture.encoding = THREE.sRGBEncoding; texture.wrapS = THREE.RepeatWrapping; texture.repeat.x = Math.max(1, length / 1.7);
      const pulse = new THREE.Mesh(new THREE.TubeGeometry(curve, 80, thin ? .035 : .075, 8, false), new THREE.MeshBasicMaterial({map: texture, transparent: true, depthWrite: false, toneMapped: false}));
      pulse.renderOrder = 2; pulse.visible = false;
      scene.add(mesh, glow, pulse);
      const arrows = [], count = thin ? 1 : Math.max(2, Math.min(7, Math.round(length / 2.8)));
      const head = new THREE.ConeGeometry(.17, .48, 10), stem = new THREE.CylinderGeometry(.045, .045, .32, 8);
      const colored = new THREE.MeshBasicMaterial({color: linear, toneMapped: false}), white = new THREE.MeshBasicMaterial({color: 0xffffff, toneMapped: false});
      for (let i = 0; i < count; i++) {
        const arrow = new THREE.Group(), stemMesh = new THREE.Mesh(stem, white);
        if (thin) arrow.scale.setScalar(.4);
        stemMesh.position.y = -.28; arrow.add(new THREE.Mesh(head, colored), stemMesh); arrow.visible = false;
        scene.add(arrow); arrows.push(arrow);
      }
      flowRoutes.push({id, curve, length, mesh, glow, pulse, texture, arrows, phase: 0, active: false, direction: 1, speed: 1.8 / length});
    }
  }

  const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)'), up = new THREE.Vector3(0, 1, 0);
  function stepFlows(dt) {
    const reduced = reducedMotion.matches;
    for (const r of flowRoutes) {
      if (r.active && !reduced) {
        r.phase = (r.phase + dt * r.speed * r.direction + 1) % 1;
        r.texture.offset.x = (r.texture.offset.x - dt * r.speed * r.texture.repeat.x * r.direction) % 1;
      }
      r.arrows.forEach((o, i) => {
        o.visible = r.active;
        if (!r.active) return;
        const u = ((reduced ? 0 : r.phase) + i / r.arrows.length) % 1;
        o.position.copy(r.curve.getPointAt(u));
        o.quaternion.setFromUnitVectors(up, r.curve.getTangentAt(u).multiplyScalar(r.direction));
      });
    }
  }

  // ---------- Home Assistant binding ----------
  function findHass() {
    let w = window;
    for (let n = 0; n < 5; n++) {
      try {
        const root = w.document.querySelector('home-assistant');
        if (root && root.hass) return root.hass;
      } catch { return null; }
      if (w === w.parent) break;
      w = w.parent;
    }
    return null;
  }

  // EnergyLIB entities are found by platform + translation_key, so renamed entity IDs still match.
  function registryEntries(h) {
    const list = Object.values(h.entities || {}).filter(e => e.platform === 'energylib');
    if (!list.length || list.some(e => e.translation_key)) return list;
    if (registryFallback) return registryFallback;
    if (!registryPending && h.callWS) {
      registryPending = true;
      h.callWS({type: 'config/entity_registry/list_for_display'}).then(r => {
        registryFallback = (r.entities || []).filter(e => e.pl === 'energylib').map(e => ({entity_id: e.ei, device_id: e.di, platform: e.pl, translation_key: e.tk}));
        refresh();
      }).catch(() => { registryFallback = list; });
    }
    return list;
  }

  function findDevices(h) {
    const byDevice = new Map();
    for (const e of registryEntries(h)) {
      if (!e.device_id || !KEYS.includes(e.translation_key)) continue;
      const map = byDevice.get(e.device_id) || {};
      map[e.translation_key] = e.entity_id; byDevice.set(e.device_id, map);
    }
    const name = id => { const d = h.devices?.[id]; return d?.name_by_user || d?.name || 'EnergyLIB'; };
    return [...byDevice].map(([id, entities]) => ({id, entities, name: name(id)}))
      .sort((a, b) => a.name.localeCompare(b.name, 'zh-CN') || a.id.localeCompare(b.id));
  }

  function storedDevice() { try { return localStorage.getItem(DEVICE_KEY); } catch { return null; } }

  function syncDeviceSelect() {
    const select = $('device-select');
    const wanted = device?.id || storedDevice();
    device = devices.find(d => d.id === wanted) || devices[0] || null;
    select.hidden = devices.length < 2;
    const signature = devices.map(d => d.id + d.name).join('|');
    if (select.dataset.signature !== signature) {
      select.dataset.signature = signature;
      select.replaceChildren(...devices.map(d => { const o = document.createElement('option'); o.value = d.id; o.textContent = d.name; return o; }));
    }
    if (device) select.value = device.id;
  }

  function stateOf(key) {
    const id = device?.entities[key];
    const s = id && hass?.states[id];
    return s && !['unknown', 'unavailable', ''].includes(s.state) ? s : null;
  }

  function reading(key) {
    const s = stateOf(key);
    if (!s) return null;
    const v = Number(s.state), unit = s.attributes.unit_of_measurement;
    if (!Number.isFinite(v)) return null;
    if (key.endsWith('_soc')) return unit === '%' && v >= 0 && v <= 100 ? v : null;
    const factor = {W: 1, kW: 1e3, MW: 1e6}[unit];
    return factor ? v * factor : null;
  }

  function note(key, v, text) {
    return !hass ? '等待 HA 连接' : !device ? '未找到设备' : !device.entities[key] ? '未找到实体' : v === null ? '暂无数据' : text;
  }

  function refresh() {
    hass = findHass();
    devices = hass ? findDevices(hass) : [];
    syncDeviceSelect();
    $('ha-link-status').textContent = hass ? 'HA 已连接 · 数据随设备更新' : '未连接 Home Assistant';
    const notice = $('energy-notice');
    notice.hidden = !!(hass && device);
    notice.textContent = !hass ? '请从 Home Assistant 侧边栏打开本页面，才能读取设备数据。' : '未找到 EnergyLIB 设备。请先在“设置 → 设备与服务”中添加 EnergyLIB 集成。';
    $('energy-device').textContent = device ? device.name : '';

    const socKey = device?.entities.battery_total_soc ? 'battery_total_soc' : 'battery_soc';
    const d = {solar: reading('solar_power'), grid: reading('grid_power'), battery: reading('battery_power'), load: reading('load_power'), soc: reading(socKey)};
    const direction = (v, pos, neg, idle) => v > 5 ? pos : v < -5 ? neg : idle;
    $('metric-solar').textContent = fmt(d.solar); $('metric-solar-note').textContent = note('solar_power', d.solar, d.solar > 5 ? '发电中' : '无输出');
    $('metric-grid').textContent = fmt(d.grid); $('metric-grid-note').textContent = note('grid_power', d.grid, direction(d.grid, '电网购电', '馈入电网', '待机'));
    $('metric-battery').textContent = fmt(d.battery); $('metric-battery-note').textContent = note('battery_power', d.battery, direction(d.battery, '放电', '充电', '待机'));
    $('metric-load').textContent = fmt(d.load); $('metric-load-note').textContent = note('load_power', d.load, '家庭用电');
    $('metric-soc').textContent = d.soc === null ? '—' : d.soc.toFixed(0) + ' %'; $('metric-soc-note').textContent = note(socKey, d.soc, '电池总电量');
    $('soc-fill').style.width = (d.soc ?? 0) + '%';
    const running = stateOf('running_state');
    $('metric-state').textContent = running ? (hass.formatEntityState ? hass.formatEntityState(running) : running.state) : '—';
    $('metric-state-note').textContent = note('running_state', running, '逆变器');
    $('flow-inverter-val').textContent = running ? $('metric-state').textContent : '—';

    const connected = Object.values(d).filter(v => v !== null).length;
    $('energy-data-summary').textContent = '设备数据 · ' + (connected ? connected + '/5 项可用' : '等待连接');
    $('energy-badge').textContent = connected ? connected + '/5 项可用' : '数据待连接';
    $('energy-badge').style.color = connected ? '#7cddb0' : '#98b5b8';
    $('flow-scenario').textContent = connected ? [d.solar > 5 ? '光伏发电' : '光伏无输出', direction(d.battery, '储能放电', '储能充电', '储能待机'), direction(d.grid, '电网购电', '馈入电网', '电网待机')].join(' · ') : '等待能源设备数据';
    $('flow-solar-val').textContent = fmt(d.solar); $('flow-grid-val').textContent = fmt(d.grid);
    $('flow-battery-val').textContent = fmt(d.battery); $('flow-load-val').textContent = fmt(d.load);
    $('flow-battery-direction').textContent = d.battery === null ? '待接入' : direction(d.battery, '放电 ↑', '充电 ↓', '待机');
    $('energy-explanation').textContent = connected ? '光带与箭头按功率方向移动；灰线表示待机或暂无数据。' : '连接 EnergyLIB 设备后，这里会显示功率流向。';
    const stamps = KEYS.map(k => stateOf(k)?.last_updated).filter(Boolean).sort();
    $('energy-updated').textContent = stamps.length ? '更新时间：' + new Date(stamps.at(-1)).toLocaleString('zh-CN', {hour12: false}) : hass ? '等待设备数据' : '';

    const vectors = [['solar', d.solar, 'M46 67 L46 105 L117 105', 'M117 105 L46 105 L46 67'], ['grid', d.grid, 'M254 67 L254 105 L183 105', 'M183 105 L254 105 L254 67'], ['home', d.load, 'M150 127 L150 161', 'M150 161 L150 127'], ['battery', d.battery, 'M46 161 L46 123 L117 113', 'M117 113 L46 123 L46 161']];
    for (const [id, v, forward, reverse] of vectors) {
      const active = v !== null && Math.abs(v) > 5, svg = $('svg-' + id);
      svg.classList.toggle('inactive', !active); svg.setAttribute('d', v !== null && v < 0 ? reverse : forward);
      svg.setAttribute('marker-end', active ? 'url(#flow-arrow-' + id + ')' : 'none');
      const route = flowRoutes.find(r => r.id === id);
      if (route) {
        route.active = active; route.direction = v !== null && v < 0 ? -1 : 1;
        route.speed = (1.5 + Math.min(Math.abs(v || 0) / 4000, 1.5)) / route.length;
        route.mesh.material.opacity = active ? .24 : .04; route.glow.material.opacity = active ? .10 : 0; route.pulse.visible = active;
      }
    }
    $('energy-pin-text').textContent = '⚡ ' + (d.soc !== null ? '电池 ' + d.soc.toFixed(0) + '%' : 'EnergyLIB · 待接入');
  }

  // ---------- Panel controls ----------
  function buildPanel() {
    $('energy-flow-defs').innerHTML = Object.entries(COLORS).map(([id, c]) => `<marker id="flow-arrow-${id}" viewBox="0 0 10 10" refX="10" refY="5" markerWidth="5" markerHeight="5" orient="auto"><path d="M0 0 L10 5 L0 10 L3 5 Z" fill="${c}"/></marker>`).join('');
    for (const id of Object.keys(COLORS)) $('svg-' + id).style.setProperty('--flow-color', COLORS[id]);
    $('energy-legend').innerHTML = [['solar', '光伏'], ['grid', '电网'], ['battery', '电池'], ['home', '家庭']].map(([id, label]) => `<span><i style="--flow-color:${COLORS[id]}"></i>${label}</span>`).join('');
    $('device-select').onchange = e => {
      device = devices.find(d => d.id === e.target.value) || device;
      try { localStorage.setItem(DEVICE_KEY, device.id); } catch { /* per-viewer convenience only */ }
      refresh();
    };
    $('quality-toggle').onclick = () => {
      fine = !fine; $('quality-toggle').textContent = fine ? '细腻画质' : '流畅画质';
      $('quality-toggle').setAttribute('aria-pressed', fine); resize();
    };
    $('energy-mini-toggle').onclick = () => document.body.classList.toggle('energy-mini-hidden');
    for (const button of document.querySelectorAll('[data-view]')) button.onclick = () => setView(button.dataset.view);
    $('daylight-toggle').onclick = () => {
      evening = !evening;
      scene.background.setHex(evening ? 0x253542 : 0xa9bac4);
      const key = scene.getObjectByName('Key_Light');
      key.intensity = evening ? .55 : 1.25; key.color.setHex(evening ? 0xffc487 : 0xffefd6);
      renderer.toneMappingExposure = evening ? .78 : .92;
      $('daylight-toggle').classList.toggle('active', evening);
    };
    const pin = $('pin-energy');
    pin.onclick = () => setView('energy');
    pins.energy = {el: pin, at: new THREE.Vector3(ENERGY_HUB.x, ENERGY_HUB.y + 4.15, ENERGY_HUB.z)};
  }

  function setView(name) {
    const [px, py, pz, tx, ty, tz, minDistance] = VIEWS[name];
    for (const b of document.querySelectorAll('[data-view]')) b.classList.toggle('active', b.dataset.view === name);
    controls.minDistance = minDistance;
    cameraTween = {t: 0, from: camera.position.clone(), fromTarget: controls.target.clone(), to: new THREE.Vector3(px, py, pz), toTarget: new THREE.Vector3(tx, ty, tz)};
  }

  function updatePins() {
    const halfW = innerWidth / 2, halfH = innerHeight / 2;
    for (const {el, at} of Object.values(pins)) {
      const p = at.clone().project(camera);
      if (p.z > 1) { el.style.display = 'none'; continue; }
      el.style.display = 'flex';
      el.style.left = (p.x * halfW + halfW) + 'px';
      el.style.top = (-p.y * halfH + halfH) + 'px';
    }
  }

  let previous = performance.now();
  function animate(now) {
    requestAnimationFrame(animate);
    const dt = Math.min(.1, ((now || performance.now()) - previous) / 1000); previous = now || performance.now();
    if (cameraTween) {
      cameraTween.t = Math.min(1, cameraTween.t + dt * 1.6);
      const k = 1 - Math.pow(1 - cameraTween.t, 3);
      camera.position.lerpVectors(cameraTween.from, cameraTween.to, k);
      controls.target.lerpVectors(cameraTween.fromTarget, cameraTween.toTarget, k);
      if (cameraTween.t >= 1) cameraTween = null;
    }
    stepFlows(dt);
    controls.update();
    updatePins();
    renderFrame();
  }

  function start() {
    if (!window.THREE || !THREE.GLTFLoader || !window.MeshoptDecoder) { failLoading('页面文件加载失败，请重新加载'); return; }
    initScene(); buildPanel(); loadModels(); buildGridPole(); buildFlows();
    refresh(); setInterval(refresh, 2000);
    window.energyTwin = {version: VERSION, focus: () => setView('energy'), getStatus: () => ({models: [...loaded], haConnected: !!hass, devices: devices.map(d => d.name), device: device && {name: device.name, entities: {...device.entities}}})};
    requestAnimationFrame(animate);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
})();
