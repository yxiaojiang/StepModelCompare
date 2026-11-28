// 管理工作台 WebGL 生命周期。两侧共享相机与控制器，各自居中且采用同一尺度。
import {
  AmbientLight, Box3, DirectionalLight, Group, Matrix4, PerspectiveCamera, Scene, Vector3, WebGLRenderer,
} from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";

const teal = 0x5bd8e1;
const amber = 0xffab5d;

function tint(model, color, ghost) {
  model.traverse((object) => {
    if (!object.isMesh) return;
    for (const material of [object.material].flat()) {
      if (material.transparent !== ghost || material.flatShading !== true) material.needsUpdate = true;
      // 平台 GLB 只保存位置与索引，按面计算法线以保留机械件的棱边。
      material.flatShading = true;
      material.color?.setHex(color);
      material.transparent = ghost;
      material.opacity = ghost ? 0.58 : 1;
      material.depthWrite = !ghost;
    }
  });
}

/** 用 host 中的 canvas 创建共享视口。返回模型、模式、复位与释放接口，WebGL 不可用时抛出异常。 */
export function createViewport(host, canvas) {
  const renderer = new WebGLRenderer({ canvas, alpha: true, antialias: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.setClearColor(0, 0);
  const scene = new Scene();
  const root = new Group();
  const queryGroup = new Group();
  const candidateGroup = new Group();
  queryGroup.matrixAutoUpdate = candidateGroup.matrixAutoUpdate = false;
  queryGroup.renderOrder = 1;
  candidateGroup.renderOrder = 2;
  root.add(queryGroup, candidateGroup);
  const keyLight = new DirectionalLight(0xffffff, 2.6);
  keyLight.position.set(2, 4, 3);
  const fillLight = new DirectionalLight(0xb0deff, 1.0);
  fillLight.position.set(-3, 1, -2);
  scene.add(root, keyLight, fillLight, new AmbientLight(0xd4e9ff, 1.2));

  const camera = new PerspectiveCamera(40, 1, 0.00001, 100);
  const controls = new OrbitControls(camera, canvas);
  controls.enableDamping = false;
  const upClose = new Vector3(1, 0.7, 1.25).normalize();
  const identity = new Matrix4();
  let mode = { transform: null, queryVisible: true, candidateVisible: true };
  let radius = 0.1;
  let width = 0;
  let height = 0;

  function draw() {
    if (!width || !height) return;
    renderer.setScissorTest(false);
    renderer.clear();
    if (mode.transform) {
      queryGroup.visible = mode.queryVisible;
      candidateGroup.visible = mode.candidateVisible;
      paint(0, width);
    } else {
      // 两个半区各自裁切，旋转、平移和缩放读取同一个相机。
      const left = Math.floor(width / 2);
      renderer.setScissorTest(true);
      queryGroup.visible = true;
      candidateGroup.visible = false;
      paint(0, left);
      queryGroup.visible = false;
      candidateGroup.visible = true;
      paint(left, width - left);
      renderer.setScissorTest(false);
    }
  }

  function paint(offset, span) {
    if (span < 1) return;
    camera.aspect = span / height;
    camera.updateProjectionMatrix();
    renderer.setViewport(offset, 0, span, height);
    renderer.setScissor(offset, 0, span, height);
    renderer.render(scene, camera);
  }

  function reset() {
    const aspect = width / (height || 1) / (mode.transform ? 1 : 2);
    const halfFov = camera.fov * Math.PI / 360;
    const angle = Math.atan(Math.tan(halfFov) * Math.min(1, aspect || 1));
    const distance = radius * 1.18 / Math.sin(angle);
    controls.minDistance = radius * 0.08;
    controls.maxDistance = Math.max(radius * 60, distance * 4);
    camera.near = radius / 3000;
    camera.far = controls.maxDistance + radius * 5;
    controls.target.set(0, 0, 0);
    camera.position.copy(upClose).multiplyScalar(distance);
    controls.update();
    draw();
  }

  function layout() {
    root.position.set(0, 0, 0);
    queryGroup.matrix.copy(identity);
    candidateGroup.matrix.copy(mode.transform ?? identity);
    root.updateMatrixWorld(true);
    const queryBounds = new Box3().setFromObject(queryGroup, true);
    const candidateBounds = new Box3().setFromObject(candidateGroup, true);
    if (mode.transform) {
      const together = queryBounds.union(candidateBounds);
      // 共享的平移只用于取景，候选件在世界坐标先应用服务端矩阵，不单独居中。
      root.position.copy(together.getCenter(new Vector3())).negate();
      radius = Math.max(together.getSize(new Vector3()).length() / 2, 0.000001);
    } else {
      for (const [group, bounds] of [[queryGroup, queryBounds], [candidateGroup, candidateBounds]]) {
        const center = bounds.getCenter(new Vector3());
        group.matrix.makeTranslation(-center.x, -center.y, -center.z);
      }
      radius = Math.max(queryBounds.getSize(new Vector3()).length() / 2,
        candidateBounds.getSize(new Vector3()).length() / 2, 0.000001);
    }
    reset();
  }

  function shade() {
    const ghost = Boolean(mode.transform && mode.queryVisible && mode.candidateVisible);
    tint(queryGroup, teal, ghost);
    tint(candidateGroup, amber, ghost);
  }

  const observer = new ResizeObserver(() => {
    const { clientWidth, clientHeight } = host;
    const initial = !width || !height;
    width = clientWidth;
    height = clientHeight;
    if (!width || !height) return;
    renderer.setSize(width, height, false);
    if (initial) reset();
    else draw();
  });
  observer.observe(host);
  controls.addEventListener("change", draw);

  return {
    // 场景资源由预览加载方拥有，此处只挂接或移除对象。
    setModels(query, candidate) {
      queryGroup.clear();
      candidateGroup.clear();
      if (query) queryGroup.add(query);
      if (candidate) candidateGroup.add(candidate);
      shade();
      layout();
    },
    setMode(transform, queryVisible, candidateVisible) {
      const changed = transform !== mode.transform;
      mode = { transform, queryVisible, candidateVisible };
      shade();
      if (changed) layout();
      else draw();
    },
    reset,
    dispose() {
      observer.disconnect();
      controls.removeEventListener("change", draw);
      controls.dispose();
      queryGroup.clear();
      candidateGroup.clear();
      renderer.dispose();
    },
  };
}
