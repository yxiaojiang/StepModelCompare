import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';

const NEW_COLOR = 0x47baf6;
const OLD_COLOR = 0xf5a452;
const matrix = rows => new THREE.Matrix4().set(...rows.flat());
const vector = coordinates => new THREE.Vector3(...coordinates);
const loader = new GLTFLoader();

function dispose(object) {
  object?.traverse(node => {
    if (node.isMesh || node.isLine) {
      node.geometry.dispose();
      node.material.dispose();
    }
  });
}

function pane(host) {
  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  host.querySelector('.canvas').append(renderer.domElement);
  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(38, 1, 0.001, 100);
  const control = new OrbitControls(camera, renderer.domElement);
  const key = new THREE.DirectionalLight(0xffffff, 2.3);
  key.position.set(2, 4, 3);
  scene.add(new THREE.HemisphereLight(0xe8f6ff, 0x647b96, 2.2), key);
  return { host, renderer, scene, camera, control, center: new THREE.Vector3() };
}

function decorate(group, color) {
  group.traverse(node => {
    if (!node.isMesh) return;
    if (!node.geometry.attributes.normal) node.geometry.computeVertexNormals();
    node.material.dispose();
    node.material = new THREE.MeshStandardMaterial({ color, roughness: 0.75, metalness: 0.08 });
  });
  return group;
}

export class Viewer {
  constructor(container) {
    this.container = container;
    this.left = pane(container.querySelector('#new-pane'));
    this.right = pane(container.querySelector('#old-pane'));
    this.panes = [this.left, this.right];
    this.views = container.querySelector('#view-mode');
    this.showBoth = container.querySelector('#visibility');
    this.opacity = container.querySelector('#opacity');
    this.holeButton = container.querySelector('#hole-view');
    this.holeText = container.querySelector('#hole-value');
    this.preset = new THREE.Vector3(1.1, 1.45, 1.5).normalize();
    this.revision = 0;
    this.views.addEventListener('change', () => this.setMode(this.views.value));
    this.showBoth.addEventListener('change', () => this.visibility());
    this.opacity.addEventListener('input', () => this.visibility());
    this.holeButton.addEventListener('click', () => this.focusHole());
    container.querySelector('#reset-view').addEventListener('click', () => this.setMode(this.views.value));
    for (const [current, other] of [[this.left, this.right], [this.right, this.left]]) {
      current.control.addEventListener('change', () => {
        if (this.syncing || this.mode === 'overlay') return;
        this.syncing = true;
        const offset = other.center.clone().sub(current.center);
        other.control.target.copy(current.control.target).add(offset);
        other.camera.position.copy(current.camera.position).add(offset);
        other.camera.up.copy(current.camera.up);
        other.control.update();
        this.syncing = false;
      });
    }
    const observer = new ResizeObserver(() => this.render());
    this.panes.forEach(view => observer.observe(view.host));
    this.left.renderer.setAnimationLoop(() => this.render());
    this.clear();
  }

  render() {
    for (const { host, renderer, camera, scene } of this.panes) {
      const canvas = host.querySelector('.canvas');
      const width = canvas.clientWidth;
      const height = canvas.clientHeight;
      if (!width || !height) continue;
      if (renderer.domElement.style.width !== `${width}px` ||
          renderer.domElement.style.height !== `${height}px`) {
        renderer.setSize(width, height);
        camera.aspect = width / height;
        camera.updateProjectionMatrix();
      }
      renderer.render(scene, camera);
    }
  }

  clear() {
    this.revision++;
    for (const object of [this.newModel, this.oldModel, this.marker]) {
      object?.removeFromParent();
      dispose(object);
    }
    this.newModel = this.oldModel = this.marker = this.result = null;
    this.panes.forEach(view => { view.host.querySelector('.model-name').textContent = ''; });
    this.container.dataset.ready = 'false';
    this.container.dataset.mode = 'side';
    this.right.host.hidden = false;
    this.views.disabled = this.showBoth.disabled = this.opacity.disabled = true;
    this.views.value = 'side';
    this.holeButton.hidden = this.holeText.hidden = true;
    this.holeText.textContent = '';
    delete this.holeText.dataset.mm;
    this.mode = 'side';
  }

  async load(newModel, oldModel, result) {
    this.clear();
    const revision = this.revision;
    const loaded = await Promise.allSettled([loader.loadAsync(newModel.glb), loader.loadAsync(oldModel.glb)]);
    if (revision !== this.revision || loaded.some(item => item.status === 'rejected')) {
      loaded.filter(item => item.status === 'fulfilled').forEach(item => dispose(item.value.scene));
      const failure = loaded.find(item => item.status === 'rejected');
      if (revision === this.revision && failure) throw failure.reason;
      return false;
    }
    this.newModel = decorate(loaded[0].value.scene, NEW_COLOR);
    this.oldModel = decorate(loaded[1].value.scene, OLD_COLOR);
    this.newInfo = newModel;
    this.result = result;
    this.left.host.querySelector('.model-name').textContent = newModel.label;
    this.right.host.querySelector('.model-name').textContent = oldModel.label;
    this.views.disabled = !result.scene_matrix;
    const hole = result.hole;
    if (hole) {
      this.holeText.hidden = false;
      this.holeButton.hidden = false;
      if (hole.status === 'ok') {
        this.holeText.dataset.mm = String(hole.max_mm);
        this.holeText.textContent = `孔区最大表面偏差 ${hole.max_mm.toFixed(3)} mm（双向网格采样估算）`;
      } else {
        this.holeText.textContent = '孔区采样不足';
      }
    }
    this.setMode('side');
    this.container.dataset.ready = 'true';
    return true;
  }

  setMode(mode) {
    if (!this.newModel || (mode === 'overlay' && !this.result.scene_matrix)) return;
    this.mode = mode;
    this.container.dataset.mode = mode;
    this.views.value = mode;
    this.showBoth.value = 'both';
    this.right.host.hidden = mode === 'overlay';
    this.left.host.querySelector('.pane-kind').textContent = mode === 'overlay' ? '同坐标叠加' : '查询件';
    this.left.scene.add(this.newModel);
    this.oldModel.matrixAutoUpdate = false;
    this.oldModel.matrix.identity();
    if (mode === 'overlay') {
      this.oldModel.matrix.copy(matrix(this.result.scene_matrix));
      this.left.scene.add(this.oldModel);
    } else {
      this.right.scene.add(this.oldModel);
    }
    this.oldModel.updateMatrixWorld(true);
    if (this.marker) this.marker.visible = false;
    const newBounds = new THREE.Box3().setFromObject(this.newModel);
    const oldBounds = new THREE.Box3().setFromObject(this.oldModel);
    if (mode === 'overlay') newBounds.union(oldBounds);
    const radius = Math.max(...[newBounds, oldBounds].map(bounds => bounds.getSize(new THREE.Vector3()).length()));
    this.syncing = true;
    for (const [view, bounds] of [[this.left, newBounds], [this.right, oldBounds]]) {
      bounds.getCenter(view.center);
      this.aim(view, view.center, this.preset, new THREE.Vector3(0, 1, 0), radius * 1.8);
    }
    this.syncing = false;
    this.visibility();
  }

  aim(view, center, direction, up, distance) {
    view.camera.position.copy(center).addScaledVector(direction, distance);
    view.camera.up.copy(up);
    view.camera.near = Math.max(distance / 5000, 1e-7);
    view.camera.far = Math.max(distance * 600, 1);
    view.camera.updateProjectionMatrix();
    // OrbitControls uses camera.up at construction. Keep the default up after
    // orienting the top-down camera, so subsequent drag operations remain stable.
    view.control.target.copy(center);
    view.control.update();
    view.camera.lookAt(center);
  }

  visibility() {
    if (!this.result) return;
    const overlay = this.mode === 'overlay';
    const both = this.showBoth.value === 'both';
    this.showBoth.disabled = !overlay;
    this.opacity.disabled = !overlay || !both;
    this.newModel.visible = !overlay || this.showBoth.value !== 'old';
    this.oldModel.visible = !overlay || this.showBoth.value !== 'new';
    for (const [model, opacity] of [[this.newModel, 0.78], [this.oldModel, Number(this.opacity.value)]]) {
      model.traverse(node => {
        if (!node.isMesh) return;
        node.material.transparent = overlay && both;
        node.material.opacity = overlay && both ? opacity : 1;
        node.material.depthWrite = !overlay || !both;
      });
    }
  }

  focusHole() {
    const region = this.result?.hole?.region;
    if (!region) return;
    this.setMode('overlay');
    const conversion = matrix(this.newInfo.cad_to_scene);
    const center = vector(region.center_mm).applyMatrix4(conversion);
    const direction = vector(region.view_mm).transformDirection(conversion);
    const up = vector(region.up_mm).transformDirection(conversion);
    const [width, height] = region.size_mm;
    this.aim(this.left, center, direction, up, Math.max(width, height) * 0.0024);
    if (this.marker) {
      this.marker.removeFromParent();
      dispose(this.marker);
    }
    const [x, y, z] = region.center_mm;
    const corners = [[-1, -1], [-1, 1], [1, 1], [1, -1]].map(([i, j]) =>
      vector([x + i * width / 2, y + j * height / 2, z + 0.12]).applyMatrix4(conversion));
    this.marker = new THREE.LineLoop(new THREE.BufferGeometry().setFromPoints(corners),
      new THREE.LineBasicMaterial({ color: 0x64f3bd, depthTest: false }));
    this.marker.renderOrder = 10;
    this.left.scene.add(this.marker);
  }
}
