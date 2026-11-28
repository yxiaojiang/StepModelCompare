// 解释后端行主序场景矩阵。前端只在后端放行、矩阵为右手系刚体且没有待复核标记时开放叠加。
import { Matrix4, Vector3 } from "three";

const epsilon = 1e-6;

/** 校验 candidate 的展示门禁与矩阵，返回 Three.js 变换或 null。不修改原数据。 */
export function overlayTransform(candidate) {
  const matrix = candidate?.align_matrix;
  if (candidate?.needs_review !== false || !Array.isArray(matrix) || matrix.length !== 4) return null;
  if (!matrix.every((row) => Array.isArray(row) && row.length === 4 && row.every(Number.isFinite))) return null;

  const [x, y, z, last] = matrix;
  if (last.some((value, index) => Math.abs(value - (index === 3 ? 1 : 0)) > epsilon)) return null;
  const rotation = [x, y, z].map((row) => new Vector3(...row.slice(0, 3)));
  for (let i = 0; i < 3; i += 1) {
    for (let j = i; j < 3; j += 1) {
      if (Math.abs(rotation[i].dot(rotation[j]) - (i === j ? 1 : 0)) > epsilon) return null;
    }
  }
  // Matrix4.set 接受行主序并自行转换存储布局，不能用 fromArray 直接载入。
  const transform = new Matrix4().set(...x, ...y, ...z, 0, 0, 0, 1);
  return Math.abs(transform.determinant() - 1) <= epsilon ? transform : null;
}
