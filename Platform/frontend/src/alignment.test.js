// 回归矩阵方向、量纲和待复核时强制禁用叠加。
import assert from "node:assert/strict";
import { test } from "node:test";
import { Vector3 } from "three";

import { overlayTransform } from "./alignment.js";

const rigid = [[0, -1, 0, 0.03], [1, 0, 0, -0.05], [0, 0, 1, 0.2], [0, 0, 0, 1]];

test("候选件按行主序先旋转再平移，不改变比例", () => {
  const result = overlayTransform({ needs_review: false, align_matrix: rigid });
  assert.ok(result);
  const vertex = new Vector3(0.2, 0, 0.1).applyMatrix4(result);
  assert.ok(vertex.distanceTo(new Vector3(0.03, 0.15, 0.3)) < 1e-12);
});

test("待复核、缺矩阵、镜像、缩放、剪切和透视均禁用叠加", () => {
  assert.equal(overlayTransform({ needs_review: true, align_matrix: rigid }), null);
  assert.equal(overlayTransform({ align_matrix: rigid }), null);
  assert.equal(overlayTransform({ needs_review: false, align_matrix: null }), null);
  for (const bad of [
    [[-1, 0, 0, 0], [0, 1, 0, 0], rigid[2], rigid[3]],
    [[0, -2, 0, 0], rigid[1], rigid[2], rigid[3]],
    [[0, -1, 0.1, 0], rigid[1], rigid[2], rigid[3]],
    [rigid[0], rigid[1], rigid[2], [0.1, 0, 0, 1]],
    [[0, -1, 0, Infinity], rigid[1], rigid[2], rigid[3]],
  ]) assert.equal(overlayTransform({ needs_review: false, align_matrix: bad }), null);
});
